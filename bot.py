import os
import time
import requests
from datetime import datetime

TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]

# Ajustes
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "300"))  # 5 min por defecto
TIMEOUT = 15
BYBIT_URL = "https://api.bybit.com/v5/market/tickers"

def tg_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        # No crashear por Telegram
        print("Telegram error:", e)

def get_price(symbol: str) -> float | None:
    params = {"category": "linear", "symbol": symbol}
    try:
        r = requests.get(BYBIT_URL, params=params, timeout=TIMEOUT)

        # Si Bybit responde algo raro (HTML / vacío), evita .json() directo
        if r.status_code != 200:
            print(f"Bybit HTTP {r.status_code} for {symbol}: {r.text[:120]}")
            return None

        # Parse seguro
        data = r.json()

        # Validaciones
        if data.get("retCode") != 0:
            print(f"Bybit retCode {data.get('retCode')} for {symbol}: {data.get('retMsg')}")
            return None

        items = data.get("result", {}).get("list", [])
        if not items:
            print(f"Bybit empty list for {symbol}: {data}")
            return None

        last = items[0].get("lastPrice")
        if last is None:
            print(f"Bybit missing lastPrice for {symbol}: {items[0]}")
            return None

        return float(last)

    except requests.exceptions.JSONDecodeError as e:
        print(f"JSON decode error for {symbol}: {e} | text={r.text[:120] if 'r' in locals() else ''}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Request error for {symbol}: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error for {symbol}: {e}")
        return None

def main():
    tg_send("🤖 Bot iniciado (Bybit tickers + Telegram).")

    while True:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        for sym in SYMBOLS:
            price = get_price(sym)
            if price is None:
                continue

            msg = f"📡 {sym} | Price: {price}\n{now}"
            tg_send(msg)

            # pequeña pausa para no spamear / evitar rate limits
            time.sleep(1)

        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    main()
