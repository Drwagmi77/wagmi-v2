# wagmi.py
import os
import logging
from telethon import TelegramClient, events

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = TelegramClient('/tmp/bot.session', os.getenv('API_ID'), os.getenv('API_HASH')).start(bot_token=os.getenv('BOT_TOKEN'))

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.reply('🎉 Bot aktif! /help yazarak komutları gör')

@bot.on(events.NewMessage(pattern='/help'))
async def help(event):
    await event.reply('🤖 Komutlar:\n/start - Botu başlat\n/help - Yardım')

logger.info("Bot başlatılıyor...")
bot.run_until_disconnected()
