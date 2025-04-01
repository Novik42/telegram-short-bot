import requests
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
import asyncio
import time
from datetime import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

TELEGRAM_TOKEN = "7793935034:AAGT6uSuzqN5hsCxkVbYKwLIoH-BkB4C2fc"
CHAT_ID = "334517684"
bot = Bot(token=TELEGRAM_TOKEN)

async def send_telegram_message(text):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

def save_to_file(text):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("short_signals_log.txt", "a") as f:
        f.write(f"[{timestamp}]
{text}

")

def get_new_tokens():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "order": "newest", "per_page": 10, "page": 1}
    response = requests.get(url, params=params)
    return response.json()

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

def get_mexc_new_listings():
    url = "https://www.mexc.com/open/api/v2/market/coin/list"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        upcoming_tokens = []
        for coin in data.get('data', []):
            if isinstance(coin, dict) and coin.get('status') == 'UPCOMING':
                name = coin.get('name', 'N/A')
                symbol = coin.get('currency', 'N/A')
                create_time = coin.get('createTime')
                listing_time = datetime.fromtimestamp(create_time / 1000).strftime('%Y-%m-%d %H:%M:%S') if create_time else "N/A"
                token_info = {
                    'name': name,
                    'symbol': symbol,
                    'listing_time': listing_time,
                    'pair': f"{symbol}/USDT"
                }
                upcoming_tokens.append(token_info)
        return upcoming_tokens
    except Exception as e:
        print(f"❌ Помилка при отриманні лістингів з MEXC: {e}")
        return []

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
        f"🚨 *{action}-сигнал!*
"
        f"🔸 {name} ({symbol})
"
        f"💰 Ціна: ${price}
"
        f"📉 Капіталізація: ${market_cap}
"
        f"📊 Рейтинг: {rating}
"
    )
    if premarket_price:
        result += f"⚡️ MEXC премаркет: ${premarket_price}
"
    return result

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    listings = get_mexc_new_listings()
    if listings:
        listings_message = "🗓️ *Найближчі лістинги на MEXC:*

"
        for token in listings:
            listings_message += (
                f"🔹 *{token['name']}* ({token['symbol']})
"
                f"📅 Лістинг: {token['listing_time']}
"
                f"📌 Пара: {token['pair']}

"
            )
        await update.message.reply_text(listings_message, parse_mode="Markdown")
    else:
        await update.message.reply_text("Наразі немає майбутніх лістингів.")

async def main_loop():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("calendar", calendar_command))
    asyncio.create_task(application.run_polling())
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
                listings_message = "🟢 *Незабаром лістинги на MEXC:*

"
                for token in listings:
                    listings_message += (
                        f"🔸 *{token['name']}* ({token['symbol']})
"
                        f"📅 Лістинг: {token['listing_time']}
"
                        f"📌 Пара: {token['pair']}

"
                    )
                await send_telegram_message(listings_message)
                save_to_file(listings_message)
            if news:
                news_text = "📰 *Останні новини:*
" + "
".join(news)
                await send_telegram_message(news_text)
                save_to_file(news_text)
        except Exception as e:
            error_msg = f"❌ Помилка в циклі: {e}"
            print(error_msg)
            save_to_file(error_msg)
        await asyncio.sleep(3600)

class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), StubHandler)
    server.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

if __name__ == "__main__":
    asyncio.run(main_loop())