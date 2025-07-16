from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from solana.rpc.api import Client
from datetime import datetime, timedelta

# .env dosyasını yükle
load_dotenv()

# Ortam değişkenlerini al
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")
SOLANA_WALLET_ADDRESS = os.getenv("SOLANA_WALLET_ADDRESS")
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Veritabanı modeli
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

# Veritabanı bağlantısı
engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

# Solana istemcisi
solana_client = Client(SOLANA_RPC)

# Üyelik türleri
memberships = {
    "trial": {"amount": 0.1, "duration": 3 * 24 * 60 * 60},  # 3 gün
    "weekly": {"amount": 0.3, "duration": 7 * 24 * 60 * 60},  # 1 hafta
    "monthly": {"amount": 1, "duration": 30 * 24 * 60 * 60},  # 1 ay
    "six_month": {"amount": 2, "duration": 180 * 24 * 60 * 60}  # 6 ay
}

# Botu başlat
application = Application.builder().token(BOT_TOKEN).build()

# /start komutu
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("New Membership", callback_data='new_membership')],
        [InlineKeyboardButton("Renew Membership", callback_data='renew_membership')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome to the WAGMI VIP world!\nOur AI-powered signal bot scans 24/7 and automatically sends signals to our group.\nJoin now!",
        reply_markup=reply_markup
    )

# Buton tıklama handler'ı
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

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
            f"Please send {amount} SOL to this address: {SOLANA_WALLET_ADDRESS}\nAfter sending, click 'I Sent the Payment'."
        )
        keyboard = [[InlineKeyboardButton("I Sent the Payment", callback_data=f'verify_{data}')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Click below to verify:", reply_markup=reply_markup)
    elif data.startswith("verify_"):
        membership_type = data.split("_")[1]
        user_id = query.from_user.id
        session.add(TempVerification(user_id=user_id, membership_type=membership_type))
        session.commit()
        await query.message.reply_text("Please enter your Solana wallet address with /verify <address>\nExample: /verify 7XjK3Xz1jZ2k3Xz4y5Z6A7B8C9D0E1F2G3H4I5J6K7L8M9N0P1Q2R3S4T5U6V7W8X9Y0Z")

# Ödeme doğrulama
async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    try:
        address = update.message.text.split("/verify ")[1]
        verification = session.query(TempVerification).filter_by(user_id=user_id).first()
        if not verification:
            await update.message.reply_text("Please select a membership tier first with /start.")
            return
        membership_type = verification.membership_type
        amount = memberships[membership_type]["amount"]
        duration = memberships[membership_type]["duration"]

        # Basit Solana kontrolü (gerçek uygulamada daha detaylı olmalı)
        signatures = solana_client.get_signatures_for_address(SOLANA_WALLET_ADDRESS, limit=10)
        for sig in signatures.value:
            tx = solana_client.get_transaction(sig.signature)
            if tx.value and tx.value.meta and tx.value.transaction.message.account_keys[0] == address:
                if tx.value.meta.post_balances[1] - tx.value.meta.pre_balances[1] == int(amount * 1e9):  # SOL to lamports
                    expiry_date = datetime.now() + timedelta(seconds=duration)
                    session.add(Membership(user_id=user_id, membership_type=membership_type, expiry_date=expiry_date))
                    session.delete(verification)
                    session.commit()
                    await update.message.reply_text("Payment verified! You are being added to the WAGMI VIP group.")
                    await context.bot.invite_chat_member(GROUP_CHAT_ID, user_id)
                    return
        await update.message.reply_text("Payment not found. Please check the address or amount.")
    except Exception as e:
        await update.message.reply_text("Invalid Solana address. Please enter a valid address.")
    finally:
        session.close()

# Cron işlevi için basit bir döngü (Render’da gerçek cron gerekebilir)
async def check_expired(context: ContextTypes.DEFAULT_TYPE) -> None:
    expired = session.query(Membership).filter(Membership.expiry_date < datetime.now()).all()
    for member in expired:
        await context.bot.kick_chat_member(GROUP_CHAT_ID, member.user_id)
        session.delete(member)
        await context.bot.send_message(member.user_id, "Your membership has expired. Use /start to renew.")
    session.commit()

# Handler'ları ekle
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button))
application.add_handler(CommandHandler("verify", verify))

# Cron benzeri kontrol (her 24 saatte bir)
application.job_queue.run_repeating(check_expired, interval=86400)

# Botu çalıştır
application.run_polling()
