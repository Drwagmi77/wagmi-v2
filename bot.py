from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
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
    awaiting_wallet = Column(String)  # "yes" if waiting for user wallet

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Solana client
solana_client = Client(SOLANA_RPC)

# Membership configuration (you can expand this later)
amount_required = 0.1  # in SOL
duration_seconds = 3 * 24 * 60 * 60  # 3 days

# Create bot application
application = Application.builder().token(BOT_TOKEN).build()

# START button
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("Start", callback_data='start_flow')]]
    await update.message.reply_text("Welcome to WAGMI VIP Bot. Click below to begin:", reply_markup=InlineKeyboardMarkup(keyboard))

# BUTTON flow
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "start_flow":
        keyboard = [[InlineKeyboardButton("I Sent the Payment", callback_data='sent_payment')]]
        await query.edit_message_text(
            text=f"Please send **{amount_required} SOL** to this address:\n\n`{SOLANA_WALLET_ADDRESS}`\n\nAfter sending, click the button below.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "sent_payment":
        session = Session()
        user_id = query.from_user.id
        session.merge(TempVerification(user_id=user_id, awaiting_wallet="yes"))
        session.commit()
        session.close()

        await query.message.reply_text("Please enter the **Solana address** you used to make the payment:")

# HANDLE WALLET ADDRESS
async def handle_wallet_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_input = update.message.text.strip()
    session = Session()

    record = session.query(TempVerification).filter_by(user_id=user_id, awaiting_wallet="yes").first()
    if not record:
        await update.message.reply_text("Please start the process by clicking /start.")
        session.close()
        return

    await update.message.reply_text("Verifying your payment. Please wait...")

    try:
        # Check recent transactions to the bot wallet
        response = solana_client.get_signatures_for_address(SOLANA_WALLET_ADDRESS, limit=10)
        signatures = response.get("result", [])

        for sig in signatures:
            tx = solana_client.get_transaction(sig["signature"])
            if not tx["result"]:
                continue

            accounts = tx["result"]["transaction"]["message"]["accountKeys"]
            if user_input not in accounts:
                continue

            pre_balances = tx["result"]["meta"]["preBalances"]
            post_balances = tx["result"]["meta"]["postBalances"]
            if len(pre_balances) < 2 or len(post_balances) < 2:
                continue

            lamports_sent = post_balances[1] - pre_balances[1]
            if lamports_sent == int(amount_required * 1e9):
                # Payment verified
                expiry = datetime.utcnow() + timedelta(seconds=duration_seconds)
                session.merge(Membership(user_id=user_id, membership_type="trial", expiry_date=expiry))
                session.delete(record)
                session.commit()

                # Add user to group
                await context.bot.send_message(chat_id=user_id, text="✅ Payment confirmed! Adding you to the VIP group now...")
                await context.bot.invite_chat_member(chat_id=GROUP_CHAT_ID, user_id=user_id)
                session.close()
                return

        await update.message.reply_text("❌ Payment not found. Make sure you sent exactly the correct amount from the address you provided.")
    except Exception as e:
        await update.message.reply_text("❌ Error verifying payment. Please try again or contact support.")
    finally:
        session.close()

# REMOVE EXPIRED MEMBERS
async def remove_expired_members(context: ContextTypes.DEFAULT_TYPE):
    session = Session()
    expired = session.query(Membership).filter(Membership.expiry_date < datetime.utcnow()).all()
    for member in expired:
        try:
            await context.bot.ban_chat_member(GROUP_CHAT_ID, member.user_id)
            await context.bot.unban_chat_member(GROUP_CHAT_ID, member.user_id)
            await context.bot.send_message(chat_id=member.user_id, text="Your VIP membership has expired. Please return to /start to renew.")
            session.delete(member)
        except:
            continue
    session.commit()
    session.close()

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(handle_button))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wallet_address))

# Periodic job (every 24h)
application.job_queue.run_repeating(remove_expired_members, interval=86400)

# Run bot
application.run_polling()
