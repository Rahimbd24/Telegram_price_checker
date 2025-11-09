# bot.py
import os
import sys
import logging
import asyncio
import aiohttp
from aiohttp import web
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ----------------- CONFIG -----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")  # e.g. https://telegram-price-checker.onrender.com
PORT = int(os.environ.get("PORT", 8443))

if not BOT_TOKEN:
    print("❌ BOT_TOKEN not set. Set the BOT_TOKEN env var and restart.")
    sys.exit(1)
if not RENDER_URL:
    print("❌ RENDER_EXTERNAL_URL not set. Set the RENDER_EXTERNAL_URL env var and restart.")
    sys.exit(1)

RENDER_URL = RENDER_URL.rstrip("/")

# ----------------- LOGGING -----------------
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- APIs -----------------
COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
CRYPTOCOMPARE_URL = "https://min-api.cryptocompare.com/data/price"

# ----------------- HANDLERS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name if update.effective_user else "there"
    await update.message.reply_text(f"👋 Hi {user}! Send a crypto name or symbol and I'll return the USD price.")

async def get_crypto_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().lower()
    async with aiohttp.ClientSession() as session:
        # search coin
        try:
            async with session.get(COINGECKO_SEARCH_URL, params={"query": query}) as r:
                r.raise_for_status()
                j = await r.json()
                coins = j.get("coins") or []
                if not coins:
                    await update.message.reply_text(f"❌ No coin found for '{query}'.")
                    return
                coin = coins[0]
                coin_id = coin["id"]
                coin_name = coin["name"]
                coin_symbol = coin["symbol"]
        except Exception as e:
            logger.exception("Coin search failed")
            await update.message.reply_text("⚠️ Error searching for that coin.")
            return

        # try primary price provider (CoinGecko)
        price_usd = None
        note = ""
        try:
            async with session.get(COINGECKO_PRICE_URL, params={"ids": coin_id, "vs_currencies": "usd"}) as r:
                r.raise_for_status()
                pj = await r.json()
                if coin_id in pj and "usd" in pj[coin_id]:
                    price_usd = pj[coin_id]["usd"]
                else:
                    raise ValueError("No USD price in CoinGecko response")
        except Exception:
            logger.warning("CoinGecko failed, trying backup provider")
            try:
                async with session.get(CRYPTOCOMPARE_URL, params={"fsym": coin_symbol.upper(), "tsyms": "USD"}) as r2:
                    r2.raise_for_status()
                    bj = await r2.json()
                    if "USD" in bj:
                        price_usd = bj["USD"]
                        note = " (via backup)"
            except Exception:
                logger.exception("Backup provider failed")
                await update.message.reply_text("⚠️ Both price providers failed.")
                return

        if price_usd is None:
            await update.message.reply_text("❌ Couldn't determine the price.")
            return

        formatted = f"${price_usd:,.8f}" if 0 < price_usd < 0.01 else f"${price_usd:,.2f}"
        await update.message.reply_text(f"<b>{coin_symbol.upper()}</b> ({coin_name})\n<b>Price (USD):</b> {formatted}{note}", parse_mode="HTML")

# ----------------- HEALTH ROUTE & TELEGRAM WEBHOOK HANDLER -----------------
async def handle_health(request: web.Request):
    return web.Response(text="✅ Bot is running fine!", status=200)

async def handle_telegram_update(request: web.Request):
    """
    Receives Telegram update JSON at POST /<BOT_TOKEN>, converts to Update
    and passes it to the application's dispatcher to be processed by handlers.
    """
    app: Application = request.app["tg_app"]
    bot: Bot = request.app["bot"]
    try:
        data = await request.json()
    except Exception:
        logger.exception("Invalid JSON in Telegram update")
        return web.Response(status=400, text="invalid json")

    try:
        update = Update.de_json(data, bot)
    except Exception:
        logger.exception("Failed to create Update from JSON")
        return web.Response(status=400, text="bad update")

    # Try to hand over to dispatcher: use dispatcher.process_update (async)
    dispatcher = getattr(app, "dispatcher", None)
    if dispatcher and hasattr(dispatcher, "process_update"):
        # schedule processing, respond quickly
        asyncio.create_task(dispatcher.process_update(update))
        return web.Response(status=200, text="ok")
    # fallback: if update_queue exists, put it there
    update_queue = getattr(app, "update_queue", None)
    if update_queue is not None:
        try:
            update_queue.put_nowait(update)
            return web.Response(status=200, text="ok")
        except Exception:
            logger.exception("Could not queue update")
            return web.Response(status=500, text="queue error")

    logger.error("No dispatcher or update_queue to handle incoming update")
    return web.Response(status=500, text="no handler")

# ----------------- STARTUP & SHUTDOWN -----------------
async def start_services():
    # Build PTB Application and register handlers (do not run run_webhook)
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_crypto_price))

    # Initialize application (makes application.bot and dispatcher ready)
    await application.initialize()
    await application.start()  # start background tasks if any

    bot = application.bot  # telegram.Bot instance

    # Build webhook URL and set webhook at Telegram
    url_path = BOT_TOKEN
    webhook_url = f"{RENDER_URL}/{url_path}"
    logger.info("Setting webhook to %s", webhook_url)
    try:
        await bot.set_webhook(webhook_url)
        logger.info("Webhook set successfully")
    except Exception:
        logger.exception("Failed to set webhook at Telegram (set_webhook)")

    # Build aiohttp server and attach app + bot to it so handlers can access them
    web_app = web.Application()
    web_app["tg_app"] = application
    web_app["bot"] = bot
    web_app.router.add_get("/", handle_health)
    web_app.router.add_post(f"/{BOT_TOKEN}", handle_telegram_update)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("HTTP server started at 0.0.0.0:%s — health at / and webhook at /%s", PORT, BOT_TOKEN)

    # Keep running until stopped (PTB's dispatcher will handle updates via process_update)
    try:
        # Wait forever (or until cancelled)
        await asyncio.Event().wait()
    finally:
        # Clean shutdown
        logger.info("Shutting down: removing webhook and stopping application")
        try:
            await bot.delete_webhook()
        except Exception:
            logger.exception("Failed to delete webhook")
        await application.stop()
        await application.shutdown()
        await runner.cleanup()

def main():
    try:
        asyncio.run(start_services())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Exiting.")

if __name__ == "__main__":
    main()
