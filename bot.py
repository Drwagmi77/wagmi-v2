from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from solana.rpc.api import Client
from datetime import datetime, timedelta

# Load environment variables from .env file
load_dotenv()

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))  # Make sure this is an integer
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
    membership_type = Column(String)

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Solana client
solana_client = Client(SOLANA_RPC)

# Membership plans
memberships = {
    "trial": {"amount": 0.1, "duration": 3 * 24 * 60 * 60},       # 3 days
    "weekly": {"amount": 0.3, "duration": 7 * 24 * 60 * 60},      # 1 week
    "monthly": {"amount": 1, "duration": 30 * 24 * 60 * 60},      # 1 month
    "six_month": {"amount": 2, "duration": 180 * 24 * 60 * 60}    # 6 months
}

# Create bot application
application = Application.builder().token(BOT_TOKEN).build()

# /start command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("New Membership", callback_data='new_membership')],
        [InlineKeyboardButton("Renew Membership", callback_data='renew_membership')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome to the WAGMI VIP world!\nOur AI-powered signal bot runs 24/7 and sends signals automatically to our group.\nJoin now!",
        reply_markup=reply_markup
    )

# Button callback handler
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    session = Session()

    if data in ["new_membership", "renew_membership"]:
        keyboard = [
            [InlineKeyboardButton("3-Day Trial (0.1 SOL)", callback_data='trial')],
            [InlineKeyboardButton("Weekly (0.3 SOL)", callback_data='weekly')],
            [InlineKeyboardButton("Monthly (1 SOL)", callback_data='monthly')],
            [InlineKeyboardButton("6-Month (2 SOL)", callback_data='six_month')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Please select a membership tier:", reply_markup=reply_markup)

    elif data in memberships:
        amount = memberships[data]["amount"]
        await query.edit_message_text(
            f"Please send {amount} SOL to this address:\n`{SOLANA_WALLET_ADDRESS}`\n\n"
            "After sending, please click the 'I Sent the Payment' button below.",
            parse_mode="Markdown"
        )
        keyboard = [[InlineKeyboardButton("I Sent the Payment", callback_data=f'verify_{data}')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Click below to verify your payment:", reply_markup=reply_markup)

    elif data.startswith("verify_"):
        membership_type = data.split("_")[1]
        user_id = query.from_user.id

        # Save temp verification
        existing = session.query(TempVerification).filter_by(user_id=user_id).first()
        if existing:
            existing.membership_type = membership_type
        else:
            session.add(TempVerification(user_id=user_id, membership_type=membership_type))
        session.commit()

        await query.message.reply_text(
            "Please enter your SOL wallet address (the one you sent the payment from) to verify your payment."
        )
    session.close()

# User sends SOL address for verification
async def verify_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    session = Session()
    verification = session.query(TempVerification).filter_by(user_id=user_id).first()
    if not verification:
        await update.message.reply_text(
            "Please start by selecting a membership tier with /start."
        )
        session.close()
        return

    address = update.message.text.strip()
    membership_type = verification.membership_type
    amount = memberships[membership_type]["amount"]
    duration = memberships[membership_type]["duration"]

    await update.message.reply_text("Checking your payment, please wait...")

    try:
        # Check recent signatures to the bot's SOL wallet address
        signatures_resp = solana_client.get_signatures_for_address(SOLANA_WALLET_ADDRESS, limit=20)
        found_payment = False

        for sig_info in signatures_resp.value:
            tx_resp = solana_client.get_transaction(sig_info.signature)
            tx = tx_resp.value
            if not tx or not tx.meta:
                continue

            # Check if sender address matches and amount matches (lamports)
            sender = tx.transaction.message.account_keys[0]
            if sender != address:
                continue

            # Calculate lamports sent to bot wallet
            pre_balances = tx.meta.pre_balances
            post_balances = tx.meta.post_balances
            # The bot wallet is usually account 1 in transaction message account_keys
            # We assume index 1 is the bot wallet - adjust if needed
            bot_wallet_index = None
            try:
                bot_wallet_index = tx.transaction.message.account_keys.index(SOLANA_WALLET_ADDRESS)
            except ValueError:
                bot_wallet_index = 1  # fallback index

            if bot_wallet_index is None:
                continue

            lamports_sent = post_balances[bot_wallet_index] - pre_balances[bot_wallet_index]
            sol_sent = lamports_sent / 1e9

            if abs(sol_sent - amount) < 0.0001:
                found_payment = True
                break

        if found_payment:
            expiry_date = datetime.utcnow() + timedelta(seconds=duration)
            # Add membership to DB
            membership = session.query(Membership).filter_by(user_id=user_id).first()
            if membership:
                membership.membership_type = membership_type
                membership.expiry_date = expiry_date
            else:
                membership = Membership(user_id=user_id, membership_type=membership_type, expiry_date=expiry_date)
                session.add(membership)

            # Remove temp verification
            session.delete(verification)
            session.commit()

            await update.message.reply_text(
                "Payment verified! You are now a WAGMI VIP member. Adding you to the group..."
            )
            try:
                await context.bot.invite_chat_member(GROUP_CHAT_ID, user_id)
            except Exception as e:
                await update.message.reply_text(
                    "Failed to add you to the group automatically. Please check if the bot has admin rights."
                )
        else:
            await update.message.reply_text(
                "Payment not found or amount incorrect. Please check and try again."
            )
    except Exception as e:
        await update.message.reply_text(
            "An error occurred during verification. Please try again later."
        )
    finally:
        session.close()

# Periodic membership expiry check
async def check_expired(context: ContextTypes.DEFAULT_TYPE) -> None:
    session = Session()
    expired_members = session.query(Membership).filter(Membership.expiry_date < datetime.utcnow()).all()
    for member in expired_members:
        try:
            await context.bot.ban_chat_member(GROUP_CHAT_ID, member.user_id)
            await context.bot.send_message(member.user_id, "Your membership has expired. Please renew via /start.")
        except Exception:
            pass
        session.delete(member)
    session.commit()
    session.close()

# Handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, verify_address))

# Schedule membership expiry check every 24 hours
application.job_queue.run_repeating(check_expired, interval=86400)

# Run bot
application.run_polling()
