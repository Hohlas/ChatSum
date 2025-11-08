import os
import asyncio
from telethon import TelegramClient, events
from openai import OpenAI
from dotenv import load_dotenv
import json
from datetime import datetime

# Загрузка переменных окружения
load_dotenv()

# Конфигурация Telegram
API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE = os.getenv('TELEGRAM_PHONE')
CHAT_ID = int(os.getenv('CHAT_ID'))

# Конфигурация Perplexity
PERPLEXITY_API_KEY = os.getenv('PERPLEXITY_API_KEY')

# Инициализация клиентов
telegram_client = TelegramClient('session_name', API_ID, API_HASH)

perplexity_client = OpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url='https://api.perplexity.ai'
)

results = []


async def analyze_message(message_text, sender, date):
    """Анализирует сообщение с помощью Perplexity API"""
    try:
        response = perplexity_client.chat.completions.create(
            model='sonar',
            messages=[
                {'role': 'system', 'content': 'Ты - аналитик сообщений в Telegram чатах.'},
                {'role': 'user', 'content': f'Проанализируй сообщение от {sender}: {message_text}'}
            ],
            max_tokens=500,
            temperature=0.3
        )
        
        analysis = response.choices[0].message.content
        
        return {
            'message': message_text,
            'sender': sender,
            'date': date,
            'analysis': analysis,
            'timestamp': datetime.now().isoformat()
        }
        
    except Exception as e:
        print(f"Ошибка при анализе: {e}")
        return None


@telegram_client.on(events.NewMessage(chats=[CHAT_ID]))
async def handler_new_message(event):
    """Обработчик новых сообщений в реальном времени"""
    sender = await event.get_sender()
    sender_name = sender.first_name if hasattr(sender, 'first_name') else 'Unknown'
    
    message_text = event.raw_text
    message_date = event.date.isoformat()
    
    print(f"\n📩 Новое сообщение от {sender_name}:")
    print(f"   {message_text[:100]}...")
    
    result = await analyze_message(message_text, sender_name, message_date)
    
    if result:
        results.append(result)
        print(f"✅ Анализ завершён")
        save_results()


def save_results():
    """Сохраняет результаты анализа в JSON файл"""
    with open('analysis_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


async def analyze_history(limit=100):
    """Анализирует историю сообщений из чата"""
    print(f"🔄 Загрузка последних {limit} сообщений...")
    
    messages = await telegram_client.get_messages(CHAT_ID, limit=limit)
    
    for message in reversed(messages):
        if message.text:
            sender = await message.get_sender()
            sender_name = sender.first_name if hasattr(sender, 'first_name') else 'Unknown'
            
            result = await analyze_message(
                message.text,
                sender_name,
                message.date.isoformat()
            )
            
            if result:
                results.append(result)
                print(f"✅ Обработано сообщение от {sender_name}")
            
            await asyncio.sleep(2)  # Задержка для избежания rate limits
    
    save_results()
    print(f"\n📊 Анализ завершён. Обработано {len(results)} сообщений")


async def main():
    """Основная функция запуска"""
    print("🚀 Запуск Telegram бота...")
    
    await telegram_client.start(phone=PHONE)
    print("✅ Подключение к Telegram установлено")
    
    # Опция 1: Анализировать историю
    # await analyze_history(limit=50)
    
    # Опция 2: Мониторить новые сообщения в реальном времени
    print("👀 Мониторинг новых сообщений...")
    await telegram_client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
