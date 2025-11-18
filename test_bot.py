import os
import asyncio
from telethon import TelegramClient, events
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Загрузка переменных окружения
load_dotenv('private.txt')

# Конфигурация Telegram
API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
PHONE = os.getenv('TELEGRAM_PHONE')

# ID канала для результатов (если не указан - используется "Избранное")
RESULTS_DESTINATION = os.getenv('TELEGRAM_GROUP_ID', 'me')
if RESULTS_DESTINATION != 'me':
    try:
        RESULTS_DESTINATION = int(RESULTS_DESTINATION)
    except ValueError:
        print(f"⚠️  Неверный формат TELEGRAM_GROUP_ID: {RESULTS_DESTINATION}")
        print("   Использую 'Избранное' вместо канала")
        RESULTS_DESTINATION = 'me'

# Инициализация клиента
telegram_client = TelegramClient('session_name', API_ID, API_HASH)


async def collect_messages_test(chat_id, limit=2):
    """
    ТЕСТОВАЯ функция: собирает только N последних сообщений
    
    Args:
        chat_id: ID чата для анализа
        limit: Количество последних сообщений (по умолчанию 2)
    
    Returns:
        Список словарей с информацией о сообщениях
    """
    print(f"🔄 Загрузка {limit} последних сообщений...")
    print(f"📍 ID чата: {chat_id}")
    
    messages_data = []
    count = 0
    
    async for message in telegram_client.iter_messages(chat_id):
        if count >= limit:
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
                'date': message.date.strftime('%Y-%m-%d %H:%M:%S'),
                'message_id': message.id
            })
            count += 1
    
    # Сортируем по времени (от старых к новым)
    messages_data.reverse()
    
    print(f"✅ Загружено {len(messages_data)} сообщений")
    return messages_data


def format_messages_display(messages_data):
    """Форматирует сообщения для красивого отображения"""
    if not messages_data:
        return "❌ Нет сообщений"
    
    result = "=" * 60 + "\n"
    result += f"📊 ТЕСТОВАЯ ЗАГРУЗКА: {len(messages_data)} сообщений\n"
    result += "=" * 60 + "\n\n"
    
    for i, msg in enumerate(messages_data, 1):
        result += f"📩 Сообщение #{i} (ID: {msg['message_id']})\n"
        result += f"👤 Отправитель: {msg['sender']}\n"
        result += f"📅 Дата: {msg['date']}\n"
        result += f"💬 Текст: {msg['text'][:200]}{'...' if len(msg['text']) > 200 else ''}\n"
        result += "-" * 60 + "\n\n"
    
    return result


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/test'))
async def handle_test_command(event):
    """
    Тестовая команда для проверки загрузки сообщений
    
    Использование:
    /test - загрузит 2 последних сообщения
    /test 5 - загрузит 5 последних сообщений
    """
    try:
        # Парсим параметры команды
        message_text = event.raw_text
        parts = message_text.split()
        
        limit = 2  # По умолчанию 2 сообщения
        
        # Если указан параметр - используем его
        if len(parts) > 1:
            try:
                limit = int(parts[1])
            except ValueError:
                await event.delete()
                await telegram_client.send_message(RESULTS_DESTINATION, "❌ Неверный формат. Используйте: /test или /test 5")
                return
        
        # Получаем название чата для информации
        chat = await event.get_chat()
        chat_name = chat.title if hasattr(chat, 'title') else "этого чата"
        
        # Информируем о начале (удаляем своё сообщение с командой для чистоты)
        await event.delete()
        
        # Отправляем уведомление в канал/Избранное
        await telegram_client.send_message(RESULTS_DESTINATION, f"🔄 ТЕСТ: Загружаю {limit} последних сообщений из чата '{chat_name}'...")
        
        # Собираем сообщения
        messages_data = await collect_messages_test(event.chat_id, limit=limit)
        
        if not messages_data:
            await telegram_client.send_message(RESULTS_DESTINATION, f"❌ Не найдено текстовых сообщений в чате '{chat_name}'")
            return
        
        # Форматируем и отправляем результат в канал/Избранное
        display_text = f"📍 Чат: **{chat_name}**\n\n" + format_messages_display(messages_data)
        
        # Отправляем в канал/Избранное (разбиваем на части если нужно)
        max_length = 4096  # Ограничение Telegram
        if len(display_text) > max_length:
            # Отправляем первую часть
            await telegram_client.send_message(RESULTS_DESTINATION, display_text[:max_length])
            # Отправляем остаток
            remaining = display_text[max_length:]
            while remaining:
                await telegram_client.send_message(RESULTS_DESTINATION, remaining[:max_length])
                remaining = remaining[max_length:]
        else:
            await telegram_client.send_message(RESULTS_DESTINATION, display_text)
        
        print("✅ Тест успешно завершён")
        
        # Выводим в консоль для отладки
        print("\n" + display_text)
        
    except Exception as e:
        error_msg = f"❌ Ошибка при выполнении команды: {e}"
        print(error_msg)
        await telegram_client.send_message(RESULTS_DESTINATION, error_msg)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/help'))
async def handle_help_command(event):
    """Обработчик команды /help - показывает справку по командам"""
    help_text = """
🧪 **ТЕСТОВЫЙ БОТ - Справка**

**Команды для тестирования:**

`/test` - загрузить 2 последних сообщения
`/test 5` - загрузить 5 последних сообщений
`/test 10` - загрузить 10 последних сообщений

`/help` - показать эту справку

**Что проверяется:**
✅ Подключение к Telegram API
✅ Чтение сообщений из чата
✅ Получение информации об отправителях
✅ Форматирование дат и текста

**🔒 Приватность:**
Результаты отправляются в ваш приватный канал/Избранное.
Ваше сообщение с командой автоматически удаляется.
Никто в чате не увидит ни команду, ни результаты!

**Примечание:** 
Это тестовая версия БЕЗ Perplexity API.
Просто проверяем загрузку сообщений.
"""
    await event.delete()
    await telegram_client.send_message(RESULTS_DESTINATION, help_text)


async def main():
    """Основная функция запуска"""
    print("🧪 ТЕСТОВЫЙ РЕЖИМ: Запуск бота для проверки загрузки сообщений")
    print("=" * 60)
    print("⚠️  Perplexity API НЕ используется (экономим токены)")
    print("=" * 60)
    
    await telegram_client.start(phone=PHONE)
    print("✅ Подключение к Telegram установлено")
    
    # Показываем куда будут отправляться результаты
    destination_text = "приватный канал" if RESULTS_DESTINATION != 'me' else "Избранное"
    print(f"\n📮 Результаты будут отправляться в: {destination_text}")
    if RESULTS_DESTINATION != 'me':
        print(f"   ID канала: {RESULTS_DESTINATION}")
        # Проверяем доступность канала
        try:
            channel = await telegram_client.get_entity(RESULTS_DESTINATION)
            channel_name = channel.title if hasattr(channel, 'title') else "Канал"
            print(f"   ✅ Канал найден: {channel_name}")
        except Exception as e:
            print(f"   ⚠️  Не могу получить доступ к каналу: {e}")
            print(f"   💡 Убедитесь что вы являетесь владельцем/админом канала")
            print(f"   💡 Или закомментируйте TELEGRAM_GROUP_ID в private.txt")
    
    print("\n📌 Доступные тестовые команды:")
    print("  /test - загрузить 2 последних сообщения")
    print("  /test 5 - загрузить 5 последних сообщений")
    print("  /help - справка")
    print("\n💡 Отправьте команду /test в любом чате для проверки")
    print("=" * 60)
    print("\n👀 Ожидание команд...")
    
    await telegram_client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())

