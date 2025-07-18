from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from sqlalchemy import create_engine, Column, BigInteger, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from solana.rpc.api import Client
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import math

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
    user_id = Column(BigInteger, primary_key=True)
    membership_type = Column(String)
    expiry_date = Column(DateTime)

class TempVerification(Base):
    __tablename__ = "wagmi_temp_verifications"
    user_id = Column(BigInteger, primary_key=True)
    wallet_address = Column(String)

# Ensure tables are created
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

# Tolerance for lamports comparison
LAMBERT_TOLERANCE = 5000

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

    with Session() as session:
        try:
            if query.data in ["new_membership", "renew_membership"]:
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

                record = session.query(TempVerification).filter_by(user_id=user_id).first()
                if record:
                    record.wallet_address = "pending"
                else:
                    session.add(TempVerification(user_id=user_id, wallet_address="pending"))
                session.commit()

                payment_message = (
                    f"Please send **{amount} SOL** to this address:\n\n`{SOLANA_WALLET_ADDRESS}`\n\n"
                    "After sending, click the button below to enter your wallet address."
                )
                keyboard = [[InlineKeyboardButton("Enter Wallet Address", callback_data=f'enter_wallet_{membership_type}')]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(payment_message, parse_mode="Markdown", reply_markup=reply_markup)

            elif query.data.startswith("enter_wallet_"):
                membership_type = query.data.split("enter_wallet_")[1]
                record = session.query(TempVerification).filter_by(user_id=user_id).first()
                if record:
                    record.wallet_address = "awaiting"
                else:
                    session.add(TempVerification(user_id=user_id, wallet_address="awaiting"))
                session.commit()

                await query.edit_message_text("Please enter the Solana wallet address you used for payment:")
        except Exception as e:
            await query.message.reply_text(f"Error: {str(e)}. Please try again or contact support.")
            session.rollback()

# Handle wallet address input and verify payment
async def handle_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.message.from_user.id
    wallet_address = update.message.text.strip()

    with Session() as session:
        try:
            record = session.query(TempVerification).filter_by(user_id=user_id, wallet_address="awaiting").first()
            if not record:
                await update.message.reply_text("Please start the membership process with /start.")
                return

            await update.message.reply_text("Verifying your payment. Please wait...")

            signatures_resp = solana_client.get_signatures_for_address(SOLANA_WALLET_ADDRESS, limit=20)
            if not signatures_resp.get("result"):
                await update.message.reply_text("Could not fetch transactions from Solana network. Try again later.")
                return

            signatures = signatures_resp["result"]
            found_membership_type = None

            for sig in signatures:
                signature = sig["signature"]
                tx_resp = solana_client.get_transaction(signature)
                tx = tx_resp.get("result")
                if not tx:
                    continue

                accounts = tx["transaction"]["message"]["accountKeys"]
                if wallet_address not in accounts:
                    continue

                pre_balances = tx["meta"]["preBalances"]
                post_balances = tx["meta"]["postBalances"]

                try:
                    sender_index = accounts.index(wallet_address)
                    receiver_index = accounts.index(SOLANA_WALLET_ADDRESS)
                except ValueError:
                    continue

                lamports_sent = pre_balances[sender_index] - post_balances[sender_index]

                for mem_type, mem_info in memberships.items():
                    expected_lamports = int(mem_info["amount"] * 1_000_000_000)
                    if math.isclose(lamports_sent, expected_lamports, abs_tol=LAMBERT_TOLERANCE):
                        found_membership_type = mem_type
                        break

                if found_membership_type:
                    expiry_date = datetime.utcnow() + timedelta(seconds=memberships[found_membership_type]["duration"])
                    membership = session.query(Membership).filter_by(user_id=user_id).first()
                    if membership:
                        membership.membership_type = found_membership_type
                        membership.expiry_date = expiry_date
                    else:
                        session.add(Membership(user_id=user_id, membership_type=found_membership_type, expiry_date=expiry_date))

                    session.delete(record)
                    session.commit()

                    await context.bot.send_message(chat_id=user_id, text="✅ Payment verified! Adding you to the VIP group...")
                    invite_link = await context.bot.export_chat_invite_link(GROUP_CHAT_ID)
                    await context.bot.send_message(chat_id=user_id, text=f"Join the VIP group here:\n{invite_link}")
                    return

            await update.message.reply_text("❌ Payment not found. Ensure you sent the correct amount from the provided address.")
        except Exception as e:
            print(f"Error verifying payment: {e}")
            await update.message.reply_text("❌ Error verifying payment. Please try again or contact support.")
            session.rollback()

# Remove expired members
async def remove_expired_members(context: ContextTypes.DEFAULT_TYPE):
    with Session() as session:
        try:
            expired = session.query(Membership).filter(Membership.expiry_date < datetime.utcnow()).all()
            for member in expired:
                try:
                    await context.bot.ban_chat_member(GROUP_CHAT_ID, member.user_id)
                    await context.bot.unban_chat_member(GROUP_CHAT_ID, member.user_id)
                    await context.bot.send_message(chat_id=member.user_id, text="Your VIP membership has expired. Use /start to renew.")
                    session.delete(member)
                except Exception as e:
                    print(f"Error removing expired member {member.user_id}: {e}")
            session.commit()
        except Exception as e:
            print(f"Error in remove_expired_members: {e}")
            session.rollback()

# Error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"Update {update} caused error {context.error}")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(handle_button))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wallet))
application.add_error_handler(error_handler)

# Periodic job
application.job_queue.run_repeating(remove_expired_members, interval=86400)

# Run bot with webhook
if __name__ == "__main__":
    if 'RENDER' in os.environ:
        port = int(os.environ.get('PORT', 443))
        hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'wagmi-v2.onrender.com')
        webhook_url = f"https://{hostname}/webhook"
        print(f"Starting webhook on {webhook_url}")

        # Telegram'a webhook'u bildiriyoruz
        import asyncio
        asyncio.run(application.bot.set_webhook(url=webhook_url))

        application.run_webhook(
            listen='0.0.0.0',
            port=port,
            webhook_url=webhook_url
        )
