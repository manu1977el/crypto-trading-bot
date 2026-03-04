import requests
import time

BOT_TOKEN = "TU_TOKEN"
CHAT_ID = "7476702452"

symbols = [
"BTCUSDT",
"ETHUSDT",
"SOLUSDT",
"BNBUSDT",
"XRPUSDT"
]

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }
    requests.post(url, data=payload)

def get_price(symbol):
    url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
    data = requests.get(url).json()
    return float(data["result"]["list"][0]["lastPrice"])

while True:

    for symbol in symbols:

        price = get_price(symbol)

        message = f"""
🚨 MARKET UPDATE

{symbol}

Price: {price}
"""

        send_message(message)

    time.sleep(600)
