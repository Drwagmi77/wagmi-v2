import os
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from solders.pubkey import Pubkey
from solders.rpc.requests import GetSignaturesForAddress
from solana.rpc.api import Client

TOKEN = os.getenv("BOT_TOKEN")
VIP_CHAT_ID = int(os.getenv("VIP_CHAT_ID", "-1002701984074"))  # Grup chat_id
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "Fify9uEQ98CgQ6T3NeNUCQC7qvEAUmnhrsRmzKm3n4Gf")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

user_states = {}
user_membership = {}

PRICE_OPTIONS = {
    "trial": {"price": 0.1, "duration": timedelta(days=3)},
    "weekly": {"price": 0.3, "duration": timedelta(weeks=1)},
    "monthly": {"price": 1.0, "duration": timedelta(days=30)},
    "lifetime": {"price": 2.0, "duration": None},
}

solana_client = Client("https://api.mainnet-beta.solana.com")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💳 3 Days Trial - 0.1 SOL", callback_data="buy_trial")],
        [InlineKeyboardButton("📆 Weekly - 0.3 SOL", callback_data="buy_weekly")],
        [InlineKeyboardButton("🗓 Monthly - 1 SOL", callback_data="buy_monthly")],
        [InlineKeyboardButton("♾ Lifetime - 2 SOL", callback_data="buy_lifetime")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 Welcome!\nChoose a subscription option to join our VIP group 👇",
        reply_markup=reply_markup
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data.replace("buy_", "")
    user_id = query.from_user.id
    user_states[user_id] = {"plan": choice}

    price = PRICE_OPTIONS[choice]["price"]

    await query.message.reply_text(
        f"💸 Send exactly *{price} SOL* to the following address:\n\n"
        f"`{WALLET_ADDRESS}`\n\n"
        f"After sending, click the button below 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ I Sent the Payment", callback_data="confirm_payment")]
        ]),
        parse_mode="Markdown"
    )


async def handle_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    wallet_address = update.message.text.strip()

    if user_id not in user_states or "plan" not in user_states[user_id]:
        await update.message.reply_text("Please select a subscription plan first using /start.")
        return

    plan = user_states[user_id]["plan"]
    price = PRICE_OPTIONS[plan]["price"]

    await update.message.reply_text("⏳ Verifying your payment...")

    try:
        txs = solana_client.get_signatures_for_address(Pubkey.from_string(WALLET_ADDRESS), limit=20)
        found = False

        for tx in txs.value:
            sig = tx.signature
            parsed = solana_client.get_transaction(sig)

            if not parsed.value:
                continue

            try:
                account_keys = parsed.value.transaction.message.account_keys
                if any(wallet_address in str(key) for key in account_keys):
                    post_balance = parsed.value.meta.post_balances[0] / 1e9
                    pre_balance = parsed.value.meta.pre_balances[0] / 1e9
                    amount = abs(post_balance - pre_balance)

                    if amount >= price:
                        found = True
                        break
            except Exception as e:
                continue

        if found:
            duration = PRICE_OPTIONS[plan]["duration"]
            expire_time = None if duration is None else datetime.utcnow() + duration

            user_membership[user_id] = {"plan": plan, "expires": expire_time}

            await context.bot.send_message(
                chat_id=VIP_CHAT_ID,
                text=f"✅ New member: @{update.message.from_user.username or user_id} ({plan})"
            )

            await context.bot.invite_chat_member(chat_id=VIP_CHAT_ID, user_id=user_id)
            await update.message.reply_text("🎉 Payment confirmed! You have been added to the VIP group.")
        else:
            await update.message.reply_text("❌ Payment not found. Make sure you sent the correct amount from the correct wallet.")
    except Exception as e:
        logger.error(str(e))
        await update.message.reply_text("⚠️ Error checking transaction. Please try again later.")


async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "🧾 Please enter the wallet address you used to send the payment:"
    )


async def remove_expired_members(application):
    while True:
        now = datetime.utcnow()
        to_remove = []

        for user_id, data in user_membership.items():
            if data["expires"] and data["expires"] < now:
                to_remove.append(user_id)

        for user_id in to_remove:
            try:
                await application.bot.ban_chat_member(chat_id=VIP_CHAT_ID, user_id=user_id)
                await application.bot.unban_chat_member(chat_id=VIP_CHAT_ID, user_id=user_id)
                logger.info(f"Removed expired user: {user_id}")
            except Exception as e:
                logger.error(f"Failed to remove user {user_id}: {str(e)}")

            del user_membership[user_id]

        await asyncio.sleep(60)


application = ApplicationBuilder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(handle_button, pattern="^buy_"))
application.add_handler(CallbackQueryHandler(confirm_payment, pattern="^confirm_payment$"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wallet))

application.job_queue.run_once(lambda ctx: remove_expired_members(application), when=1)


if __name__ == "__main__":
    if 'RENDER' in os.environ:
        port = int(os.environ.get('PORT', 443))
        hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'wagmi-v2.onrender.com')
        webhook_url = f"https://{hostname}/webhook"

        print(f"Starting webhook on {webhook_url}")

        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="/webhook",
            webhook_url=webhook_url
        )
