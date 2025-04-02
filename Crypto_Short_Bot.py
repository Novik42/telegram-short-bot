import requests
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import os

# 🔹 Конфігурація
TELEGRAM_TOKEN = "7793935034:AAGT6uSuzqN5hsCxkVbYKwLIoH-BkB4C2fc"
CHAT_ID = "334517684"
WEBHOOK_URL = "https://<your-render-domain>.onrender.com/webhook"  # заміни <your-render-domain>
bot = Bot(token=TELEGRAM_TOKEN)

# 🔹 Надсилання повідомлення
async def send_telegram_message(text):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

# 🔹 Логи
def save_to_file(text):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("short_signals_log.txt", "a") as f:
        f.write(f"[{timestamp}]\n{text}\n\n")

# 🔹 Нові токени
def get_new_tokens():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "order": "newest", "per_page": 10, "page": 1}
    response = requests.get(url, params=params)
    return response.json()

# 🔹 Премаркет
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

# 🔹 Лістинги
def get_mexc_new_listings():
    url = "https://www.mexc.com/open/api/v2/market/coin/list"
    try:
        response = requests.get(url)
        data = response.json()
        tokens = []
        for coin in data.get("data", []):
            if isinstance(coin, dict) and coin.get("status") == "UPCOMING":
                listing_time = datetime.fromtimestamp(coin["createTime"] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                tokens.append({
                    "name": coin.get("name", "N/A"),
                    "symbol": coin.get("currency", "N/A"),
                    "listing_time": listing_time,
                    "pair": f"{coin.get('currency')}/USDT"
                })
        return tokens
    except Exception as e:
        print(f"❌ Помилка при отриманні лістингів з MEXC: {e}")
        return []

# 🔹 Новини Lookonchain
def get_latest_twitter_news():
    try:
        url = "https://nitter.net/Lookonchain"
        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        tweets = soup.find_all("div", class_="timeline-item")
        return [tweet.text.strip() for tweet in tweets[:3]]
    except Exception as e:
        print(f"Помилка при отриманні новин з Twitter: {e}")
        return []

# 🔹 Аналіз токена
def analyze_token(token):
    name = token["name"]
    price = token["current_price"]
    market_cap = token.get("market_cap", 0)
    symbol = token["symbol"].upper()

    if symbol in ["BTC", "ETH", "USDT", "XRP", "BNB"]:
        return None

    premarket_price = get_mexc_premarket(symbol)
    if market_cap > 100_000_000:
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

# 🔹 /calendar
async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    listings = get_mexc_new_listings()
    if listings:
        text = "🗓️ *Найближчі лістинги на MEXC:*\n\n"
        for token in listings:
            text += (
                f"🔹 *{token['name']}* ({token['symbol']})\n"
                f"📅 Лістинг: {token['listing_time']}\n"
                f"📌 Пара: {token['pair']}\n\n"
            )
    else:
        text = "Наразі немає майбутніх лістингів."
    await update.message.reply_text(text, parse_mode="Markdown")

# 🔹 Фонові задачі
async def background_tasks():
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
                text = "🟢 *Незабаром лістинги на MEXC:*\n\n"
                for token in listings:
                    text += (
                        f"🔸 *{token['name']}* ({token['symbol']})\n"
                        f"📅 Лістинг: {token['listing_time']}\n"
                        f"📌 Пара: {token['pair']}\n\n"
                    )
                await send_telegram_message(text)
                save_to_file(text)

            if news:
                news_text = "📰 *Останні новини:*\n" + "\n".join(news)
                await send_telegram_message(news_text)
                save_to_file(news_text)

        except Exception as e:
            error_msg = f"❌ Помилка в циклі: {e}"
            print(error_msg)
            save_to_file(error_msg)

        await asyncio.sleep(3600)

# 🔹 HTTP-заглушка
class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), StubHandler)
    server.serve_forever()

# 🔹 Основна функція з вебхуком
async def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("calendar", calendar_command))
    threading.Thread(target=run_http_server, daemon=True).start()

    await application.initialize()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    await application.start()
    await application.updater.start_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path="/webhook",
        webhook_url=WEBHOOK_URL,
    )
    await background_tasks()

if __name__ == "__main__":
    asyncio.run(main())
