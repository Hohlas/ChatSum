#!/usr/bin/env python3
"""
Скрипт для получения ID каналов
Используется для настройки TELEGRAM_GROUP_ID в private.txt
"""

import os
import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv('private.txt')

API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE = os.getenv('TELEGRAM_PHONE')


async def list_channels():
    """Показывает список всех ваших каналов с их ID"""
    client = TelegramClient('session_name', API_ID, API_HASH)
    await client.start(phone=PHONE)
    
    print("=" * 70)
    print("📺 ВАШИ КАНАЛЫ И ГРУППЫ")
    print("=" * 70)
    print()
    
    channels_found = False
    
    async for dialog in client.iter_dialogs():
        # Показываем каналы и супергруппы
        if dialog.is_channel:
            channels_found = True
            channel_type = "Канал" if not dialog.is_group else "Супергруппа"
            print(f"📌 Название: {dialog.name}")
            print(f"   Тип: {channel_type}")
            print(f"   ID: {dialog.id}")
            if hasattr(dialog.entity, 'username') and dialog.entity.username:
                print(f"   Username: @{dialog.entity.username}")
            print("-" * 70)
    
    if not channels_found:
        print("❌ У вас нет каналов или супергрупп")
        print("💡 Создайте приватный канал в Telegram и запустите скрипт снова")
    else:
        print()
        print("💡 Скопируйте нужный ID и добавьте в private.txt:")
        print("   TELEGRAM_GROUP_ID=-1001234567890")
        print()
    
    await client.disconnect()


if __name__ == '__main__':
    print()
    print("🔍 Поиск ваших каналов...")
    print()
    asyncio.run(list_channels())

