import os
import logging
import asyncio
import re
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
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "7930dbab-e806-4f3f-bf3b-716a14c6e3c3")
WALLET_ADDRESS_REGEX = r"^[1-9A-HJ-NP-Za-km-z]{42,44}$"

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
        [InlineKeyboardButton("💳 3-Day Trial - 0.1 SOL", callback_data="buy_trial")],
        [InlineKeyboardButton("📆 Weekly Pass - 0.3 SOL", callback_data="buy_weekly")],
        [InlineKeyboardButton("🗓 Monthly Access - 1 SOL", callback_data="buy_monthly")],
        [InlineKeyboardButton("♾ Lifetime Membership - 2 SOL", callback_data="buy_lifetime")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🚀 Welcome to WAGMI's AI-Powered Signal Group! 🌟\n"
        "Join our exclusive VIP community for top-tier trading signals.\n"
        "Choose your subscription plan below to get started! 👇",
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
        "Once you've made the payment, click below to confirm! 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Payment Sent", callback_data="confirm_payment")]
        ]),
        parse_mode="Markdown"
    )

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "📤 Please provide the wallet address you used to send the payment:"
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
                encoding="jsonParsed",
                max_supported_transaction_version=0  # Hata düzeltmesi
            ).value
            
            if not tx:
                continue

            # Gönderici adresini kontrol et
            sender = str(tx.transaction.message.account_keys[0])
            # Transfer miktarını hesapla (SOL cinsinden)
            transferred = abs(tx.meta.post_balances[0] - tx.meta.pre_balances[0]) / 1e9
            
            if sender == wallet_address and transferred >= expected_sol:
                logger.info(f"Payment verified: {transferred} SOL from {sender}")
                return True
                
    except Exception as e:
        logger.error(f"Payment verification failed: {e}")
    
    return False

async def handle_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        logger.warning("handle_wallet received an update without a message")
        return
    
    user_id = update.message.from_user.id
    wallet_address = update.message.text.strip()

    if not re.match(WALLET_ADDRESS_REGEX, wallet_address):
        await update.message.reply_text("⚠️ Please enter a valid Solana wallet address.")
        return

    if user_id not in user_states or "plan" not in user_states[user_id]:
        await update.message.reply_text("⚠️ Please select a subscription plan first using /start.")
        return

    plan = user_states[user_id]["plan"]
    price = PRICE_OPTIONS[plan]["price"]
    duration = PRICE_OPTIONS[plan]["duration"]
    expire_time = None if duration is None else datetime.utcnow() + duration

    await update.message.reply_text("🔍 Verifying your payment... (This may take up to 5 minutes)")

    # Ödeme doğrulaması (4 kez, 75 saniye aralıklarla)
    for _ in range(4):
        if await verify_payment(wallet_address, price):
            user_membership[user_id] = {"plan": plan, "expires": expire_time}
            logger.info(f"Payment verified: User {user_id}, plan {plan}, amount {price} SOL")
            await context.bot.send_message(
                chat_id=VIP_CHAT_ID,
                text=f"✅ New VIP Member: @{update.effective_user.username} ({plan})"
            )
            await context.bot.invite_chat_member(chat_id=VIP_CHAT_ID, user_id=user_id)
            await update.message.reply_text(
                "🎉 Payment confirmed! Welcome to the WAGMI VIP Signal Group! 🚀"
            )
            return
        await asyncio.sleep(75)

    await update.message.reply_text(
        f"❌ No payment of {price} SOL found from {wallet_address} to {WALLET_ADDRESS}. "
        "Please check your transaction and try again or use /support."
    )

async def remove_expired_members(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.utcnow()
    expired_users = [
        user_id for user_id, data in user_membership.items()
        if data["expires"] and data["expires"] < now
    ]

    for user_id in expired_users:
        try:
            await context.bot.ban_chat_member(chat_id=VIP_CHAT_ID, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=VIP_CHAT_ID, user_id=user_id)
            del user_membership[user_id]
            logger.info(f"Removed expired user: {user_id}")
        except Exception as e:
            logger.error(f"Failed to remove user {user_id}: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text("⚠️ An error occurred. Please try again or contact support with /support.")
    elif update and update.callback_query:
        await update.callback_query.message.reply_text("⚠️ An error occurred. Please try again or contact support with /support.")

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Having trouble with your payment? 💸 Please share your wallet address and details, "
        "and our team will assist you promptly! 🚀"
    )

def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_button, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(confirm_payment, pattern="^confirm_payment$"))
    application.add_handler(MessageHandler(filters.Regex(WALLET_ADDRESS_REGEX), handle_wallet))
    application.add_handler(CommandHandler("support", support))
    application.add_error_handler(error_handler)

    # Job Queue
    application.job_queue.run_repeating(remove_expired_members, interval=300, first=10)

    # Start bot
    if 'RENDER' in os.environ:
        port = int(os.environ.get('PORT', 443))
        webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/webhook"
        application.bot.delete_webhook(drop_pending_updates=True)
        print(f"Starting webhook on {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            webhook_url=webhook_url
        )
    else:
        print("Starting polling mode")
        application.run_polling()

if __name__ == "__main__":
    main()
