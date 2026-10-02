"""Генерирует StringSession для GitHub Actions.

Запускается один раз локально: python gen_session.py
Копирует вывод (строку) в GitHub Secret TELEGRAM_SESSION.
"""

import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv('private.txt', override=False)

API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE = os.getenv('TELEGRAM_PHONE')


async def main():
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start(phone=PHONE)
    encoded = client.session.save()
    print("=" * 60)
    print("StringSession (скопируйте в GitHub Secret TELEGRAM_SESSION):")
    print("=" * 60)
    print(encoded)
    print("=" * 60)
    print("⚠️  НЕ коммитьте эту строку и НЕ публикуйте её.")
    print("⚠️  Не запускайте параллельно эту сессию и VPS-бота.")
    await client.disconnect()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())