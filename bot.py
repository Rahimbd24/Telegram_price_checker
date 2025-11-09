# corrected bot.py
import aiohttp
import logging
import os
import sys
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from aiohttp import web

# --- Config ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
PORT = int(os.environ.get('PORT', 8443))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')

# Validate env vars early
if not BOT_TOKEN:
    logging.error("BOT_TOKEN is not set. Set the BOT_TOKEN environment variable and restart.")
    sys.exit(1)

if not RENDER_URL:
    logging.error("RENDER_EXTERNAL_URL is not set. Set RENDER_EXTERNAL_URL (e.g. https://your-app.example.com) and restart.")
    sys.exit(1)

# Ensure RENDER_URL has no trailing slash for consistent building
RENDER_URL = RENDER_URL.rstrip('/')

# --- API Endpoints ---
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/price"  # backup API

# --- Setup Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- /start Handler ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name if update.effective_user else "there"
    await update.message.reply_text(
        f"👋 Welcome, {user_name}!\n\n"
        "Send me a crypto name or symbol and I'll return the USD price."
    )

# --- Price Checker ---
async def get_crypto_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.lower().strip()

    async with aiohttp.ClientSession() as session:
        # Search
        search_params = {'query': user_input}
        coin_id, coin_name, coin_symbol = None, "", ""
        try:
            async with session.get(COINGECKO_SEARCH_URL, params=search_params) as search_response:
                search_response.raise_for_status()
                search_data = await search_response.json()

                if not search_data.get('coins'):
                    await update.message.reply_text(f"❌ Sorry, I couldn't find any coin matching '{user_input}'.")
                    return

                first_coin = search_data['coins'][0]
                coin_id, coin_name, coin_symbol = first_coin['id'], first_coin['name'], first_coin['symbol']

        except aiohttp.ClientError as e:
            logger.error(f"Search API Error: {e}")
            await update.message.reply_text("Error fetching data from the Search API.")
            return
        except Exception as e:
            logger.error(f"Unexpected error during search: {e}")
            await update.message.reply_text("Unexpected error during search.")
            return

        # Price fetch with failover
        price_usd = None
        message_note = ""
        try:
            price_params = {'ids': coin_id, 'vs_currencies': 'usd'}
            async with session.get(COINGECKO_PRICE_URL, params=price_params) as price_response:
                price_response.raise_for_status()
                price_data = await price_response.json()

                if coin_id in price_data and 'usd' in price_data[coin_id]:
                    price_usd = price_data[coin_id].get('usd', 0)
                else:
                    # raise an exception to trigger backup logic
                    raise Exception("Price data not found in CoinGecko response")

        except Exception as e:
            # catch both aiohttp.ClientError and general exceptions so backup runs
            logger.warning(f"CoinGecko failed or returned no price ({e}). Trying backup provider...")
            try:
                backup_params = {'fsym': coin_symbol.upper(), 'tsyms': 'USD'}
                async with session.get(CRYPTOCOMPARE_URL, params=backup_params) as backup_response:
                    backup_response.raise_for_status()
                    backup_data = await backup_response.json()

                    if 'USD' not in backup_data:
                        raise Exception(f"Backup API didn't recognize symbol: {coin_symbol.upper()}")

                    price_usd = backup_data['USD']
                    message_note = "\n_(Price via backup provider)_"

            except Exception as backup_e:
                logger.error(f"BACKUP API FAILED: {backup_e}")
                await update.message.reply_text("Error fetching data. Both primary and backup APIs are down.")
                return

    # Format price (safeguard if price_usd is None)
    if price_usd is None:
        await update.message.reply_text("Could not determine price.")
        return

    if 0 < price_usd < 0.01:
        formatted_price = f"${price_usd:,.8f}"
    else:
        formatted_price = f"${price_usd:,.2f}"

    # Use HTML parse mode for safer formatting
    message = (
        f"<b>{coin_symbol.upper()}</b> ({coin_name})\n\n"
        f"<b>Current Price (USD):</b> {formatted_price}"
    )
    if message_note:
        message += f"\n<em>{message_note.strip(' _')}</em>"

    await update.message.reply_text(message, parse_mode='HTML')

# --- Health check ---
async def health_check(request: web.Request):
    return web.Response(text="OK, Bot is alive!", status=200)

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_crypto_price))

    # Add health-check route to the internal aiohttp app
    application.web_app.add_routes([web.get('/', health_check)])

    # Use BOT_TOKEN as the webhook path (more secure) and build webhook_url accordingly
    url_path = BOT_TOKEN  # local path the aiohttp app will listen on
    webhook_url = f"{RENDER_URL}/{url_path}"

    logger.info(f"Starting bot... setting webhook to {webhook_url}")
    # run_webhook will run the built-in web_app and Telegram webhook server
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=url_path,
        webhook_url=webhook_url
    )
    logger.info("Webhook bot started successfully!")

if __name__ == "__main__":
    main()
