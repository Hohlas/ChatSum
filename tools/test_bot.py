import os
import asyncio
import random
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


async def get_or_create_topic(chat_name):
    """
    Находит или создает тему в канале по названию чата
    
    Args:
        chat_name: Название чата-источника
    
    Returns:
        ID темы (topic_id) или None если канал не поддерживает темы
    """
    if RESULTS_DESTINATION == 'me':
        # Избранное не поддерживает темы
        return None
    
    try:
        # Получаем информацию о канале
        channel = await telegram_client.get_entity(RESULTS_DESTINATION)
        
        # Проверяем, является ли канал форумом
        if not hasattr(channel, 'forum') or not channel.forum:
            print(f"⚠️  Канал не является форумом. Темы не поддерживаются.")
            return None
        
        # Ищем существующую тему с таким названием
        from telethon.tl.functions.channels import GetForumTopicsRequest
        try:
            result = await telegram_client(GetForumTopicsRequest(
                channel=channel,
                offset_date=0,
                offset_id=0,
                offset_topic=0,
                limit=100
            ))
            
            # Ищем тему по названию
            for topic in result.topics:
                if hasattr(topic, 'title') and topic.title == chat_name:
                    print(f"✅ Найдена существующая тема: {chat_name} (ID: {topic.id})")
                    return topic.id
        except Exception as e:
            print(f"⚠️  Ошибка при поиске тем: {e}")
        
        # Если тема не найдена - создаём новую
        from telethon.tl.functions.channels import CreateForumTopicRequest
        try:
            result = await telegram_client(CreateForumTopicRequest(
                channel=channel,
                title=chat_name,
                random_id=random.randrange(-2**63, 2**63)
            ))
            
            # Получаем ID созданной темы из ответа
            topic_id = result.updates[0].id if hasattr(result, 'updates') and result.updates else None
            print(f"✅ Создана новая тема: {chat_name} (ID: {topic_id})")
            return topic_id
        except Exception as e:
            print(f"❌ Ошибка при создании темы: {e}")
            print(f"   Детали: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            return None
            
    except Exception as e:
        print(f"❌ Ошибка при работе с темами: {e}")
        import traceback
        traceback.print_exc()
        return None


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
        
        # Получаем или создаем тему для этого чата
        topic_id = await get_or_create_topic(chat_name)
        
        # Отправляем уведомление в канал/Избранное/Тему
        await telegram_client.send_message(
            RESULTS_DESTINATION, 
            f"🔄 ТЕСТ: Загружаю {limit} последних сообщений из чата '{chat_name}'...",
            reply_to=topic_id
        )
        
        # Собираем сообщения
        messages_data = await collect_messages_test(event.chat_id, limit=limit)
        
        if not messages_data:
            await telegram_client.send_message(
                RESULTS_DESTINATION, 
                f"❌ Не найдено текстовых сообщений в чате '{chat_name}'",
                reply_to=topic_id
            )
            return
        
        # Форматируем и отправляем результат в канал/Избранное/Тему
        display_text = f"📍 Чат: **{chat_name}**\n\n" + format_messages_display(messages_data)
        
        # Отправляем в канал/Избранное/Тему (разбиваем на части если нужно)
        max_length = 4096  # Ограничение Telegram
        if len(display_text) > max_length:
            # Отправляем первую часть
            await telegram_client.send_message(
                RESULTS_DESTINATION, 
                display_text[:max_length],
                reply_to=topic_id
            )
            # Отправляем остаток
            remaining = display_text[max_length:]
            while remaining:
                await telegram_client.send_message(
                    RESULTS_DESTINATION, 
                    remaining[:max_length],
                    reply_to=topic_id
                )
                remaining = remaining[max_length:]
        else:
            await telegram_client.send_message(
                RESULTS_DESTINATION, 
                display_text,
                reply_to=topic_id
            )
        
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
✅ Автоматическое создание тем по названию чата

**🔒 Приватность:**
Результаты отправляются в ваш приватный канал/Избранное.
Ваше сообщение с командой автоматически удаляется.
Никто в чате не увидит ни команду, ни результаты!

**📁 Организация:**
Для каждого чата автоматически создается отдельная тема.
Все сообщения группируются по источнику!

**Примечание:** 
Это тестовая версия БЕЗ Gemini API.
Просто проверяем загрузку сообщений.
"""
    await event.delete()
    
    # Получаем название чата для создания/использования темы
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Справка"
    topic_id = await get_or_create_topic(chat_name)
    
    await telegram_client.send_message(RESULTS_DESTINATION, help_text, reply_to=topic_id)


async def main():
    """Основная функция запуска"""
    print("🧪 ТЕСТОВЫЙ РЕЖИМ: Запуск бота для проверки загрузки сообщений")
    print("=" * 60)
    print("⚠️  Gemini API НЕ используется (экономим токены)")
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
            
            # Проверяем, является ли канал форумом
            if hasattr(channel, 'forum') and channel.forum:
                print(f"   📁 Форум включен: темы будут создаваться автоматически")
            else:
                print(f"   ℹ️  Форум не включен: все сообщения в общий чат")
                print(f"   💡 Чтобы включить темы, зайдите в настройки канала:")
                print(f"      Управление каналом → Темы → Включить")
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

