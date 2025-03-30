import requests
from telegram import Bot
from bs4 import BeautifulSoup
import asyncio
import time
from datetime import datetime

# 🔹 Введи свій Telegram-токен і CHAT_ID
TELEGRAM_TOKEN = "7793935034:AAGT6uSuzqN5hsCxkVbYKwLIoH-BkB4C2fc"
CHAT_ID = "334517684"

bot = Bot(token=TELEGRAM_TOKEN)

# 🔹 Надсилання повідомлення в Telegram (асинхронно)
async def send_telegram_message(text):
    await bot.send_message(chat_id=CHAT_ID, text=text)

# 🔹 Запис результатів у файл
def save_to_file(text):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("short_signals_log.txt", "a") as f:
        f.write(f"[{timestamp}]\n{text}\n\n")

# 🔹 Отримання нових токенів з CoinGecko API
def get_new_tokens():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "order": "newest", "per_page": 10, "page": 1}
    response = requests.get(url, params=params)
    return response.json()

# 🔹 Отримання премаркетних цін з MEXC API
def get_mexc_premarket(symbol):
    url = f"https://www.mexc.com/open/api/v2/market/depth?symbol={symbol}_usdt&depth=5"
    try:
        response = requests.get(url)
        if response.status_code != 200 or not response.text:
            print(f"❗ Порожня або помилкова відповідь від MEXC для {symbol}: {response.text}")
            return None
        data = response.json()
        asks = data.get("data", {}).get("asks", [])
        if asks:
            return float(asks[0][0])
    except Exception as e:
        print(f"Помилка при обробці MEXC даних для {symbol}: {e}")
    return None

# 🔹 Вебскрапінг Twitter/X для новин
def get_latest_twitter_news():
    url = "https://nitter.net/Lookonchain"
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        tweets = soup.find_all("div", class_="timeline-item")
        news = [tweet.text.strip() for tweet in tweets[:3]]
        return news
    except Exception as e:
        print(f"Помилка при отриманні новин з Twitter: {e}")
    return []

# 🔹 Аналіз токенів
def analyze_token(token):
    name = token["name"]
    price = token["current_price"]
    market_cap = token.get("market_cap", 0)
    symbol = token["symbol"].upper()

    # 🔻 Фільтр популярних токенів
    if symbol in ["BTC", "ETH", "USDT", "XRP", "BNB"]:
        return None

    premarket_price = get_mexc_premarket(symbol)

    if market_cap > 100000000:
        return None

    result = f"🔥 Новий токен для шорту: {name}\n💰 Ціна: ${price}\n📉 Капіталізація: ${market_cap}\n"
    if premarket_price:
        result += f"⚡️ Премаркет на MEXC: ${premarket_price}\n"

    return result

# 🔹 Основна асинхронна функція
async def main_loop():
    while True:
        try:
            await send_telegram_message("🔄 Автоматичний запуск перевірки токенів")
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

        await asyncio.sleep(3600)  # запуск раз на годину

# 🔹 Простий HTTP-сервер-заглушка для Render
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), StubHandler)
    server.serve_forever()

# 🔹 Запуск
if __name__ == "__main__":
    threading.Thread(target=run_http_server, daemon=True).start()
    time.sleep(2)  # дати Render час на сканування порту
    asyncio.run(main_loop())
