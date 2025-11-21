import os
import asyncio
import random
import re
from telethon import TelegramClient, events
from openai import OpenAI
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta, timezone
import httpx
from telegraph import Telegraph

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
        print(f"⚠️  Файл {filename} не найден, используется пустой список")
        return []
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Удаляем комментарии (строки начинающиеся с #)
        lines = [line.strip() for line in content.split('\n') 
                 if line.strip() and not line.strip().startswith('#')]
        
        # Объединяем все строки и разделяем по различным разделителям
        users = []
        for line in lines:
            # Поддерживаем разделители: пробел, запятая, точка с запятой, перенос строки
            parts = re.split(r'[,;\s]+', line)
            users.extend([p.strip() for p in parts if p.strip()])
        
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
        Кортеж (model_name, use_reasoning)
    """
    default_model = 'sonar-pro'  # Рекомендуемая модель для Perplexity API
    default_reasoning = False
    
    if not os.path.exists(filename):
        print(f"⚠️  Файл {filename} не найден, используется модель по умолчанию: {default_model}")
        return default_model, default_reasoning
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        model = default_model
        use_reasoning = default_reasoning
        
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
        
        return model, use_reasoning
    except Exception as e:
        print(f"❌ Ошибка при чтении {filename}: {e}")
        return default_model, default_reasoning


def save_model_config(filename, model, use_reasoning):
    """
    Сохраняет конфигурацию модели в файл
    
    Args:
        filename: Путь к файлу
        model: Название модели
        use_reasoning: Использовать ли reasoning режим
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
            f.write(f"USE_REASONING={'true' if use_reasoning else 'false'}\n")
        return True
    except Exception as e:
        print(f"❌ Ошибка при сохранении {filename}: {e}")
        return False


# Загружаем конфигурацию из файлов при старте
EXCLUDED_USERS = load_users_from_file(EXCLUDED_USERS_FILE)
PRIORITY_USERS = load_users_from_file(PRIORITY_USERS_FILE)
ANALYSIS_PROMPT = load_prompt_from_file(PROMPT_FILE)
CURRENT_MODEL, USE_REASONING = load_model_config(MODEL_CONFIG_FILE)

# Инициализация клиентов
telegram_client = TelegramClient('session_name', API_ID, API_HASH)

# Диагностика API ключа
print(f"🔑 Проверка Perplexity API ключа:")
print(f"   Длина: {len(PERPLEXITY_API_KEY)} символов")
print(f"   Первые 10 символов: {PERPLEXITY_API_KEY[:10]}...")
print(f"   Последние 10 символов: ...{PERPLEXITY_API_KEY[-10:]}")

# Убеждаемся что API ключ содержит только ASCII символы
has_non_ascii = False
try:
    PERPLEXITY_API_KEY.encode('ascii')
    print(f"   ✅ Ключ содержит только ASCII символы")
except UnicodeEncodeError:
    has_non_ascii = True
    print("   ⚠️  ВНИМАНИЕ: API ключ содержит не-ASCII символы!")
    print(f"   Проблемные символы: {[c for c in PERPLEXITY_API_KEY if ord(c) > 127]}")
    # Удаляем все не-ASCII символы
    PERPLEXITY_API_KEY = PERPLEXITY_API_KEY.encode('ascii', errors='ignore').decode('ascii')
    print(f"   После очистки: {len(PERPLEXITY_API_KEY)} символов")

# Создаем httpx клиент с явной обработкой заголовков
class ASCIIHeadersClient(httpx.Client):
    """Custom httpx client that ensures all headers are ASCII-safe"""
    def build_request(self, *args, **kwargs):
        request = super().build_request(*args, **kwargs)
        # Конвертируем все заголовки в ASCII-безопасный формат
        safe_headers = {}
        for key, value in request.headers.items():
            try:
                # Пытаемся закодировать значение в ASCII
                if isinstance(value, str):
                    value.encode('ascii')
                safe_headers[key] = value
            except (UnicodeEncodeError, AttributeError):
                # Если не получается - конвертируем в безопасную строку
                safe_value = str(value).encode('ascii', errors='ignore').decode('ascii')
                safe_headers[key] = safe_value
                print(f"⚠️  Исправлен заголовок '{key}': '{value}' -> '{safe_value}'")
        request.headers = httpx.Headers(safe_headers)
        return request

http_client = ASCIIHeadersClient(
    timeout=180.0,  # Увеличен до 3 минут для больших запросов
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
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
    
    for msg in messages_data:
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
    
    return optimized


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
    
    print(f"🤖 Отправка {len(messages_data)} сообщений в Perplexity для анализа...")
    print(f"   Модель: {model}")
    
    # Определяем лимиты в зависимости от модели
    # Sonar/Sonar-Pro имеют контекст 127K токенов (на основе Llama 3.3 70B)
    if 'sonar' in model.lower():
        max_chars = 250000  # ~60K токенов для Sonar (оставляем запас)
    else:
        max_chars = 200000  # Консервативный лимит для других моделей
    
    # Формируем ОПТИМИЗИРОВАННЫЙ JSON для экономии токенов
    # Используем общую функцию для единообразия с /copy
    optimized_structure = build_optimized_json_structure(messages_data, chat_id_str, period_start_date=period_start_date)
    
    # Используем ensure_ascii=False для сохранения кириллицы
    messages_json = json.dumps(optimized_structure, ensure_ascii=False, indent=2)
    
    # Проверяем размер и при необходимости разбиваем на части
    if len(messages_json) > max_chars:
        print(f"⚠️  Данных слишком много ({len(messages_json)} символов)")
        print(f"   Максимум для модели {model}: {max_chars} символов")
        
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
            'model': model,
            'messages': [
                {'role': 'system', 'content': system_content},
                {'role': 'user', 'content': user_content}
            ],
            'temperature': 0.3,
            'max_tokens': 4000
        }
        
        # Добавляем reasoning для поддерживаемых моделей
        # Примечание: не все модели в Perplexity поддерживают reasoning
        # Обычно это экспериментальная фича
        if use_reasoning and 'sonar' in model.lower():
            print("   🧠 Режим reasoning включен")
            # Perplexity может не поддерживать этот параметр
            # request_params['reasoning'] = True
        
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
            
            # Формируем статистику для сообщения
            stats_message = f"📊 **Анализ завершен**\n\n"
            stats_message += f"📈 **Статистика:**\n"
            stats_message += f"• Тем: {topics_count}\n"
            
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
                
                stats_message += f"• Токенов: {total_tokens:,}\n"
                stats_message += f"• Стоимость: ${total_cost:.4f}\n"
            
            # Формируем полный контент для Telegraph (с статистикой в конце)
            full_content = summary
            if usage_info and prompt_tokens is not None:
                full_content += f"\n\n---\n\n"
                full_content += f"📊 **Использовано токенов:**\n"
                full_content += f"• Промпт: {prompt_tokens:,}\n"
                full_content += f"• Ответ: {completion_tokens:,}\n"
                full_content += f"• Всего: {total_tokens:,}\n"
                full_content += f"💰 Стоимость: ${total_cost:.4f}\n"
            
            # Получаем время начала анализа из period_start_date (дата первого сообщения исходного периода)
            period_start_time = ""
            period_start_dt = None
            if period_start_date:
                try:
                    # Парсим дату из формата "2025-11-20 12:01:31"
                    period_start_dt = datetime.strptime(period_start_date, '%Y-%m-%d %H:%M:%S')
                    period_start_time = period_start_dt.strftime('%d.%m %H:%M')
                except (ValueError, TypeError):
                    # Если формат не совпадает, используем как есть или берем первые 16 символов
                    period_start_time = period_start_date[:16] if len(period_start_date) >= 16 else period_start_date
            
            # Если не удалось получить время начала, используем текущее время
            if not period_start_time:
                period_start_dt = datetime.now()
                period_start_time = period_start_dt.strftime('%d.%m %H:%M')
            
            # Получаем дату последнего сообщения (самое свежее)
            period_end_dt = None
            period_end_time = ""
            if messages_data:
                try:
                    # Находим последнее сообщение по дате (самое свежее)
                    last_message = max(messages_data, key=lambda x: x.get('date', ''))
                    last_date_str = last_message.get('date', '')
                    if last_date_str:
                        period_end_dt = datetime.strptime(last_date_str, '%Y-%m-%d %H:%M:%S')
                        period_end_time = period_end_dt.strftime('%d.%m %H:%M')
                except (ValueError, TypeError, KeyError):
                    # Если не удалось, используем текущее время
                    period_end_dt = datetime.now()
                    period_end_time = period_end_dt.strftime('%d.%m %H:%M')
            
            # Вычисляем период в часах
            period_hours = None
            if period_start_dt and period_end_dt:
                delta = period_end_dt - period_start_dt
                period_hours = int(delta.total_seconds() / 3600)
            
            # Формируем информацию о периоде
            period_info = ""
            if period_hours is not None:
                period_info = f"\n\n📅 **Период анализа:**\n"
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
            
            # Публикуем статью в Telegraph
            article_title = f"Анализ чата: {chat_name} ({period_start_time})"
            article_url = publish_to_telegraph(article_title, full_content, author_name="Chat Filter Bot")
            
            if article_url:
                stats_message += period_info
                stats_message += f"\n\n📰 **Статья в Telegraph:**\n{article_url}"
                # Удаляем временный файл анализа после успешной публикации
                try:
                    if os.path.exists(analysis_filename):
                        os.remove(analysis_filename)
                        print(f"🗑️  Временный файл {analysis_filename} удален")
                except Exception as e:
                    print(f"⚠️  Не удалось удалить файл {analysis_filename}: {e}")
            else:
                # Если не удалось опубликовать в Telegraph, сохраняем в файл как запасной вариант
                stats_message += period_info
                stats_message += f"\n\n⚠️ Не удалось опубликовать в Telegraph. Сохраняю в файл..."
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
            
            # Получаем дату последнего сообщения
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
                period_hours = int(delta.total_seconds() / 3600)
            
            # Формируем информацию о периоде
            period_info = ""
            if period_hours is not None:
                period_info = f"\n📅 **Период экспорта:**\n"
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
            
            # Создаем JSON строку
            json_export = json.dumps(export_data, ensure_ascii=False, indent=2)
            
            # Сохраняем в файл
            filename = f"export_{chat_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(json_export)
            
            # Отправляем файл
            await telegram_client.send_file(
                RESULTS_DESTINATION,
                filename,
                caption=f"📋 **Экспорт сообщений**\n\n"
                       f"Чат: {chat_name}\n"
                       f"Всего: {len(messages_data)} сообщений\n"
                       f"После фильтрации: {len(optimized_messages)} сообщений\n"
                       f"{period_info}\n"
                       f"💡 Готово для копирования в Perplexity!\n"
                       f"📊 Формат: Оптимизированный JSON v2.0\n"
                       f"   • Древовидная структура с replies\n"
                       f"   • Без полей date и chat_id в сообщениях\n"
                       f"   • Метаданные в metadata",
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
    config_text = f"""
⚙️ **Текущая конфигурация бота**

**🤖 Модель AI:**
• Текущая модель: `{CURRENT_MODEL}`
• Reasoning: {'Включен' if USE_REASONING else 'Выключен'}

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
    text = f"""
🤖 **Текущая модель для анализа**

**Модель:** `{CURRENT_MODEL}`
**Reasoning:** {'Включен ✅' if USE_REASONING else 'Выключен ❌'}

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
    global EXCLUDED_USERS, PRIORITY_USERS, ANALYSIS_PROMPT, CURRENT_MODEL, USE_REASONING
    
    EXCLUDED_USERS = load_users_from_file(EXCLUDED_USERS_FILE)
    PRIORITY_USERS = load_users_from_file(PRIORITY_USERS_FILE)
    ANALYSIS_PROMPT = load_prompt_from_file(PROMPT_FILE)
    CURRENT_MODEL, USE_REASONING = load_model_config(MODEL_CONFIG_FILE)
    
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
