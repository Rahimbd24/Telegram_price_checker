import aiohttp
import logging
import os
import sys
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8443))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

# Validate environment variables early
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN not set in environment variables.")
    sys.exit(1)

if not RENDER_URL:
    print("❌ ERROR: RENDER_EXTERNAL_URL not set. Example: https://your-app.onrender.com")
    sys.exit(1)

RENDER_URL = RENDER_URL.rstrip("/")  # remove trailing slash

# ==================== API ENDPOINTS ====================
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/price"  # backup API

# ==================== LOGGING ====================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name if update.effective_user else "there"
    await update.message.reply_text(
        f"👋 Hi {user}!\n\nSend me a cryptocurrency name or symbol and I’ll return the current USD price."
    )

async def get_crypto_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip().lower()
    async with aiohttp.ClientSession() as session:
        # Search CoinGecko
        try:
            async with session.get(COINGECKO_SEARCH_URL, params={"query": user_input}) as res:
                res.raise_for_status()
                data = await res.json()
                if not data.get("coins"):
                    await update.message.reply_text(f"❌ No coin found for '{user_input}'.")
                    return
                coin = data["coins"][0]
                coin_id = coin["id"]
                coin_name = coin["name"]
                coin_symbol = coin["symbol"]
        except Exception as e:
            logger.error(f"Search error: {e}")
            await update.message.reply_text("⚠️ Error fetching search data.")
            return

        # Get price from CoinGecko
        price_usd = None
        note = ""
        try:
            async with session.get(
                COINGECKO_PRICE_URL, params={"ids": coin_id, "vs_currencies": "usd"}
            ) as res:
                res.raise_for_status()
                price_data = await res.json()
                if coin_id in price_data and "usd" in price_data[coin_id]:
                    price_usd = price_data[coin_id]["usd"]
                else:
                    raise ValueError("No USD price found in CoinGecko response")
        except Exception as e:
            logger.warning(f"CoinGecko failed ({e}), using backup...")
            # Backup: CryptoCompare
            try:
                async with session.get(
                    CRYPTOCOMPARE_URL, params={"fsym": coin_symbol.upper(), "tsyms": "USD"}
                ) as res:
                    res.raise_for_status()
                    backup_data = await res.json()
                    if "USD" in backup_data:
                        price_usd = backup_data["USD"]
                        note = " (via backup)"
            except Exception as e2:
                logger.error(f"Backup API failed: {e2}")
                await update.message.reply_text("⚠️ Both APIs failed to fetch price.")
                return

        if price_usd is None:
            await update.message.reply_text("❌ Could not get price data.")
            return

        # Format price
        if 0 < price_usd < 0.01:
            formatted = f"${price_usd:,.8f}"
        else:
            formatted = f"${price_usd:,.2f}"

        msg = (
            f"<b>{coin_symbol.upper()}</b> ({coin_name})\n"
            f"<b>Price (USD):</b> {formatted}{note}"
        )
        await update.message.reply_text(msg, parse_mode="HTML")

# ==================== HEALTH CHECK ====================
async def health_check(request):
    return web.Response(text="✅ Bot is running fine!", status=200)

# ==================== MAIN APP ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_crypto_price))

    # Add health-check route safely if supported
    try:
        if hasattr(app, "web_app"):
            app.web_app.add_routes([web.get("/", health_check)])
            logger.info("Health check route added successfully.")
        else:
            logger.warning("No web_app attribute — skipping health route.")
    except Exception as e:
        logger.warning(f"Could not add health route: {e}")

    # Build webhook URL and path
    url_path = BOT_TOKEN  # acts as secret endpoint path
    webhook_url = f"{RENDER_URL}/{url_path}"
    logger.info(f"Attempting to start webhook on: {webhook_url}")

    # Try to run webhook; fallback to polling if webhook extras aren't installed
    try:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=url_path,
            webhook_url=webhook_url,
        )
        logger.info("Webhook started successfully (run_webhook returned).")
    except RuntimeError as rte:
        # This is the error you saw when 'webhooks' extras are not installed
        logger.error("RuntimeError while starting webhook: %s", rte)
        logger.error(
            "If you want webhook support, install with: pip install 'python-telegram-bot[webhooks]'\n"
            "Falling back to polling to keep the bot running."
        )
        try:
            app.run_polling()
        except Exception as e:
            logger.critical("Polling also failed: %s", e)
            raise

if __name__ == "__main__":
    main()
