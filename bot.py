import requests
import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Config (Render থেকে স্বয়ংক্রিয়ভাবে লোড হবে) ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
PORT = int(os.environ.get('PORT', 8443))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')

# --- !!! এই দুটি লাইন আপনার কোডে মিসিং ছিল !!! ---
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"

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

# --- Main Price Checker Function (আগের মতোই) ---
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


# --- বট চালু করার মূল ফাংশন ---
def main():
    """বটটি Webhook মোডে চালু করবে"""
    application = Application.builder().token(BOT_TOKEN).build()

    # --- হ্যান্ডলার যোগ করা ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_crypto_price))

    # --- Webhook চালু করা ---
    logger.info(f"Starting bot... setting webhook to {RENDER_URL}")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook", # আপনি URL-এর শেষে এটি দেখতে পাবেন
        webhook_url=f"{RENDER_URL}/webhook" # টেলিগ্রামকে এই URL-টি দেওয়া হবে
    )
    logger.info(f"Webhook bot started successfully!")


if __name__ == "__main__":
    main()