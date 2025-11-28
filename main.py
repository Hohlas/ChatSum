import os
import asyncio
import random
import re
import shutil
from telethon import TelegramClient, events
from openai import OpenAI
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta, timezone
import httpx
from telegraph import Telegraph


def ensure_private_file():
    """
    Создает файл private.txt из шаблона private.txt.example, если он не существует.
    Возвращает True, если файл был только что создан (нужна настройка).
    """
    private_file = 'private.txt'
    template_file = 'private.txt.example'
    
    if os.path.exists(private_file):
        return False  # Файл уже существует
    
    if not os.path.exists(template_file):
        print(f"⚠️  Файл {template_file} не найден!")
        print(f"   Создайте файл {private_file} вручную с вашими API ключами.")
        return False
    
    try:
        # Копируем шаблон в private.txt
        shutil.copy2(template_file, private_file)
        print(f"✅ Создан файл {private_file} из шаблона {template_file}")
        print(f"⚠️  ВАЖНО: Отредактируйте {private_file} и укажите ваши реальные API ключи!")
        print(f"   После этого перезапустите бота.")
        return True  # Файл был создан из шаблона
    except Exception as e:
        print(f"❌ Ошибка при создании {private_file}: {e}")
        print(f"   Создайте файл {private_file} вручную, скопировав {template_file}")
        return False


def validate_config():
    """
    Проверяет, что конфигурация заполнена реальными значениями, а не заглушками.
    """
    api_id = os.getenv('TELEGRAM_API_ID', '')
    api_hash = os.getenv('TELEGRAM_API_HASH', '')
    phone = os.getenv('TELEGRAM_PHONE', '')
    perplexity_key = os.getenv('PERPLEXITY_API_KEY', '').strip()
    
    # Список заглушек, которые могут быть в шаблоне
    placeholders = [
        'ваш_api_id', 'ваш_api_hash', 'ваш_perplexity_ключ',
        'your_api_id', 'your_api_hash', 'your_perplexity_key',
        'ваш_telegram_api_id', 'ваш_telegram_api_hash'
    ]
    
    errors = []
    
    # Проверка TELEGRAM_API_ID
    if not api_id or api_id in placeholders:
        errors.append("TELEGRAM_API_ID не заполнен или содержит заглушку")
    else:
        try:
            int(api_id)  # Проверяем, что это число
        except ValueError:
            errors.append(f"TELEGRAM_API_ID должен быть числом, получено: {api_id}")
    
    # Проверка TELEGRAM_API_HASH
    if not api_hash or api_hash in placeholders:
        errors.append("TELEGRAM_API_HASH не заполнен или содержит заглушку")
    
    # Проверка TELEGRAM_PHONE
    if not phone or phone in placeholders:
        errors.append("TELEGRAM_PHONE не заполнен или содержит заглушку")
    elif not phone.startswith('+'):
        errors.append("TELEGRAM_PHONE должен начинаться с '+' (например, +79001234567)")
    
    # Проверка PERPLEXITY_API_KEY
    if not perplexity_key or perplexity_key in placeholders:
        errors.append("PERPLEXITY_API_KEY не заполнен или содержит заглушку")
    
    return errors


# Создаем private.txt из шаблона, если его нет
file_just_created = ensure_private_file()

# Загрузка переменных окружения
load_dotenv('private.txt')

# Проверяем конфигурацию
config_errors = validate_config()
if config_errors:
    if file_just_created:
        print("\n" + "="*60)
        print("📋 Файл private.txt создан из шаблона")
        print("="*60)
    else:
        print("\n❌ Ошибки конфигурации в private.txt:")
    
    for error in config_errors:
        print(f"   • {error}")
    
    print("\n📝 Инструкция:")
    print("   1. Откройте файл private.txt")
    print("   2. Замените все значения-заглушки на ваши реальные API ключи")
    print("   3. Перезапустите бота")
    print("\n💡 Где получить ключи:")
    print("   • Telegram API: https://my.telegram.org/auth")
    print("   • Perplexity API: https://www.perplexity.ai/settings/api")
    exit(1)

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

# Конфигурация Perplexity
# Очищаем API ключ от возможных невидимых символов и пробелов
PERPLEXITY_API_KEY = os.getenv('PERPLEXITY_API_KEY', '').strip()

if not PERPLEXITY_API_KEY:
    print("⚠️  ВНИМАНИЕ: PERPLEXITY_API_KEY не найден в private.txt!")

# Конфигурация фильтрации сообщений
MIN_MESSAGE_LENGTH = 3  # Минимальная длина сообщения (символов)
NOISE_PATTERNS = [
    r'^[\+\-\*]+$',  # +, -, *, ++, --
    r'^(ок|ok|лол|lol|хаха|haha|да|yes|нет|no)$',  # Односложные ответы
    r'^[\.\!\?]+$',  # Только знаки препинания
    r'^[👍👎👌✅❌🔥💪🎉😂😅]+$',  # Только эмодзи
]

# Пути к конфигурационным файлам
EXCLUDED_USERS_FILE = 'EXCLUDED_USERS.txt'
PRIORITY_USERS_FILE = 'PRIORITY_USERS.txt'
PROMPT_FILE = 'PROMPT.txt'
MODEL_CONFIG_FILE = 'MODEL_CONFIG.txt'


def load_users_from_file(filename):
    """
    Загружает список пользователей из файла
    Args:
        filename: Путь к файлу со списком пользователей
    Returns:
        Список имен пользователей
    """
    if not os.path.exists(filename):
        print(f"⚠️ Файл {filename} не найден, используется пустой список")
        return []
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Удаляем комментарии (строки начинающиеся с #)
        lines = [line.strip() for line in content.split('\n')
                 if line.strip() and not line.strip().startswith('#')]
        
        # Обрабатываем каждую строку
        users = []
        for line in lines:
            # ИСПРАВЛЕНИЕ: Разделяем только по запятой и точке с запятой
            # НЕ разделяем по пробелам, чтобы сохранить составные имена
            if ',' in line or ';' in line:
                parts = re.split(r'[,;]+', line)
                users.extend([p.strip() for p in parts if p.strip()])
            else:
                # Если нет разделителей - вся строка это одно имя
                users.append(line.strip())
        
        return users
        
    except Exception as e:
        print(f"❌ Ошибка при чтении {filename}: {e}")
        return []


def load_prompt_from_file(filename):
    """
    Загружает промпт из файла
    
    Args:
        filename: Путь к файлу с промптом
    
    Returns:
        Текст промпта или дефолтный промпт при ошибке
    """
    if not os.path.exists(filename):
        print(f"⚠️  Файл {filename} не найден, используется дефолтный промпт")
        return "Проанализируй сообщения и создай структурированную выжимку."
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        print(f"❌ Ошибка при чтении {filename}: {e}")
        return "Проанализируй сообщения и создай структурированную выжимку."


def save_users_to_file(filename, users):
    """
    Сохраняет список пользователей в файл
    
    Args:
        filename: Путь к файлу
        users: Список пользователей
    """
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("# Автоматически обновлено ботом\n")
            f.write("# Можно редактировать вручную\n\n")
            for user in users:
                f.write(f"{user}\n")
        return True
    except Exception as e:
        print(f"❌ Ошибка при сохранении {filename}: {e}")
        return False


def save_prompt_to_file(filename, prompt):
    """
    Сохраняет промпт в файл
    
    Args:
        filename: Путь к файлу
        prompt: Текст промпта
    """
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(prompt)
        return True
    except Exception as e:
        print(f"❌ Ошибка при сохранении {filename}: {e}")
        return False


def load_model_config(filename):
    """
    Загружает конфигурацию модели из файла
    
    Args:
        filename: Путь к файлу с конфигурацией модели
    
    Returns:
        Кортеж (model_name, use_reasoning, use_html_export)
    """
    default_model = 'sonar-pro'  # Рекомендуемая модель для Perplexity API
    default_reasoning = False
    default_html_export = True  # По умолчанию используем HTML
    
    if not os.path.exists(filename):
        print(f"⚠️  Файл {filename} не найден, используется модель по умолчанию: {default_model}")
        return default_model, default_reasoning, default_html_export
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        model = default_model
        use_reasoning = default_reasoning
        use_html_export = default_html_export
        
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip().upper()
                value = value.strip()
                
                if key == 'MODEL':
                    model = value
                elif key == 'USE_REASONING':
                    use_reasoning = value.lower() in ('true', 'yes', '1', 'on')
                elif key == 'USE_HTML_EXPORT':
                    use_html_export = value.lower() in ('true', 'yes', '1', 'on')
        
        return model, use_reasoning, use_html_export
    except Exception as e:
        print(f"❌ Ошибка при чтении {filename}: {e}")
        return default_model, default_reasoning, default_html_export


def save_model_config(filename, model, use_reasoning, use_html_export=True):
    """
    Сохраняет конфигурацию модели в файл
    
    Args:
        filename: Путь к файлу
        model: Название модели
        use_reasoning: Использовать ли reasoning режим
        use_html_export: Использовать ли HTML вместо Telegraph
    """
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("# Конфигурация модели Perplexity API\n")
            f.write("# Автоматически обновлено ботом\n\n")
            f.write("# ⚠️ ВАЖНО: Через Perplexity API доступны ТОЛЬКО модели Sonar!\n")
            f.write("# Claude, GPT и другие модели доступны только в веб-интерфейсе Perplexity Pro\n\n")
            f.write("# Доступные модели через API:\n")
            f.write("# - sonar (базовая модель, на основе Llama 3.3 70B)\n")
            f.write("# - sonar-pro (улучшенная версия с лучшим качеством) - РЕКОМЕНДУЕТСЯ\n\n")
            f.write(f"MODEL={model}\n\n")
            f.write("# Использовать ли режим reasoning (экспериментально)\n")
            f.write(f"USE_REASONING={'true' if use_reasoning else 'false'}\n\n")
            f.write("# Использовать HTML файлы вместо Telegraph\n")
            f.write("# true - создавать локальные HTML файлы и отправлять в Telegram\n")
            f.write("# false - публиковать на Telegraph (требует интернет-соединение)\n")
            f.write(f"USE_HTML_EXPORT={'true' if use_html_export else 'false'}\n")
        return True
    except Exception as e:
        print(f"❌ Ошибка при сохранении {filename}: {e}")
        return False


# Загружаем конфигурацию из файлов при старте
EXCLUDED_USERS = load_users_from_file(EXCLUDED_USERS_FILE)
PRIORITY_USERS = load_users_from_file(PRIORITY_USERS_FILE)
ANALYSIS_PROMPT = load_prompt_from_file(PROMPT_FILE)
CURRENT_MODEL, USE_REASONING, USE_HTML_EXPORT = load_model_config(MODEL_CONFIG_FILE)

# Инициализация клиентов
telegram_client = TelegramClient('session_name', API_ID, API_HASH)

# Валидация API ключа
print(f"🔑 Проверка Perplexity API ключа:")
print(f"   Длина: {len(PERPLEXITY_API_KEY)} символов")
print(f"   Первые 10 символов: {PERPLEXITY_API_KEY[:10]}...")
print(f"   Последние 10 символов: ...{PERPLEXITY_API_KEY[-10:]}")

# Проверка, что ключ содержит только ASCII символы
try:
    PERPLEXITY_API_KEY.encode('ascii')
    print(f"   ✅ API-ключ корректный (ASCII)")
except UnicodeEncodeError:
    print("   ❌ ОШИБКА: API-ключ содержит недопустимые символы!")
    print("   Проверьте файл private.txt на наличие невидимых символов")
    exit(1)

# Создаём стандартный HTTP-клиент с настройками таймаута и лимитов соединений
http_client = httpx.Client(
    timeout=180.0,
    limits=httpx.Limits(
        max_keepalive_connections=5,
        max_connections=10
    )
)

perplexity_client = OpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url='https://api.perplexity.ai',
    http_client=http_client,
    max_retries=2
)


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
            return None
            
    except Exception as e:
        print(f"❌ Ошибка при работе с темами: {e}")
        return None


def is_noise_message(text):
    """
    Проверяет, является ли сообщение бессодержательным (шум/флуд)
    
    Args:
        text: Текст сообщения
    
    Returns:
        True если сообщение - шум, False если содержательное
    """
    if not text or len(text.strip()) < MIN_MESSAGE_LENGTH:
        return True
    
    text_clean = text.strip().lower()
    
    # Проверяем по паттернам
    for pattern in NOISE_PATTERNS:
        if re.match(pattern, text_clean, re.IGNORECASE):
            return True
    
    return False


def optimize_messages(messages_data, chat_id_str):
    """
    Оптимизирует список сообщений для экономии токенов API
    
    Args:
        messages_data: Список сообщений
        chat_id_str: ID чата в формате строки (для ссылок)
    
    Returns:
        Оптимизированный список сообщений
    """
    print(f"🔄 Оптимизация {len(messages_data)} сообщений...")
    
    optimized = []
    excluded_count = 0
    noise_count = 0
    
    # Собираем уникальные имена отправителей для диагностики
    unique_senders = set()
    
    for msg in messages_data:
        unique_senders.add(msg['sender'])
        
        # Фильтруем исключенных пользователей
        if msg['sender'] in EXCLUDED_USERS:
            excluded_count += 1
            continue
        
        # Фильтруем бессодержательные сообщения
        if is_noise_message(msg['text']):
            noise_count += 1
            continue
        
        # Добавляем chat_id для создания ссылок
        msg['chat_id'] = chat_id_str
        
        optimized.append(msg)
    
    print(f"✅ Оптимизация завершена:")
    print(f"   • Исходно: {len(messages_data)} сообщений")
    print(f"   • Исключено пользователей: {excluded_count}")
    print(f"   • Удалено шума/флуда: {noise_count}")
    print(f"   • Итого для анализа: {len(optimized)} сообщений")
    print(f"   • Экономия: {len(messages_data) - len(optimized)} сообщений ({round((len(messages_data) - len(optimized)) / len(messages_data) * 100, 1)}%)")
    
    # Диагностика приоритетных пользователей
    if PRIORITY_USERS:
        print(f"\n🔍 Проверка приоритетных пользователей:")
        for priority_user in PRIORITY_USERS:
            if priority_user in unique_senders:
                # Считаем сообщения от приоритетного пользователя
                priority_msg_count = sum(1 for msg in optimized if msg['sender'] == priority_user)
                print(f"   ✅ {priority_user}: найдено {priority_msg_count} сообщений")
            else:
                print(f"   ⚠️  {priority_user}: НЕ найден в сообщениях")
    
    return optimized


def count_messages_with_urls(messages_data):
    """
    Подсчитывает сообщения содержащие URL
    
    Args:
        messages_data: Список сообщений
    
    Returns:
        Кортеж (количество сообщений с URL, список сообщений с URL)
    """
    url_pattern = re.compile(r'https?://[^\s]+')
    count = 0
    urls = []
    
    for msg in messages_data:
        text = msg.get('text', '')
        if url_pattern.search(text):
            count += 1
            urls.append({
                'sender': msg.get('sender'),
                'message_id': msg.get('message_id'),
                'text': text[:100]  # Первые 100 символов
            })
    
    return count, urls


async def collect_messages(chat_id, hours=None, days=None, limit=None):
    """
    Собирает сообщения из чата с догрузкой родительских сообщений для контекста
    
    Args:
        chat_id: ID чата для анализа
        hours: Количество часов назад (опционально)
        days: Количество дней назад (опционально)
        limit: Количество последних сообщений (опционально)
    
    Returns:
        Кортеж (список сообщений, chat_id_str для ссылок, period_start_date)
        period_start_date - дата первого сообщения исходного периода (до догрузки родительских)
    """
    # Получаем информацию о чате для формирования ссылок
    chat = await telegram_client.get_entity(chat_id)
    # Преобразуем chat_id в формат для ссылок (убираем -100 префикс)
    chat_id_str = str(chat_id).replace('-100', '')
    
    messages_data = []
    loaded_ids = set()  # Отслеживаем загруженные ID
    reply_to_ids = set()  # Отслеживаем ID на которые есть ответы
    
    if limit:
        # Режим: последние N сообщений
        print(f"🔄 Загрузка последних {limit} сообщений...")
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
                
                # Добавляем информацию об ответе на сообщение (если есть)
                reply_to = None
                if message.reply_to and hasattr(message.reply_to, 'reply_to_msg_id'):
                    reply_to = message.reply_to.reply_to_msg_id
                    reply_to_ids.add(reply_to)
                
                loaded_ids.add(message.id)
                messages_data.append({
                    'sender': sender_name,
                    'text': message.text,
                    'date': message.date.strftime('%Y-%m-%d %H:%M:%S'),
                    'message_id': message.id,
                    'reply_to': reply_to
                })
                count += 1
    else:
        # Режим: за период времени
        hours = hours or 0
        days = days or 0
        if hours == 0 and days == 0:
            hours = 24  # По умолчанию 24 часа
        
        print(f"🔄 Загрузка сообщений за последние {days} дней и {hours} часов...")
        # Используем UTC для сравнения с message.date (Telegram API возвращает UTC)
        time_limit = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
        
        async for message in telegram_client.iter_messages(chat_id):
            # Прерываем, если достигли временного предела
            # Приводим message.date к UTC, если он не имеет timezone
            msg_date = message.date
            if msg_date.tzinfo is None:
                # Если message.date без timezone, считаем его UTC
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            elif msg_date.tzinfo != timezone.utc:
                # Если message.date с другим timezone, конвертируем в UTC
                msg_date = msg_date.astimezone(timezone.utc)
            
            if msg_date < time_limit:
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
                
                # Добавляем информацию об ответе на сообщение (если есть)
                reply_to = None
                if message.reply_to and hasattr(message.reply_to, 'reply_to_msg_id'):
                    reply_to = message.reply_to.reply_to_msg_id
                    reply_to_ids.add(reply_to)
                
                loaded_ids.add(message.id)
                messages_data.append({
                    'sender': sender_name,
                    'text': message.text,
                    'date': message.date.strftime('%Y-%m-%d %H:%M:%S'),
                    'message_id': message.id,
                    'reply_to': reply_to
                })
    
    # Сортируем по времени (от старых к новым)
    messages_data.reverse()
    
    # Сохраняем дату первого сообщения исходного периода (ДО догрузки родительских)
    period_start_date = messages_data[0].get('date', '') if messages_data else ''
    initial_messages_count = len(messages_data)
    
    print(f"✅ Загружено {len(messages_data)} сообщений")
    
    # Догружаем недостающие родительские сообщения для контекста
    missing_ids = reply_to_ids - loaded_ids
    if missing_ids:
        # Ограничиваем до 50 сообщений
        missing_ids_limited = list(missing_ids)[:50]
        print(f"🔄 Догрузка {len(missing_ids_limited)} родительских сообщений для контекста...")
        
        try:
            missing_messages = await telegram_client.get_messages(chat_id, ids=missing_ids_limited)
            
            # Обрабатываем догруженные сообщения
            for msg in missing_messages:
                if msg and msg.text and not isinstance(msg, list):
                    sender = await msg.get_sender()
                    sender_name = "Unknown"
                    
                    if hasattr(sender, 'first_name'):
                        sender_name = sender.first_name
                        if hasattr(sender, 'last_name') and sender.last_name:
                            sender_name += f" {sender.last_name}"
                    elif hasattr(sender, 'title'):
                        sender_name = sender.title
                    
                    # Проверяем есть ли у догруженного сообщения свой reply_to
                    reply_to = None
                    if msg.reply_to and hasattr(msg.reply_to, 'reply_to_msg_id'):
                        reply_to = msg.reply_to.reply_to_msg_id
                    
                    messages_data.append({
                        'sender': sender_name,
                        'text': msg.text,
                        'date': msg.date.strftime('%Y-%m-%d %H:%M:%S'),
                        'message_id': msg.id,
                        'reply_to': reply_to
                    })
                    loaded_ids.add(msg.id)
            
            # Пересортировываем с учетом догруженных
            messages_data.sort(key=lambda x: x['date'])
            print(f"✅ Догружено {len([m for m in missing_messages if m and m.text])} родительских сообщений")
            
        except Exception as e:
            print(f"⚠️  Не удалось загрузить некоторые родительские сообщения: {e}")
    
    return messages_data, chat_id_str, period_start_date


def safe_str(value):
    """Безопасное преобразование в строку с обработкой кириллицы"""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='ignore')
    return str(value)


def build_tree_structure(messages_data):
    """
    Преобразует плоский список сообщений в древовидную структуру
    
    Args:
        messages_data: Плоский список сообщений с reply_to
    
    Returns:
        Список корневых сообщений с вложенными replies
    """
    # Создаем словарь для быстрого поиска сообщений по ID
    messages_by_id = {}
    # Отслеживаем, какие сообщения являются ответами (не должны быть в root_messages)
    is_reply = set()
    
    # Первый проход: создаем все объекты сообщений
    for msg in messages_data:
        msg_id = msg['message_id']
        messages_by_id[msg_id] = {
            'id': msg_id,
            's': msg['sender'],  # sender → s
            't': msg['text'],    # text → t
            'r': []               # replies → r
        }
    
    # Второй проход: строим дерево и отмечаем ответы
    for msg in messages_data:
        msg_id = msg['message_id']
        reply_to = msg.get('reply_to')
        
        current_msg = messages_by_id[msg_id]
        
        if reply_to and reply_to in messages_by_id:
            # Это ответ на существующее сообщение - добавляем в replies родителя
            messages_by_id[reply_to]['r'].append(current_msg)  # replies → r
            # Отмечаем, что это сообщение является ответом
            is_reply.add(msg_id)
        # Если reply_to отсутствует или родитель не найден, сообщение будет корневым
    
    # Собираем корневые сообщения (те, которые не являются ответами)
    root_messages = []
    for msg in messages_data:
        msg_id = msg['message_id']
        if msg_id not in is_reply:
            root_messages.append(messages_by_id[msg_id])
    
    # Удаляем пустые массивы replies для экономии токенов
    def clean_empty_replies(msg):
        if not msg['r']:  # replies → r
            del msg['r']
        else:
            for reply in msg['r']:  # replies → r
                clean_empty_replies(reply)
    
    for msg in root_messages:
        clean_empty_replies(msg)
    
    return root_messages


def build_optimized_json_structure(messages_data, chat_id_str, chat_name=None, total_messages=None, filtered_messages=None, period_start_date=None):
    """
    Формирует оптимизированную JSON структуру для экспорта/анализа
    
    Единая функция для /sum и /copy - устраняет дублирование кода.
    
    Args:
        messages_data: Плоский список сообщений (после фильтрации)
        chat_id_str: ID чата для ссылок
        chat_name: Название чата (опционально, для экспорта)
        total_messages: Общее количество сообщений (опционально, для экспорта)
        filtered_messages: Количество отфильтрованных сообщений (опционально, для экспорта)
        period_start_date: Дата первого сообщения исходного периода (до догрузки родительских)
    
    Returns:
        Словарь с оптимизированной структурой: {'metadata': {...}, 'messages': [...]}
    """
    # Используем переданную дату начала периода, или берем из первого сообщения (запасной вариант)
    if period_start_date:
        period_start = period_start_date
    else:
        period_start = messages_data[0].get('date', '') if messages_data else ''
    
    # Строим древовидную структуру с вложенными replies
    tree_messages = build_tree_structure(messages_data)
    
    # Формируем metadata
    metadata = {
        'chat_id': safe_str(chat_id_str),
        'period_start': safe_str(period_start)
    }
    
    # Дополнительные поля для экспорта (/copy)
    if chat_name is not None:
        metadata['chat_name'] = chat_name
        metadata['export_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if total_messages is not None:
        metadata['total_messages'] = total_messages
    if filtered_messages is not None:
        metadata['filtered_messages'] = filtered_messages
    
    return {
        'metadata': metadata,
        'messages': tree_messages
    }


async def create_summary(messages_data, chat_id_str, model='sonar', use_reasoning=False, period_start_date=None):
    """
    Создает выжимку из сообщений с помощью Perplexity API
    
    Args:
        messages_data: Список словарей с сообщениями (включая reply_to)
        chat_id_str: ID чата для ссылок
        model: Модель для использования (sonar, claude-3.5-sonnet и т.д.)
        use_reasoning: Использовать ли reasoning режим (для моделей с поддержкой)
    
    Returns:
        Кортеж (текст выжимки, информация об использовании токенов)
    """
    if not messages_data:
        return "❌ Нет сообщений для анализа за указанный период (все отфильтровано)"
    
    # Определяем финальную модель с учётом reasoning
    if use_reasoning:
        # Переключаемся на reasoning-версию модели
        reasoning_models = {
            'sonar': 'sonar-reasoning',
            'sonar-pro': 'sonar-reasoning-pro'
        }
        actual_model = reasoning_models.get(model, 'sonar-reasoning')
        print(f"🤖 Отправка {len(messages_data)} сообщений в Perplexity для анализа...")
        print(f"   🧠 Используем reasoning модель: {actual_model}")
    else:
        actual_model = model
        print(f"🤖 Отправка {len(messages_data)} сообщений в Perplexity для анализа...")
        print(f"   ⚡ Используем стандартную модель: {actual_model}")
    
    # Определяем лимиты контекста в зависимости от модели (в токенах)
    # Источник: официальная документация Perplexity API
    context_limits = {
        'sonar': 128000,
        'sonar-pro': 200000,           # Самый большой контекст!
        'sonar-reasoning': 128000,
        'sonar-reasoning-pro': 128000,
        'sonar-deep-research': 128000
    }
    
    max_tokens = context_limits.get(actual_model, 128000)
    # Конвертируем токены в символы для проверки
    # Для кириллицы: ~2.5 символа на токен (более плотное кодирование чем английский)
    # Оставляем запас 20% для системного промпта и метаданных
    max_chars = int(max_tokens * 2.5 * 0.8)
    
    print(f"   📊 Лимит контекста: {max_tokens:,} токенов ({max_chars:,} символов для кириллицы)")
    
    # Формируем ОПТИМИЗИРОВАННЫЙ JSON для экономии токенов
    # Используем общую функцию для единообразия с /copy
    optimized_structure = build_optimized_json_structure(messages_data, chat_id_str, period_start_date=period_start_date)
    
    # Используем ensure_ascii=False для сохранения кириллицы
    messages_json = json.dumps(optimized_structure, ensure_ascii=False, indent=2)
    
    # Проверяем размер и при необходимости разбиваем на части
    if len(messages_json) > max_chars:
        print(f"⚠️  Данных слишком много ({len(messages_json)} символов)")
        print(f"   Максимум для модели {actual_model}: {max_chars} символов")
        
        # Вариант 1: Разбить на несколько запросов (рекомендуется)
        # Вариант 2: Взять только последние сообщения (самые актуальные)
        # Выбираем вариант 2 как более простой, но с предупреждением
        
        ratio = max_chars / len(messages_json)
        limit = int(len(messages_data) * ratio * 0.95)  # 0.95 для запаса
        
        print(f"   📌 Решение: Берем последние {limit} сообщений (самые актуальные)")
        print(f"   ⚠️  ПОТЕРЯ ДАННЫХ: {len(messages_data) - limit} старых сообщений не попадут в анализ")
        print(f"   💡 Рекомендация: уменьшите период анализа (например /analyze 12h вместо 24h)")
        
        # Берем ПОСЛЕДНИЕ сообщения (самые актуальные), а не первые!
        messages_data_limited = messages_data[-limit:]  # Изменено на последние!
        
        # Используем общую функцию для формирования структуры
        # Используем period_start_date из ограниченной выборки (первое сообщение)
        period_start_limited = messages_data_limited[0].get('date', '') if messages_data_limited else period_start_date
        optimized_structure = build_optimized_json_structure(messages_data_limited, chat_id_str, period_start_date=period_start_limited)
        
        messages_json = json.dumps(optimized_structure, ensure_ascii=False, indent=2)
    
    try:
        # Формируем параметры запроса
        # Важно: убеждаемся что все строки в Unicode и правильно закодированы
        # Подставляем список приоритетных пользователей в промпт
        prompt_with_priority = ANALYSIS_PROMPT
        if PRIORITY_USERS:
            priority_list = ', '.join(PRIORITY_USERS)
            # Заменяем плейсхолдер {PRIORITY_USERS} на список пользователей
            prompt_with_priority = prompt_with_priority.replace('{PRIORITY_USERS}', priority_list)
        
        system_content = safe_str(prompt_with_priority)
        user_content = safe_str(f'Данные сообщений для анализа (JSON):\n\n{messages_json}')
        
        # Проверяем что контент корректный Unicode
        try:
            system_content.encode('utf-8')
            user_content.encode('utf-8')
        except UnicodeEncodeError as ue:
            print(f"⚠️  Ошибка кодировки в контенте: {ue}")
            # Принудительно очищаем от проблемных символов
            system_content = system_content.encode('utf-8', errors='ignore').decode('utf-8')
            user_content = user_content.encode('utf-8', errors='ignore').decode('utf-8')
        
        request_params = {
            'model': actual_model,  # Используем actual_model вместо model
            'messages': [
                {'role': 'system', 'content': system_content},
                {'role': 'user', 'content': user_content}
            ],
            'temperature': 0.3,
            'max_tokens': 4000
        }
        
        # Выводим информацию о размере запроса
        total_chars = len(system_content) + len(user_content)
        print(f"   📊 Размер запроса: {total_chars:,} символов")
        
        # Оцениваем примерное время обработки
        estimated_time = max(30, total_chars // 500)  # ~500 символов/секунду
        if estimated_time > 60:
            print(f"   ⏱️  Ожидаемое время обработки: ~{estimated_time} сек")
            print(f"   ⏳ Пожалуйста, подождите...")
        
        # Отправляем запрос с повторными попытками при таймауте
        max_retries = 2
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                response = perplexity_client.chat.completions.create(**request_params)
                break  # Успешно - выходим из цикла
            except Exception as retry_error:
                if 'timeout' in str(retry_error).lower() and retry_count < max_retries:
                    retry_count += 1
                    print(f"   ⚠️  Таймаут. Повторная попытка {retry_count}/{max_retries}...")
                    continue
                else:
                    # Если это не таймаут или исчерпаны попытки - пробрасываем исключение
                    raise
        
        summary = response.choices[0].message.content
        print("✅ Выжимка успешно создана")
        
        # Собираем статистику использования токенов
        usage_info = None
        if hasattr(response, 'usage'):
            usage = response.usage
            usage_info = {
                'prompt_tokens': usage.prompt_tokens if hasattr(usage, 'prompt_tokens') else 0,
                'completion_tokens': usage.completion_tokens if hasattr(usage, 'completion_tokens') else 0,
                'total_tokens': usage.total_tokens if hasattr(usage, 'total_tokens') else 0
            }
            print(f"   📊 Использовано токенов:")
            print(f"      Промпт: {usage_info['prompt_tokens']}")
            print(f"      Ответ: {usage_info['completion_tokens']}")
            print(f"      Всего: {usage_info['total_tokens']}")
        
        return summary, usage_info
        
    except Exception as e:
        error_msg = f"❌ Ошибка при создании выжимки: {e}"
        print(error_msg)
        print(f"   Модель: {model}")
        print(f"   Размер данных: {len(messages_json)} символов")
        print(f"   Тип ошибки: {type(e).__name__}")
        
        # Подробный traceback для отладки
        import traceback
        print("   Подробная трассировка:")
        traceback.print_exc()
        
        return error_msg, None


def save_analysis(messages_data, summary):
    """Сохраняет результаты анализа в JSON файл
    
    Returns:
        str: Имя созданного файла
    """
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
    return filename


def calculate_period_info(messages_data, optimized_messages, period_start_date, label="анализа"):
    """
    Вычисляет информацию о периоде сообщений
    
    Args:
        messages_data: Список всех сообщений (для получения конечной даты)
        optimized_messages: Список отфильтрованных сообщений (для подсчета)
        period_start_date: Дата начала периода в формате 'YYYY-MM-DD HH:MM:SS'
        label: Метка для заголовка ("анализа" или "экспорта")
    
    Returns:
        Tuple (period_info_text, period_start_time, period_end_time, period_start_dt, period_end_dt)
    """
    # Получаем время начала периода
    period_start_time = ""
    period_start_dt = None
    if period_start_date:
        try:
            period_start_dt = datetime.strptime(period_start_date, '%Y-%m-%d %H:%M:%S')
            period_start_time = period_start_dt.strftime('%d.%m %H:%M')
        except (ValueError, TypeError):
            period_start_time = period_start_date[:16] if len(period_start_date) >= 16 else period_start_date
    
    if not period_start_time:
        period_start_dt = datetime.now()
        period_start_time = period_start_dt.strftime('%d.%m %H:%M')
    
    # Получаем дату последнего сообщения (самое свежее)
    period_end_dt = None
    period_end_time = ""
    if messages_data:
        try:
            last_message = max(messages_data, key=lambda x: x.get('date', ''))
            last_date_str = last_message.get('date', '')
            if last_date_str:
                period_end_dt = datetime.strptime(last_date_str, '%Y-%m-%d %H:%M:%S')
                period_end_time = period_end_dt.strftime('%d.%m %H:%M')
        except (ValueError, TypeError, KeyError):
            period_end_dt = datetime.now()
            period_end_time = period_end_dt.strftime('%d.%m %H:%M')
    
    # Вычисляем период в часах
    period_hours = None
    if period_start_dt and period_end_dt:
        delta = period_end_dt - period_start_dt
        # Используем round() для математического округления и abs() для защиты от отрицательных значений
        period_hours = abs(round(delta.total_seconds() / 3600))
    
    # Формируем информацию о периоде
    period_info = ""
    if period_hours is not None:
        period_info = f"\n\n📅 **Период {label}:**\n"
        period_info += f"• Обработано: {len(optimized_messages)} сообщений\n"
        if period_hours < 24:
            period_info += f"• За период: {period_hours} часов\n"
        else:
            period_days = period_hours // 24
            remaining_hours = period_hours % 24
            if remaining_hours > 0:
                period_info += f"• За период: {period_days} дней {remaining_hours} часов\n"
            else:
                period_info += f"• За период: {period_days} дней\n"
        period_info += f"• С {period_start_time} по {period_end_time}\n"
    
    return period_info, period_start_time, period_end_time, period_start_dt, period_end_dt


def publish_to_telegraph(title, content, author_name="Chat Filter Bot"):
    """
    Публикует статью в Telegraph
    
    Args:
        title: Заголовок статьи
        content: Содержимое статьи (Markdown текст)
        author_name: Имя автора (опционально)
    
    Returns:
        URL опубликованной статьи или None при ошибке
    """
    try:
        # Создаем экземпляр Telegraph (анонимный аккаунт)
        telegraph = Telegraph()
        
        # Создаем аккаунт (анонимный, можно использовать один для всех публикаций)
        account = telegraph.create_account(short_name=author_name)
        telegraph = Telegraph(access_token=account['access_token'])
        
        # Конвертируем Markdown в HTML для Telegraph
        # Telegraph поддерживает только определённые теги: a, aside, b, blockquote, br, code, em, figcaption, figure, h3, h4, hr, i, iframe, img, li, ol, p, pre, s, strong, u, ul, video
        
        # Разбиваем на строки для построчной обработки
        lines = content.split('\n')
        html_paragraphs = []
        in_list = False
        current_paragraph = []
        
        for line in lines:
            line_stripped = line.strip()
            
            # Пустая строка - завершаем текущий параграф
            if not line_stripped:
                if current_paragraph:
                    # Объединяем накопленные строки параграфа с переносами
                    para_text = '<br>'.join(current_paragraph)
                    # Конвертируем Markdown элементы
                    para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
                    para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
                    para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
                    html_paragraphs.append(f'<p>{para_text}</p>')
                    current_paragraph = []
                if in_list:
                    html_paragraphs.append('</ul>')
                    in_list = False
                continue
            
            # Разделитель тем
            if line_stripped == '---':
                if current_paragraph:
                    para_text = '<br>'.join(current_paragraph)
                    para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
                    para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
                    para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
                    html_paragraphs.append(f'<p>{para_text}</p>')
                    current_paragraph = []
                if in_list:
                    html_paragraphs.append('</ul>')
                    in_list = False
                html_paragraphs.append('<hr>')
                continue
            
            # Заголовок темы (начинается с 💡)
            if line_stripped.startswith('💡'):
                if current_paragraph:
                    para_text = '<br>'.join(current_paragraph)
                    para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
                    para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
                    para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
                    html_paragraphs.append(f'<p>{para_text}</p>')
                    current_paragraph = []
                if in_list:
                    html_paragraphs.append('</ul>')
                    in_list = False
                # Конвертируем Markdown в заголовке
                text = line_stripped
                text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)  # **text** -> <b>text</b>
                text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', text)    # *text* -> <i>text</i>
                html_paragraphs.append(f'<h3>{text}</h3>')
                continue
            
            # Список
            if line_stripped.startswith('- ') or line_stripped.startswith('* '):
                if current_paragraph:
                    para_text = '<br>'.join(current_paragraph)
                    para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
                    para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
                    para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
                    html_paragraphs.append(f'<p>{para_text}</p>')
                    current_paragraph = []
                if not in_list:
                    html_paragraphs.append('<ul>')
                    in_list = True
                item_text = line_stripped.lstrip('- *').strip()
                # Конвертируем Markdown элементы в списке
                item_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', item_text)
                item_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', item_text)
                item_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', item_text)
                html_paragraphs.append(f'<li>{item_text}</li>')
                continue
            
            # Обычная строка - добавляем к текущему параграфу
            if in_list:
                html_paragraphs.append('</ul>')
                in_list = False
            current_paragraph.append(line_stripped)
        
        # Завершаем последний параграф
        if current_paragraph:
            para_text = '<br>'.join(current_paragraph)
            para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
            para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
            para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
            html_paragraphs.append(f'<p>{para_text}</p>')
        
        if in_list:
            html_paragraphs.append('</ul>')
        
        html_content = ''.join(html_paragraphs)
        
        # Публикуем статью
        response = telegraph.create_page(
            title=title,
            html_content=html_content,
            author_name=author_name
        )
        
        if response and 'url' in response:
            article_url = response['url']
            print(f"✅ Статья опубликована в Telegraph: {article_url}")
            return article_url
        else:
            print(f"❌ Ошибка при публикации в Telegraph: {response}")
            return None
            
    except Exception as e:
        print(f"❌ Ошибка при публикации в Telegraph: {e}")
        import traceback
        traceback.print_exc()
        return None


def create_html_report(title, content, author_name="Chat Filter Bot"):
    """
    Создает локальный HTML отчет со стилями в духе Telegraph
    
    Args:
        title: Заголовок отчета
        content: Содержимое отчета (Markdown текст)
        author_name: Имя автора (опционально)
    
    Returns:
        Путь к созданному HTML файлу или None при ошибке
    """
    try:
        # Создаем папку для HTML отчетов, если её нет
        reports_dir = 'html_reports'
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir)
            print(f"📁 Создана папка {reports_dir}/")
        
        # Конвертируем Markdown в HTML (используем ту же логику что и для Telegraph)
        lines = content.split('\n')
        html_paragraphs = []
        in_list = False
        current_paragraph = []
        
        for line in lines:
            line_stripped = line.strip()
            
            # Пустая строка - завершаем текущий параграф
            if not line_stripped:
                if current_paragraph:
                    para_text = '<br>'.join(current_paragraph)
                    para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
                    para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
                    para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
                    html_paragraphs.append(f'<p>{para_text}</p>')
                    current_paragraph = []
                if in_list:
                    html_paragraphs.append('</ul>')
                    in_list = False
                continue
            
            # Разделитель тем
            if line_stripped == '---':
                if current_paragraph:
                    para_text = '<br>'.join(current_paragraph)
                    para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
                    para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
                    para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
                    html_paragraphs.append(f'<p>{para_text}</p>')
                    current_paragraph = []
                if in_list:
                    html_paragraphs.append('</ul>')
                    in_list = False
                html_paragraphs.append('<hr>')
                continue
            
            # Заголовок темы (начинается с 💡)
            if line_stripped.startswith('💡'):
                if current_paragraph:
                    para_text = '<br>'.join(current_paragraph)
                    para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
                    para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
                    para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
                    html_paragraphs.append(f'<p>{para_text}</p>')
                    current_paragraph = []
                if in_list:
                    html_paragraphs.append('</ul>')
                    in_list = False
                text = line_stripped
                text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
                text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', text)
                html_paragraphs.append(f'<h3>{text}</h3>')
                continue
            
            # Пункт списка
            if line_stripped.startswith('• '):
                if current_paragraph:
                    para_text = '<br>'.join(current_paragraph)
                    para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
                    para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
                    para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
                    html_paragraphs.append(f'<p>{para_text}</p>')
                    current_paragraph = []
                if not in_list:
                    html_paragraphs.append('<ul>')
                    in_list = True
                text = line_stripped[2:]
                text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
                text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', text)
                text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', text)
                html_paragraphs.append(f'<li>{text}</li>')
                continue
            
            # Обычный текст - добавляем в текущий параграф
            current_paragraph.append(line_stripped)
        
        # Завершаем оставшийся параграф
        if current_paragraph:
            para_text = '<br>'.join(current_paragraph)
            para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)
            para_text = re.sub(r'\*([^\*]+)\*', r'<i>\1</i>', para_text)
            para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
            html_paragraphs.append(f'<p>{para_text}</p>')
        
        if in_list:
            html_paragraphs.append('</ul>')
        
        html_body = ''.join(html_paragraphs)
        
        # Создаем полноценный HTML документ со стилями в стиле Telegraph
        html_template = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="author" content="{author_name}">
    <title>{title}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Georgia', 'Times New Roman', serif;
            font-size: 18px;
            line-height: 1.6;
            color: #222;
            background-color: #f4f4f4;
            padding: 20px;
        }}
        
        .container {{
            max-width: 680px;
            margin: 0 auto;
            background-color: #fff;
            padding: 40px 50px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        
        h1 {{
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 30px;
            line-height: 1.3;
        }}
        
        h3 {{
            font-size: 22px;
            font-weight: bold;
            margin-top: 30px;
            margin-bottom: 15px;
            line-height: 1.3;
        }}
        
        p {{
            margin-bottom: 15px;
        }}
        
        a {{
            color: #3390ec;
            text-decoration: none;
        }}
        
        a:hover {{
            text-decoration: underline;
        }}
        
        b, strong {{
            font-weight: bold;
        }}
        
        i, em {{
            font-style: italic;
        }}
        
        ul {{
            margin-left: 20px;
            margin-bottom: 15px;
        }}
        
        li {{
            margin-bottom: 8px;
        }}
        
        hr {{
            border: none;
            border-top: 1px solid #ddd;
            margin: 30px 0;
        }}
        
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 14px;
            color: #888;
            text-align: center;
        }}
        
        @media (max-width: 768px) {{
            body {{
                padding: 10px;
            }}
            
            .container {{
                padding: 25px 20px;
            }}
            
            h1 {{
                font-size: 26px;
            }}
            
            h3 {{
                font-size: 20px;
            }}
            
            body {{
                font-size: 16px;
            }}
        }}
        
        /* Темная тема - автоматически применяется если в системе включен темный режим */
        @media (prefers-color-scheme: dark) {{
            body {{
                color: #e4e4e4;
                background-color: #1a1a1a;
            }}
            
            .container {{
                background-color: #2d2d2d;
                box-shadow: 0 1px 3px rgba(0,0,0,0.3);
            }}
            
            a {{
                color: #6ab7ff;
            }}
            
            hr {{
                border-top: 1px solid #444;
            }}
            
            .footer {{
                border-top: 1px solid #3a3a3a;
                color: #999;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        {html_body}
        <div class="footer">
            Создано {datetime.now().strftime('%d.%m.%Y %H:%M')}
        </div>
    </div>
</body>
</html>'''
        
        # Генерируем имя файла
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:50]
        filename = f"{reports_dir}/report_{safe_title}_{timestamp}.html"
        
        # Сохраняем файл
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_template)
        
        print(f"✅ HTML отчет создан: {filename}")
        return filename
        
    except Exception as e:
        print(f"❌ Ошибка при создании HTML отчета: {e}")
        import traceback
        traceback.print_exc()
        return None


async def process_chat_command(event, use_ai=True):
    """
    Универсальная функция обработки команд /sum и /copy
    
    Args:
        event: Событие Telegram
        use_ai: True для /sum (с AI анализом), False для /copy (только экспорт)
    """
    try:
        # Парсим параметры команды
        message_text = event.raw_text
        parts = message_text.split()
        
        hours = None
        days = None
        limit = None
        
        # Обрабатываем параметры
        if len(parts) > 1:
            param = parts[1].lower()
            
            # Проверяем, что это - время или количество
            if 'h' in param:
                hours = int(param.replace('h', ''))
            elif 'd' in param:
                days = int(param.replace('d', ''))
            elif param.isdigit():
                # Это количество сообщений
                limit = int(param)
            
            # Если есть второй параметр (например, 3d 6h)
            if len(parts) > 2:
                param2 = parts[2].lower()
                if 'h' in param2:
                    hours = int(param2.replace('h', ''))
                elif 'd' in param2:
                    days = int(param2.replace('d', ''))
        
        # Если ничего не указано, по умолчанию 24 часа
        if hours is None and days is None and limit is None:
            hours = 24
        
        # Получаем название чата для информации
        chat = await event.get_chat()
        chat_name = chat.title if hasattr(chat, 'title') else "чата"
        
        # Удаляем команду из чата (для приватности)
        await event.delete()
        
        # Получаем или создаем тему для этого чата
        topic_id = await get_or_create_topic(chat_name)
        
        # Формируем сообщение о начале
        action = "анализ" if use_ai else "экспорт"
        if limit:
            status_msg = f"🔄 Начинаю {action} последних {limit} сообщений из чата '{chat_name}'..."
        else:
            status_msg = f"🔄 Начинаю {action} чата '{chat_name}' за последние {days or 0} дней и {hours or 0} часов..."
        
        # Информируем о начале в канале/Избранном/Теме
        await telegram_client.send_message(
            RESULTS_DESTINATION, 
            status_msg,
            reply_to=topic_id
        )
        
        # Собираем сообщения
        messages_data, chat_id_str, period_start_date = await collect_messages(event.chat_id, hours=hours, days=days, limit=limit)
        
        if not messages_data:
            await telegram_client.send_message(
                RESULTS_DESTINATION, 
                f"❌ За указанный период не найдено сообщений в чате '{chat_name}'",
                reply_to=topic_id
            )
            return
        
        # Оптимизируем сообщения (фильтруем шум)
        optimized_messages = optimize_messages(messages_data, chat_id_str)
        
        # Подсчитываем сообщения с URL
        url_count, url_messages = count_messages_with_urls(optimized_messages)
        if url_count > 0:
            print(f"\n📎 Найдено сообщений с URL: {url_count}")
            for msg in url_messages[:10]:  # Показываем первые 10
                text_preview = msg['text']
                print(f"   • {text_preview}...")
            if len(url_messages) > 10:
                print(f"   ... и еще {len(url_messages) - 10} сообщений с URL")
        
        # Предупреждение о больших запросах (особенно для AI анализа)
        if use_ai and len(optimized_messages) > 200:
            await telegram_client.send_message(
                RESULTS_DESTINATION,
                f"⚠️ **Внимание:** Большой объем сообщений ({len(optimized_messages)})\n"
                f"Обработка может занять несколько минут. Пожалуйста, подождите...\n"
                f"💡 Совет: Для больших объемов лучше использовать `/copy`, а затем анализировать вручную.",
                reply_to=topic_id
            )
        
        if not optimized_messages:
            await telegram_client.send_message(
                RESULTS_DESTINATION, 
                f"⚠️ После фильтрации не осталось сообщений.\n"
                f"Загружено: {len(messages_data)}, все отфильтрованы.",
                reply_to=topic_id
            )
            return
        
        # Ветвление: с AI или без
        if use_ai:
            # Режим /sum - анализ с AI
            summary, usage_info = await create_summary(optimized_messages, chat_id_str, model=CURRENT_MODEL, use_reasoning=USE_REASONING, period_start_date=period_start_date)
            
            # Проверяем, что summary не является сообщением об ошибке
            if summary.startswith('❌'):
                # Если получили ошибку, отправляем её пользователю и выходим
                await telegram_client.send_message(
                    RESULTS_DESTINATION,
                    f"{summary}\n\n⚠️ Анализ прерван. Попробуйте позже или уменьшите количество сообщений.",
                    reply_to=topic_id
                )
                return
            
            analysis_filename = save_analysis(optimized_messages, summary)
            
            # Подсчитываем количество тем (по разделителю "---")
            # Темы разделяются строкой "---" на отдельной строке
            # Количество тем = количество разделителей + 1 (если есть хотя бы одна тема)
            separator_count = summary.count('\n---\n')
            topics_count = separator_count + 1 if separator_count > 0 or summary.strip() else 0
            
            # Вычисляем информацию о периоде (перед формированием статистики)
            period_info, period_start_time, period_end_time, period_start_dt, period_end_dt = calculate_period_info(
                messages_data, optimized_messages, period_start_date, label="анализа"
            )
            
            # Вычисляем длительность для вывода
            period_hours = None
            period_text = ""
            if period_start_dt and period_end_dt:
                delta = period_end_dt - period_start_dt
                period_hours = abs(round(delta.total_seconds() / 3600))
                if period_hours < 24:
                    period_text = f"{period_hours} часов"
                else:
                    period_days = period_hours // 24
                    remaining_hours = period_hours % 24
                    if remaining_hours > 0:
                        period_text = f"{period_days} дней {remaining_hours} часов"
                    else:
                        period_text = f"{period_days} дней"
            
            # Добавляем информацию о токенах и стоимости
            prompt_tokens = None
            completion_tokens = None
            total_tokens = None
            total_cost = None
            
            if usage_info:
                prompt_tokens = usage_info['prompt_tokens']
                completion_tokens = usage_info['completion_tokens']
                total_tokens = usage_info['total_tokens']
                
                # Расчет стоимости для sonar-pro
                # https://docs.perplexity.ai/guides/pricing
                # sonar-pro: $3 per 1M input tokens, $15 per 1M output tokens
                input_cost = (prompt_tokens / 1_000_000) * 3.0
                output_cost = (completion_tokens / 1_000_000) * 15.0
                total_cost = input_cost + output_cost
            
            # Формируем статистику в новом формате
            stats_message = f"📊 Анализ завершен\n\n"
            stats_message += f"• Обработано: {len(optimized_messages)} сообщений = {topics_count} Тем\n"
            if url_count > 0:
                stats_message += f"• URL в сообщениях: {url_count}\n"
            if period_text:
                stats_message += f"• За период: {period_text}\n"
                stats_message += f"• С {period_start_time} по {period_end_time}\n"
            if usage_info and total_tokens:
                stats_message += f"• Токенов: {total_tokens:,} = ${total_cost:.4f}\n"
            
            # Формируем полный контент для Telegraph (с статистикой в конце)
            full_content = summary
            # Закомментировано: статистика токенов в конце статьи Telegraph
            # if usage_info and prompt_tokens is not None:
            #     full_content += f"\n\n---\n\n"
            #     full_content += f"📊 **Использовано токенов:**\n"
            #     full_content += f"• Промпт: {prompt_tokens:,}\n"
            #     full_content += f"• Ответ: {completion_tokens:,}\n"
            #     full_content += f"• Всего: {total_tokens:,}\n"
            #     full_content += f"💰 Стоимость: ${total_cost:.4f}\n"
            
            # Добавляем информацию о боте в конец статьи
            full_content += f"\n---\n"
            full_content += f"[Chat Sum Bot](https://github.com/Hohlas/ChatSum) | [Hohla](https://t.me/hohlas)\n\n"
            full_content += f"💰 0x94f69c258cD251bcB77DBb6156DA13E32dCb8Ef4\n"
            
            article_title = f"Анализ чата: {chat_name} ({period_start_time})"
            
            # Выбираем способ экспорта на основе конфигурации
            if USE_HTML_EXPORT:
                # Создаем HTML отчет и отправляем файл
                html_file = create_html_report(article_title, full_content, author_name="Chat Filter Bot")
                
                if html_file:
                    # Отправляем HTML файл как документ
                    await telegram_client.send_file(
                        RESULTS_DESTINATION,
                        html_file,
                        caption=stats_message,
                        reply_to=topic_id
                    )
                    print(f"✅ HTML отчет отправлен в Telegram")
                    
                    # Удаляем временный файл анализа после успешной публикации
                    try:
                        if os.path.exists(analysis_filename):
                            os.remove(analysis_filename)
                            print(f"🗑️  Временный файл {analysis_filename} удален")
                    except Exception as e:
                        print(f"⚠️  Не удалось удалить файл {analysis_filename}: {e}")
                else:
                    # Если не удалось создать HTML, отправляем просто статистику
                    stats_message += f"\n⚠️ Не удалось создать HTML отчет"
                    await telegram_client.send_message(
                        RESULTS_DESTINATION, 
                        stats_message,
                        reply_to=topic_id
                    )
            else:
                # Используем Telegraph (старый способ)
                article_url = publish_to_telegraph(article_title, full_content, author_name="Chat Filter Bot")
                
                if article_url:
                    stats_message += f"\n📰 **Статья в Telegraph:**\n{article_url}"
                    # Удаляем временный файл анализа после успешной публикации
                    try:
                        if os.path.exists(analysis_filename):
                            os.remove(analysis_filename)
                            print(f"🗑️  Временный файл {analysis_filename} удален")
                    except Exception as e:
                        print(f"⚠️  Не удалось удалить файл {analysis_filename}: {e}")
                else:
                    # Если не удалось опубликовать в Telegraph, сохраняем в файл как запасной вариант
                    stats_message += f"\n⚠️ Не удалось опубликовать в Telegraph. Сохраняю в файл..."
                    filename = f"analysis_{chat_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write(full_content)
                    
                    await telegram_client.send_file(
                        RESULTS_DESTINATION,
                        filename,
                        caption=f"📄 **Полный анализ чата '{chat_name}'**\n\n"
                               f"Тем: {topics_count}\n"
                               f"Сообщений проанализировано: {len(optimized_messages)}",
                        reply_to=topic_id
                    )
                    os.remove(filename)
                
                # Отправляем статистику с ссылкой на статью
                await telegram_client.send_message(
                    RESULTS_DESTINATION, 
                    stats_message,
                    reply_to=topic_id
                )
            
            print("✅ Анализ с AI успешно завершён")
        
        else:
            # Режим /copy - экспорт без AI
            # Используем общую функцию для формирования структуры (такая же как в /sum)
            export_data = build_optimized_json_structure(
                optimized_messages,
                chat_id_str,
                chat_name=chat_name,
                total_messages=len(messages_data),
                filtered_messages=len(optimized_messages),
                period_start_date=period_start_date
            )
            
            # Вычисляем информацию о периоде
            period_info, period_start_time, period_end_time, period_start_dt, period_end_dt = calculate_period_info(
                messages_data, optimized_messages, period_start_date, label="экспорта"
            )
            
            # Создаем JSON строку
            json_export = json.dumps(export_data, ensure_ascii=False, indent=2)
            
            # Сохраняем в файл
            filename = f"export_{chat_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(json_export)
            
            # Вычисляем длительность для caption
            period_hours = None
            period_text = ""
            if period_start_dt and period_end_dt:
                delta = period_end_dt - period_start_dt
                period_hours = abs(round(delta.total_seconds() / 3600))
                if period_hours < 24:
                    period_text = f"{period_hours} часов"
                else:
                    period_days = period_hours // 24
                    remaining_hours = period_hours % 24
                    if remaining_hours > 0:
                        period_text = f"{period_days} дней {remaining_hours} часов"
                    else:
                        period_text = f"{period_days} дней"
            
            # Формируем caption в новом формате
            caption = f"📋 Экспорт завершен\n\n"
            caption += f"• Обработано: {len(optimized_messages)} сообщений\n"
            if period_text:
                caption += f"• За период: {period_text}\n"
                caption += f"• С {period_start_time} по {period_end_time}\n"
            caption += f"\n💡 Готово для копирования в Perplexity!\n"
            caption += f"📊 Формат: JSON v2.0 (s/t/r)"
            
            # Отправляем файл
            await telegram_client.send_file(
                RESULTS_DESTINATION,
                filename,
                caption=caption,
                reply_to=topic_id
            )
            
            # Удаляем временный файл
            os.remove(filename)
            
            print(f"✅ Экспорт завершен: {len(optimized_messages)} сообщений")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при выполнении команды: {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        
        # Пытаемся отправить ошибку в тему (если возможно)
        try:
            chat = await event.get_chat()
            chat_name = chat.title if hasattr(chat, 'title') else "чата"
            topic_id = await get_or_create_topic(chat_name)
            await telegram_client.send_message(RESULTS_DESTINATION, error_msg, reply_to=topic_id)
        except:
            await telegram_client.send_message(RESULTS_DESTINATION, error_msg)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/config'))
async def handle_config_command(event):
    """Показывает текущую конфигурацию"""
    export_mode = "HTML файлы 📄" if USE_HTML_EXPORT else "Telegraph 🌐"
    config_text = f"""
⚙️ **Текущая конфигурация бота**

**🤖 Модель AI:**
• Текущая модель: `{CURRENT_MODEL}`
• Reasoning: {'Включен' if USE_REASONING else 'Выключен'}
• Экспорт результатов: {export_mode}

**📝 Исключенные пользователи** ({len(EXCLUDED_USERS)}):
{', '.join(EXCLUDED_USERS) if EXCLUDED_USERS else 'Нет'}

**⭐ Приоритетные пользователи** ({len(PRIORITY_USERS)}):
{', '.join(PRIORITY_USERS) if PRIORITY_USERS else 'Нет'}

**🎯 Настройки фильтрации:**
• Минимальная длина сообщения: {MIN_MESSAGE_LENGTH} символов
• Паттернов шума: {len(NOISE_PATTERNS)}

**📄 Файлы конфигурации:**
• {EXCLUDED_USERS_FILE}
• {PRIORITY_USERS_FILE}
• {PROMPT_FILE}
• {MODEL_CONFIG_FILE}

**Команды управления:**

**Просмотр:**
`/show_excluded` - показать исключенных пользователей
`/show_priority` - показать приоритетных пользователей
`/show_prompt` - показать текущий промпт
`/show_model` - показать настройки модели AI

**Редактирование:**
`/add_excluded username` - добавить в исключенные
`/remove_excluded username` - убрать из исключенных
`/add_priority username` - добавить в приоритетные
`/remove_priority username` - убрать из приоритетных
`/set_model model_name` - сменить модель AI

**Обновление:**
`/reload_config` - перезагрузить конфигурацию из файлов

💡 Можно также редактировать файлы напрямую на сервере
"""
    await event.delete()
    
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    
    await telegram_client.send_message(RESULTS_DESTINATION, config_text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/show_excluded'))
async def handle_show_excluded_command(event):
    """Показывает список исключенных пользователей"""
    text = f"📝 **Исключенные пользователи** ({len(EXCLUDED_USERS)}):\n\n"
    if EXCLUDED_USERS:
        for i, user in enumerate(EXCLUDED_USERS, 1):
            text += f"{i}. {user}\n"
    else:
        text += "Список пуст"
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/show_priority'))
async def handle_show_priority_command(event):
    """Показывает список приоритетных пользователей"""
    text = f"⭐ **Приоритетные пользователи** ({len(PRIORITY_USERS)}):\n\n"
    if PRIORITY_USERS:
        for i, user in enumerate(PRIORITY_USERS, 1):
            text += f"{i}. {user}\n"
    else:
        text += "Список пуст"
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/show_prompt'))
async def handle_show_prompt_command(event):
    """Показывает текущий промпт"""
    prompt_preview = ANALYSIS_PROMPT[:1000] + "..." if len(ANALYSIS_PROMPT) > 1000 else ANALYSIS_PROMPT
    text = f"📄 **Текущий промпт** ({len(ANALYSIS_PROMPT)} символов):\n\n{prompt_preview}\n\n"
    text += f"💡 Полный промпт в файле: {PROMPT_FILE}"
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/add_excluded\s+(.+)'))
async def handle_add_excluded_command(event):
    """Добавляет пользователя в список исключенных"""
    global EXCLUDED_USERS
    username = event.pattern_match.group(1).strip()
    
    if username in EXCLUDED_USERS:
        text = f"⚠️ Пользователь **{username}** уже в списке исключенных"
    else:
        EXCLUDED_USERS.append(username)
        if save_users_to_file(EXCLUDED_USERS_FILE, EXCLUDED_USERS):
            text = f"✅ Пользователь **{username}** добавлен в исключенные\n\nТекущий список ({len(EXCLUDED_USERS)}): {', '.join(EXCLUDED_USERS)}"
        else:
            EXCLUDED_USERS.remove(username)  # Откатываем изменение
            text = f"❌ Ошибка при сохранении в файл"
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/remove_excluded\s+(.+)'))
async def handle_remove_excluded_command(event):
    """Удаляет пользователя из списка исключенных"""
    global EXCLUDED_USERS
    username = event.pattern_match.group(1).strip()
    
    if username not in EXCLUDED_USERS:
        text = f"⚠️ Пользователь **{username}** не найден в списке исключенных"
    else:
        EXCLUDED_USERS.remove(username)
        if save_users_to_file(EXCLUDED_USERS_FILE, EXCLUDED_USERS):
            text = f"✅ Пользователь **{username}** удален из исключенных\n\nТекущий список ({len(EXCLUDED_USERS)}): {', '.join(EXCLUDED_USERS) if EXCLUDED_USERS else 'Пуст'}"
        else:
            EXCLUDED_USERS.append(username)  # Откатываем изменение
            text = f"❌ Ошибка при сохранении в файл"
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/add_priority\s+(.+)'))
async def handle_add_priority_command(event):
    """Добавляет пользователя в список приоритетных"""
    global PRIORITY_USERS
    username = event.pattern_match.group(1).strip()
    
    if username in PRIORITY_USERS:
        text = f"⚠️ Пользователь **{username}** уже в списке приоритетных"
    else:
        PRIORITY_USERS.append(username)
        if save_users_to_file(PRIORITY_USERS_FILE, PRIORITY_USERS):
            text = f"✅ Пользователь **{username}** добавлен в приоритетные\n\nТекущий список ({len(PRIORITY_USERS)}): {', '.join(PRIORITY_USERS)}"
        else:
            PRIORITY_USERS.remove(username)  # Откатываем изменение
            text = f"❌ Ошибка при сохранении в файл"
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/remove_priority\s+(.+)'))
async def handle_remove_priority_command(event):
    """Удаляет пользователя из списка приоритетных"""
    global PRIORITY_USERS
    username = event.pattern_match.group(1).strip()
    
    if username not in PRIORITY_USERS:
        text = f"⚠️ Пользователь **{username}** не найден в списке приоритетных"
    else:
        PRIORITY_USERS.remove(username)
        if save_users_to_file(PRIORITY_USERS_FILE, PRIORITY_USERS):
            text = f"✅ Пользователь **{username}** удален из приоритетных\n\nТекущий список ({len(PRIORITY_USERS)}): {', '.join(PRIORITY_USERS) if PRIORITY_USERS else 'Пуст'}"
        else:
            PRIORITY_USERS.append(username)  # Откатываем изменение
            text = f"❌ Ошибка при сохранении в файл"
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/show_model'))
async def handle_show_model_command(event):
    """Показывает текущую настройку модели"""
    export_mode = "HTML файлы 📄" if USE_HTML_EXPORT else "Telegraph 🌐"
    text = f"""
🤖 **Текущая модель для анализа**

**Модель:** `{CURRENT_MODEL}`
**Reasoning:** {'Включен ✅' if USE_REASONING else 'Выключен ❌'}
**Экспорт результатов:** {export_mode}

⚠️ **ВАЖНО:** Через Perplexity API доступны ТОЛЬКО модели Sonar!
Claude, GPT и другие модели доступны только в веб-интерфейсе Perplexity Pro.

**💰 Доступные модели через API:**

**Sonar (базовая):**
• Основа: Llama 3.3 70B
• Входящие: ~$0.20 / 1M токенов
• Исходящие: ~$0.20 / 1M токенов
• Контекст: 127K токенов
• Скорость: Быстро ⚡
• Качество: Хорошее ✅
• Интеграция с поиском в реальном времени

**Sonar Pro (улучшенная) ⭐ РЕКОМЕНДУЕТСЯ:**
• Основа: Llama 3.3 70B (оптимизирована)
• Входящие: ~$1.00 / 1M токенов
• Исходящие: ~$1.00 / 1M токенов
• Контекст: 127K токенов
• Скорость: Быстро ⚡⚡
• Качество: Отличное ⭐⭐⭐
• Лучшая точность и глубина анализа

**Управление:**
`/set_model sonar` - базовая модель (дешевле)
`/set_model sonar-pro` - улучшенная (рекомендуется) ⭐

💡 Текущая модель сохраняется в файле {MODEL_CONFIG_FILE}

📚 Альтернатива:
Если нужен Claude/GPT - используйте их напрямую через OpenAI API или Anthropic API, а не через Perplexity.
"""
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/set_model\s+(.+)'))
async def handle_set_model_command(event):
    """Устанавливает модель для анализа"""
    global CURRENT_MODEL
    
    model = event.pattern_match.group(1).strip()
    
    # Валидируем название модели
    # ⚠️ Только модели Sonar доступны через Perplexity API!
    valid_models = [
        'sonar',           # Базовая модель
        'sonar-pro',       # Улучшенная версия (рекомендуется)
    ]
    
    if model not in valid_models:
        text = f"⚠️ Неизвестная или недоступная модель: **{model}**\n\n"
        text += "⚠️ **Важно:** Через Perplexity API доступны ТОЛЬКО модели Sonar!\n\n"
        text += "Доступные модели:\n"
        for m in valid_models:
            text += f"• `{m}`\n"
        text += "\n💡 Claude, GPT и другие модели доступны только в веб-интерфейсе Perplexity Pro"
    else:
        old_model = CURRENT_MODEL
        CURRENT_MODEL = model
        
        if save_model_config(MODEL_CONFIG_FILE, CURRENT_MODEL, USE_REASONING):
            text = f"✅ Модель изменена: **{old_model}** → **{CURRENT_MODEL}**\n\n"
            text += "Изменения вступят в силу для следующего анализа.\n"
            text += f"Используйте `/show_model` для просмотра деталей."
        else:
            CURRENT_MODEL = old_model  # Откатываем
            text = f"❌ Ошибка при сохранении конфигурации модели"
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/reload_config'))
async def handle_reload_config_command(event):
    """Перезагружает конфигурацию из файлов"""
    global EXCLUDED_USERS, PRIORITY_USERS, ANALYSIS_PROMPT, CURRENT_MODEL, USE_REASONING, USE_HTML_EXPORT
    
    EXCLUDED_USERS = load_users_from_file(EXCLUDED_USERS_FILE)
    PRIORITY_USERS = load_users_from_file(PRIORITY_USERS_FILE)
    ANALYSIS_PROMPT = load_prompt_from_file(PROMPT_FILE)
    CURRENT_MODEL, USE_REASONING, USE_HTML_EXPORT = load_model_config(MODEL_CONFIG_FILE)
    
    text = f"""
✅ **Конфигурация перезагружена из файлов**

📝 Исключенные пользователи: {len(EXCLUDED_USERS)}
⭐ Приоритетные пользователи: {len(PRIORITY_USERS)}
📄 Промпт: {len(ANALYSIS_PROMPT)} символов
🤖 Модель: {CURRENT_MODEL}

💡 Используйте `/config` для просмотра деталей
"""
    
    await event.delete()
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Конфигурация"
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/sum'))
async def handle_sum_command(event):
    """
    Обработчик команды /sum для анализа чата с AI
    
    Примеры:
    /sum 3h - анализ за 3 часа
    /sum 45 - анализ 45 сообщений
    """
    await process_chat_command(event, use_ai=True)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/copy'))
async def handle_copy_command(event):
    """
    Обработчик команды /copy для экспорта без AI
    
    Примеры:
    /copy 3h - экспорт за 3 часа
    /copy 45 - экспорт 45 сообщений
    """
    await process_chat_command(event, use_ai=False)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/help'))
async def handle_help_command(event):
    """Обработчик команды /help - показывает справку по командам"""
    help_text = """
📖 **Справка по командам бота**

**📊 Основные команды:**

`/sum` - анализ и выжимка чата (с AI)
Примеры:
  • `/sum` - за последние 24 часа
  • `/sum 3h` - за последние 3 часа
  • `/sum 2d` - за последние 2 дня
  • `/sum 45` - последние 45 сообщений
  • `/sum 100` - последние 100 сообщений

`/copy` - экспорт без анализа (для ручной обработки)
Примеры:
  • `/copy 3h` - экспорт за 3 часа
  • `/copy 50` - экспорт 50 сообщений
  • Результат: JSON файл + текст для Perplexity

`/help` - показать эту справку

**⚙️ Управление конфигурацией:**

`/config` - показать текущую конфигурацию
`/show_excluded` - список исключенных пользователей
`/show_priority` - список приоритетных пользователей
`/show_prompt` - показать текущий промпт
`/show_model` - показать настройки модели AI

`/add_excluded username` - добавить в исключенные
`/remove_excluded username` - убрать из исключенных
`/add_priority username` - добавить в приоритетные
`/remove_priority username` - убрать из приоритетных
`/set_model model_name` - сменить модель AI

`/reload_config` - перезагрузить из файлов

**🤖 Доступные модели AI (только Sonar через API!):**
• `sonar` - базовая модель, дешевая
• `sonar-pro` - улучшенная версия ⭐ (рекомендуется)

⚠️ Claude, GPT доступны только в веб-интерфейсе Perplexity Pro

**Как это работает:**

**`/sum` (с AI анализом):**
1. Собирает сообщения (по времени или количеству)
2. Фильтрует шум и исключенных пользователей
3. Отправляет в Perplexity AI (модель Sonar Pro)
4. Получает структурированную выжимку по темам
5. Отправляет результат в ваш канал

**`/copy` (без AI, только экспорт):**
1. Собирает и фильтрует сообщения
2. Создает JSON файл с метаданными
3. Отправляет вам для ручного анализа
4. Удобно для копирования в Perplexity вручную

**🔍 Что анализируется:**
• Основные темы обсуждений (включая микро-дискуссии)
• Аргументы участников с сохранением терминологии
• Время начала каждой темы
• Итоговые тенденции и выводы
• Ссылки на первые реплики каждого участника

**🎯 Оптимизация:**
• Автоматически исключаются указанные пользователи
• Фильтруется технический флуд (+, ок, лол и т.п.)
• Удаляются бессодержательные сообщения
• Приоритет участникам: Zinur, Restyle Pon, Lex, ProMint, Sergey

**📁 Организация:**
• Для каждого чата автоматически создается отдельная тема в канале
• Все анализы группируются по источнику

**🔒 Приватность:**
• Ваша команда автоматически удаляется из чата
• Результаты отправляются в ваш приватный канал/Избранное
• Никто в чате не узнает, что вы делали анализ

**Примечание:** Бот реагирует только на ваши собственные команды (исходящие сообщения).
"""
    await event.delete()
    
    # Получаем название чата для создания/использования темы
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Справка"
    topic_id = await get_or_create_topic(chat_name)
    
    await telegram_client.send_message(RESULTS_DESTINATION, help_text, reply_to=topic_id)


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
    
    # Показываем настройки модели
    print(f"\n🤖 Модель AI:")
    print(f"   • Текущая модель: {CURRENT_MODEL}")
    print(f"   • Reasoning режим: {'Включен' if USE_REASONING else 'Выключен'}")
    print(f"   • Экспорт результатов: {'HTML файлы 📄' if USE_HTML_EXPORT else 'Telegraph 🌐'}")
    
    # Показываем настройки фильтрации
    print(f"\n🎯 Настройки оптимизации:")
    print(f"   • Исключенные пользователи: {', '.join(EXCLUDED_USERS) if EXCLUDED_USERS else 'Нет'}")
    print(f"   • Приоритетные пользователи: {', '.join(PRIORITY_USERS) if PRIORITY_USERS else 'Нет'}")
    print(f"   • Минимальная длина сообщения: {MIN_MESSAGE_LENGTH} символов")
    
    print("\n📌 Доступные команды:")
    print("  Анализ:")
    print("    /sum - анализ чата с AI (по времени или количеству)")
    print("    /sum 3h - последние 3 часа")
    print("    /sum 45 - последние 45 сообщений")
    print("  Экспорт:")
    print("    /copy - экспорт без AI (для ручного анализа)")
    print("    /copy 3h - экспорт за 3 часа")
    print("    /copy 50 - экспорт 50 сообщений")
    print("  Конфигурация:")
    print("    /config - показать конфигурацию")
    print("    /show_model - показать настройки модели AI")
    print("    /set_model - сменить модель AI")
    print("    /add_excluded, /remove_excluded - управление исключенными")
    print("    /add_priority, /remove_priority - управление приоритетными")
    print("    /reload_config - перезагрузить из файлов")
    print("  Справка:")
    print("    /help - полная справка по командам")
    print("\n💡 Отправьте команду /sum в любом чате для анализа с AI")
    print("💡 Используйте /copy для экспорта без затрат на API")
    print("=" * 60)
    print("\n👀 Ожидание команд...")
    print("💡 Нажмите Ctrl+C для остановки бота")
    
    try:
        await telegram_client.run_until_disconnected()
    except KeyboardInterrupt:
        print("\n🔄 Завершение работы...")
        await telegram_client.disconnect()
        print("✅ Соединение с Telegram закрыто")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n")
        print("=" * 60)
        print("🛑 Бот остановлен пользователем (Ctrl+C)")
        print("=" * 60)
        print("\n💡 Для запуска снова используйте: python3 main.py")
        print("✅ Все сессии сохранены\n")
