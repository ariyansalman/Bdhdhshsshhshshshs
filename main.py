"""
Telegram Bot entry point for the Pixel 10 Pro Google One Gemini Bot.

Commands:
  /start        – Show welcome message and available commands
  /login        – Begin credential capture flow (email → password → 2FA)
  /check_offer  – Run Google One automation and look for Gemini Pro offer
  /get_link     – Show the last captured offer link
  /status       – Show current session status and device profile
"""

import asyncio
import io
import logging
import sys

# Load the Selenium runtime bootstrap before google_automation imports
# Service and constructs the Chrome driver.
try:
    import sitecustomize  # noqa: F401
except Exception:
    pass

from telegram import Update, InputMediaPhoto, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
from device_simulator import create_device_profile
from google_automation import GoogleAutomationError, check_gemini_offer

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)
logger = logging.getLogger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────
AWAIT_EMAIL, AWAIT_PASSWORD, AWAIT_TOTP = range(3)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_session(chat_id: int) -> dict:
    """Return (creating if absent) the session dict for *chat_id*."""
    if chat_id not in config.SESSION_STORE:
        config.SESSION_STORE[chat_id] = {}
    return config.SESSION_STORE[chat_id]


def _make_progress_callback(bot, chat_id: int, loop: asyncio.AbstractEventLoop):
    """Return a thread-safe callback for Telegram progress updates."""
    def _cb(msg: str, screenshot_bytes: bytes | None = None):
        async def _send():
            try:
                if screenshot_bytes:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=io.BytesIO(screenshot_bytes),
                        caption=msg,
                    )
                else:
                    await bot.send_message(chat_id=chat_id, text=msg)
            except Exception as e:
                logger.warning("Progress send error: %s", e)

        asyncio.run_coroutine_threadsafe(_send(), loop)

    return _cb


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *Pixel 10 Pro Google One Bot*\n\n"
        "Use /login to start a session.\n"
        "Use /check\\_offer to run the offer check.\n"
        "Use /get\\_link to show the last captured offer link.\n"
        "Use /status to view the current session.",
        parse_mode="Markdown",
    )


async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📧 Please enter your Gmail address:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return AWAIT_EMAIL


async def login_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip()
    context.user_data["pending_email"] = email
    await update.message.reply_text(
        f"✅ Email received: `{email}`\n\n🔒 Now enter your password:",
        parse_mode="Markdown",
    )
    return AWAIT_PASSWORD


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["pending_password"] = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.effective_chat.send_message(
        "✅ Password received.\n\n"
        "🔐 Now enter your *2FA secret key* or send `none` if 2FA is not enabled.",
        parse_mode="Markdown",
    )
    return AWAIT_TOTP


async def login_totp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    raw = update.message.text.strip()
    totp_secret = None if raw.lower() == "none" else raw.upper().replace(" ", "")

    email = context.user_data.pop("pending_email", "")
    password = context.user_data.pop("pending_password", "")
    try:
        await update.message.delete()
    except Exception:
        pass

    session = _get_session(chat_id)
    session["email"] = email
    session["password"] = password
    session["totp_secret"] = totp_secret
    session["device"] = create_device_profile()
    session["offer_link"] = None

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "✅ *Credentials saved* — new Pixel 10 Pro profile created.\n\n"
            + session["device"].summary()
            + "\n\nUse /check\\_offer to start the automation."
        ),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("pending_email", None)
    context.user_data.pop("pending_password", None)
    await update.message.reply_text("❌ Login cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def check_offer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)

    if not session.get("email") or not session.get("password"):
        await update.message.reply_text("⚠️ No credentials found. Please use /login first.")
        return

    device = session.get("device") or create_device_profile()
    session["device"] = device

    await update.message.reply_text(
        "🚀 *Starting automation — you'll get a live update at every step.*\n\n"
        "Screenshots will be sent for each stage so you can follow along.",
        parse_mode="Markdown",
    )

    loop = asyncio.get_running_loop()
    progress_cb = _make_progress_callback(context.bot, chat_id, loop)

    try:
        offer_link = await loop.run_in_executor(
            None,
            lambda: check_gemini_offer(
                session["email"],
                session["password"],
                device,
                totp_secret=session.get("totp_secret"),
                progress_callback=progress_cb,
            ),
        )
    except GoogleAutomationError as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {exc}")
        return
    except Exception as exc:
        logger.exception("Unexpected error in check_offer for chat %s", chat_id)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Unexpected error: {exc}")
        return

    if offer_link:
        session["offer_link"] = offer_link
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🎉 *Gemini Pro Offer Found!*\n\n"
                "Tap the link below to activate your offer:\n\n"
                f"🔗 {offer_link}\n\n"
                "Use /get\\_link to retrieve this link again."
            ),
            parse_mode="Markdown",
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text="😔 No active Gemini Pro offer detected on this account.",
        )


async def get_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    link = _get_session(update.effective_chat.id).get("offer_link")
    if link:
        await update.message.reply_text(f"🔗 Last captured offer link:\n\n{link}")
    else:
        await update.message.reply_text("ℹ️ No offer link captured yet. Use /check_offer first.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _get_session(update.effective_chat.id)
    await update.message.reply_text(
        "📊 Session status\n\n"
        f"Email: {'set' if session.get('email') else 'not set'}\n"
        f"Password: {'set' if session.get('password') else 'not set'}\n"
        f"2FA: {'enabled' if session.get('totp_secret') else 'not set'}\n"
        f"Offer link: {'available' if session.get('offer_link') else 'not available'}"
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled Telegram error", exc_info=context.error)


# ── Application ───────────────────────────────────────────────────────────────
def main() -> None:
    application = Application.builder().token(config.BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[CommandHandler("login", login_start)],
        states={
            AWAIT_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_email)],
            AWAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
            AWAIT_TOTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_totp)],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conversation)
    application.add_handler(CommandHandler("check_offer", check_offer))
    application.add_handler(CommandHandler("get_link", get_link))
    application.add_handler(CommandHandler("status", status))
    application.add_error_handler(error_handler)

    logger.info("Bot is running. Press Ctrl-C to stop.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
