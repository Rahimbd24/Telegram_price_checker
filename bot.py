import requests
import logging
import os
import time
from flask import Flask, request # <-- We are using Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Config ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
PORT = int(os.environ.get('PORT', 8080)) # Gunicorn uses this port
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')

# --- API Endpoints ---
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/price" # Backup API

# --- Setup Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Telegram Bot Application ---
application = Application.builder().token(BOT_TOKEN).build()

# --- Flask App ---
app = Flask(__name__) # This is our web server


# --- /start Command Handler ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.from_user.first_name
    await update.message.reply_text(
        f"👋 Welcome, {user_name}!\n\n"
        "I am an advanced crypto price checker bot. Send me any crypto name or "
        "symbol, and I will get the real-time USD price for you."
    )

# --- Main Price Checker Function (Failover logic) ---
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

    # Step 2: Price check (with Failover)
    price_usd = None
    message_note = ""
    try:
        # First try: CoinGecko
        price_params = {'ids': coin_id, 'vs_currencies': 'usd'}
        price_response = requests.get(COINGECKO_PRICE_URL, params=price_params)
        price_response.raise_for_status()
        price_data = price_response.json()
        if coin_id in price_data and 'usd' in price_data[coin_id]:
            price_usd = price_data[coin_id].get('usd', 0)
        else:
            raise Exception("Price data not found in CoinGecko response")
    except requests.exceptions.RequestException as e:
        # Second try: CryptoCompare (Backup)
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

    # Step 3: Send message
    if 0 < price_usd < 0.01: formatted_price = f"${price_usd:,.8f}"
    else: formatted_price = f"${price_usd:,.2f}"
    
    message = (
        f"🪙 **{coin_symbol.upper()}** ({coin_name})\n\n"
        f"💰 Current Price (USD): **{formatted_price}**"
    )
    message += message_note
    await update.message.reply_text(message, parse_mode='Markdown')


# --- UptimeRobot "Health Check" Route ---
@app.route('/')
def health_check():
    """Tells UptimeRobot the bot is alive."""
    return "OK, Bot is alive!", 200

# --- Telegram Webhook Route ---
@app.route('/webhook', methods=['POST'])
async def webhook():
    """Handles messages from Telegram."""
    update_json = request.get_json(force=True)
    update = Update.de_json(update_json, application.bot)
    await application.update_queue.put(update)
    return 'ok'

# --- Set Webhook Route (Run this once) ---
@app.route('/set_webhook')
def set_webhook():
    """Sets the webhook for Telegram."""
    webhook_url = f"{RENDER_URL}/webhook"
    success = application.bot.set_webhook(webhook_url)
    if success:
        return f"Webhook set to {webhook_url}!"
    else:
        return "Webhook setup failed."

# --- Add handlers to the bot application ---
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_crypto_price))