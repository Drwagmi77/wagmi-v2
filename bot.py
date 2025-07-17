from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from solana.rpc.api import Client
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
SOLANA_WALLET_ADDRESS = os.getenv("SOLANA_WALLET_ADDRESS")
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Database setup
Base = declarative_base()

class Membership(Base):
    __tablename__ = "wagmi_memberships"
    user_id = Column(Integer, primary_key=True)
    membership_type = Column(String)
    expiry_date = Column(DateTime)

class TempVerification(Base):
    __tablename__ = "wagmi_temp_verifications"
    user_id = Column(Integer, primary_key=True)
    wallet_address = Column(String)

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Solana client
solana_client = Client(SOLANA_RPC)

# Membership configuration
memberships = {
    "trial": {"amount": 0.1, "duration": 3 * 24 * 60 * 60},  # 3 days
    "weekly": {"amount": 0.3, "duration": 7 * 24 * 60 * 60},  # 1 week
    "monthly": {"amount": 1, "duration": 30 * 24 * 60 * 60},  # 1 month
    "six_month": {"amount": 2, "duration": 180 * 24 * 60 * 60}  # 6 months
}

# Create bot application
application = Application.builder().token(BOT_TOKEN).build()

# START command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("New Membership", callback_data='new_membership')],
        [InlineKeyboardButton("Renew Membership", callback_data='renew_membership')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome to the WAGMI VIP world!\nOur AI-powered signal bot scans 24/7 and sends signals to our group.\nJoin now!",
        reply_markup=reply_markup
    )

# Handle button interactions
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = Session()

    if query.data == "new_membership" or query.data == "renew_membership":
        keyboard = [
            [InlineKeyboardButton("3-Day Trial (0.1 SOL)", callback_data='trial')],
            [InlineKeyboardButton("Weekly (0.3 SOL)", callback_data='weekly')],
            [InlineKeyboardButton("Monthly (1 SOL)", callback_data='monthly')],
            [InlineKeyboardButton("6-Month (2 SOL)", callback_data='six_month')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Select a membership tier:", reply_markup=reply_markup)

    elif query.data in memberships:
        membership_type = query.data
        amount = memberships[membership_type]["amount"]
        duration = memberships[membership_type]["duration"]
        
        # Store the membership type and wait for wallet address
        session.merge(TempVerification(user_id=user_id, wallet_address="pending"))
        session.commit()

        # Send payment instruction with copyable address
        payment_message = f"Please send **{amount} SOL** to this address:\n\n`{SOLANA_WALLET_ADDRESS}`\n\nAfter sending, click the button below to enter your wallet address."
        keyboard = [[InlineKeyboardButton("Enter Wallet Address", callback_data=f'enter_wallet_{membership_type}')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(payment_message, parse_mode="Markdown", reply_markup=reply_markup)

    elif query.data.startswith("enter_wallet_"):
        membership_type = query.data.split("enter_wallet_")[1]
        await query.edit_message_text("Please enter the Solana wallet address you used for payment:")
        session.merge(TempVerification(user_id=user_id, wallet_address="awaiting"))
        session.commit()

# Handle wallet address input (automatic verification)
async def handle_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    wallet_address = update.message.text.strip()
    session = Session()

    record = session.query(TempVerification).filter_by(user_id=user_id, wallet_address="awaiting").first()
    if not record:
        await update.message.reply_text("Please start the membership process with /start.")
        session.close()
        return

    await update.message.reply_text("Verifying your payment. Please wait...")

    try:
        # Check recent transactions
        signatures = solana_client.get_signatures_for_address(SOLANA_WALLET_ADDRESS, limit=10)
        for sig in signatures.value:
            tx = solana_client.get_transaction(sig.signature)
            if tx.value and wallet_address in tx.value.transaction.message.account_keys:
                pre_balances = tx.value.meta.pre_balances
                post_balances = tx.value.meta.post_balances
                if len(pre_balances) >= 2 and len(post_balances) >= 2:
                    lamports_sent = post_balances[1] - pre_balances[1]
                    membership_type = next((k for k, v in memberships.items() if v["amount"] * 1e9 == lamports_sent), None)
                    if membership_type:
                        expiry_date = datetime.utcnow() + timedelta(seconds=memberships[membership_type]["duration"])
                        session.merge(Membership(user_id=user_id, membership_type=membership_type, expiry_date=expiry_date))
                        session.delete(record)
                        session.commit()
                        await context.bot.send_message(chat_id=user_id, text="✅ Payment verified! Adding you to the VIP group...")
                        await context.bot.invite_chat_member(chat_id=GROUP_CHAT_ID, user_id=user_id)
                        session.close()
                        return

        await update.message.reply_text("❌ Payment not found. Ensure you sent the correct amount from the provided address.")
    except Exception as e:
        await update.message.reply_text("❌ Error verifying payment. Please try again or contact support.")
    finally:
        session.close()

# Remove expired members
async def remove_expired_members(context: ContextTypes.DEFAULT_TYPE):
    session = Session()
    expired = session.query(Membership).filter(Membership.expiry_date < datetime.utcnow()).all()
    for member in expired:
        try:
            await context.bot.ban_chat_member(GROUP_CHAT_ID, member.user_id)
            await context.bot.unban_chat_member(GROUP_CHAT_ID, member.user_id)  # Kick and unban to remove
            await context.bot.send_message(chat_id=member.user_id, text="Your VIP membership has expired. Use /start to renew.")
            session.delete(member)
        except Exception as e:
            continue
    session.commit()
    session.close()

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(handle_button))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wallet))

# Periodic job (every 24 hours)
application.job_queue.run_repeating(remove_expired_members, interval=86400)

# Run bot
application.run_polling()
