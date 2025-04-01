import requests
from telegram import Bot
from bs4 import BeautifulSoup
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
import time

# 🔹 Введи свій Telegram-токен і CHAT_ID
TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

bot = Bot(token=TELEGRAM_TOKEN)

# 🔹 Асинхронне повідомлення в Telegram
async def send_telegram_message(text):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

# 🔹 Запис логів у файл
def save_to_file(text):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("short_signals_log.txt", "a") as f:
        f.write(f"[{timestamp}]\n{text}\n\n")

# 🔹 Отримання нових токенів з CoinGecko
def get_new_tokens():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "order": "newest", "per_page": 10, "page": 1}
    response = requests.get(url, params=params)
    return response.json()

# 🔹 Отримання премаркетних цін з MEXC
def get_mexc_premarket(symbol):
    url = f"https://www.mexc.com/open/api/v2/market/depth?symbol={symbol}_usdt&depth=5"
    try:
        response = requests.get(url)
        data = response.json()
        asks = data.get("data", {}).get("asks", [])
        return float(asks[0][0]) if asks else None
    except Exception as e:
        print(f"Помилка MEXC: {e}")
        return None

# 🔹 Отримання новин з Lookonchain
def get_latest_twitter_news():
    url = "https://nitter.net/Lookonchain"
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        tweets = soup.find_all("div", class_="timeline-item")
        news = [tweet.text.strip() for tweet in tweets[:3]]
        return news
    except Exception as e:
        print(f"Помилка Twitter: {e}")
        return []

# 🔹 Оцінка (рейтинг) токена
def calculate_rating(token):
    rating = 0
    market_cap = token.get("market_cap", 0)
    volume = token.get("total_volume", 0)

    if market_cap > 10000000:
        rating += 3
    if volume > 500000:
        rating += 3
    if token.get("price_change_percentage_24h", 0) > 0:
        rating += 4

    return rating

# 🔹 Аналіз токенів з визначенням LONG/SHORT
def analyze_token(token):
    name = token["name"]
    symbol = token["symbol"].upper()
    price = token["current_price"]
    market_cap = token.get("market_cap", 0)
    premarket_price = get_mexc_premarket(symbol)

    rating = calculate_rating(token)

    direction = "🔴 SHORT" if rating < 5 else "🟢 LONG"

    result = (
        f"*{name}* ({symbol})\n"
        f"💰 Ціна: ${price}\n"
        f"🧢 Капа: ${market_cap:,}\n"
        f"📊 Рейтинг: {rating}/10\n"
        f"{direction}"
    )

    if premarket_price:
        result += f"\n⚡️ Премаркет (MEXC): ${premarket_price}"

    return result

# 🔹 Основний цикл роботи бота
async def main_loop():
    await send_telegram_message("🚀 Перевірка токенів стартувала.")
    while True:
        try:
            tokens = get_new_tokens()
            news = get_latest_twitter_news()

            for token in tokens:
                result = analyze_token(token)
                if result:
                    await send_telegram_message(result)
                    save_to_file(result)

            if news:
                news_text = "📰 Останні новини:\n" + "\n".join(news)
                await send_telegram_message(news_text)
                save_to_file(news_text)

        except Exception as e:
            error_msg = f"❌ Помилка в циклі: {e}"
            print(error_msg)
            save_to_file(error_msg)

        await asyncio.sleep(3600)  # пауза 1 година

# 🔹 HTTP-заглушка для Render
class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), StubHandler)
    server.serve_forever()

# 🔹 Точка входу
if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    time.sleep(2)  # затримка для Render
    asyncio.run(main_loop())
