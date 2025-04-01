import requests
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from bs4 import BeautifulSoup
import asyncio
from datetime import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# 🔹 Конфігурація
TELEGRAM_TOKEN = "7793935034:AAGT6uSuzqN5hsCxkVbYKwLIoH-BkB4C2fc"
CHAT_ID = "334517684"
bot = Bot(token=TELEGRAM_TOKEN)

# 🔹 Надсилання повідомлення в Telegram
async def send_telegram_message(text):
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")

# 🔹 Запис у файл
def save_to_file(text):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("short_signals_log.txt", "a") as f:
        f.write(f"[{timestamp}]\n{text}\n\n")

# 🔹 CoinGecko — нові токени
def get_new_tokens():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "order": "newest", "per_page": 10, "page": 1}
    response = requests.get(url, params=params)
    return response.json()

# 🔹 MEXC — премаркет
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

# 🔹 MEXC — майбутні лістинги
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

# 🔹 Twitter/X новини
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

# 🔹 Аналіз токену
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

# 🔹 Команда /calendar
async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    listings = get_mexc_new_listings()
    if listings:
        listings_message = "🗓️ *Найближчі лістинги на MEXC:*\n\n"
        for token in listings:
            listings_message += (
                f"🔹 *{token['name']}* ({token['symbol']})\n"
                f"📅 Лістинг: {token['listing_time']}\n"
                f"📌 Пара: {token['pair']}\n\n"
            )
        await update.message.reply_text(listings_message, parse_mode="Markdown")
    else:
        await update.message.reply_text("Наразі немає майбутніх лістингів.")

# 🔹 Основний цикл
async def process_loop():
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

# 🔹 Старт http-сервера (для Render)
class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), StubHandler)
    server.serve_forever()

# 🔹 Запуск Telegram бота і фонових задач
async def start():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("calendar", calendar_command))

    threading.Thread(target=run_http_server, daemon=True).start()

    # Паралельний запуск application + циклу
    async def background_tasks():
        while True:
            try:
                await process_loop()
            except Exception as e:
                error_msg = f"❌ Помилка в циклі: {e}"
                print(error_msg)
                save_to_file(error_msg)
            await asyncio.sleep(3600)

    await asyncio.gather(application.run_polling(), background_tasks())

# 🔹 Запуск
if __name__ == "__main__":
    asyncio.run(start())
