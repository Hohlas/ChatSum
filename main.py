import os
import asyncio
import random
import re
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
PERPLEXITY_API_KEY = os.getenv('PERPLEXITY_API_KEY')

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

perplexity_client = OpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url='https://api.perplexity.ai'
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


async def collect_messages(chat_id, hours=24, days=0):
    """
    Собирает сообщения из чата за указанный период
    
    Args:
        chat_id: ID чата для анализа
        hours: Количество часов назад (по умолчанию 24)
        days: Количество дней назад (по умолчанию 0)
    
    Returns:
        Кортеж (список сообщений, chat_id_str для ссылок)
    """
    print(f"🔄 Загрузка сообщений за последние {days} дней и {hours} часов...")
    
    # Вычисляем временную границу
    time_limit = datetime.now() - timedelta(days=days, hours=hours)
    
    # Получаем информацию о чате для формирования ссылок
    chat = await telegram_client.get_entity(chat_id)
    # Преобразуем chat_id в формат для ссылок (убираем -100 префикс)
    chat_id_str = str(chat_id).replace('-100', '')
    
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
                'date': message.date.strftime('%Y-%m-%d %H:%M:%S'),
                'message_id': message.id
            })
    
    # Сортируем по времени (от старых к новым)
    messages_data.reverse()
    
    print(f"✅ Загружено {len(messages_data)} сообщений")
    return messages_data, chat_id_str


async def create_summary(messages_data, model='sonar', use_reasoning=False):
    """
    Создает выжимку из сообщений с помощью Perplexity API
    
    Args:
        messages_data: Список словарей с сообщениями (включая chat_id, message_id)
        model: Модель для использования (sonar, claude-3.5-sonnet и т.д.)
        use_reasoning: Использовать ли reasoning режим (для моделей с поддержкой)
    
    Returns:
        Текст выжимки
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
    
    # Формируем JSON для отправки (более структурированный формат)
    messages_json = json.dumps([
        {
            'sender': msg['sender'],
            'text': msg['text'],
            'date': msg['date'],
            'message_id': msg['message_id'],
            'chat_id': msg['chat_id']
        }
        for msg in messages_data
    ], ensure_ascii=False, indent=2)
    
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
        
        messages_json = json.dumps([
            {
                'sender': msg['sender'],
                'text': msg['text'],
                'date': msg['date'],
                'message_id': msg['message_id'],
                'chat_id': msg['chat_id']
            }
            for msg in messages_data_limited
        ], ensure_ascii=False, indent=2)
    
    try:
        # Формируем параметры запроса
        request_params = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': ANALYSIS_PROMPT},
                {'role': 'user', 'content': f'Данные сообщений для анализа (JSON):\n\n{messages_json}'}
            ],
            'temperature': 0.3
        }
        
        # Настройка max_tokens
        # Sonar может генерировать до 4K токенов ответа
        request_params['max_tokens'] = 4000
        
        # Добавляем reasoning для поддерживаемых моделей
        # Примечание: не все модели в Perplexity поддерживают reasoning
        # Обычно это экспериментальная фича
        if use_reasoning and 'sonar' in model.lower():
            print("   🧠 Режим reasoning включен")
            # Perplexity может не поддерживать этот параметр
            # request_params['reasoning'] = True
        
        response = perplexity_client.chat.completions.create(**request_params)
        
        summary = response.choices[0].message.content
        print("✅ Выжимка успешно создана")
        
        # Показываем статистику использования токенов
        if hasattr(response, 'usage'):
            usage = response.usage
            print(f"   📊 Использовано токенов:")
            print(f"      Промпт: {usage.prompt_tokens if hasattr(usage, 'prompt_tokens') else 'N/A'}")
            print(f"      Ответ: {usage.completion_tokens if hasattr(usage, 'completion_tokens') else 'N/A'}")
            print(f"      Всего: {usage.total_tokens if hasattr(usage, 'total_tokens') else 'N/A'}")
        
        return summary
        
    except Exception as e:
        error_msg = f"❌ Ошибка при создании выжимки: {e}"
        print(error_msg)
        print(f"   Модель: {model}")
        print(f"   Размер данных: {len(messages_json)} символов")
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
        
        # Получаем или создаем тему для этого чата
        topic_id = await get_or_create_topic(chat_name)
        
        # Информируем о начале анализа в канале/Избранном/Теме
        await telegram_client.send_message(
            RESULTS_DESTINATION, 
            f"🔄 Начинаю анализ чата '{chat_name}' за последние {days} дней и {hours} часов...",
            reply_to=topic_id
        )
        
        # Собираем сообщения
        messages_data, chat_id_str = await collect_messages(event.chat_id, hours=hours, days=days)
        
        if not messages_data:
            await telegram_client.send_message(
                RESULTS_DESTINATION, 
                f"❌ За указанный период не найдено сообщений в чате '{chat_name}'",
                reply_to=topic_id
            )
            return
        
        # Оптимизируем сообщения (фильтруем шум)
        optimized_messages = optimize_messages(messages_data, chat_id_str)
        
        if not optimized_messages:
            await telegram_client.send_message(
                RESULTS_DESTINATION, 
                f"⚠️ После фильтрации не осталось сообщений для анализа.\n"
                f"Загружено: {len(messages_data)}, но все были отфильтрованы как шум или от исключенных пользователей.",
                reply_to=topic_id
            )
            return
        
        # Создаем выжимку с использованием настроенной модели
        summary = await create_summary(optimized_messages, model=CURRENT_MODEL, use_reasoning=USE_REASONING)
        
        # Сохраняем результаты (сохраняем оптимизированные данные)
        save_analysis(optimized_messages, summary)
        
        # Отправляем выжимку пользователю в канал/Избранное/Тему
        response = f"📍 Чат: **{chat_name}**\n\n"
        response += f"📊 **Выжимка чата**\n\n"
        response += f"Период: последние {days} дней и {hours} часов\n"
        response += f"Загружено сообщений: {len(messages_data)}\n"
        response += f"Проанализировано после фильтрации: {len(optimized_messages)}\n\n"
        response += f"**Результат анализа:**\n\n{summary}"
        
        # Если сообщение слишком длинное, разбиваем на части
        max_length = 4096  # Ограничение Telegram
        if len(response) > max_length:
            # Отправляем первую часть
            await telegram_client.send_message(
                RESULTS_DESTINATION, 
                response[:max_length],
                reply_to=topic_id
            )
            # Отправляем остаток
            remaining = response[max_length:]
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
                response,
                reply_to=topic_id
            )
        
        print("✅ Анализ успешно завершён и отправлен пользователю")
        
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


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/help'))
async def handle_help_command(event):
    """Обработчик команды /help - показывает справку по командам"""
    help_text = """
📖 **Справка по командам бота**

**📊 Основные команды:**

`/analyze` - анализ чата за последние 24 часа

`/analyze [время]` - анализ за указанный период
Примеры:
  • `/analyze 12h` - за последние 12 часов
  • `/analyze 2d` - за последние 2 дня
  • `/analyze 3d 6h` - за последние 3 дня и 6 часов

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
1. Бот собирает все сообщения из текущего чата за указанный период
2. Фильтрует шум и бессодержательные сообщения (экономия токенов API)
3. Отправляет оптимизированные данные в Perplexity AI для анализа
4. Получает структурированную выжимку по темам с ссылками
5. Отправляет результат в ваш приватный канал/Избранное

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
    print("    /analyze - анализ чата за последние 24 часа")
    print("    /analyze [время] - анализ за указанный период")
    print("  Конфигурация:")
    print("    /config - показать конфигурацию")
    print("    /show_model - показать настройки модели AI")
    print("    /set_model - сменить модель AI")
    print("    /add_excluded, /remove_excluded - управление исключенными")
    print("    /add_priority, /remove_priority - управление приоритетными")
    print("    /reload_config - перезагрузить из файлов")
    print("  Справка:")
    print("    /help - полная справка по командам")
    print("\n💡 Отправьте команду /analyze в любом чате для начала анализа")
    print("=" * 60)
    print("\n👀 Ожидание команд...")
    
    await telegram_client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
