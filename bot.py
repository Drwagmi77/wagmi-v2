from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from solana.rpc.api import Client
from datetime import datetime, timedelta

# Load .env variables
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))  # Make sure this is int
SOLANA_WALLET_ADDRESS = os.getenv("SOLANA_WALLET_ADDRESS")
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

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

solana_client = Client(SOLANA_RPC)

memberships = {
    "trial": {"amount": 0.1, "duration": 3 * 24 * 60 * 60},      # 3 days
    "weekly": {"amount": 0.3, "duration": 7 * 24 * 60 * 60},     # 7 days
    "monthly": {"amount": 1, "duration": 30 * 24 * 60 * 60},     # 30 days
    "six_month": {"amount": 2, "duration": 180 * 24 * 60 * 60}   # 6 months
}

application = Application.builder().token(BOT_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("New Membership", callback_data='new_membership')],
        [InlineKeyboardButton("Renew Membership", callback_data='renew_membership')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome to the WAGMI VIP world!\n"
        "Our AI-powered signal bot scans 24/7 and automatically sends signals to our group.\n"
        "Join now!",
        reply_markup=reply_markup
    )

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
            f"Please send {amount} SOL to this address:\n{SOLANA_WALLET_ADDRESS}\n"
            "After sending, click 'I Sent the Payment' below."
        )
        keyboard = [[InlineKeyboardButton("I Sent the Payment", callback_data=f'verify_{data}')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Click below to verify:", reply_markup=reply_markup)

    elif data.startswith("verify_"):
        membership_type = data.split("_")[1]
        user_id = query.from_user.id

        # Save temp verification to DB
        session.merge(TempVerification(user_id=user_id, membership_type=membership_type))
        session.commit()

        await query.edit_message_text("Okay! Now please enter the Solana wallet address you used to send the payment.")

    session.close()

async def verify_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = Session()
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    if not text.startswith("0") and len(text) < 32:
        # Simple validation
        await update.message.reply_text("Please enter a valid Solana wallet address.")
        session.close()
        return

    verification = session.query(TempVerification).filter_by(user_id=user_id).first()
    if not verification:
        await update.message.reply_text("Please select a membership tier first by typing /start.")
        session.close()
        return

    membership_type = verification.membership_type
    amount = memberships[membership_type]["amount"]
    duration = memberships[membership_type]["duration"]

    # Check Solana transactions
    try:
        signatures = solana_client.get_signatures_for_address(SOLANA_WALLET_ADDRESS, limit=20)
        found_payment = False

        for sig in signatures.value:
            tx = solana_client.get_transaction(sig.signature)
            if not tx.value or not tx.value.meta:
                continue

            # Check if payment from the address matches amount
            # Here simplified check: if sender address matches user address and amount matches
            accounts = tx.value.transaction.message.account_keys
            pre_balances = tx.value.meta.pre_balances
            post_balances = tx.value.meta.post_balances

            if text in accounts:
                sender_index = accounts.index(text)
                receiver_index = accounts.index(SOLANA_WALLET_ADDRESS) if SOLANA_WALLET_ADDRESS in accounts else -1
                if receiver_index == -1:
                    continue

                sent_amount = pre_balances[sender_index] - post_balances[sender_index]
                received_amount = post_balances[receiver_index] - pre_balances[receiver_index]

                # Convert lamports to SOL
                sent_amount_sol = sent_amount / 1e9
                received_amount_sol = received_amount / 1e9

                # Check if sent amount approximately equals expected amount
                if abs(sent_amount_sol - amount) < 0.00001:
                    found_payment = True
                    break

        if found_payment:
            expiry_date = datetime.now() + timedelta(seconds=duration)
            session.merge(Membership(user_id=user_id, membership_type=membership_type, expiry_date=expiry_date))
            session.delete(verification)
            session.commit()
            await update.message.reply_text("Payment verified! You are now a WAGMI VIP member. You will be added to the VIP group shortly.")
            await context.bot.invite_chat_member(GROUP_CHAT_ID, user_id)
        else:
            await update.message.reply_text("Payment not found or amount is incorrect. Please double-check your transaction.")

    except Exception as e:
        await update.message.reply_text("An error occurred while verifying the payment. Please try again later.")

    session.close()

async def check_expired(context: ContextTypes.DEFAULT_TYPE) -> None:
    session = Session()
    expired_members = session.query(Membership).filter(Membership.expiry_date < datetime.now()).all()
    for member in expired_members:
        try:
            await context.bot.ban_chat_member(GROUP_CHAT_ID, member.user_id)
            await context.bot.send_message(member.user_id, "Your membership has expired. Please renew it using /start.")
            session.delete(member)
        except:
            pass
    session.commit()
    session.close()

application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, verify_address))

# Check expirations every 24 hours
application.job_queue.run_repeating(check_expired, interval=86400, first=10)

application.run_polling()
