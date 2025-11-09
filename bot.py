import requests
import logging
import os # <-- নতুন: Environment Variable পড়ার জন্য
from flask import Flask, request # <-- নতুন: ওয়েব সার্ভারের জন্য
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Bot Token (Render থেকে লোড হবে) ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')

# --- CoinGecko API Endpoints ---
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"

# --- Setup Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Telegram Bot Application ---
# আমরা application-কে এখানে তৈরি করছি
application = Application.builder().token(BOT_TOKEN).build()


# --- /start Command Handler ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.from_user.first_name
    await update.message.reply_text(
        f"👋 Welcome, {user_name}!\n\n"
        "I am an advanced crypto price checker bot. Send me any crypto name or "
        "symbol, and I will get the real-time USD price for you."
    )

# --- Main Price Checker Function ---
async def get_crypto_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.lower().strip()

    # Step 1: Search API to find the correct coin ID
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

    # Step 2: Use the found ID to get the price
    price_params = {'ids': coin_id, 'vs_currencies': 'usd'}

    try:
        price_response = requests.get(COINGECKO_PRICE_URL, params=price_params)
        price_response.raise_for_status()
        price_data = price_response.json()

        if coin_id in price_data:
            price_usd = price_data[coin_id].get('usd', 0)
            
            if 0 < price_usd < 0.01:
                formatted_price = f"${price_usd:,.8f}"
            else:
                formatted_price = f"${price_usd:,.2f}"
            
            message = (
                f"🪙 **{coin_symbol.upper()}** ({coin_name})\n\n"
                f"💰 Current Price (USD): **{formatted_price}**"
            )
            await update.message.reply_text(message, parse_mode='Markdown')
        else:
            await update.message.reply_text("Price data not found in API response.")

    except requests.exceptions.RequestException as e:
        logger.error(f"Price API Error: {e}")
        await update.message.reply_text("Error fetching data from the Price API.")

# --- Flask Web Server Setup ---
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
async def webhook():
    """Handles incoming updates from Telegram."""
    update_json = request.get_json(force=True)
    update = Update.de_json(update_json, application.bot)
    await application.update_queue.put(update)
    return 'ok' # Telegram-কে জানাতে হবে যে মেসেজ পেয়েছি

# --- Bot Handlers (আগের মতোই) ---
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_crypto_price))