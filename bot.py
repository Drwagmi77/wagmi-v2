import os
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from solana.rpc.commitment import Confirmed

# Config
TOKEN = os.getenv("BOT_TOKEN")
VIP_CHAT_ID = int(os.getenv("VIP_CHAT_ID", "-1002701984074"))
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "Fify9uEQ98CgQ6T3NeNUCQC7qvEAUmnhrsRmzKm3n4Gf")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

# Initialize Solana Client
solana_client = Client(
    f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}",
    timeout=30,
    commitment=Confirmed
)

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Globals
user_states = {}
user_membership = {}
PRICE_OPTIONS = {
    "trial": {"price": 0.1, "duration": timedelta(days=3)},
    "weekly": {"price": 0.3, "duration": timedelta(weeks=1)},
    "monthly": {"price": 1.0, "duration": timedelta(days=30)},
    "lifetime": {"price": 2.0, "duration": None},
}

# Handlers
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
        f"💸 Send exactly *{price} SOL* to:\n\n`{WALLET_ADDRESS}`\n\n"
        "After payment, click below 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ I Paid", callback_data="confirm_payment")]
        ]),
        parse_mode="Markdown"
    )

async def verify_payment(wallet_address: str, expected_sol: float) -> bool:
    try:
        signatures = solana_client.get_signatures_for_address(
            Pubkey.from_string(WALLET_ADDRESS),
            limit=5,
            commitment=Confirmed
        ).value

        for sig in signatures:
            tx = solana_client.get_transaction(
                sig.signature,
                encoding="jsonParsed"
            ).value
            
            if not tx:
                continue

            sender = str(tx.transaction.message.account_keys[0])
            transferred = sum(tx.meta.post_balances) - sum(tx.meta.pre_balances)
            
            if sender == wallet_address and transferred >= (expected_sol * 10**9):
                return True
                
    except Exception as e:
        logger.error(f"Payment verification failed: {e}")
    
    return False

async def check_payment_periodically(user_id, wallet_address, plan, context):
    for _ in range(4):
        if await verify_payment(wallet_address, PRICE_OPTIONS[plan]["price"]):
            duration = PRICE_OPTIONS[plan]["duration"]
            user_membership[user_id] = {
                "plan": plan,
                "expires": datetime.utcnow() + duration if duration else None
            }
            
            await context.bot.send_message(
                chat_id=VIP_CHAT_ID,
                text=f"✅ New member: @{update.effective_user.username} ({plan})"
            )
            await context.bot.invite_chat_member(
                chat_id=VIP_CHAT_ID,
                user_id=user_id
            )
            await context.bot.send_message(
                chat_id=user_id,
                text="🎉 Payment confirmed! You've been added to VIP group."
            )
            return
            
        await asyncio.sleep(75)

    await context.bot.send_message(
        chat_id=user_id,
        text="❌ Payment not found. You can send transaction hash manually."
    )

async def handle_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    wallet_address = update.message.text.strip()

    if user_id not in user_states:
        await update.message.reply_text("⚠️ Please select a plan first using /start")
        return

    await update.message.reply_text("🔍 Verifying payment... (up to 5 minutes)")
    asyncio.create_task(
        check_payment_periodically(
            user_id,
            wallet_address,
            user_states[user_id]["plan"],
            context
        )
    )

async def handle_transaction_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tx_hash = update.message.text.strip()
    user_id = update.message.from_user.id
    
    if user_id not in user_states:
        await update.message.reply_text("⚠️ Please select a plan first using /start")
        return

    try:
        tx = solana_client.get_transaction(tx_hash).value
        if tx:
            transferred = sum(tx.meta.post_balances) - sum(tx.meta.pre_balances)
            required = PRICE_OPTIONS[user_states[user_id]["plan"]]["price"] * 10**9
            
            if transferred >= required:
                await update.message.reply_text("✅ Payment verified! Processing...")
                # Trigger membership logic
                return
    except Exception as e:
        logger.error(f"TX hash check failed: {e}")
    
    await update.message.reply_text("❌ Invalid transaction or insufficient amount")

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "📤 Please send your wallet address OR transaction hash:"
    )

async def remove_expired_members(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.utcnow()
    expired_users = [
        user_id for user_id, data in user_membership.items()
        if data["expires"] and data["expires"] < now
    ]

    for user_id in expired_users:
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

def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_button, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(confirm_payment, pattern="^confirm_payment$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wallet))
    application.add_handler(MessageHandler(filters.Regex(r"^[A-Ha-h0-9]{64,88}$"), handle_transaction_hash))

    # Job Queue
    application.job_queue.run_repeating(
        remove_expired_members,
        interval=300,  # 5 minutes
        first=10
    )

    # Start bot
    if 'RENDER' in os.environ:
        port = int(os.environ.get('PORT', 443))
        webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/webhook"
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=webhook_url
        )
    else:
        application.run_polling()

if __name__ == "__main__":
    main()
