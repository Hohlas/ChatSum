"""Генерирует StringSession для GitHub Actions.

Запускается один раз локально (после ./setup.sh):

    ./venv/bin/python gen_session.py

Строка печатается на экран И сохраняется в telegram_session.txt
(gitignored) — push_github_secrets.sh подхватит её автоматически,
копировать через буфер обмена не обязательно.
"""

import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv('private.txt', override=False)

API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE = os.getenv('TELEGRAM_PHONE')

SESSION_FILE = 'telegram_session.txt'


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
    with open(SESSION_FILE, 'w', encoding='utf-8') as f:
        f.write(encoded.strip() + '\n')
    try:
        os.chmod(SESSION_FILE, 0o600)
    except OSError:
        pass
    print(f"✅ Строка также сохранена в {SESSION_FILE} (gitignored).")
    print(f"   push_github_secrets.sh использует её автоматически.")
    await client.disconnect()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())