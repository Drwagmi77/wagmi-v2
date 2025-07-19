import os
import logging
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

# Store user memberships and expiration times
user_memberships = {}

# Subscription plans with 5% tolerance and English messages
PRICE_OPTIONS = {
    "trial": {
        "price": 0.1,
        "min_price": 0.095,
        "duration": timedelta(days=3),
        "message": "3-day trial"
    },
    "weekly": {
        "price": 0.3,
        "min_price": 0.285,
        "duration": timedelta(weeks=1),
        "message": "weekly"
    },
    "monthly": {
        "price": 1.0,
        "min_price": 0.95,
        "duration": timedelta(days=30),
        "message": "monthly"
    },
    "lifetime": {
        "price": 2.0,
        "min_price": 1.9,
        "duration": None,
        "message": "lifetime"
    },
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
        "👋 Welcome to VIP Membership Bot!\n\n"
        "Please choose a subscription plan to join our VIP group:",
        reply_markup=reply_markup
    )

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data.replace("buy_", "")
    price = PRICE_OPTIONS[choice]["price"]

    await query.edit_message_text(
        f"💸 Please send *{price} SOL* (±5%) to our wallet address:\n\n"
        f"`{WALLET_ADDRESS}`\n\n"
        "After sending, click the button below and enter YOUR Solana wallet address that sent the payment:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ I Sent the Payment", callback_data=f"confirm_{choice}")]
        ]),
        parse_mode="Markdown"
    )

async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("confirm_", "")
    context.user_data["selected_plan"] = choice
    
    await query.edit_message_text(
        "🔍 Please enter YOUR Solana wallet address that sent the payment:"
    )

async def check_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_wallet = update.message.text.strip()
    user_id = update.message.from_user.id
    choice = context.user_data.get("selected_plan")
    
    if not choice:
        await update.message.reply_text("Please select a plan first using /start")
        return

    price_info = PRICE_OPTIONS[choice]
    await update.message.reply_text("⏳ Checking your payment, please wait...")

    try:
        # Get recent transactions from user's wallet
        txs = solana_client.get_signatures_for_address(Pubkey.from_string(user_wallet), limit=10)
        
        for tx in txs.value:
            # Get transaction details
            tx_detail = solana_client.get_transaction(tx.signature)
            if not tx_detail.value:
                continue
                
            # Check if transaction is to our wallet
            try:
                receiver_index = tx_detail.value.transaction.message.account_keys.index(Pubkey.from_string(WALLET_ADDRESS))
                amount_received = tx_detail.value.meta.post_balances[receiver_index] - tx_detail.value.meta.pre_balances[receiver_index]
                amount_received_sol = amount_received / 1e9
                
                # Check if amount is within 5% range
                if amount_received_sol >= price_info["min_price"]:
                    # Calculate expiration time
                    expire_time = None
                    if price_info["duration"]:
                        expire_time = datetime.now() + price_info["duration"]
                    
                    # Store membership info
                    user_memberships[user_id] = {
                        "plan": choice,
                        "expires": expire_time,
                        "joined": datetime.now()
                    }
                    
                    # Add to VIP group
                    try:
                        await context.bot.send_message(
                            chat_id=VIP_CHAT_ID,
                            text=f"✅ New member: @{update.message.from_user.username} ({price_info['message']} plan)"
                        )
                        await context.bot.invite_chat_member(
                            chat_id=VIP_CHAT_ID,
                            user_id=user_id
                        )
                        
                        # Send success message
                        if expire_time:
                            expire_str = expire_time.strftime("%Y-%m-%d %H:%M UTC")
                            message = (
                                f"🎉 Payment confirmed! You have been added to the VIP group.\n\n"
                                f"Your {price_info['message']} membership will expire on {expire_str}.\n\n"
                                f"Enjoy your VIP benefits!"
                            )
                        else:
                            message = (
                                f"🎉 Payment confirmed! You have been added to the VIP group.\n\n"
                                f"You have lifetime access to VIP benefits!"
                            )
                        
                        await update.message.reply_text(message)
                        return
                    except Exception as e:
                        logger.error(f"Failed to add user to VIP group: {e}")
                        await update.message.reply_text("❌ Failed to add you to VIP group. Please contact support.")
                        return

            except ValueError:
                continue

        # If no valid transaction found
        await update.message.reply_text(
            "❌ No valid transaction found. Please check:\n"
            "- You sent from the correct wallet\n"
            "- Sent amount is within ±5% of required\n"
            "- Transaction is confirmed\n\n"
            "Try again or contact support."
        )
    except Exception as e:
        logger.error(f"Wallet check error: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again later.")

async def remove_expired_members(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    to_remove = []

    for user_id, membership in user_memberships.items():
        if membership["expires"] and membership["expires"] < now:
            to_remove.append(user_id)

    for user_id in to_remove:
        try:
            # Remove from group
            await context.bot.ban_chat_member(
                chat_id=VIP_CHAT_ID,
                user_id=user_id
            )
            await context.bot.unban_chat_member(
                chat_id=VIP_CHAT_ID,
                user_id=user_id
            )
            
            # Send expiration message
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚠️ Your VIP membership has expired.\n\n"
                         "To continue enjoying VIP benefits, please renew your subscription using /start.\n\n"
                         "Thank you for being with us!"
                )
            except Exception as e:
                logger.error(f"Failed to send expiration message to {user_id}: {e}")
            
            # Remove from memberships
            del user_memberships[user_id]
            logger.info(f"Removed expired user: {user_id}")
            
        except Exception as e:
            logger.error(f"Failed to remove user {user_id}: {e}")

def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_button, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(confirm_payment, pattern="^confirm_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_wallet))

    # Periodic job to remove expired members (runs every minute)
    job_queue = application.job_queue
    job_queue.run_repeating(remove_expired_members, interval=60.0, first=10)

    # Start the bot
    logger.info("Starting polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
