import requests
import logging
import os
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from aiohttp import web  # <-- নতুন: বিল্ট-ইন ওয়েব সার্ভারে রুট যোগ করার জন্য

# --- Config ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
PORT = int(os.environ.get('PORT', 8443))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')

# --- API Endpoints ---
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/price" # ব্যাকআপ API

# --- Setup Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- /start Command Handler ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.from_user.first_name
    await update.message.reply_text(
        f"👋 Welcome, {user_name}!\n\n"
        "I am an advanced crypto price checker bot. Send me any crypto name or "
        "symbol, and I will get the real-time USD price for you."
    )

# --- Main Price Checker Function (Failover লজিক সহ) ---
async def get_crypto_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.lower().strip()

    # Step 1: Search API
    search_params = {'query': user_input}
    coin_id, coin_name, coin_symbol = None, "", ""
    try:
        search_response = requests.get(COINGECKO_SEARCH_URL, params=search_params)
        search_response.raise_for_status()
        search_data = search_response.json()
        if not search_data.get('coins'):
            await update.message.reply_text(f"❌ Sorry, I couldn't find any coin matching '{user_input}'.")
            return
        first_coin = search_data['coins'][0]
        coin_id, coin_name, coin_symbol = first_coin['id'], first_coin['name'], first_coin['symbol']
    except requests.exceptions.RequestException as e:
        logger.error(f"Search API Error: {e}")
        await update.message.reply_text("Error fetching data from the Search API.")
        return

    # Step 2: প্রাইস খোঁজা (Failover লজিক)
    price_usd = None
    message_note = ""
    try:
        # প্রথম চেষ্টা: CoinGecko
        price_params = {'ids': coin_id, 'vs_currencies': 'usd'}
        price_response = requests.get(COINGECKO_PRICE_URL, params=price_params)
        price_response.raise_for_status()
        price_data = price_response.json()
        if coin_id in price_data and 'usd' in price_data[coin_id]:
            price_usd = price_data[coin_id].get('usd', 0)
        else:
            raise Exception("Price data not found in CoinGecko response")
    except requests.exceptions.RequestException as e:
        # দ্বিতীয় চেষ্টা: CryptoCompare (ব্যাকআপ)
        logger.warning(f"CoinGecko FAILED ({e}). Trying Backup API...")
        try:
            backup_params = {'fsym': coin_symbol.upper(), 'tsyms': 'USD'}
            backup_response = requests.get(CRYPTOCOMPARE_URL, params=backup_params)
            backup_response.raise_for_status()
            backup_data = backup_response.json()
            if 'USD' not in backup_data:
                raise Exception(f"Backup API didn't recognize symbol: {coin_symbol.upper()}")
            price_usd = backup_data['USD']
            message_note = "\n_(Price via backup provider)_"
        except Exception as backup_e:
            logger.error(f"BACKUP API FAILED: {backup_e}")
            await update.message.reply_text("Error fetching data. Both primary and backup APIs are down.")
            return

    # Step 3: মেসেজ পাঠানো
    if 0 < price_usd < 0.01: formatted_price = f"${price_usd:,.8f}"
    else: formatted_price = f"${price_usd:,.2f}"
    
    message = (
        f"🪙 **{coin_symbol.upper()}** ({coin_name})\n\n"
        f"💰 Current Price (USD): **{formatted_price}**"
    )
    message += message_note
    await update.message.reply_text(message, parse_mode='Markdown')

# --- নতুন: UptimeRobot-এর জন্য "Health Check" রুট ---
async def health_check(request: web.Request):
    """UptimeRobot কে জানানোর জন্য যে বটটি বেঁচে আছে।"""
    return web.Response(text="OK, Bot is alive!", status=200)

# --- বট চালু করার মূল ফাংশন ---
def main():
    """বটটি Webhook মোডে চালু করবে"""
    application = Application.builder().token(BOT_TOKEN).build()

    # --- বট হ্যান্ডলার যোগ করা ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_crypto_price))

    # --- নতুন: বিল্ট-ইন সার্ভারে Health Check রুটটি যোগ করা ---
    # এটি অবশ্যই run_webhook-এর *আগে* করতে হবে
    application.add_routes([web.get('/', health_check)])

    # --- Webhook চালু করা ---
    logger.info(f"Starting bot... setting webhook to {RENDER_URL}/webhook")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="", # Webhook এখন মূল URL-এ সেট হবে
        webhook_url=f"{RENDER_URL}"
    )
    logger.info(f"Webhook bot started successfully!")

if __name__ == "__main__":
    main()