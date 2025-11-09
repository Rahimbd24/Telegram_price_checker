import os
import sys
import logging
import aiohttp
from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =============== CONFIG ===============
BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8443))

if not BOT_TOKEN:
    print("❌ BOT_TOKEN not set.")
    sys.exit(1)
if not RENDER_URL:
    print("❌ RENDER_EXTERNAL_URL not set (e.g. https://your-app.onrender.com).")
    sys.exit(1)

RENDER_URL = RENDER_URL.rstrip("/")

# =============== LOGGING ===============
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# =============== APIs ===============
COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/price"

# =============== COMMANDS ===============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name if update.effective_user else "there"
    await update.message.reply_text(
        f"👋 Hi {user}!\n\nSend me any cryptocurrency name or symbol and I’ll show you the USD price."
    )


async def get_crypto_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    async with aiohttp.ClientSession() as session:
        # Step 1: Search CoinGecko
        try:
            async with session.get(COINGECKO_SEARCH_URL, params={"query": text}) as res:
                res.raise_for_status()
                data = await res.json()
                if not data.get("coins"):
                    await update.message.reply_text(f"❌ No results for '{text}'.")
                    return
                coin = data["coins"][0]
                coin_id, coin_name, coin_symbol = (
                    coin["id"],
                    coin["name"],
                    coin["symbol"],
                )
        except Exception as e:
            logger.error(f"Coin search failed: {e}")
            await update.message.reply_text("⚠️ Error fetching coin data.")
            return

        # Step 2: Get price
        price_usd = None
        note = ""
        try:
            async with session.get(
                COINGECKO_PRICE_URL,
                params={"ids": coin_id, "vs_currencies": "usd"},
            ) as res:
                res.raise_for_status()
                data = await res.json()
                if coin_id in data and "usd" in data[coin_id]:
                    price_usd = data[coin_id]["usd"]
                else:
                    raise ValueError("No USD price found")
        except Exception as e:
            logger.warning(f"CoinGecko failed ({e}), trying backup.")
            try:
                async with session.get(
                    CRYPTOCOMPARE_URL,
                    params={"fsym": coin_symbol.upper(), "tsyms": "USD"},
                ) as res:
                    res.raise_for_status()
                    backup_data = await res.json()
                    if "USD" in backup_data:
                        price_usd = backup_data["USD"]
                        note = " (via backup)"
            except Exception as e2:
                logger.error(f"Backup failed: {e2}")
                await update.message.reply_text("❌ Both APIs failed to fetch price.")
                return

        if price_usd is None:
            await update.message.reply_text("❌ Price not available.")
            return

        # Format price
        formatted = f"${price_usd:,.8f}" if 0 < price_usd < 0.01 else f"${price_usd:,.2f}"
        message = (
            f"<b>{coin_symbol.upper()}</b> ({coin_name})\n"
            f"<b>Price (USD):</b> {formatted}{note}"
        )
        await update.message.reply_text(message, parse_mode="HTML")


# =============== HEALTH CHECK ===============
async def health_check(request: web.Request):
    return web.Response(text="✅ Bot is running fine!", status=200)


# =============== MAIN ===============
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_crypto_price))

    # Add health-check route to the built-in web_app (so only one server runs)
    application.web_app.add_routes([web.get("/", health_check)])

    # Set secure webhook endpoint
    url_path = BOT_TOKEN  # secret path
    webhook_url = f"{RENDER_URL}/{url_path}"

    logger.info(f"🚀 Starting webhook at {webhook_url}")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=url_path,
        webhook_url=webhook_url,
    )


if __name__ == "__main__":
    main()
