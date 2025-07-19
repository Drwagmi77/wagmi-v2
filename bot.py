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
from solana.rpc.api import Client
from dotenv import load_dotenv

# Environment variables
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")
VIP_CHAT_ID = os.getenv("VIP_CHAT_ID")

# Validate critical environment variables
assert TOKEN, "BOT_TOKEN environment variable is missing!"
assert HELIUS_API_KEY, "HELIUS_API_KEY environment variable is missing!"
assert WALLET_ADDRESS, "WALLET_ADDRESS environment variable is missing!"
assert VIP_CHAT_ID, "VIP_CHAT_ID environment variable is missing!"

VIP_CHAT_ID = int(VIP_CHAT_ID)

# Initialize Solana client
solana_client = Client(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# State management
user_states = {}
user_membership = {}

# Subscription plans
PRICE_OPTIONS = {
    "trial": {"price": 0.1, "duration": timedelta(days=3)},
    "weekly": {"price": 0.3, "duration": timedelta(weeks=1)},
    "monthly": {"price": 1.0, "duration": timedelta(days=30)},
    "lifetime": {"price": 2.0, "duration": None},
}

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

    await query.edit_message_text(
        f"💸 Send exactly *{price} SOL* to the following address:\n\n"
        f"`{WALLET_ADDRESS}`\n\n"
        f"After sending, click the button below 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ I Sent the Payment", callback_data="confirm_payment")]
        ]),
        parse_mode="Markdown"
    )

async def verify_transaction(tx_hash: str, expected_amount: float) -> bool:
    try:
        tx = solana_client.get_transaction(tx_hash)
        if not tx.value:
            return False

        receiver_index = tx.value.transaction.message.account_keys.index(Pubkey.from_string(WALLET_ADDRESS))
        amount_received = tx.value.meta.post_balances[receiver_index] - tx.value.meta.pre_balances[receiver_index]
        amount_received_sol = amount_received / 1e9

        return amount_received_sol >= expected_amount
    except Exception as e:
        logger.error(f"Transaction verification failed: {e}")
        return False

async def check_payment_periodically(user_id: int, plan: str, context: ContextTypes.DEFAULT_TYPE):
    price = PRICE_OPTIONS[plan]["price"]
    duration = PRICE_OPTIONS[plan]["duration"]
    expire_time = None if duration is None else datetime.utcnow() + duration

    max_checks = 4
    interval_seconds = 75

    for attempt in range(max_checks):
        try:
            # Get recent transactions for the wallet
            txs = solana_client.get_signatures_for_address(Pubkey.from_string(WALLET_ADDRESS), limit=20)
            
            for tx in txs.value:
                if await verify_transaction(tx.signature, price):
                    user_membership[user_id] = {
                        "plan": plan,
                        "expires": expire_time,
                        "tx_hash": str(tx.signature)
                    }

                    # Add to VIP group
                    try:
                        await context.bot.send_message(
                            chat_id=VIP_CHAT_ID,
                            text=f"✅ New member: @{user_id} ({plan})"
                        )
                        await context.bot.invite_chat_member(
                            chat_id=VIP_CHAT_ID,
                            user_id=user_id
                        )
                        await context.bot.send_message(
                            chat_id=user_id,
                            text="🎉 Payment confirmed! You have been added to the VIP group."
                        )
                        return
                    except Exception as e:
                        logger.error(f"Failed to add user to VIP group: {e}")
                        await context.bot.send_message(
                            chat_id=user_id,
                            text="❌ Failed to add you to VIP group. Please contact support."
                        )
                        return

        except Exception as e:
            logger.error(f"Payment check error (attempt {attempt + 1}): {e}")

        await asyncio.sleep(interval_seconds)

    await context.bot.send_message(
        chat_id=user_id,
        text="❌ Payment not found after 5 minutes. Please check your transaction and try again."
    )

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in user_states:
        await query.edit_message_text("Please select a plan first using /start")
        return

    await query.edit_message_text(
        "🔍 Please send the transaction hash (ID) of your payment:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Back to Plans", callback_data="back_to_plans")]
        ])
    )

async def handle_transaction_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    tx_hash = update.message.text.strip()

    if user_id not in user_states:
        await update.message.reply_text("Please select a plan first using /start")
        return

    plan = user_states[user_id]["plan"]
    price = PRICE_OPTIONS[plan]["price"]

    await update.message.reply_text("⏳ Verifying your payment, please wait...")

    if await verify_transaction(tx_hash, price):
        duration = PRICE_OPTIONS[plan]["duration"]
        expire_time = None if duration is None else datetime.utcnow() + duration

        user_membership[user_id] = {
            "plan": plan,
            "expires": expire_time,
            "tx_hash": tx_hash
        }

        try:
            await context.bot.send_message(
                chat_id=VIP_CHAT_ID,
                text=f"✅ New member: @{update.message.from_user.username} ({plan})"
            )
            await context.bot.invite_chat_member(
                chat_id=VIP_CHAT_ID,
                user_id=user_id
            )
            await update.message.reply_text("🎉 Payment confirmed! You have been added to the VIP group.")
        except Exception as e:
            logger.error(f"Failed to add user to VIP group: {e}")
            await update.message.reply_text("❌ Failed to add you to VIP group. Please contact support.")
    else:
        await update.message.reply_text(
            "❌ Transaction verification failed. Please check:\n"
            "- Correct transaction hash\n"
            "- Sent amount matches exactly\n"
            "- Transaction is confirmed\n\n"
            "Try again or contact support."
        )

async def remove_expired_members(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.utcnow()
    to_remove = []

    for user_id, data in user_membership.items():
        if data["expires"] and data["expires"] < now:
            to_remove.append(user_id)

    for user_id in to_remove:
        try:
            await context.bot.ban_chat_member(
                chat_id=VIP_CHAT_ID,
                user_id=user_id
            )
            await context.bot.unban_chat_member(
                chat_id=VIP_CHAT_ID,
                user_id=user_id
            )
            del user_membership[user_id]
            logger.info(f"Removed expired user: {user_id}")
        except Exception as e:
            logger.error(f"Failed to remove user {user_id}: {e}")

async def back_to_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(query.message, context)

def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_button, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(confirm_payment, pattern="^confirm_payment$"))
    application.add_handler(CallbackQueryHandler(back_to_plans, pattern="^back_to_plans$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_transaction_hash))

    # Periodic jobs
    job_queue = application.job_queue
    job_queue.run_repeating(remove_expired_members, interval=60.0, first=10)

    # Start the bot
    if 'RENDER' in os.environ:
        port = int(os.environ.get('PORT', 443))
        hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'wagmi-v2.onrender.com')
        webhook_url = f"https://{hostname}/webhook"

        logger.info(f"Starting webhook on {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="/webhook",
            webhook_url=webhook_url
        )
    else:
        logger.info("Starting polling...")
        application.run_polling()

if __name__ == "__main__":
    main()
