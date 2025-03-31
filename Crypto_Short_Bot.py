import requests
from telegram import Bot
from bs4 import BeautifulSoup
import asyncio
import time
from datetime import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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

# 🔹 Скоринг токена та сигнал
def score_token(token, premarket_price=None, news_mentions=None):
    score = 0
    reasons = []

    if token.get("market_cap") and token["market_cap"] < 50_000_000:
        score += 2
        reasons.append("🧢 Низька капа")

    if premarket_price and token["current_price"] > premarket_price * 1.5:
        score += 2
        reasons.append("📉 Перекуп на премаркеті")

    if news_mentions:
        score += 2
        reasons.append("📰 Є згадка у новинах")

    if token.get("price_change_percentage_24h", 0) < -10:
        score += 2
        reasons.append("📉 Дамп ціни")

    if token.get("total_volume", 0) < 100_000:
        score += 1
        reasons.append("💤 Малий обсяг")

    return score, reasons

def determine_signal(score):
    if score >= 7:
        return "SHORT", "🔻 Шортовий ризик високий"
    elif score <= 2:
        return "LONG", "🟢 Потенціал для лонгу"
    else:
        return "⚠️ НЕВИЗНАЧЕНО", "Ризик нейтральний або змішаний"

def build_message(token, score, reasons, signal_type, comment):
    name = token["name"]
    price = token["current_price"]
    cap = token.get("market_cap", 0)
    symbol = token["symbol"].upper()

    msg = f"🔍 *{name}* ({symbol})\n💰 Ціна: ${price:.4f}\n🧢 Капа: ${cap:,}\n"
    msg += f"📊 Скоринг: {score}/10\n{comment}\n\n"
    if reasons:
        msg += "📌 Причини:\n" + "\n".join(f"– {r}" for r in reasons)

    return msg

# 🔹 Аналіз токена
def analyze_token(token, news=None):
    symbol = token["symbol"].upper()
    if symbol in ["BTC", "ETH", "USDT", "XRP", "BNB"]:
        return None

    premarket_price = get_mexc_premarket(symbol)
    news_mentions = [n for n in news if symbol in n.upper()] if news else []
    score, reasons = score_token(token, premarket_price, news_mentions)
    signal_type, comment = determine_signal(score)

    if signal_type in ["LONG", "SHORT"]:
        return build_message(token, score, reasons, signal_type, comment)
    return None

# 🔹 Основна асинхронна функція
async def main_loop():
    while True:
        try:
            await send_telegram_message("🔄 Автоматичний запуск перевірки токенів")
            tokens = get_new_tokens()
            news = get_latest_twitter_news()

            for token in tokens:
                result = analyze_token(token, news)
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

# 🔹 HTTP-заглушка + запуск бота
class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 10000), StubHandler).serve_forever(), daemon=True).start()
    time.sleep(2)
    asyncio.run(main_loop())
