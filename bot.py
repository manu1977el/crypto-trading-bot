import os
import time
import json
import math
import requests
from datetime import datetime, timezone

# =========================
# ENV / CONFIG
# =========================
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]

CAPITAL = float(os.getenv("CAPITAL", "100"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))  # 1% por trade
MAX_SIGNALS_PER_DAY = int(os.getenv("MAX_SIGNALS_PER_DAY", "5"))
SCAN_EVERY_SECONDS = int(os.getenv("SCAN_EVERY_SECONDS", "60"))

# Bybit (public)
BYBIT_BASE = "https://api.bybit.com"
KLINE_ENDPOINT = "/v5/market/kline"
CATEGORY = os.getenv("BYBIT_CATEGORY", "linear")  # linear = USDT perpetual (futuros)
TIMEOUT = 15

# Timeframes
TF_MAP = {
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
}

# Universe (grandes). Ajusta si quieres.
SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "ADAUSDT","DOGEUSDT","LINKUSDT","AVAXUSDT","DOTUSDT",
    "LTCUSDT","ATOMUSDT","UNIUSDT","NEARUSDT","INJUSDT",
    "APTUSDT","ARBUSDT","OPUSDT","MATICUSDT","FTMUSDT",
    "SUIUSDT","SEIUSDT","TIAUSDT","RNDRUSDT","AAVEUSDT",
    "FILUSDT","ETCUSDT","TRXUSDT","XLMUSDT","ICPUSDT",
    "BCHUSDT","EOSUSDT","THETAUSDT","KAVAUSDT","GRTUSDT",
    "RUNEUSDT","IMXUSDT","STXUSDT","MKRUSDT","SNXUSDT",
    "DYDXUSDT","ZRXUSDT","COMPUSDT","CRVUSDT","ENJUSDT",
    "1INCHUSDT","SANDUSDT","MANAUSDT","CHZUSDT","APEUSDT"
]

STATE_FILE = "state.json"

# Strategy params (intradía + swing)
EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ATR_PERIOD = 14

ATR_MULT_SL_INTRADAY = 1.5
ATR_MULT_SL_SWING = 2.0
RISK_REWARD = 2.0  # TP = 2R

# Setup thresholds
BREAKOUT_LOOKBACK = 40        # velas
VOL_SPIKE_MULT = 1.8          # volumen actual > 1.8x media
CHOP_CROSS_LIMIT = 10         # si cruza EMA20 demasiadas veces, es rango (chop)

SCORE_THRESHOLD = 8.0         # solo manda señales >= 8
MAX_SIGNALS_PER_SCAN = 2      # para no mandar 20 seguidas si el mercado se vuelve loco

# =========================
# Telegram
# =========================
def tg_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print("Telegram error:", e)

# =========================
# State
# =========================
def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("State save error:", e)

def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# =========================
# Indicators (pure python)
# =========================
def ema(values, period):
    # returns list of EMA with same length, first value = first close
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out

def rsi(closes, period=14):
    if len(closes) < period + 2:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i-1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def atr(highs, lows, closes, period=14):
    if len(closes) < period + 2:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    # Wilder smoothing
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a

def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:.2f}"
    if p >= 10:
        return f"{p:.4f}"
    return f"{p:.6f}"

# =========================
# Bybit data
# =========================
def fetch_klines(symbol: str, interval: str, limit: int = 200):
    # returns list of candles old->new: (ts, o,h,l,c,v)
    url = BYBIT_BASE + KLINE_ENDPOINT
    params = {"category": CATEGORY, "symbol": symbol, "interval": interval, "limit": str(limit)}
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            print("Bybit HTTP", r.status_code, symbol, interval, r.text[:120])
            return None
        data = r.json()
        if data.get("retCode") != 0:
            print("Bybit retCode", data.get("retCode"), symbol, interval, data.get("retMsg"))
            return None
        rows = data.get("result", {}).get("list", [])
        if not rows:
            return None
        # Bybit devuelve newest->oldest. Convertimos a old->new
        rows = list(reversed(rows))
        candles = []
        for row in rows:
            # [startTime, open, high, low, close, volume, turnover]
            ts = int(row[0])
            o = float(row[1]); h = float(row[2]); l = float(row[3]); c = float(row[4]); v = float(row[5])
            candles.append((ts, o, h, l, c, v))
        return candles
    except Exception as e:
        print("fetch_klines error", symbol, interval, e)
        return None

# =========================
# Market filter (anti-chop)
# =========================
def chop_score(closes, ema20, lookback=30):
    # cuenta cruces de close vs EMA20
    if len(closes) < lookback + 2:
        return 0
    crosses = 0
    start = len(closes) - lookback
    for i in range(start + 1, len(closes)):
        prev = closes[i-1] - ema20[i-1]
        cur = closes[i] - ema20[i]
        if prev == 0:
            continue
        if (prev > 0 and cur < 0) or (prev < 0 and cur > 0):
            crosses += 1
    return crosses

# =========================
# Scoring + trade plan
# =========================
def position_size(entry, sl):
    risk_eur = CAPITAL * RISK_PCT
    dist = abs(entry - sl)
    if dist <= 0:
        return None
    qty = risk_eur / dist
    return qty

def build_plan(direction, entry, atr_val, is_swing=False):
    if atr_val is None or atr_val <= 0:
        return None
    atr_mult = ATR_MULT_SL_SWING if is_swing else ATR_MULT_SL_INTRADAY
    if direction == "LONG":
        sl = entry - atr_mult * atr_val
        tp = entry + RISK_REWARD * (entry - sl)
    else:
        sl = entry + atr_mult * atr_val
        tp = entry - RISK_REWARD * (sl - entry)

    qty = position_size(entry, sl)
    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "atr": atr_val,
        "atr_mult": atr_mult,
        "qty": qty
    }

def score_signal(trend_ok, vol_spike, breakout_strength, rsi_edge, chop_ok):
    # score 0..10
    score = 0.0
    score += 2.5 if trend_ok else 0.5
    score += 2.0 if vol_spike else 0.5
    score += min(2.0, breakout_strength * 2.0)  # 0..2
    score += min(2.0, rsi_edge * 2.0)           # 0..2
    score += 1.5 if chop_ok else 0.0
    return max(0.0, min(10.0, score))

# =========================
# Strategy detection
# =========================
def detect_setups(symbol: str, tf_name: str, candles):
    # returns list of dict signals
    ts, o, h, l, c, v = zip(*candles)
    closes = list(c); highs = list(h); lows = list(l); vols = list(v)

    if len(closes) < 120:
        return []

    e20 = ema(closes, EMA_FAST)
    e50 = ema(closes, EMA_SLOW)
    r = rsi(closes, RSI_PERIOD)
    a = atr(highs, lows, closes, ATR_PERIOD)

    last_close = closes[-1]
    prev_close = closes[-2]
    last_vol = vols[-1]
    avg_vol = sum(vols[-(BREAKOUT_LOOKBACK+1):-1]) / BREAKOUT_LOOKBACK

    trend_long = e20[-1] > e50[-1]
    trend_short = e20[-1] < e50[-1]
    ema_gap = abs(e20[-1] - e50[-1]) / last_close if last_close else 0.0

    # chop filter (anti lateral)
    crosses = chop_score(closes, e20, lookback=30)
    chop_ok = crosses <= CHOP_CROSS_LIMIT

    vol_spike = avg_vol > 0 and (last_vol / avg_vol) >= VOL_SPIKE_MULT

    signals = []

    # Determine if swing TF
    is_swing = tf_name in ("1h", "4h")

    # Setup A: Breakout / Breakdown (con volumen)
    if len(highs) > BREAKOUT_LOOKBACK + 2:
        prev_high = max(highs[-(BREAKOUT_LOOKBACK+1):-1])
        prev_low  = min(lows[-(BREAKOUT_LOOKBACK+1):-1])

        # breakout long
        if prev_close <= prev_high < last_close and chop_ok:
            breakout_strength = (last_close - prev_high) / (a if a else last_close)
            rsi_edge = 0.5 if (r is not None and r < 75) else 0.2
            sc = score_signal(trend_long, vol_spike, breakout_strength, rsi_edge, chop_ok)
            signals.append({
                "setup": "Breakout",
                "dir": "LONG",
                "score": sc,
                "reason": f"Ruptura > max {BREAKOUT_LOOKBACK} velas; vol_spike={'YES' if vol_spike else 'NO'}; chop_crosses={crosses}"
            })

        # breakdown short
        if prev_close >= prev_low > last_close and chop_ok:
            breakout_strength = (prev_low - last_close) / (a if a else last_close)
            rsi_edge = 0.5 if (r is not None and r > 25) else 0.2
            sc = score_signal(trend_short, vol_spike, breakout_strength, rsi_edge, chop_ok)
            signals.append({
                "setup": "Breakdown",
                "dir": "SHORT",
                "score": sc,
                "reason": f"Ruptura < min {BREAKOUT_LOOKBACK} velas; vol_spike={'YES' if vol_spike else 'NO'}; chop_crosses={crosses}"
            })

    # Setup B: Trend pullback bounce/reject
    # LONG: trend_long y cierre vuelve a cruzar por encima de EMA20
    if trend_long:
        if (prev_close < e20[-2]) and (last_close > e20[-1]):
            breakout_strength = ema_gap  # cuanto más gap, más fuerte la tendencia
            rsi_edge = 0.6 if (r is not None and r < 65) else 0.3
            sc = score_signal(True, vol_spike, breakout_strength, rsi_edge, chop_ok)
            signals.append({
                "setup": "PullbackTrend",
                "dir": "LONG",
                "score": sc,
                "reason": f"Trend EMA20>EMA50 + rebote EMA20; chop_crosses={crosses}"
            })

    # SHORT: trend_short y cierre cruza por debajo de EMA20
    if trend_short:
        if (prev_close > e20[-2]) and (last_close < e20[-1]):
            breakout_strength = ema_gap
            rsi_edge = 0.6 if (r is not None and r > 35) else 0.3
            sc = score_signal(True, vol_spike, breakout_strength, rsi_edge, chop_ok)
            signals.append({
                "setup": "PullbackTrend",
                "dir": "SHORT",
                "score": sc,
                "reason": f"Trend EMA20<EMA50 + rechazo EMA20; chop_crosses={crosses}"
            })

    # Setup C: RSI Reversion (solo si no hay trend fuerte)
    # para evitar pelear contra tendencia
    if ema_gap < 0.004 and r is not None:
        # LONG if RSI crosses up from oversold-ish
        # (aprox: r < 30)
        if r < 30:
            sc = score_signal(False, vol_spike, 0.2, 0.9, True)
            signals.append({
                "setup": "RSIReversion",
                "dir": "LONG",
                "score": sc,
                "reason": f"RSI<{30} (mean reversion) + EMA gap bajo"
            })
        # SHORT if RSI > 70
        if r > 70:
            sc = score_signal(False, vol_spike, 0.2, 0.9, True)
            signals.append({
                "setup": "RSIReversion",
                "dir": "SHORT",
                "score": sc,
                "reason": f"RSI>{70} (mean reversion) + EMA gap bajo"
            })

    # Attach plan for each
    out = []
    for s in signals:
        plan = build_plan(s["dir"], last_close, a, is_swing=is_swing)
        if plan is None:
            continue
        s["tf"] = tf_name
        s["entry"] = plan["entry"]
        s["sl"] = plan["sl"]
        s["tp"] = plan["tp"]
        s["atr"] = plan["atr"]
        s["qty"] = plan["qty"]
        s["atr_mult"] = plan["atr_mult"]
        out.append(s)

    return out

# =========================
# Dedupe + daily limit
# =========================
def key_for(sig):
    return f"{sig['setup']}|{sig['dir']}|{sig['tf']}"

def already_sent_today(state, symbol, sig_key, day):
    sent = state.get("sent", {}).get(day, {})
    return sig_key in sent.get(symbol, [])

def mark_sent(state, symbol, sig_key, day):
    state.setdefault("sent", {}).setdefault(day, {}).setdefault(symbol, [])
    state["sent"][day][symbol].append(sig_key)

def daily_count(state, day):
    return int(state.get("count", {}).get(day, 0))

def inc_daily(state, day):
    state.setdefault("count", {})
    state["count"][day] = daily_count(state, day) + 1

# =========================
# Main loop
# =========================
def main():
    state = load_state()
    tg_send("🤖 Scanner ON: 5m/15m/1h/4h | LONG+SHORT | max 5 señales/día")

    while True:
        day = utc_day()

        # reset viejo para no crecer infinito
        state.setdefault("sent", {})
        state.setdefault("count", {})
        # limpia días antiguos (mantén 3 días)
        if len(state["sent"]) > 3:
            for d in sorted(state["sent"].keys())[:-3]:
                state["sent"].pop(d, None)
                state["count"].pop(d, None)

        remaining = MAX_SIGNALS_PER_DAY - daily_count(state, day)
        if remaining <= 0:
            time.sleep(SCAN_EVERY_SECONDS)
            continue

        candidates = []
        for sym in SYMBOLS:
            if remaining <= 0:
                break

            for tf_name, interval in TF_MAP.items():
                candles = fetch_klines(sym, interval, limit=220)
                if not candles:
                    continue

                sigs = detect_setups(sym, tf_name, candles)
                for sig in sigs:
                    if sig["score"] < SCORE_THRESHOLD:
                        continue
                    sig_key = key_for(sig)
                    if already_sent_today(state, sym, sig_key, day):
                        continue

                    candidates.append((sig["score"], sym, sig))

            # pequeña pausa anti-rate-limit
            time.sleep(0.2)

        # ordena por score y manda top N (máx por scan y por día)
        candidates.sort(key=lambda x: x[0], reverse=True)

        sent_now = 0
        for _, sym, sig in candidates:
            if daily_count(state, day) >= MAX_SIGNALS_PER_DAY:
                break
            if sent_now >= MAX_SIGNALS_PER_SCAN:
                break

            sig_key = key_for(sig)
            if already_sent_today(state, sym, sig_key, day):
                continue

            risk_eur = CAPITAL * RISK_PCT
            qty_txt = "N/A"
            if sig["qty"] is not None:
                qty_txt = f"{sig['qty']:.4f}"

            msg = (
                f"🚨 SIGNAL ({sig['tf']})\n\n"
                f"{sym} — {sig['dir']} — {sig['setup']}\n"
                f"Score: {sig['score']:.1f}/10\n\n"
                f"Entry: {fmt_price(sig['entry'])}\n"
                f"SL: {fmt_price(sig['sl'])}  (≈ {sig['atr_mult']} ATR)\n"
                f"TP: {fmt_price(sig['tp'])}  (RR {RISK_REWARD})\n"
                f"ATR({ATR_PERIOD}): {fmt_price(sig['atr'])}\n\n"
                f"Risk: {risk_eur:.2f}€ ({RISK_PCT*100:.0f}% de {CAPITAL:.0f}€)\n"
                f"Posición (ref): {qty_txt} unidades\n\n"
                f"Notas: {sig['reason']}\n"
                f"Fuente: Bybit {CATEGORY}"
            )

            tg_send(msg)
            mark_sent(state, sym, sig_key, day)
            inc_daily(state, day)
            save_state(state)
            sent_now += 1

        time.sleep(SCAN_EVERY_SECONDS)

if __name__ == "__main__":
    main()
