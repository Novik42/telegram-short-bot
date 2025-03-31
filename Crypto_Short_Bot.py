import requests
from telegram import Bot
from bs4 import BeautifulSoup
import asyncio
import time
from datetime import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# 🔹 Введи свій Telegram-токен і CHAT_ID
TELEGRAM_TOKEN = "ТВОЙ_TELEGRAM_TOKEN"
CHAT_ID = "ТВОЙ_CHAT_ID"

bot = Bot(token=TELEGRAM_TOKEN)

# 🔹 Надсилання повідомлення в Telegram (асинхронно)
async def send_telegram_message(text):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

# 🔹 Запис результатів у файл
def save_to_file(text):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("short_signals_log.txt", "a") as f:
        f.write(f"[{timestamp}]\n{text}\n\n")

# 🔹 Отримання нових токенів з CoinGecko API
def get_new_tokens():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "order": "newest", "per_page": 20, "page": 1}
    response = requests.get(url, params=params)
    return response.json()

# 🔹 Отримання премаркетних цін з MEXC API
def get_mexc_premarket(symbol):
    url = f"https://www.mexc.com/open/api/v2/market/depth?symbol={symbol}_usdt&depth=5"
    try:
        response = requests.get(url)
        if response.status_code != 200 or not response.text:
            return None
        data = response.json()
        asks = data.get("data", {}).get("asks", [])
        if asks:
            return float(asks[0][0])
    except Exception:
        pass
    return None

# 🔹 Аналіз токенів
def analyze_token(token):
    name = token["name"]
    price = token["current_price"]
    market_cap = token.get("market_cap", 0)
    symbol = token["symbol"].upper()
    date_listed = token.get("atl_date", "невідомо")[:10]

    # 🔻 Фільтр популярних токенів та великих капіталізацій
    excluded_tokens = ["BTC", "ETH", "USDT", "SOL", "ADA", "DOGE", "TRX", "USDC"]
    if symbol in excluded_tokens or market_cap > 100000000:
        return None

    premarket_price = get_mexc_premarket(symbol)

    # 🔻 Логіка LONG/SHORT
    if premarket_price:
        if premarket_price > price:
            potential = "🟢 Потенціал для лонгу"
        elif premarket_price < price:
            potential = "🔴 Потенціал для шорту"
        else:
            potential = "⚪️ Нейтрально"
    else:
        potential = "⚠️ Недостатньо даних"

    # 🔹 Попередній автоматичний рейтинг (для прикладу)
    rating = 6 if market_cap < 50000000 else 4
    rating_text = f"{rating}/10"

    result = (
        f"🚀 *Новий токен*: {name} ({symbol})\n"
        f"💲 Ціна: ${price}\n"
        f"📊 Капа: ${market_cap:,}\n"
        f"📅 Лістинг: {date_listed} (CoinGecko)\n"
        f"📈 Рейтинг: {rating_text}\n"
        f"{potential}"
    )

    return result

# 🔹 Основна асинхронна функція
async def main_loop():
    while True:
        try:
            await send_telegram_message("🔄 Автоматичний запуск перевірки токенів")
            tokens = get_new_tokens()

            for token in tokens:
                result = analyze_token(token)
                if result:
                    await send_telegram_message(result)
                    save_to_file(result)

        except Exception as e:
            error_msg = f"❌ Помилка в циклі: {e}"
            save_to_file(error_msg)

        await asyncio.sleep(3600)  # запуск раз на годину

# 🔹 HTTP-заглушка для Render
class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), StubHandler)
    server.serve_forever()

# 🔹 Запуск бота
if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    time.sleep(2)
    asyncio.run(main_loop())