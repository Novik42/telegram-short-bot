import requests
from telegram import Bot
from bs4 import BeautifulSoup
import asyncio
import time
from datetime import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# 🔹 Встав свій Telegram-токен та CHAT_ID
TELEGRAM_TOKEN = "7793935034:AAGT6uSuzqN5hsCxkVbYKwLIoH-BkB4C2fc"
CHAT_ID = "334517684"

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
    params = {"vs_currency": "usd", "order": "newest", "per_page": 10, "page": 1}
    response = requests.get(url, params=params)
    return response.json()

# 🔹 Отримання премаркетних цін з MEXC API
def get_mexc_premarket(symbol):
    url = f"https://www.mexc.com/open/api/v2/market/depth?symbol={symbol}_USDT&depth=5"
    try:
        response = requests.get(url)
        data = response.json()
        asks = data.get("data", {}).get("asks", [])
        if asks:
            return float(asks[0][0])
    except Exception as e:
        print(f"Помилка MEXC для {symbol}: {e}")
    return None

# 🔹 Перевірка нових лістингів на MEXC
def get_mexc_new_listings():
    url = "https://www.mexc.com/open/api/v2/market/coin/list"
    response = requests.get(url).json()

    upcoming_tokens = []

    for coin in response.get('data', []):
        if coin['status'] == 'UPCOMING':
            token_info = {
                'name': coin['name'],
                'symbol': coin['currency'],
                'listing_time': datetime.fromtimestamp(coin['createTime'] / 1000).strftime('%Y-%m-%d %H:%M:%S'),
                'pair': f"{coin['currency']}/USDT"
            }
            upcoming_tokens.append(token_info)

    return upcoming_tokens

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

    if symbol in ["BTC", "ETH", "USDT", "XRP", "BNB"]:
        return None

    premarket_price = get_mexc_premarket(symbol)
    if market_cap > 100000000:
        return None

    action = "SHORT" if premarket_price and premarket_price < price else "LONG"
    rating = "⚠️ Високий ризик" if action == "SHORT" else "✅ Помірний ризик"

    result = (
        f"🚨 *{action}-сигнал!*\n"
        f"🔸 {name} ({symbol})\n"
        f"💰 Ціна: ${price}\n"
        f"📉 Капіталізація: ${market_cap}\n"
        f"📊 Рейтинг: {rating}\n"
    )

    if premarket_price:
        result += f"⚡️ MEXC премаркет: ${premarket_price}\n"

    return result

# 🔹 Основна асинхронна функція
async def main_loop():
    while True:
        try:
            await send_telegram_message("🚀 Перевірка токенів стартувала.")
            tokens = get_new_tokens()
            news = get_latest_twitter_news()
            listings = get_mexc_new_listings()

            for token in tokens:
                result = analyze_token(token)
                if result:
                    await send_telegram_message(result)
                    save_to_file(result)

            if listings:
                listings_message = "🟢 *Незабаром лістинги на MEXC:*\n\n"
                for token in listings:
                    listings_message += (
                        f"🔸 *{token['name']}* ({token['symbol']})\n"
                        f"📅 Лістинг: {token['listing_time']}\n"
                        f"📌 Пара: {token['pair']}\n\n"
                    )

                await send_telegram_message(listings_message)
                save_to_file(listings_message)

            if news:
                news_text = "📰 *Останні новини:*\n" + "\n".join(news)
                await send_telegram_message(news_text)
                save_to_file(news_text)

        except Exception as e:
            error_msg = f"❌ Помилка в циклі: {e}"
            print(error_msg)
            save_to_file(error_msg)

        await asyncio.sleep(3600)

# 🔹 HTTP-заглушка (для Render)
class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), StubHandler)
    server.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# 🔹 Запуск бота
if __name__ == "__main__":
    asyncio.run(main_loop())
