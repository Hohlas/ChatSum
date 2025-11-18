import os
import asyncio
from telethon import TelegramClient, events
from openai import OpenAI
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta

# Загрузка переменных окружения
load_dotenv('private.txt')

# Конфигурация Telegram
API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE = os.getenv('TELEGRAM_PHONE')
CHAT_ID = int(os.getenv('CHAT_ID'))

# ID канала для результатов (если не указан - используется "Избранное")
RESULTS_DESTINATION = os.getenv('TELEGRAM_GROUP_ID', 'me')

# Конфигурация Perplexity
PERPLEXITY_API_KEY = os.getenv('PERPLEXITY_API_KEY')

# Промт для анализа сообщений (можно настроить под свои нужды)
ANALYSIS_PROMPT = """Ты - аналитик сообщений в Telegram чатах. 
Твоя задача - создать краткую, структурированную выжимку из предоставленных сообщений.

Проанализируй все сообщения и создай выжимку, которая должна включать:
1. Основные темы обсуждения
2. Ключевые решения или выводы
3. Важные факты, даты, цифры
4. Упоминания о задачах или действиях
5. Спорные моменты или вопросы, требующие внимания

Выжимка должна быть структурированной, лаконичной и информативной.
"""

# Инициализация клиентов
telegram_client = TelegramClient('session_name', API_ID, API_HASH)

perplexity_client = OpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url='https://api.perplexity.ai'
)


async def collect_messages(chat_id, hours=24, days=0):
    """
    Собирает сообщения из чата за указанный период
    
    Args:
        chat_id: ID чата для анализа
        hours: Количество часов назад (по умолчанию 24)
        days: Количество дней назад (по умолчанию 0)
    
    Returns:
        Список словарей с информацией о сообщениях
    """
    print(f"🔄 Загрузка сообщений за последние {days} дней и {hours} часов...")
    
    # Вычисляем временную границу
    time_limit = datetime.now() - timedelta(days=days, hours=hours)
    
    messages_data = []
    async for message in telegram_client.iter_messages(chat_id):
        # Прерываем, если достигли временного предела
        if message.date < time_limit:
            break
            
        if message.text:
            sender = await message.get_sender()
            sender_name = "Unknown"
            
            if hasattr(sender, 'first_name'):
                sender_name = sender.first_name
                if hasattr(sender, 'last_name') and sender.last_name:
                    sender_name += f" {sender.last_name}"
            elif hasattr(sender, 'title'):
                sender_name = sender.title
            
            messages_data.append({
                'sender': sender_name,
                'text': message.text,
                'date': message.date.strftime('%Y-%m-%d %H:%M:%S')
            })
    
    # Сортируем по времени (от старых к новым)
    messages_data.reverse()
    
    print(f"✅ Загружено {len(messages_data)} сообщений")
    return messages_data


async def create_summary(messages_data):
    """
    Создает выжимку из сообщений с помощью Perplexity API
    
    Args:
        messages_data: Список словарей с сообщениями
    
    Returns:
        Текст выжимки
    """
    if not messages_data:
        return "❌ Нет сообщений для анализа за указанный период"
    
    print(f"🤖 Отправка {len(messages_data)} сообщений в Perplexity для анализа...")
    
    # Формируем текст со всеми сообщениями
    messages_text = "\n\n".join([
        f"[{msg['date']}] {msg['sender']}: {msg['text']}"
        for msg in messages_data
    ])
    
    # Ограничиваем размер, если сообщений очень много
    max_chars = 15000  # Оставляем место для промта и ответа
    if len(messages_text) > max_chars:
        messages_text = messages_text[:max_chars] + "\n\n... (сообщения обрезаны из-за ограничения размера)"
    
    try:
        response = perplexity_client.chat.completions.create(
            model='sonar',
            messages=[
                {'role': 'system', 'content': ANALYSIS_PROMPT},
                {'role': 'user', 'content': f'Сообщения для анализа:\n\n{messages_text}'}
            ],
            max_tokens=2000,
            temperature=0.3
        )
        
        summary = response.choices[0].message.content
        print("✅ Выжимка успешно создана")
        return summary
        
    except Exception as e:
        error_msg = f"❌ Ошибка при создании выжимки: {e}"
        print(error_msg)
        return error_msg


def save_analysis(messages_data, summary):
    """Сохраняет результаты анализа в JSON файл"""
    result = {
        'timestamp': datetime.now().isoformat(),
        'messages_count': len(messages_data),
        'messages': messages_data,
        'summary': summary
    }
    
    filename = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"💾 Результаты сохранены в {filename}")


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/analyze'))
async def handle_analyze_command(event):
    """
    Обработчик команды /analyze для запуска анализа чата
    
    Примеры использования:
    /analyze - анализ за последние 24 часа
    /analyze 12h - анализ за последние 12 часов
    /analyze 2d - анализ за последние 2 дня
    /analyze 3d 6h - анализ за последние 3 дня и 6 часов
    """
    try:
        # Парсим параметры команды
        message_text = event.raw_text
        parts = message_text.split()
        
        hours = 24
        days = 0
        
        # Обрабатываем параметры
        for part in parts[1:]:
            part = part.lower()
            if 'h' in part:
                hours = int(part.replace('h', ''))
            elif 'd' in part:
                days = int(part.replace('d', ''))
        
        # Получаем название чата для информации
        chat = await event.get_chat()
        chat_name = chat.title if hasattr(chat, 'title') else "чата"
        
        # Удаляем команду из чата (для приватности)
        await event.delete()
        
        # Информируем о начале анализа в канале/Избранном
        await telegram_client.send_message(RESULTS_DESTINATION, f"🔄 Начинаю анализ чата '{chat_name}' за последние {days} дней и {hours} часов...")
        
        # Собираем сообщения
        messages_data = await collect_messages(event.chat_id, hours=hours, days=days)
        
        if not messages_data:
            await telegram_client.send_message(RESULTS_DESTINATION, f"❌ За указанный период не найдено сообщений в чате '{chat_name}'")
            return
        
        # Создаем выжимку
        summary = await create_summary(messages_data)
        
        # Сохраняем результаты
        save_analysis(messages_data, summary)
        
        # Отправляем выжимку пользователю в канал/Избранное
        response = f"📍 Чат: **{chat_name}**\n\n"
        response += f"📊 **Выжимка чата**\n\n"
        response += f"Период: последние {days} дней и {hours} часов\n"
        response += f"Сообщений проанализировано: {len(messages_data)}\n\n"
        response += f"**Результат анализа:**\n\n{summary}"
        
        # Если сообщение слишком длинное, разбиваем на части
        max_length = 4096  # Ограничение Telegram
        if len(response) > max_length:
            # Отправляем первую часть
            await telegram_client.send_message(RESULTS_DESTINATION, response[:max_length])
            # Отправляем остаток
            remaining = response[max_length:]
            while remaining:
                await telegram_client.send_message(RESULTS_DESTINATION, remaining[:max_length])
                remaining = remaining[max_length:]
        else:
            await telegram_client.send_message(RESULTS_DESTINATION, response)
        
        print("✅ Анализ успешно завершён и отправлен пользователю")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при выполнении команды: {e}"
        print(error_msg)
        await telegram_client.send_message(RESULTS_DESTINATION, error_msg)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/help'))
async def handle_help_command(event):
    """Обработчик команды /help - показывает справку по командам"""
    help_text = """
📖 **Справка по командам бота**

**Основные команды:**

`/analyze` - анализ чата за последние 24 часа

`/analyze [время]` - анализ за указанный период
Примеры:
  • `/analyze 12h` - за последние 12 часов
  • `/analyze 2d` - за последние 2 дня
  • `/analyze 3d 6h` - за последние 3 дня и 6 часов

`/help` - показать эту справку

**Как это работает:**
1. Бот собирает все сообщения из текущего чата за указанный период
2. Отправляет их в Perplexity AI для анализа
3. Получает структурированную выжимку
4. Отправляет результат в ваше "Избранное"

**🔒 Приватность:**
• Ваша команда автоматически удаляется из чата
• Результаты отправляются в ваш приватный канал/Избранное
• Никто в чате не узнает, что вы делали анализ

**Примечание:** Бот реагирует только на ваши собственные команды (исходящие сообщения).
"""
    await event.delete()
    await telegram_client.send_message(RESULTS_DESTINATION, help_text)


async def main():
    """Основная функция запуска"""
    print("🚀 Запуск Telegram бота для анализа чатов...")
    print("=" * 60)
    
    await telegram_client.start(phone=PHONE)
    print("✅ Подключение к Telegram установлено")
    
    # Показываем куда будут отправляться результаты
    destination_text = "приватный канал" if RESULTS_DESTINATION != 'me' else "Избранное"
    print(f"\n📮 Результаты будут отправляться в: {destination_text}")
    if RESULTS_DESTINATION != 'me':
        print(f"   ID канала: {RESULTS_DESTINATION}")
    
    print("\n📌 Доступные команды:")
    print("  /analyze - анализ чата за последние 24 часа")
    print("  /analyze [время] - анализ за указанный период")
    print("  /help - справка по командам")
    print("\n💡 Отправьте команду /analyze в любом чате для начала анализа")
    print("=" * 60)
    print("\n👀 Ожидание команд...")
    
    await telegram_client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
