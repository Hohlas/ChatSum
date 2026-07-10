import os
import asyncio
import random
import re
import shutil
import time
from collections import Counter
from telethon import TelegramClient, events
from openai import AsyncOpenAI, AuthenticationError, APIStatusError
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta, timezone
import httpx
from telegraph import Telegraph
from telegraph.exceptions import RetryAfterError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Скомпилированные регулярные выражения для конвертации Markdown в HTML
MD_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
MD_ITALIC_RE = re.compile(r'\*(.+?)\*')
MD_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^\)]+)\)')

# Блокировка для синхронизации доступа к глобальным конфигурационным переменным
config_lock = asyncio.Lock()


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
    google_key = os.getenv('GOOGLE_API_KEY', '').strip()
    extra_google_keys = [
        value.strip()
        for key, value in os.environ.items()
        if re.fullmatch(r'GOOGLE_API_KEY\d+', key) and value.strip()
    ]
    gemini_model = os.getenv('GEMINI_MODEL', '').strip()
    
    # Список заглушек, которые могут быть в шаблоне
    placeholders = [
        'ваш_api_id', 'ваш_api_hash', 'ваш_google_ключ', 'ваша_gemini_модель',
        'your_api_id', 'your_api_hash', 'your_google_key', 'your_gemini_model',
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
    
    # Проверка GOOGLE_API_KEY
    valid_google_keys = [k for k in ([google_key] + extra_google_keys) if k and k not in placeholders]
    if not valid_google_keys:
        errors.append("Не найден ни один валидный GOOGLE_API_KEY / GOOGLE_API_KEYN")
    
    # Проверка GEMINI_MODEL
    if not gemini_model or gemini_model in placeholders:
        errors.append("GEMINI_MODEL не заполнен или содержит заглушку")
    
    return errors


def load_google_api_keys():
    """
    Загружает список Google API ключей из private.txt / environment.
    Поддерживает GOOGLE_API_KEY, GOOGLE_API_KEY1, GOOGLE_API_KEY2, ...
    """
    keys = []

    primary_key = os.getenv('GOOGLE_API_KEY', '').strip()
    if primary_key:
        keys.append(primary_key)

    indexed_keys = []
    for env_key, env_value in os.environ.items():
        if re.fullmatch(r'GOOGLE_API_KEY\d+', env_key):
            value = env_value.strip()
            if value:
                indexed_keys.append((env_key, value))

    indexed_keys.sort(key=lambda item: int(re.search(r'(\d+)$', item[0]).group(1)))
    keys.extend([value for _, value in indexed_keys])

    # Убираем дубликаты с сохранением порядка
    unique_keys = []
    seen = set()
    for key in keys:
        if key not in seen:
            seen.add(key)
            unique_keys.append(key)

    return unique_keys


def mask_api_key(api_key):
    if not api_key:
        return 'пустой'
    if len(api_key) <= 12:
        return api_key
    return f"{api_key[:8]}...{api_key[-6:]}"


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
    print("   • Google AI Studio: https://aistudio.google.com")
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

# Конфигурация Google Gemini
# Очищаем API ключ от возможных невидимых символов и пробелов
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', '').strip()
GEMINI_DEFAULT_MODEL = os.getenv('GEMINI_MODEL', '').strip()
GEMINI_REASONING_EFFORT = os.getenv('GEMINI_REASONING_EFFORT', '').strip().lower()
GEMINI_CHUNK_MAX_CHARS = os.getenv('GEMINI_CHUNK_MAX_CHARS', '').strip()
GEMINI_TEMPERATURE_STR = os.getenv('GEMINI_TEMPERATURE', '0').strip()
try:
    GEMINI_TEMPERATURE = float(GEMINI_TEMPERATURE_STR)
except ValueError:
    print(f"⚠️ Неверное значение GEMINI_TEMPERATURE={GEMINI_TEMPERATURE_STR}, использую 0")
    GEMINI_TEMPERATURE = 0.0

ALLOWED_REASONING_EFFORTS = {'none', 'low', 'medium', 'high'}

if not load_google_api_keys():
    print("⚠️  ВНИМАНИЕ: Не найден ни один GOOGLE_API_KEY / GOOGLE_API_KEYN в private.txt!")

# Конфигурация фильтрации сообщений
MIN_MESSAGE_LENGTH = 3  # Минимальная длина сообщения (символов)
NOISE_PATTERNS = [
    r'^[\+\-\*]+$',  # +, -, *, ++, --
    r'^(ок|ok|лол|lol|хаха|haha|да|yes|нет|no)$',  # Односложные ответы
    r'^[\.\!\?]+$',  # Только знаки препинания
    r'^[👍👎👌✅❌🔥💪🎉😂😅]+$',  # Только эмодзи
]

# Базовая конфигурация разбиения на чанки (по символам)
# Используется как fallback для моделей без специальных настроек.
DEFAULT_CHUNK_MAX_CHARS = 60000
DEFAULT_CHUNK_OVERLAP_RATIO = 0.05
CHUNK_MAX_CHARS = DEFAULT_CHUNK_MAX_CHARS
CHUNK_OVERLAP_CHARS = int(DEFAULT_CHUNK_MAX_CHARS * DEFAULT_CHUNK_OVERLAP_RATIO)
CHUNK_DELAY_SECONDS = 10   # Задержка между запросами к API (для соблюдения RPM лимита)

def get_model_generation_config(model_name):
    """
    Возвращает параметры генерации и чанкования для выбранной модели.

    Модель продолжает задаваться через private.txt, а эта функция лишь
    подбирает безопасные дефолты и точечные overrides.
    """
    config = {
        'context_limit_tokens': 128000,
        'output_max_tokens': 10000,
        'reasoning_effort': None,
        'chunk_max_chars': DEFAULT_CHUNK_MAX_CHARS,
        'chunk_overlap_chars': int(DEFAULT_CHUNK_MAX_CHARS * DEFAULT_CHUNK_OVERLAP_RATIO),
    }

    if model_name in ('gemini-1.5-flash', 'gemini-1.5-flash-latest'):
        config['context_limit_tokens'] = 128000
    elif model_name in ('gemini-1.5-pro', 'gemini-1.5-pro-latest'):
        config['context_limit_tokens'] = 2000000
    elif model_name in ('gemini-2.0-flash', 'gemini-2.0-flash-lite'):
        config['context_limit_tokens'] = 1048576
    elif model_name == 'gemini-2.5-flash':
        config.update({
            'context_limit_tokens': 1048576,
            'output_max_tokens': 65536,
            'chunk_max_chars': 60000,
        })
        config['chunk_overlap_chars'] = int(config['chunk_max_chars'] * DEFAULT_CHUNK_OVERLAP_RATIO)

    if GEMINI_REASONING_EFFORT:
        if GEMINI_REASONING_EFFORT in ALLOWED_REASONING_EFFORTS:
            config['reasoning_effort'] = GEMINI_REASONING_EFFORT
        else:
            print(
                f"⚠️  Неверное значение GEMINI_REASONING_EFFORT: {GEMINI_REASONING_EFFORT}. "
                f"Допустимые значения: {', '.join(sorted(ALLOWED_REASONING_EFFORTS))}. "
                f"Использую значение по умолчанию для модели."
            )

    if GEMINI_CHUNK_MAX_CHARS:
        try:
            chunk_max_chars = int(GEMINI_CHUNK_MAX_CHARS)
            if chunk_max_chars <= 0:
                raise ValueError
            config['chunk_max_chars'] = chunk_max_chars
            config['chunk_overlap_chars'] = int(chunk_max_chars * DEFAULT_CHUNK_OVERLAP_RATIO)
        except ValueError:
            print(
                f"⚠️  Неверное значение GEMINI_CHUNK_MAX_CHARS: {GEMINI_CHUNK_MAX_CHARS}. "
                f"Ожидается положительное целое число. Использую значение по умолчанию для модели."
            )

    return config


def extract_api_error_message(error):
    """
    Извлекает поле error.message из ответа API, если оно доступно.
    Возвращает короткий человекочитаемый текст для логов и Telegram.
    """
    response = getattr(error, 'response', None)
    if response is None:
        return str(error)

    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, list) and payload:
        first_item = payload[0]
        if isinstance(first_item, dict):
            payload = first_item

    if isinstance(payload, dict):
        error_obj = payload.get('error')
        if isinstance(error_obj, dict):
            message = error_obj.get('message')
            if isinstance(message, str) and message.strip():
                return message.strip()

    text = getattr(response, 'text', None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    return str(error)


def is_quota_exceeded_error(error_message):
    """
    Проверяет, что ошибка связана с исчерпанием квоты Gemini.
    """
    if not error_message:
        return False

    error_lower = error_message.lower()
    return (
        'quota exceeded' in error_lower
        or 'resource_exhausted' in error_lower
        or 'generaterequestsperdayperprojectpermodel-freetier' in error_lower
    )


def trim_text_for_telegram(text, max_length=3500):
    """
    Ограничивает длину текста для безопасной отправки в Telegram.
    """
    if not text or len(text) <= max_length:
        return text

    return text[:max_length - 20].rstrip() + "\n... [обрезано]"


def should_rotate_key_for_error(error_message):
    """
    Ошибки, при которых есть смысл переключить API key и повторить запрос.
    """
    if not error_message:
        return False

    error_lower = error_message.lower()
    return (
        is_quota_exceeded_error(error_message)
        or 'invalid api key' in error_lower
        or 'api key not valid' in error_lower
        or 'permission denied' in error_lower
        or 'access denied' in error_lower
    )


async def execute_gemini_request(request_params):
    """
    Выполняет запрос к Gemini с retry при временных сбоях и ротацией ключей при ошибках квоты/доступа.
    """
    last_error = None
    attempts = max(1, len(GOOGLE_API_KEYS))
    max_retries_per_key = 3

    for attempt_idx in range(attempts):
        for retry in range(max_retries_per_key):
            try:
                return await google_client.chat.completions.create(**request_params)
            except Exception as e:
                last_error = e
                error_str = str(e)

                # Временные сбои сервера / лимиты — retry с задержкой на том же ключе
                is_retryable = any(
                    code in error_str for code in ('503', '429', 'UNAVAILABLE', 'RESOURCE_EXHAUSTED')
                ) or 'timeout' in error_str.lower()
                if is_retryable and retry < max_retries_per_key - 1:
                    delay = (retry + 1) * 10
                    print(f"   ⚠️  Сервер перегружен/таймаут. Повтор через {delay}с (попытка {retry + 2}/{max_retries_per_key})...")
                    await asyncio.sleep(delay)
                    continue

                # Ошибки аутентификации — ротация ключа
                if isinstance(e, AuthenticationError):
                    api_message = extract_api_error_message(e)
                    if attempt_idx < attempts - 1 and should_rotate_key_for_error(api_message):
                        rotate_google_api_key("ключ недоступен или невалиден, пробуем следующий")
                        break  # выходим из retry-цикла, пробуем следующий ключ
                    raise

                # Ошибки статуса API (квоты, доступ) — ротация ключа
                if isinstance(e, APIStatusError):
                    api_message = extract_api_error_message(e)
                    if attempt_idx < attempts - 1 and should_rotate_key_for_error(api_message):
                        rotate_google_api_key("квота/доступ исчерпаны, пробуем следующий ключ")
                        break  # выходим из retry-цикла, пробуем следующий ключ
                    raise

                raise

    if last_error:
        raise last_error

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


def update_env_value(filename, key, value):
    """
    Обновляет или добавляет переменную окружения в файле .env (private.txt)
    
    Args:
        filename: Путь к файлу
        key: Имя переменной
        value: Значение переменной
    """
    if not os.path.exists(filename):
        print(f"⚠️  Файл {filename} не найден")
        return False
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
        
        updated = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#') or '=' not in stripped:
                new_lines.append(line)
                continue
            
            current_key = stripped.split('=', 1)[0].strip()
            if current_key == key:
                new_lines.append(f"{key}={value}")
                updated = True
            else:
                new_lines.append(line)
        
        if not updated:
            if new_lines and new_lines[-1] != '':
                new_lines.append('')
            new_lines.append(f"{key}={value}")
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(new_lines) + '\n')
        
        return True
    except Exception as e:
        print(f"❌ Ошибка при обновлении {filename}: {e}")
        return False


def load_model_config(filename):
    """
    Загружает конфигурацию модели из файла
    
    Args:
        filename: Путь к файлу с конфигурацией модели
    
    Returns:
        Кортеж (model_name, use_reasoning, use_html_export)
    """
    default_model = GEMINI_DEFAULT_MODEL  # Модель из private.txt (GEMINI_MODEL)
    default_reasoning = False
    default_html_export = True  # По умолчанию используем HTML
    
    if not os.path.exists(filename):
        # Не выводим предупреждение, если модель уже задана в private.txt
        if not GEMINI_DEFAULT_MODEL:
            print(f"⚠️  Файл {filename} не найден и GEMINI_MODEL не задан в private.txt")
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
        
        # Если модель задана в private.txt, она имеет приоритет
        if GEMINI_DEFAULT_MODEL:
            model = GEMINI_DEFAULT_MODEL
        
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
            f.write("# Конфигурация модели Google Gemini (Google AI Studio)\n")
            f.write("# Автоматически обновлено ботом\n\n")
            f.write(f"# Модель указывается строкой (например, {GEMINI_DEFAULT_MODEL})\n")
            f.write("# Полный список моделей смотрите в Google AI Studio\n\n")
            f.write(f"MODEL={model}\n\n")
            f.write("# Использовать ли режим reasoning (может игнорироваться моделью)\n")
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
if GEMINI_DEFAULT_MODEL:
    CURRENT_MODEL = GEMINI_DEFAULT_MODEL

# Инициализация клиентов
telegram_client = TelegramClient('session_name', API_ID, API_HASH)
GOOGLE_API_KEYS = load_google_api_keys()
current_google_key_index = 0
google_analysis_counter = 0

# Валидация API ключа
print(f"🔑 Проверка ключей Google AI Studio:")
for idx, api_key in enumerate(GOOGLE_API_KEYS, 1):
    print(f"   • Ключ {idx}: {mask_api_key(api_key)} (длина {len(api_key)} символов)")
    try:
        api_key.encode('ascii')
        print("     ✅ API-ключ корректный (ASCII)")
    except UnicodeEncodeError:
        print("     ❌ ОШИБКА: API-ключ содержит недопустимые символы!")
        print("     Проверьте файл private.txt на наличие невидимых символов")
        exit(1)

print(f"   🔁 Всего ключей: {len(GOOGLE_API_KEYS)}")
print(f"   🔑 Стартовый активный ключ: {mask_api_key(GOOGLE_API_KEYS[0] if GOOGLE_API_KEYS else GOOGLE_API_KEY)}")

# Создаём асинхронный HTTP-клиент с настройками таймаута и лимитов соединений
http_client = httpx.AsyncClient(
    timeout=180.0,
    limits=httpx.Limits(
        max_keepalive_connections=5,
        max_connections=10
    )
)

def create_google_client(api_key):
    return AsyncOpenAI(
        api_key=api_key,
        base_url='https://generativelanguage.googleapis.com/v1beta/openai/',
        http_client=http_client,
        max_retries=2
    )
def get_current_google_api_key():
    if not GOOGLE_API_KEYS:
        return GOOGLE_API_KEY
    return GOOGLE_API_KEYS[current_google_key_index]


def set_google_api_key_index(index):
    global current_google_key_index, google_client
    if not GOOGLE_API_KEYS:
        return
    current_google_key_index = index % len(GOOGLE_API_KEYS)
    google_client = create_google_client(GOOGLE_API_KEYS[current_google_key_index])


def rotate_google_api_key(reason=None):
    """
    Переключает активный Google API key на следующий по кругу.
    """
    if not GOOGLE_API_KEYS:
        return None

    set_google_api_key_index(current_google_key_index + 1)
    active_key = get_current_google_api_key()
    if reason:
        print(f"🔄 Переключение Google API key: {reason}")
    print(f"   🔑 Активный ключ: {mask_api_key(active_key)} ({current_google_key_index + 1}/{len(GOOGLE_API_KEYS)})")
    return active_key


def select_google_api_key_for_new_analysis():
    """
    Выбирает ключ по кругу для нового запуска /sum.
    Первый анализ использует первый ключ, затем второй, третий и т.д.
    """
    global google_analysis_counter
    if not GOOGLE_API_KEYS:
        return None

    next_index = google_analysis_counter % len(GOOGLE_API_KEYS)
    google_analysis_counter += 1
    set_google_api_key_index(next_index)
    active_key = get_current_google_api_key()
    print("🔄 Выбор Google API key для нового анализа")
    print(f"   🔑 Активный ключ: {mask_api_key(active_key)} ({current_google_key_index + 1}/{len(GOOGLE_API_KEYS)})")
    return active_key


google_client = create_google_client(get_current_google_api_key())


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
            topic_id = None
            if hasattr(result, 'updates') and result.updates:
                if hasattr(result.updates[0], 'id'):
                    topic_id = result.updates[0].id
                else:
                    print(f"⚠️ Неожиданная струкция updates: {type(result.updates[0])}")
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


def get_sender_name(sender) -> str:
    """
    Извлекает имя отправителя из объекта sender.
    
    Args:
        sender: Объект отправителя от Telegram API
    
    Returns:
        Имя отправителя в виде строки
    """
    if sender is None:
        return "Unknown"
    
    if hasattr(sender, 'first_name'):
        name = sender.first_name or ""
        if hasattr(sender, 'last_name') and sender.last_name:
            name += f" {sender.last_name}"
        return name.strip() or "Unknown"
    
    if hasattr(sender, 'title'):
        return sender.title or "Unknown"
    
    return "Unknown"


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
        sender = msg.get('sender')
        unique_senders.add(sender)
        
        # Фильтруем исключенных пользователей
        if sender and sender in EXCLUDED_USERS:
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
        # Оптимизация: используем Counter для O(n) вместо O(n*m)
        sender_counts = Counter(msg['sender'] for msg in optimized)
        for priority_user in PRIORITY_USERS:
            if priority_user in unique_senders:
                count = sender_counts.get(priority_user, 0)
                print(f"   ✅ {priority_user}: найдено {count} сообщений")
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


async def collect_messages(chat_id, hours=None, days=None, limit=None, range_start=None, range_end=None, time_range_start=None, time_range_end=None):
    """
    Собирает сообщения из чата с догрузкой родительских сообщений для контекста
    
    Args:
        chat_id: ID чата для анализа
        hours: Количество часов назад (опционально)
        days: Количество дней назад (опционально)
        limit: Количество последних сообщений (опционально)
        range_start: Номер первого сообщения от конца, включительно (опционально)
        range_end: Номер последнего сообщения от конца, включительно (опционально)
        time_range_start: Ближняя граница диапазона времени от текущего момента (опционально)
        time_range_end: Дальняя граница диапазона времени от текущего момента (опционально)
    
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
    
    if time_range_start and time_range_end:
        # Режим: диапазон по времени от текущего момента.
        # Например 2d-3d означает сообщения старше 2 дней, но новее 3 дней.
        print(f"🔄 Загрузка сообщений за диапазон {time_range_start}-{time_range_end} назад...")
        now_utc = datetime.now(timezone.utc)
        newer_than = now_utc - time_range_end
        older_than = now_utc - time_range_start

        async for message in telegram_client.iter_messages(chat_id):
            msg_date = message.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            elif msg_date.tzinfo != timezone.utc:
                msg_date = msg_date.astimezone(timezone.utc)

            if msg_date < newer_than:
                break
            if msg_date > older_than:
                continue

            if message.text:
                sender = await message.get_sender()
                sender_name = get_sender_name(sender)

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
    elif range_start and range_end:
        # Режим: диапазон текстовых сообщений от конца чата.
        # Например 600-800 означает сообщения с 600-го по 800-е от newest к oldest.
        print(f"🔄 Загрузка сообщений диапазона {range_start}-{range_end} от конца...")
        text_position = 0
        async for message in telegram_client.iter_messages(chat_id):
            if not message.text:
                continue

            text_position += 1
            if text_position < range_start:
                continue
            if text_position > range_end:
                break

            sender = await message.get_sender()
            sender_name = get_sender_name(sender)

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
    elif limit:
        # Режим: последние N сообщений
        print(f"🔄 Загрузка последних {limit} сообщений...")
        count = 0
        async for message in telegram_client.iter_messages(chat_id):
            if count >= limit:
                break
            if message.text:
                sender = await message.get_sender()
                sender_name = get_sender_name(sender)
                
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
                sender_name = get_sender_name(sender)
                
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
    
    # Проверяем, есть ли сообщения перед доступом к messages_data[0]
    if not messages_data:
        print("⚠️ Нет текстовых сообщений за указанный период")
        return [], chat_id_str, ''
    
    # Сохраняем дату первого сообщения исходного периода (ДО догрузки родительских)
    period_start_date = messages_data[0].get('date', '')
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
                    sender_name = get_sender_name(sender)
                    
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


def plural_days(n):
    """Правильная форма слова 'день' для числа n: 1 день, 2 дня, 6 дней."""
    if 11 <= n % 100 <= 19:
        return "дней"
    last_digit = n % 10
    if last_digit == 1:
        return "день"
    if 2 <= last_digit <= 4:
        return "дня"
    return "дней"


def plural_hours(n):
    """Правильная форма слова 'час' для числа n: 1 час, 2 часа, 6 часов."""
    if 11 <= n % 100 <= 19:
        return "часов"
    last_digit = n % 10
    if last_digit == 1:
        return "час"
    if 2 <= last_digit <= 4:
        return "часа"
    return "часов"


def plural_messages(n):
    """Правильная форма слова 'сообщение' для числа n."""
    if 11 <= n % 100 <= 19:
        return "сообщений"
    last_digit = n % 10
    if last_digit == 1:
        return "сообщение"
    if 2 <= last_digit <= 4:
        return "сообщения"
    return "сообщений"


def format_period_text(period_hours):
    """Форматирует длительность периода с правильными склонениями."""
    if period_hours is None:
        return ""
    if period_hours < 24:
        return f"{period_hours} {plural_hours(period_hours)}"
    period_days = period_hours // 24
    remaining_hours = period_hours % 24
    if remaining_hours > 0:
        return f"{period_days} {plural_days(period_days)} {remaining_hours} {plural_hours(remaining_hours)}"
    return f"{period_days} {plural_days(period_days)}"


def format_timedelta_short(delta):
    """Форматирует timedelta для командного статуса: 2d, 12h или 1d6h."""
    total_seconds = int(delta.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600

    if days and hours:
        return f"{days}d{hours}h"
    if days:
        return f"{days}d"
    return f"{hours}h"


def build_processed_label(count, range_start=None, range_end=None, time_range_start=None, time_range_end=None):
    """
    Формирует подпись для статистики.
    Для явных диапазонов показывает исходный диапазон команды, а не только число после фильтрации.
    """
    if range_start and range_end:
        return f"{range_start}-{range_end} сообщений"
    if time_range_start and time_range_end:
        return f"{format_timedelta_short(time_range_start)}-{format_timedelta_short(time_range_end)} назад ({count} {plural_messages(count)})"
    return f"{count} {plural_messages(count)}"


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
    
    # Преобразуем плоский список сообщений с переименованием полей
    # sender → s, text → t, message_id → id, reply_to → r
    # Поле date исключаем из финального JSON
    flat_messages = []
    for msg in messages_data:
        flat_msg = {
            'id': msg['message_id'],
            's': msg['sender'],
            't': msg['text']
        }
        # Добавляем reply_to только если оно есть
        if msg.get('reply_to'):
            flat_msg['r'] = msg['reply_to']
        flat_messages.append(flat_msg)
    
    # Формируем metadata
    metadata = {
        'chat_id': safe_str(chat_id_str),
        'period_start': safe_str(period_start)
    }
    
    # Дополнительные поля для экспорта (/copy)
    if chat_name is not None:
        metadata['chat_name'] = chat_name
        metadata['export_date'] = datetime.now(MSK).strftime('%Y-%m-%d %H:%M:%S')
    if total_messages is not None:
        metadata['total_messages'] = total_messages
    if filtered_messages is not None:
        metadata['filtered_messages'] = filtered_messages
    
    return {
        'metadata': metadata,
        'messages': flat_messages
    }


def estimate_message_json_size(message):
    """
    Оценивает размер сообщения в JSON представлении.
    
    Args:
        message: Словарь с полями message_id, sender, text, reply_to
    
    Returns:
        Примерный размер в символах JSON-строки
    """
    # Базовая структура: {"id":X,"s":"...","t":"...","r":X}
    # +10% на экранирование кавычек и спецсимволов
    base_size = 25  # {"id":,"s":"","t":""}
    id_size = len(str(message.get('message_id', 0)))
    sender_size = int(len(message.get('sender', '')) * 1.1)  # +10% на Unicode
    text_size = int(len(message.get('text', '')) * 1.1)      # +10% на экранирование
    reply_size = 15 if message.get('reply_to') else 0       # ,"r":X
    
    return base_size + id_size + sender_size + text_size + reply_size


def split_messages_by_chars(messages_data, max_chars=CHUNK_MAX_CHARS, overlap_chars=CHUNK_OVERLAP_CHARS):
    """
    Разбивает список сообщений на чанки по количеству символов с перехлестом.
    
    Args:
        messages_data: Список сообщений
        max_chars: Максимальное количество символов в одном чанке
        overlap_chars: Минимальное количество символов для перехлеста
    
    Returns:
        Список кортежей: (chunk_messages, start_index, end_index)
        где start_index и end_index - индексы в исходном списке (1-based для отображения)
    
    Пример:
        - Чанк 1: [0:500] → сообщения 1-500 (≈85k символов)
        - Чанк 2: [450:950] → сообщения 451-950 (перехлёст 50 сообщений)
    """
    chunks = []
    current_chunk = []
    current_size = 0
    chunk_start_index = 0
    
    for idx, msg in enumerate(messages_data):
        msg_size = estimate_message_json_size(msg)
        
        # Если сообщение превышает лимит одного чанка - обрезаем текст
        if msg_size > max_chars:
            # Обрезаем текст сообщения до максимально допустимого размера
            msg = msg.copy()
            max_text_size = max_chars - 50  # оставляем место для остальных полей
            if len(msg.get('text', '')) > max_text_size:
                msg['text'] = msg['text'][:max_text_size] + '... [обрезано]'
            msg_size = estimate_message_json_size(msg)
        
        # Проверяем, влезет ли в текущий чанк
        if current_size + msg_size > max_chars and current_chunk:
            # Сохраняем текущий чанк
            chunks.append((current_chunk, chunk_start_index + 1, idx))
            
            # Рассчитываем перехлёст
            overlap_messages = []
            overlap_size = 0
            
            # Идём с конца текущего чанка назад
            for prev_msg in reversed(current_chunk):
                prev_size = estimate_message_json_size(prev_msg)
                
                overlap_messages.append(prev_msg)
                overlap_size += prev_size
                if overlap_size >= overlap_chars:
                    break
            
            # Разворачиваем список (быстрее чем insert(0) в цикле)
            overlap_messages.reverse()
            
            # Начинаем новый чанк с перехлёста
            current_chunk = overlap_messages + [msg]
            current_size = overlap_size + msg_size
            chunk_start_index = idx - len(overlap_messages)
        else:
            current_chunk.append(msg)
            current_size += msg_size
    
    # Добавляем последний чанк
    if current_chunk:
        chunks.append((current_chunk, chunk_start_index + 1, len(messages_data)))
    
    return chunks


def estimate_chunk_request_chars(chunk_messages):
    """
    Грубая оценка размера одного AI-запроса в символах.
    Нужна только для пользовательской оценки времени ожидания.
    """
    messages_chars = sum(estimate_message_json_size(msg) for msg in chunk_messages)
    prompt_chars = len(ANALYSIS_PROMPT) if ANALYSIS_PROMPT else 0
    # Запас на JSON-обертку, system/user роли и служебные поля.
    overhead_chars = 2500
    return prompt_chars + messages_chars + overhead_chars


def estimate_total_ai_processing_seconds(chunks, use_ai=True, use_html_export=True):
    """
    Консервативная оценка полного времени анализа:
    AI-обработка чанков + паузы между чанками + публикация результатов.
    """
    if not use_ai or not chunks:
        return 0

    ai_seconds = 0
    for chunk_messages, _, _ in chunks:
        request_chars = estimate_chunk_request_chars(chunk_messages)
        # Консервативная эвристика: большие запросы к Gemini часто занимают
        # десятки секунд и больше, поэтому берем более реалистичную оценку,
        # чем просто паузы между чанками.
        ai_seconds += max(45, request_chars // 2000)

    chunk_pause_seconds = max(0, len(chunks) - 1) * CHUNK_DELAY_SECONDS
    publish_pause_seconds = max(0, len(chunks) - 1) * 4
    publish_overhead_seconds = 10 if use_html_export else 5

    return ai_seconds + chunk_pause_seconds + publish_pause_seconds + publish_overhead_seconds


def is_valid_summary(text):
    """
    Проверяет, что ответ содержит саммари, а не сырой JSON.
    
    Args:
        text: Текст ответа от API
    
    Returns:
        True если ответ валидный (содержит текстовое саммари), False если это JSON
    """
    if not text:
        return False
    
    text_stripped = text.strip()
    
    # Пытаемся распарсить как JSON
    if text_stripped.startswith('{'):
        try:
            json.loads(text_stripped)
            return False  # Это валидный JSON - значит саммари нет
        except json.JSONDecodeError:
            pass  # Не валидный JSON, значит это текст
    
    # Проверка на markdown code block с JSON
    if text_stripped.startswith('```json') or text_stripped.startswith('```'):
        lines = text_stripped.split('\n')
        if len(lines) > 1:
            content = '\n'.join(lines[1:]).strip()
            if content.startswith('{'):
                try:
                    json.loads(content)
                    return False
                except json.JSONDecodeError:
                    pass
    
    return True


def clean_summary_response(text):
    """
    Очищает ответ от markdown code blocks и конвертирует JSON в читаемый текст.
    Используется как финальная защита, когда AI всё равно возвращает JSON.
    
    Args:
        text: Текст ответа от API (возможно с markdown code blocks и JSON)
    
    Returns:
        Очищенный текстовый саммари
    """
    if not text:
        return text
    
    text_stripped = text.strip()
    
    # Извлекаем содержимое из markdown code block
    if text_stripped.startswith('```'):
        lines = text_stripped.split('\n')
        # Убираем первую строку (```json или ```)
        if len(lines) > 1:
            content_lines = []
            for i, line in enumerate(lines[1:], 1):
                # Нашли закрывающий ```
                if line.strip() == '```':
                    content_lines = lines[1:i]
                    break
            if content_lines:
                text_stripped = '\n'.join(content_lines).strip()
    
    # Пробуем распарсить как JSON и сконвертировать в текст
    if text_stripped.startswith('{'):
        try:
            data = json.loads(text_stripped)
            # Конвертируем JSON структуру в читаемый текст
            parts = []
            
            # Если есть topic и summary на верхнем уровне
            if isinstance(data, dict):
                if 'topic' in data and 'summary' in data:
                    parts.append(f"💡 {data['topic']}")
                    parts.append("")
                    parts.append(data['summary'])
                    if 'messages' in data and isinstance(data['messages'], list):
                        parts.append("")
                        parts.append("**Сообщения:**")
                        for msg in data['messages']:
                            if isinstance(msg, dict):
                                author = msg.get('author', 'Unknown')
                                text = msg.get('text', '')
                                parts.append(f"- **{author}**: {text[:200]}{'...' if len(text) > 200 else ''}")
                else:
                    # Другая структура JSON - просто форматируем ключ-значение
                    for key, value in data.items():
                        if isinstance(value, str):
                            parts.append(f"**{key}**: {value}")
                        elif isinstance(value, list) and len(value) > 0:
                            parts.append(f"**{key}**:")
                            for item in value[:5]:  # Ограничиваем 5 элементами
                                if isinstance(item, dict):
                                    item_text = ', '.join(f"{k}: {v}" for k, v in list(item.items())[:2])
                                    parts.append(f"  - {item_text}")
                        else:
                            parts.append(f"**{key}**: {str(value)[:100]}")
            
            return '\n'.join(parts) if parts else text_stripped
        except json.JSONDecodeError:
            pass  # Не валидный JSON, возвращаем как есть
    
    return text_stripped


def split_summary_into_parts(summary_text):
    """
    Разбирает объединённый саммари на отдельные части для публикации в Telegraph.
    
    Args:
        summary_text: Полный текст саммари (может содержать несколько частей)
    
    Returns:
        Список кортежей: (part_title, part_content, start_idx, end_idx)
        Если саммари не содержит частей, возвращает [(None, summary_text, None, None)]
    """
    # Проверяем, содержит ли саммари несколько частей
    # Формат: "ЧАСТЬ N (сообщения X-Y):"
    if "═══════════════════════════════════════" not in summary_text or "ЧАСТЬ " not in summary_text:
        # Обычный саммари без чанков
        return [(None, summary_text, None, None)]
    
    parts = []
    
    # Ищем все части с помощью регулярного выражения
    # Паттерн: разделитель + ЧАСТЬ N (сообщения X-Y): + разделитель + контент
    pattern = r'═+\nЧАСТЬ (\d+) \(сообщения (\d+)-(\d+)\):.*?\n═+\n(.*?)(?=\n═+\nЧАСТЬ |\Z)'
    
    matches = re.findall(pattern, summary_text, re.DOTALL)
    
    if not matches:
        # Не удалось распарсить - возвращаем как есть
        return [(None, summary_text, None, None)]
    
    for match in matches:
        part_num = int(match[0])
        start_idx = int(match[1])
        end_idx = int(match[2])
        content = match[3].strip()
        
        part_title = f"Часть {part_num}"
        parts.append((part_title, content, start_idx, end_idx))
    
    return parts


async def create_summary(chunks, chat_id_str, model=None, use_reasoning=False, period_start_date=None):
    """
    Создает выжимку из сообщений с помощью Google Gemini.
    Использует предварительно разбитые на чанки сообщения.
    
    Args:
        chunks: Список кортежей (chunk_messages, start_index, end_index)
        chat_id_str: ID чата для ссылок
        model: Название модели (например, значение из GEMINI_MODEL). Если None, используется GEMINI_DEFAULT_MODEL
        use_reasoning: Использовать ли reasoning режим (для моделей с поддержкой)
        period_start_date: Дата начала периода для метаданных
    
    Returns:
        Кортеж (текст выжимки, информация об использовании токенов)
    """
    if not chunks:
        return "❌ Нет сообщений для анализа за указанный период (все отфильтровано)", None
    
    # Используем переданную модель или модель по умолчанию
    actual_model = model or GEMINI_DEFAULT_MODEL
    if not actual_model:
        return "❌ В private.txt не задана переменная GEMINI_MODEL", None
    model_config = get_model_generation_config(actual_model)
    output_max_tokens = model_config['output_max_tokens']
    reasoning_effort = model_config['reasoning_effort']
    chunk_max_chars = model_config['chunk_max_chars']
    chunk_overlap_chars = model_config['chunk_overlap_chars']

    total_messages = sum(len(c[0]) for c in chunks)
    num_chunks = len(chunks)
    
    print(f"🤖 Отправка {total_messages} сообщений в Google Gemini для анализа...")
    if use_reasoning:
        print(f"   🧠 Reasoning режим не поддерживается, используем: {actual_model}")
    else:
        print(f"   ⚡ Используем стандартную модель: {actual_model}")
    
    max_tokens = model_config['context_limit_tokens']
    max_chars = int(max_tokens * 2.5 * 0.8)  # Для кириллицы с запасом 20%

    print(f"   🤖 Модель: {actual_model}")
    print(f"   📊 Лимит контекста: {max_tokens:,} токенов ({max_chars:,} символов для кириллицы)")
    print(f"   ✍️ Лимит генерации ответа: {output_max_tokens:,} токенов")
    if reasoning_effort:
        print(f"   🧠 Thinking: {reasoning_effort}")
    print(f"   📦 Лимит входа на чанк: {chunk_max_chars:,} символов")
    
    # Подготовка промпта с приоритетными пользователями
    # Извлекаем уникальные имена отправителей из всех чанков
    actual_senders = set()
    for chunk_messages, _, _ in chunks:
        for msg in chunk_messages:
            actual_senders.add(msg.get('sender', ''))
    
    # Фильтруем PRIORITY_USERS - оставляем только тех, кто есть в сообщениях
    relevant_priority_users = [user for user in PRIORITY_USERS if user in actual_senders]
    
    prompt_with_priority = ANALYSIS_PROMPT
    if relevant_priority_users:
        priority_list = ', '.join(relevant_priority_users)
        prompt_with_priority = prompt_with_priority.replace('{PRIORITY_USERS}', priority_list)
        skipped = len(PRIORITY_USERS) - len(relevant_priority_users)
        if skipped > 0:
            print(f"   👥 Приоритетные пользователи: {priority_list} (скрыто {skipped} отсутствующих в чате)")
        else:
            print(f"   👥 Приоритетные пользователи: {priority_list}")
    else:
        if PRIORITY_USERS:
            prompt_with_priority = prompt_with_priority.replace('{PRIORITY_USERS}', 'приоритетных пользователей (нет в текущем чате)')
            print(f"   👥 Приоритетные пользователи: нет в текущем чате (из {len(PRIORITY_USERS)} заданных)")
        else:
            prompt_with_priority = prompt_with_priority.replace('{PRIORITY_USERS}', 'приоритетных пользователей (не заданы)')
            print(f"   👥 Приоритетные пользователи: не заданы")
    system_content = safe_str(prompt_with_priority)
    
    # Добавляем критические инструкции для предотвращения JSON-ответов
    system_content += "\n\n⚠️ CRITICAL: Return ONLY plain text summary. NEVER return JSON. NEVER use code blocks (```). NEVER echo input JSON structure."
    
    # Проверяем, нужно ли разбивать на чанки
    if num_chunks > 1:
        # ═══════════════════════════════════════════════════════════════
        # РЕЖИМ ЧАНКОВ: работаем с переданными чанками
        # ═══════════════════════════════════════════════════════════════
        print(f"📦 Разбиение на {num_chunks} чанков (по символам, max={chunk_max_chars}, перехлест={chunk_overlap_chars})")
        
        chunk_summaries = []  # Список кортежей: (start_idx, end_idx, summary_text, is_error)
        total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'errors': []
        }
        errors_count = 0
        stop_due_to_quota = False
        
        for chunk_idx, (chunk_messages, start_idx, end_idx) in enumerate(chunks, 1):
            if stop_due_to_quota:
                skipped_msg = "⚠️ Чанк пропущен: обработка остановлена после исчерпания квоты Gemini API"
                chunk_summaries.append((start_idx, end_idx, skipped_msg, True))
                errors_count += 1
                continue

            chunk_size = len(chunk_messages)
            print(f"\n📦 Обработка чанка {chunk_idx} из {num_chunks} ({chunk_size} сообщений: {start_idx}-{end_idx})")
            
            # Формируем JSON для текущего чанка
            chunk_period_start = chunk_messages[0].get('date', '') if chunk_messages else period_start_date
            optimized_structure = build_optimized_json_structure(
                chunk_messages, chat_id_str, period_start_date=chunk_period_start
            )
            messages_json = json.dumps(optimized_structure, ensure_ascii=False)
            
            # Примечание: Размер чанка уже проверен в split_messages_by_chars()
            # Эта проверка здесь на всякий случай
            if len(messages_json) > max_chars:
                print(f"   ⚠️  Чанк слишком большой ({len(messages_json)} символов), используем ограничение")
            
            user_content = safe_str(f'Данные сообщений для анализа (JSON):\n\n{messages_json}')
            
            # Санитизация: удаляем NULL bytes которые могут быть проблемой
            user_content = user_content.replace('\x00', '')
            
            request_params = {
                'model': actual_model,
                'messages': [
                    {'role': 'system', 'content': system_content},
                    {'role': 'user', 'content': user_content}
                ],
                'temperature': GEMINI_TEMPERATURE,
                'max_tokens': output_max_tokens
            }

            if reasoning_effort:
                request_params['reasoning_effort'] = reasoning_effort
            
            total_chars = len(system_content) + len(user_content)
            print(f"   📊 Размер запроса: {total_chars:,} символов")
            
            # Отправляем запрос
            try:
                response = await execute_gemini_request(request_params)
                
                chunk_summary = response.choices[0].message.content
                
                # Проверяем, что ответ содержит саммари, а не JSON
                if not is_valid_summary(chunk_summary):
                    print(f"   ⚠️  Ответ содержит JSON вместо текста, повторяем с усиленным промптом...")
                    # Добавляем усиленную инструкцию в промпт
                    enhanced_system = system_content + "\n\nВАЖНО: Верни ТОЛЬКО текстовое саммари в виде обычного текста. НЕ возвращай JSON, НЕ используй кодовые блоки ```json. Пиши непосредственно текст."
                    request_params['messages'] = [
                        {'role': 'system', 'content': enhanced_system},
                        {'role': 'user', 'content': user_content}
                    ]
                    
                    # Retry с усиленным промптом
                    retry_valid_count = 0
                    max_valid_retries = 2
                    validation_passed = False
                    while retry_valid_count <= max_valid_retries:
                        try:
                            response = await execute_gemini_request(request_params)
                            chunk_summary = response.choices[0].message.content
                            if is_valid_summary(chunk_summary):
                                print(f"   ✅ Валидация пройдена после retry {retry_valid_count}")
                                validation_passed = True
                                break
                            retry_valid_count += 1
                            print(f"   ⚠️  Повторная попытка валидации {retry_valid_count}/{max_valid_retries}...")
                        except Exception as retry_valid_error:
                            print(f"   ⚠️  Ошибка при retry: {retry_valid_error}")
                            break
                    
                    # Если валидация так и не прошла - применяем очистку
                    if not validation_passed:
                        print(f"   ⚠️  Все retry исчерпаны, применяем очистку ответа...")
                        chunk_summary = clean_summary_response(chunk_summary)
                        print(f"   ✅ Ответ очищен и сконвертирован в текст")
                
                print(f"   ✅ Чанк {chunk_idx} обработан успешно")
                
                # Собираем статистику токенов
                if hasattr(response, 'usage'):
                    usage = response.usage
                    prompt_tokens = usage.prompt_tokens if hasattr(usage, 'prompt_tokens') else 0
                    completion_tokens = usage.completion_tokens if hasattr(usage, 'completion_tokens') else 0
                    chunk_total = usage.total_tokens if hasattr(usage, 'total_tokens') else 0
                    
                    total_usage['prompt_tokens'] += prompt_tokens
                    total_usage['completion_tokens'] += completion_tokens
                    total_usage['total_tokens'] += chunk_total
                    
                    print(f"   📊 Токенов в чанке: {chunk_total:,}")
                
                chunk_summaries.append((start_idx, end_idx, chunk_summary, False))
                
            except AuthenticationError as e:
                api_message = extract_api_error_message(e)
                error_msg = f"❌ Ошибка доступа к API: {api_message}"
                print(f"   {error_msg}")
                chunk_summaries.append((start_idx, end_idx, error_msg, True))
                total_usage['errors'].append(error_msg)
                errors_count += 1
                
            except APIStatusError as e:
                status_code = getattr(e, 'status_code', 'неизвестен')
                api_message = extract_api_error_message(e)
                error_msg = f"❌ Ошибка API (HTTP {status_code}): {api_message}"
                print(f"   {error_msg}")
                chunk_summaries.append((start_idx, end_idx, error_msg, True))
                total_usage['errors'].append(error_msg)
                errors_count += 1

                if is_quota_exceeded_error(api_message):
                    stop_due_to_quota = True
                    print("   ⛔ Обработка следующих чанков остановлена: исчерпана квота Gemini API")
                
            except Exception as e:
                error_msg = f"❌ Ошибка: {type(e).__name__}: {e}"
                print(f"   {error_msg}")
                chunk_summaries.append((start_idx, end_idx, error_msg, True))
                total_usage['errors'].append(error_msg)
                errors_count += 1
            
            # Пауза между запросами (кроме последнего чанка)
            if chunk_idx < num_chunks:
                print(f"   ⏳ Пауза {CHUNK_DELAY_SECONDS} секунд перед следующим чанком...")
                await asyncio.sleep(CHUNK_DELAY_SECONDS)
        
        # ═══════════════════════════════════════════════════════════════
        # Объединение саммари всех чанков
        # ═══════════════════════════════════════════════════════════════
        print(f"\n✅ Обработано {num_chunks} чанков, всего токенов: {total_usage['total_tokens']:,}")
        if errors_count > 0:
            print(f"   ⚠️  Ошибок при обработке: {errors_count}")
        
        # Формируем объединенный текст
        combined_parts = []
        combined_parts.append(f"📊 Обработано {total_messages} сообщений в {num_chunks} частях")
        if errors_count > 0:
            combined_parts.append(f"⚠️ Внимание: {errors_count} из {num_chunks} частей обработаны с ошибками")
        combined_parts.append("")
        
        for part_idx, (start_idx, end_idx, summary_text, is_error) in enumerate(chunk_summaries, 1):
            combined_parts.append("═══════════════════════════════════════")
            if is_error:
                combined_parts.append(f"ЧАСТЬ {part_idx} (сообщения {start_idx}-{end_idx}): ⚠️ ОШИБКА")
            else:
                combined_parts.append(f"ЧАСТЬ {part_idx} (сообщения {start_idx}-{end_idx}):")
            combined_parts.append("═══════════════════════════════════════")
            combined_parts.append(summary_text)
            combined_parts.append("")
        
        combined_summary = "\n".join(combined_parts)
        
        return combined_summary, total_usage
    
    # ═══════════════════════════════════════════════════════════════════
    # ОБЫЧНЫЙ РЕЖИМ: все сообщения в одном запросе
    # ═══════════════════════════════════════════════════════════════════
    
    chunk_messages, start_idx, end_idx = chunks[0]
    
    # Формируем JSON
    optimized_structure = build_optimized_json_structure(chunk_messages, chat_id_str, period_start_date=period_start_date)
    messages_json = json.dumps(optimized_structure, ensure_ascii=False)
    
    # Проверяем размер и при необходимости ограничиваем
    if len(messages_json) > max_chars:
        print(f"⚠️  Данных слишком много ({len(messages_json)} символов)")
        print(f"   Максимум для модели {actual_model}: {max_chars} символов")
        
        # Защита от деления на ноль и слишком маленьких данных
        if len(messages_json) < 100:
            print("⚠️ Слишком мало данных для анализа")
            return "❌ Недостаточно данных для анализа", None
        
        ratio = max_chars / len(messages_json)
        limit = int(total_messages * ratio * 0.95)
        
        print(f"   📌 Решение: Берем последние {limit} сообщений (самые актуальные)")
        print(f"   ⚠️  ПОТЕРЯ ДАННЫХ: {total_messages - limit} старых сообщений не попадут в анализ")
        print(f"   💡 Рекомендация: уменьшите период анализа (например /analyze 12h вместо 24h)")
        
        chunk_messages_limited = chunk_messages[-limit:]
        period_start_limited = chunk_messages_limited[0].get('date', '') if chunk_messages_limited else period_start_date
        optimized_structure = build_optimized_json_structure(chunk_messages_limited, chat_id_str, period_start_date=period_start_limited)
        messages_json = json.dumps(optimized_structure, ensure_ascii=False)
    
    try:
        user_content = safe_str(f'Данные сообщений для анализа (JSON):\n\n{messages_json}')
        
        # Санитизация: удаляем NULL bytes которые могут быть проблемой
        user_content = user_content.replace('\x00', '')
        
        request_params = {
            'model': actual_model,
            'messages': [
                {'role': 'system', 'content': system_content},
                {'role': 'user', 'content': user_content}
            ],
            'temperature': GEMINI_TEMPERATURE,
            'max_tokens': output_max_tokens
        }

        if reasoning_effort:
            request_params['reasoning_effort'] = reasoning_effort
        
        total_chars = len(system_content) + len(user_content)
        print(f"   📊 Размер запроса: {total_chars:,} символов")
        
        estimated_time = max(30, total_chars // 500)
        if estimated_time > 60:
            print(f"   ⏱️  Ожидаемое время обработки: ~{estimated_time} сек")
            print(f"   ⏳ Пожалуйста, подождите...")
        
        # Отправляем запрос
        response = await execute_gemini_request(request_params)
        
        summary = response.choices[0].message.content
        
        # Проверяем, что ответ содержит саммари, а не JSON
        if not is_valid_summary(summary):
            print("   ⚠️  Ответ содержит JSON вместо текста, применяем очистку...")
            summary = clean_summary_response(summary)
            print("   ✅ Ответ очищен и сконвертирован в текст")
        
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
        
    except AuthenticationError as e:
        error_msg = "❌ Ошибка доступа к Google AI Studio: ключ недействителен или доступ ограничен."
        print(error_msg)
        print(f"   Модель: {actual_model}")
        print(f"   Размер данных: {len(messages_json)} символов")
        print(f"   Тип ошибки: {type(e).__name__}")
        
        # Подробный traceback для отладки
        import traceback
        print("   Подробная трассировка:")
        traceback.print_exc()
        
        return error_msg, None
    except APIStatusError as e:
        status_code = getattr(e, 'status_code', None)
        if status_code == 401:
            error_msg = "❌ Ошибка доступа к Google AI Studio: ключ недействителен или доступ ограничен."
        elif status_code == 402:
            error_msg = "❌ Ошибка оплаты или лимитов Google AI Studio."
        else:
            code_text = status_code if status_code is not None else "неизвестен"
            error_msg = f"❌ Ошибка Google AI Studio (HTTP {code_text})."
        
        print(error_msg)
        print(f"   Модель: {actual_model}")
        print(f"   Размер данных: {len(messages_json)} символов")
        print(f"   Тип ошибки: {type(e).__name__}")
        if status_code is not None:
            print(f"   HTTP статус: {status_code}")
        
        # Подробный traceback для отладки
        import traceback
        print("   Подробная трассировка:")
        traceback.print_exc()
        
        return error_msg, None
    except Exception as e:
        error_msg = f"❌ Ошибка при создании выжимки: {e}"
        print(error_msg)
        print(f"   Модель: {actual_model}")
        print(f"   Размер данных: {len(messages_json)} символов")
        print(f"   Тип ошибки: {type(e).__name__}")
        
        # Подробный traceback для отладки
        import traceback
        print("   Подробная трассировка:")
        traceback.print_exc()
        
        return error_msg, None
def enrich_summary_with_timestamps(summary_text, messages_data):
    msg_date_map = {}
    for msg in messages_data:
        mid = msg.get('message_id')
        if mid is not None:
            msg_date_map[mid] = msg.get('date', '')

    if not msg_date_map:
        return summary_text

    parts = summary_text.split('\n💡')
    if len(parts) <= 1:
        return summary_text

    enriched = [parts[0]]

    for block in parts[1:]:
        block = '💡' + block

        link_match = re.search(r'https://t\.me/c/\d+/(\d+)', block)
        if not link_match:
            enriched.append(block)
            continue

        message_id = int(link_match.group(1))
        date_str = msg_date_map.get(message_id, '')
        if not date_str:
            enriched.append(block)
            continue

        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
            dt = dt.replace(tzinfo=timezone.utc).astimezone(MSK)
            formatted_date = dt.strftime('%d.%m %H:%M')
        except ValueError:
            enriched.append(block)
            continue

        timestamp_line = f'\n- *{formatted_date}* -\n'

        link_pos = link_match.start()
        newline_before_link = block.rfind('\n', 0, link_pos)
        if newline_before_link == -1:
            enriched.append(block)
            continue

        enriched_block = block[:newline_before_link] + timestamp_line + block[newline_before_link:]
        enriched.append(enriched_block)

    return '\n💡'.join([enriched[0]] + [b[1:] for b in enriched[1:]])


def extract_summary_time_range(summary_text):
    matches = re.findall(r'^- \*(\d{2}\.\d{2} \d{2}:\d{2})\* -$', summary_text, re.MULTILINE)
    if len(matches) >= 1:
        return matches[0], matches[-1]
    return None, None


# Разрешенные теги Telegraph (whitelist)
TELEGRAPH_ALLOWED_TAGS = {
    'a', 'aside', 'b', 'blockquote', 'br', 'code', 'em', 'figure', 'figcaption',
    'h3', 'h4', 'hr', 'i', 'iframe', 'img', 'li', 'ol', 'p', 'pre', 's',
    'strong', 'u', 'ul', 'video'
}


def fix_html_nesting(html_content):
    """
    Исправляет вложенность HTML-тегов, обеспечивая правильный порядок закрытия.
    Использует стек для отслеживания открытых тегов.
    
    Args:
        html_content: HTML строка
    
    Returns:
        HTML строка с исправленной вложенностью
    """
    if not html_content:
        return html_content
    
    # Паттерн для поиска HTML тегов (открывающих и закрывающих)
    tag_pattern = re.compile(r'<(/?)(\w+)[^>]*>')
    
    # Void-элементы не требуют закрывающего тега и не должны попадать в стек
    VOID_ELEMENTS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    
    # Стек для отслеживания открытых тегов
    open_tags = []
    result_parts = []
    last_end = 0
    
    for match in tag_pattern.finditer(html_content):
        # Добавляем текст перед тегом
        result_parts.append(html_content[last_end:match.start()])
        
        closing_slash = match.group(1)  # '/' для закрывающего тега, '' для открывающего
        tag_name = match.group(2).lower()  # имя тега
        
        if closing_slash:  # Закрывающий тег
            # Void-элементы не должны были попасть в стек — пропускаем их закрывающий тег
            if tag_name in VOID_ELEMENTS:
                pass
            elif open_tags and open_tags[-1] == tag_name:
                open_tags.pop()
                result_parts.append(match.group(0))  # Оставляем тег как есть
            else:
                # Неправильное закрытие - либо пропускаем, либо пытаемся исправить
                # Простейший подход: пропускаем этот неправильный закрывающий тег
                pass
        else:  # Открывающий тег
            result_parts.append(match.group(0))  # Оставляем тег как есть
            if tag_name not in VOID_ELEMENTS:
                open_tags.append(tag_name)
        
        last_end = match.end()
    
    # Добавляем оставшийся текст после последнего тега
    result_parts.append(html_content[last_end:])
    
    # Закрываем все незакрытые теги в обратном порядке (void-элементов в стеке нет)
    while open_tags:
        tag_name = open_tags.pop()
        result_parts.append(f'</{tag_name}>')
    
    return ''.join(result_parts)


def sanitize_html_for_telegraph(html_content):
    """
    Удаляет HTML-теги, не разрешенные Telegraph API.
    
    Args:
        html_content: HTML строка
    
    Returns:
        HTML строка только с разрешенными тегами
    """
    if not html_content:
        return html_content
    
    # Паттерн для поиска HTML тегов
    tag_pattern = re.compile(r'<(/?)(\w+)[^>]*>')
    
    def replace_tag(match):
        closing = match.group(1)  # '/' или ''
        tag = match.group(2).lower()  # имя тега
        
        if tag in TELEGRAPH_ALLOWED_TAGS:
            return match.group(0)  # Оставляем тег как есть
        else:
            return ''  # Удаляем неразрешенный тег
    
    return tag_pattern.sub(replace_tag, html_content)


def convert_markdown_to_html(content):
    """
    Конвертирует Markdown текст в HTML.
    Общая функция для publish_to_telegraph и create_html_report.
    
    Args:
        content: Markdown текст
    
    Returns:
        HTML текст
    """
    # Экранируем HTML-спецсимволы, чтобы предотвратить поломку вёрстки
    # из-за ников пользователей с символами < > & (например, sprintf(username, "id%04d", 1<<9))
    content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

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
                para_text = MD_BOLD_RE.sub(r'<b>\1</b>', para_text)
                para_text = MD_ITALIC_RE.sub(r'<i>\1</i>', para_text)
                para_text = MD_LINK_RE.sub(r'<a href="\2">\1</a>', para_text)
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
                para_text = MD_BOLD_RE.sub(r'<b>\1</b>', para_text)
                para_text = MD_ITALIC_RE.sub(r'<i>\1</i>', para_text)
                para_text = MD_LINK_RE.sub(r'<a href="\2">\1</a>', para_text)
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
                para_text = MD_BOLD_RE.sub(r'<b>\1</b>', para_text)
                para_text = MD_ITALIC_RE.sub(r'<i>\1</i>', para_text)
                para_text = MD_LINK_RE.sub(r'<a href="\2">\1</a>', para_text)
                html_paragraphs.append(f'<p>{para_text}</p>')
                current_paragraph = []
            if in_list:
                html_paragraphs.append('</ul>')
                in_list = False
            text = line_stripped
            text = MD_BOLD_RE.sub(r'<b>\1</b>', text)
            text = MD_ITALIC_RE.sub(r'<i>\1</i>', text)
            html_paragraphs.append(f'<h3>{text}</h3>')
            continue
        # Строка с датой/временем (центрированный курсив)
        ts_match = re.match(r'^-\s*\*(\d{2}\.\d{2}\s+\d{2}:\d{2})\*\s*-$', line_stripped)
        if ts_match:
            if current_paragraph:
                para_text = '<br>'.join(current_paragraph)
                para_text = MD_BOLD_RE.sub(r'<b>\1</b>', para_text)
                para_text = MD_ITALIC_RE.sub(r'<i>\1</i>', para_text)
                para_text = MD_LINK_RE.sub(r'<a href="\2">\1</a>', para_text)
                html_paragraphs.append(f'<p>{para_text}</p>')
                current_paragraph = []
            if in_list:
                html_paragraphs.append('</ul>')
                in_list = False
            html_paragraphs.append(f'<p align="center"><i>\u2014 {ts_match.group(1)} \u2014</i></p>')
            continue

        # Строка с диапазоном времени (центрированный курсив в скобках)
        tr_match = re.match(r'^\*\((\d{2}\.\d{2} \d{2}:\d{2} - \d{2}\.\d{2} \d{2}:\d{2})\)\*$', line_stripped)
        if tr_match:
            if current_paragraph:
                para_text = '<br>'.join(current_paragraph)
                para_text = MD_BOLD_RE.sub(r'<b>\1</b>', para_text)
                para_text = MD_ITALIC_RE.sub(r'<i>\1</i>', para_text)
                para_text = MD_LINK_RE.sub(r'<a href="\2">\1</a>', para_text)
                html_paragraphs.append(f'<p>{para_text}</p>')
                current_paragraph = []
            if in_list:
                html_paragraphs.append('</ul>')
                in_list = False
            html_paragraphs.append(f'<p align="center"><i>({tr_match.group(1)})</i></p>')
            continue

        # Пункт списка (может быть - или • или *)
        if line_stripped.startswith('- ') or line_stripped.startswith('* ') or line_stripped.startswith('• '):
            if current_paragraph:
                para_text = '<br>'.join(current_paragraph)
                para_text = MD_BOLD_RE.sub(r'<b>\1</b>', para_text)
                para_text = MD_ITALIC_RE.sub(r'<i>\1</i>', para_text)
                para_text = MD_LINK_RE.sub(r'<a href="\2">\1</a>', para_text)
                html_paragraphs.append(f'<p>{para_text}</p>')
                current_paragraph = []
            if not in_list:
                html_paragraphs.append('<ul>')
                in_list = True
            text = line_stripped.lstrip('- *•').strip()
            text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
            text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
            text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', text)
            html_paragraphs.append(f'<li>{text}</li>')
            continue
        
        # Обычная строка - добавляем к текущему параграфу
        if in_list:
            html_paragraphs.append('</ul>')
            in_list = False
        current_paragraph.append(line_stripped)
    
    # Завершаем последний параграф
    if current_paragraph:
        para_text = '<br>'.join(current_paragraph)
        para_text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', para_text)
        para_text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', para_text)
        para_text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', para_text)
        html_paragraphs.append(f'<p>{para_text}</p>')
    
    if in_list:
        html_paragraphs.append('</ul>')
    
    html_content = ''.join(html_paragraphs)
    
    # Исправляем вложенность HTML-тегов
    html_content = fix_html_nesting(html_content)
    
    # Санитизация: удаляем теги, не разрешенные Telegraph
    html_content = sanitize_html_for_telegraph(html_content)
    
    return html_content


def save_analysis(messages_data, summary):
    """Сохраняет результаты анализа в JSON файл
    
    Returns:
        str: Имя созданного файла или None в случае ошибки
    """
    result = {
        'timestamp': datetime.now(MSK).isoformat(),
        'messages_count': len(messages_data),
        'messages': messages_data,
        'summary': summary
    }
    
    filename = f"analysis_{datetime.now(MSK).strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"💾 Результаты сохранены в {filename}")
        return filename
    except Exception as e:
        print(f"❌ Ошибка сохранения анализа: {e}")
        return None


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
            period_start_dt = period_start_dt.replace(tzinfo=timezone.utc).astimezone(MSK)
            period_start_time = period_start_dt.strftime('%d.%m %H:%M')
        except (ValueError, TypeError):
            period_start_time = period_start_date[:16] if len(period_start_date) >= 16 else period_start_date
    
    if not period_start_time:
        period_start_dt = datetime.now(MSK)
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
                period_end_dt = period_end_dt.replace(tzinfo=timezone.utc).astimezone(MSK)
                period_end_time = period_end_dt.strftime('%d.%m %H:%M')
        except (ValueError, TypeError, KeyError):
            period_end_dt = datetime.now(MSK)
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
        msg_count = len(optimized_messages)
        period_info = f"\n\n📅 **Период {label}:**\n"
        period_info += f"• Обработано: {msg_count} {plural_messages(msg_count)}\n"
        period_info += f"• За {format_period_text(period_hours)} (с {period_start_time} по {period_end_time})\n"
    
    return period_info, period_start_time, period_end_time, period_start_dt, period_end_dt


async def create_telegraph_account(author_name="ChatSumBot"):
    """
    Создает аккаунт Telegraph для переиспользования при множественных публикациях.
    Это позволяет избежать превышения лимитов API при создании нескольких статей.
    
    Args:
        author_name: Имя автора для аккаунта
    
    Returns:
        Экземпляр Telegraph с access_token или None при ошибке
    """
    try:
        telegraph = Telegraph()
        account = await asyncio.to_thread(telegraph.create_account, short_name=author_name)
        telegraph_client = Telegraph(access_token=account['access_token'])
        print(f"✅ Telegraph аккаунт создан: {author_name}")
        return telegraph_client
    except Exception as e:
        print(f"❌ Ошибка создания аккаунта Telegraph: {e}")
        import traceback
        traceback.print_exc()
        return None


async def publish_to_telegraph(title, content, author_name="Chat Filter Bot", telegraph_client=None, max_retries=3):
    """
    Публикует статью в Telegraph с обработкой ограничений частоты запросов (flood control).
    
    Args:
        title: Заголовок статьи
        content: Содержимое статьи (Markdown текст)
        author_name: Имя автора (опционально)
        telegraph_client: Существующий клиент Telegraph (для переиспользования аккаунта)
        max_retries: Максимальное количество попыток при flood control (по умолчанию 3)
    
    Returns:
        URL опубликованной статьи или None при ошибке
    """
    retry_count = 0
    base_delay = 3  # Базовая задержка из сообщения об ошибке Telegraph
    
    while retry_count <= max_retries:
        try:
            # Если клиент не передан, создаем новый аккаунт (старое поведение)
            if telegraph_client is None:
                telegraph = Telegraph()
                account = await asyncio.to_thread(telegraph.create_account, short_name=author_name)
                telegraph = Telegraph(access_token=account['access_token'])
            else:
                telegraph = telegraph_client
            
            # Конвертируем Markdown в HTML (используем общую функцию)
            html_content = convert_markdown_to_html(content)
            
            # Публикуем статью
            response = await asyncio.to_thread(
                telegraph.create_page,
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
        
        except RetryAfterError as e:
            retry_count += 1
            if retry_count > max_retries:
                print(f"❌ Превышено количество попыток ({max_retries}) при публикации в Telegraph")
                return None
            
            # Экспоненциальная задержка: 3s, 6s, 9s...
            wait_time = base_delay * retry_count
            print(f"⚠️  Flood control. Попытка {retry_count}/{max_retries}. Ожидание {wait_time} сек...")
            await asyncio.sleep(wait_time)
            
        except Exception as e:
            error_str = str(e)
            print(f"❌ Ошибка при публикации в Telegraph: {error_str}")
            if 'CONTENT_TOO_BIG' in error_str:
                html_size = len(html_content.encode('utf-8'))
                print(f"   📏 Размер HTML: {html_size} байт (лимит Telegraph ~64KB)")
            import traceback
            traceback.print_exc()
            return None
    
    # Если все попытки исчерпаны
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
        
        # Конвертируем Markdown в HTML (используем общую функцию)
        html_body = convert_markdown_to_html(content)
        
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
            Создано {datetime.now(MSK).strftime('%d.%m.%Y %H:%M')}
        </div>
    </div>
</body>
</html>'''
        
        # Генерируем имя файла
        timestamp = datetime.now(MSK).strftime('%Y%m%d_%H%M%S')
        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:50]
        filename = f"{reports_dir}/{safe_title}_{timestamp}.html"
        
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


async def run_analysis(chat_id, chat_name, hours=None, days=None, limit=None,
                        range_start=None, range_end=None, time_range_start=None,
                        time_range_end=None, use_ai=True, post_to_source=False, scheduled=False):
    """
    Ядро анализа: выполняет сбор сообщений, AI анализ и публикацию.
    
    Args:
        chat_id: ID чата
        chat_name: Имя чата (для отображения)
        hours, days, limit, range_start, range_end, time_range_start, time_range_end: параметры сбора
        use_ai: True для /sum, False для /copy
        post_to_source: публиковать ли результат в исходном чате
        scheduled: True при вызове из планировщика
    """
    try:
        # Получаем или создаем тему для этого чата
        topic_id = await get_or_create_topic(chat_name)
        
        if scheduled:
            action = "анализ" if use_ai else "экспорт"
            status_msg = f"🔄 Запланированный {action} чата '{chat_name}'..."
            await telegram_client.send_message(RESULTS_DESTINATION, status_msg, reply_to=topic_id)
        # Собираем сообщения
        messages_data, chat_id_str, period_start_date = await collect_messages(
            chat_id,
            hours=hours,
            days=days,
            limit=limit,
            range_start=range_start,
            range_end=range_end,
            time_range_start=time_range_start,
            time_range_end=time_range_end
        )
        
        if not messages_data:
            await telegram_client.send_message(
                RESULTS_DESTINATION, 
                f"❌ За указанный период не найдено сообщений в чате '{chat_name}'",
                reply_to=topic_id
            )
            return

        if use_ai and GOOGLE_API_KEYS:
            select_google_api_key_for_new_analysis()
        
        # Оптимизируем сообщения (фильтруем шум)
        optimized_messages = optimize_messages(messages_data, chat_id_str)
        
        # Подсчитываем сообщения с URL (без детального вывода в терминал)
        url_count, url_messages = count_messages_with_urls(optimized_messages)
        if url_count > 0:
            print(f"\n📎 Найдено сообщений с URL: {url_count}")
        
        # Разбиваем сообщения на чанки заранее (используется и для предупреждения, и для анализа)
        # Используем разбиение по символам вместо количества сообщений
        summary_model = GEMINI_DEFAULT_MODEL if use_ai else None
        model_config = get_model_generation_config(summary_model) if summary_model else {
            'chunk_max_chars': CHUNK_MAX_CHARS,
            'chunk_overlap_chars': CHUNK_OVERLAP_CHARS,
        }
        chunks = split_messages_by_chars(
            optimized_messages,
            max_chars=model_config['chunk_max_chars'],
            overlap_chars=model_config['chunk_overlap_chars']
        )
        num_chunks = len(chunks)

        # Предупреждение о больших запросах (особенно для AI анализа)
        if use_ai and num_chunks > 1:
            # Расчет примерного полного времени: AI + паузы + публикация
            wait_time_seconds = estimate_total_ai_processing_seconds(
                chunks,
                use_ai=use_ai,
                use_html_export=USE_HTML_EXPORT
            )
            
            wait_info = ""
            if wait_time_seconds > 0:
                minutes = wait_time_seconds // 60
                seconds = wait_time_seconds % 60
                if minutes > 0:
                    wait_info = f"\n⏳ Примерное полное время обработки: **{minutes} мин {seconds} сек**"
                else:
                    wait_info = f"\n⏳ Примерное полное время обработки: **{seconds} сек**"

            await telegram_client.send_message(
                RESULTS_DESTINATION,
                f"⚠️ **Внимание:** Большой объем сообщений ({len(optimized_messages)})\n"
                f"Обработка будет выполняться в {num_chunks} этапов.{wait_info}\n"
                f"💡 Для больших объемов можно использовать `/copy`, и анализировать вручную.",
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
            summary, usage_info = await create_summary(chunks, chat_id_str, model=GEMINI_DEFAULT_MODEL, use_reasoning=USE_REASONING, period_start_date=period_start_date)
            
            # Проверяем, что summary не является сообщением об ошибке
            if summary.startswith('❌'):
                # Если получили ошибку, отправляем её пользователю и выходим
                await telegram_client.send_message(
                    RESULTS_DESTINATION,
                    f"{summary}\n\n⚠️ Анализ прерван. Попробуйте позже или уменьшите количество сообщений.",
                    reply_to=topic_id
                )
                return
            
            summary = enrich_summary_with_timestamps(summary, optimized_messages)
            
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
            period_text = ""
            if period_start_dt and period_end_dt:
                delta = period_end_dt - period_start_dt
                period_hours = abs(round(delta.total_seconds() / 3600))
                period_text = format_period_text(period_hours)
            
            # Добавляем информацию о токенах и стоимости
            prompt_tokens = None
            completion_tokens = None
            total_tokens = None
            total_cost = None
            
            if usage_info:
                prompt_tokens = usage_info['prompt_tokens']
                completion_tokens = usage_info['completion_tokens']
                total_tokens = usage_info['total_tokens']
                
                # Стоимость не рассчитываем без явных тарифов
                total_cost = None
            
            # Формируем статистику в компактном формате
            msg_count = len(optimized_messages)
            stats_message = f"• {msg_count} {plural_messages(msg_count)}"
            if topics_count > 0:
                stats_message += f", {topics_count} Тем"
            if url_count > 0:
                stats_message += f", {url_count} URL"
            stats_message += "\n"
            if period_text and period_start_time and period_end_time:
                stats_message += f"• За {period_text} с {period_start_time} по {period_end_time}\n"
            if usage_info and total_tokens:
                stats_message += f"• {total_tokens:,} токенов / {GEMINI_DEFAULT_MODEL}\n"
            else:
                stats_message += f"• Модель: {GEMINI_DEFAULT_MODEL}\n"
            if usage_info and usage_info.get('errors'):
                stats_message += "\n⚠️ Ошибки API:\n"
                for error_text in usage_info['errors'][:3]:
                    stats_message += f"• {trim_text_for_telegram(error_text, max_length=500)}\n"
                if len(usage_info['errors']) > 3:
                    stats_message += f"• ... и ещё {len(usage_info['errors']) - 3}\n"
            
            # Убираем разделители чанков Gemini из саммари
            clean_summary = re.sub(r'📊 Обработано \d+ сообщений в \d+ частях\n\n?', '', summary)
            clean_summary = re.sub(r'⚠️ Внимание: \d+ из \d+ частей обработаны с ошибками\n\n?', '', clean_summary)
            clean_summary = re.sub(r'═+\nЧАСТЬ \d+ \(сообщения \d+-\d+\):.*?\n═+\n', '', clean_summary)

            # Формируем полный контент для Telegraph (с статистикой в конце)
            overall_first, overall_last = extract_summary_time_range(clean_summary)
            if overall_first and overall_last:
                overall_time_line = f"*({overall_first} - {overall_last})*\n\n"
                if overall_first == overall_last:
                    overall_time_line = f"*({overall_first})*\n\n"
                full_content = overall_time_line + clean_summary
            else:
                full_content = clean_summary
            # Закомментировано: статистика токенов в конце статьи Telegraph
            # if usage_info and prompt_tokens is not None:
            #     full_content += f"\n\n---\n\n"
            #     full_content += f"📊 **Использовано токенов:**\n"
            #     full_content += f"• Промпт: {prompt_tokens:,}\n"
            #     full_content += f"• Ответ: {completion_tokens:,}\n"
            #     full_content += f"• Всего: {total_tokens:,}\n"
            #     full_content += f"💰 Стоимость: ${total_cost:.4f}\n"
            
            # Добавляем информацию о боте в конец статьи (для HTML и обычного режима)
            bot_footer = f"\n---\ncreated by [ChatSumBot](https://github.com/Hohlas/ChatSum) \n"
            full_content += bot_footer
            
            def _split_content(items, separator, footer, max_size):
                res = []
                cur = []
                for item in items:
                    test = separator.join(cur + [item]) + footer
                    if len(convert_markdown_to_html(test).encode('utf-8')) > max_size and cur:
                        res.append(separator.join(cur))
                        cur = [item]
                    else:
                        cur.append(item)
                if cur:
                    res.append(separator.join(cur))
                return res

            article_title = f"Саммари чата: {chat_name}"
            
            # Делим очищенный саммари на темы для публикации в Telegraph
            # (лимит ~64KB HTML на страницу, оставляем запас ~50KB)
            MAX_PART_HTML = 50000
            topics = [t.strip() for t in clean_summary.split('\n---\n') if t.strip()]
            parts = _split_content(topics, '\n---\n', bot_footer, MAX_PART_HTML)
            # Дополнительное дробление: если часть всё ещё превышает лимит,
            # разбиваем её по параграфам (\n\n)
            final_parts = []
            for part in parts:
                if len(convert_markdown_to_html(part + bot_footer).encode('utf-8')) > MAX_PART_HTML:
                    sub_topics = [p.strip() for p in part.split('\n\n') if p.strip()]
                    sub_parts = _split_content(sub_topics, '\n\n', bot_footer, MAX_PART_HTML)
                    # Если всё ещё слишком большие — бьём по строкам
                    for sp in sub_parts:
                        if len(convert_markdown_to_html(sp + bot_footer).encode('utf-8')) > MAX_PART_HTML:
                            lines = [l for l in sp.split('\n') if l.strip()]
                            line_parts = _split_content(lines, '\n', bot_footer, MAX_PART_HTML)
                            final_parts.extend(line_parts)
                        else:
                            final_parts.append(sp)
                else:
                    final_parts.append(part)
            parts = final_parts
            if len(parts) > 1:
                summary_parts = [(f"Часть {i + 1}", content, None, None) for i, content in enumerate(parts)]
            else:
                summary_parts = [(None, parts[0] if parts else clean_summary, None, None)]
            
            if len(summary_parts) > 1:
                # ═══════════════════════════════════════════════════════════════
                # РЕЖИМ НЕСКОЛЬКИХ ЧАСТЕЙ: публикуем каждую часть отдельно в Telegraph
                # ═══════════════════════════════════════════════════════════════
                print(f"📝 Публикация {len(summary_parts)} частей в Telegraph...")
                
                # Создаем один аккаунт Telegraph для всех публикаций (избегаем flood control)
                telegraph_client = await create_telegraph_account("ChatSumBot")
                
                article_urls = []
                for part_idx, (part_title, part_content, start_idx, end_idx) in enumerate(summary_parts, 1):
                    # Извлекаем диапазон времени для этой части
                    part_time_first, part_time_last = extract_summary_time_range(part_content)
                    if part_time_first and part_time_last:
                        time_range_line = f"\n*({part_time_first} - {part_time_last})*\n"
                        if part_time_first == part_time_last:
                            time_range_line = f"\n*({part_time_first})*\n"
                    else:
                        time_range_line = ""
                    # Добавляем футер к каждой части
                    part_with_footer = time_range_line + part_content + bot_footer
                    part_article_title = f"Саммари чата: {chat_name} - Часть {part_idx}"
                    
                    part_url = await publish_to_telegraph(
                        part_article_title, 
                        part_with_footer, 
                        author_name="ChatSumBot",
                        telegraph_client=telegraph_client
                    )
                    
                    if part_url:
                        part_time_label = f"{part_time_first} - {part_time_last}" if part_time_first and part_time_last else ""
                        article_urls.append((part_title, part_url, part_time_label))
                        print(f"   ✅ Часть {part_idx}: {part_url}")
                    else:
                        part_time_label = f"{part_time_first} - {part_time_last}" if part_time_first and part_time_last else ""
                        print(f"   ❌ Не удалось опубликовать часть {part_idx}")
                        article_urls.append((part_title, None, part_time_label))
                    
                    # Пауза между публикациями (кроме последней части)
                    if part_idx < len(summary_parts):
                        print(f"   ⏳ Пауза 4 секунды перед следующей публикацией...")
                        await asyncio.sleep(4)
                
                # Формируем сообщение со ссылками на все части
                if any(url for _, url, _ in article_urls):
                    header = f"📰 Саммари чата <b>{chat_name}</b>\n"
                    # header += f"📊 Обработано в {len(summary_parts)} частях:\n\n"
                    
                    for part_title, part_url, part_time_label in article_urls:
                        display_label = f"{part_title} ({part_time_label})" if part_time_label else part_title
                        if part_url:
                            header += f"• <a href=\"{part_url}\">{display_label}</a>\n\n"
                        else:
                            header += f"• {display_label} (⚠️ ошибка публикации)\n"
                    
                    header += "\n"
                    stats_message = header + stats_message
                    stats_message += f"\n<i>created by <a href=\"https://github.com/Hohlas/ChatSum\">ChatSumBot</a></i>"
                    stats_message = trim_text_for_telegram(stats_message)

                    # Отправляем сообщение с ссылками
                    await telegram_client.send_message(
                        RESULTS_DESTINATION,
                        stats_message,
                        parse_mode='html',
                        reply_to=topic_id
                    )
                    
                    # Если запрошено, дублируем в исходный чат
                    if post_to_source:
                        try:
                            await telegram_client.send_message(
                                chat_id,
                                stats_message,
                                parse_mode='html'
                            )
                        except Exception as e:
                            print(f"⚠️  Не удалось отправить результат в исходный чат: {e}")

                    # Если USE_HTML_EXPORT=true, создаем ОБЩИЙ HTML файл со всеми частями
                    if USE_HTML_EXPORT:
                        html_file = create_html_report(article_title, full_content, author_name="ChatSumBot")
                        
                        if html_file:
                            await telegram_client.send_file(
                                RESULTS_DESTINATION,
                                html_file,
                                reply_to=topic_id
                            )
                            print(f"✅ Общий HTML отчет отправлен в Telegram")
                        else:
                            await telegram_client.send_message(
                                RESULTS_DESTINATION, 
                                "⚠️ Не удалось создать HTML отчет",
                                parse_mode='html',
                                reply_to=topic_id
                            )
                else:
                    # Все публикации провалились - сохраняем в файл
                    stats_message += f"\n⚠️ Не удалось опубликовать в Telegraph. Сохраняю в файл..."
                    filename = f"analysis_{chat_name.replace(' ', '_')}_{datetime.now(MSK).strftime('%Y%m%d_%H%M%S')}.md"
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
                    
                    await telegram_client.send_message(
                        RESULTS_DESTINATION, 
                        stats_message,
                        parse_mode='html',
                        reply_to=topic_id
                    )

                    # Если запрошено, дублируем в исходный чат
                    if post_to_source:
                        try:
                            await telegram_client.send_message(
                                chat_id,
                                stats_message,
                                parse_mode='html'
                            )
                        except Exception as e:
                            print(f"⚠️  Не удалось отправить результат в исходный чат: {e}")
                
                # Удаляем временный файл
                try:
                    if os.path.exists(analysis_filename):
                        os.remove(analysis_filename)
                        print(f"🗑️  Временный файл {analysis_filename} удален")
                except Exception as e:
                    print(f"⚠️  Не удалось удалить файл {analysis_filename}: {e}")
            
            else:
                # ═══════════════════════════════════════════════════════════════
                # ОБЫЧНЫЙ РЕЖИМ: одна публикация в Telegraph
                # ═══════════════════════════════════════════════════════════════
                single_article_title = article_title

                article_url = await publish_to_telegraph(single_article_title, full_content, author_name="ChatSumBot")
                
                if article_url:
                    # Вставляем заголовок с саммари в начало сообщения
                    header = f"📰 <a href=\"{article_url}\"><b>Саммари чата {chat_name}</b></a>\n\n"
                    stats_message = header + stats_message
                    stats_message += f"\n<i>created by <a href=\"https://github.com/Hohlas/ChatSum\">ChatSumBot</a></i>"
                    stats_message = trim_text_for_telegram(stats_message)

                    # отправляем сообщение с статистикой и ссылкой на Telegraph
                    await telegram_client.send_message(
                        RESULTS_DESTINATION,
                        stats_message,
                        parse_mode='html',
                        reply_to=topic_id
                    )

                    # Если запрошено, дублируем в исходный чат
                    if post_to_source:
                        try:
                            await telegram_client.send_message(
                                chat_id,
                                stats_message,
                                parse_mode='html'
                            )
                        except Exception as e:
                            print(f"⚠️  Не удалось отправить результат в исходный чат: {e}")

                    # Если USE_HTML_EXPORT=true, дополнительно создаем и отправляем HTML файл
                    if USE_HTML_EXPORT:
                        html_file = create_html_report(single_article_title, full_content, author_name="ChatSumBot")
                        
                        if html_file:
                            #  отправляем HTML файл отдельным сообщением
                            await telegram_client.send_file(
                                RESULTS_DESTINATION,
                                html_file,
                                reply_to=topic_id
                            )
                            print(f"✅ HTML отчет отправлен в Telegram")
                        else:
                            # Если не удалось создать HTML, отправляем просто статистику
                            await telegram_client.send_message(
                                RESULTS_DESTINATION, 
                                stats_message + "\n⚠️ Не удалось создать HTML отчет",
                                parse_mode='html',
                                reply_to=topic_id
                            )
                    

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
                    filename = f"analysis_{chat_name.replace(' ', '_')}_{datetime.now(MSK).strftime('%Y%m%d_%H%M%S')}.md"
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
                    
                    # Отправляем статистику
                    await telegram_client.send_message(
                        RESULTS_DESTINATION, 
                        stats_message,
                        parse_mode='html',
                        reply_to=topic_id
                    )

                    # Если запрошено, дублируем в исходный чат
                    if post_to_source:
                        try:
                            await telegram_client.send_message(
                                chat_id,
                                stats_message,
                                parse_mode='html'
                            )
                        except Exception as e:
                            print(f"⚠️  Не удалось отправить результат в исходный чат: {e}")
            
            print("✅ Анализ с AI успешно завершён")
        
        else:
            processed_label = build_processed_label(
                len(optimized_messages),
                range_start=range_start,
                range_end=range_end,
                time_range_start=time_range_start,
                time_range_end=time_range_end
            )

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
            
            # Создаем JSON строку (компактный формат для внешнего анализа)
            json_export = json.dumps(export_data, ensure_ascii=False)
            
            # Сохраняем в файл
            filename = f"export_{chat_name.replace(' ', '_')}_{datetime.now(MSK).strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(json_export)
            
            # Вычисляем длительность для caption
            period_text = ""
            if period_start_dt and period_end_dt:
                delta = period_end_dt - period_start_dt
                period_hours = abs(round(delta.total_seconds() / 3600))
                period_text = format_period_text(period_hours)
            
            # Формируем caption в компактном формате
            caption = f"📋 Экспорт завершен\n\n"
            caption += f"• Обработано: {processed_label}\n"
            if period_text and period_start_time and period_end_time:
                caption += f"• За {period_text} (с {period_start_time} по {period_end_time})\n"
            caption += f"\n💡 Готово для копирования в Google AI Studio!\n"
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
        error_msg = f"❌ Ошибка при выполнении анализа: {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        
        try:
            topic_id = await get_or_create_topic(chat_name)
            await telegram_client.send_message(RESULTS_DESTINATION, error_msg, reply_to=topic_id)
        except:
            await telegram_client.send_message(RESULTS_DESTINATION, error_msg)


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

        # Если параметры прикреплены к команде без пробела (например /sum2d+)
        if len(parts) == 1:
            m = re.match(r'^(/(?:sum|copy))(.+)$', parts[0], re.IGNORECASE)
            if m:
                parts = [m.group(1), m.group(2)]

        # Проверяем суффикс '+' для публикации в исходном чате
        post_to_source = False
        filtered_params = []
        for p in parts[1:]:
            if p == '+' or p.endswith('+'):
                post_to_source = True
                if p != '+':
                    filtered_params.append(p[:-1])
            else:
                filtered_params.append(p)
        if filtered_params != parts[1:]:
            parts = [parts[0]] + filtered_params

        # Получаем чат один раз и используем для логирования и далее
        chat = await event.get_chat()
        chat_name_log = chat.title if hasattr(chat, 'title') else "Private"
        chat_name = chat.title if hasattr(chat, 'title') else "чата"
        
        command_name = "/sum" if use_ai else "/copy"
        params = " ".join(parts[1:]) if len(parts) > 1 else "(по умолчанию 24h)"
        print(f"\n📥 Команда: {command_name} {params} | Чат: {chat_name_log}")
        
        hours = None
        days = None
        limit = None
        range_start = None
        range_end = None
        time_range_start = None
        time_range_end = None
        
        # Обрабатываем параметры
        # Поддерживаем форматы: /sum 3h, /sum 2d, /sum 100, /sum 1d 6h, /sum 600-800, /sum 2d-3d, /sum 3-5d, /sum 2-4h
        if len(parts) > 1:
            # Обрабатываем все параметры (может быть несколько, напр. "1d 6h")
            for param in parts[1:]:
                param_clean = param.lower().strip()
                
                time_range_match = re.fullmatch(r'(\d+)([hd])\s*-\s*(\d+)([hd])', param_clean)
                time_range_match2 = re.fullmatch(r'(\d+)\s*-\s*(\d+)([hd])', param_clean)
                range_match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', param_clean)
                if time_range_match:
                    start_val = int(time_range_match.group(1))
                    start_unit = time_range_match.group(2)
                    end_val = int(time_range_match.group(3))
                    end_unit = time_range_match.group(4)

                    start_delta = timedelta(days=start_val) if start_unit == 'd' else timedelta(hours=start_val)
                    end_delta = timedelta(days=end_val) if end_unit == 'd' else timedelta(hours=end_val)

                    if start_delta > timedelta(0) and end_delta > start_delta:
                        time_range_start = start_delta
                        time_range_end = end_delta
                        range_start = None
                        range_end = None
                        limit = None
                        hours = None
                        days = None
                elif time_range_match2:
                    start_val = int(time_range_match2.group(1))
                    end_val = int(time_range_match2.group(2))
                    unit = time_range_match2.group(3)

                    start_delta = timedelta(days=start_val) if unit == 'd' else timedelta(hours=start_val)
                    end_delta = timedelta(days=end_val) if unit == 'd' else timedelta(hours=end_val)

                    if start_delta > timedelta(0) and end_delta > start_delta:
                        time_range_start = start_delta
                        time_range_end = end_delta
                        range_start = None
                        range_end = None
                        limit = None
                        hours = None
                        days = None
                elif range_match:
                    start_val = int(range_match.group(1))
                    end_val = int(range_match.group(2))
                    if start_val > 0 and end_val >= start_val:
                        range_start = start_val
                        range_end = end_val
                        time_range_start = None
                        time_range_end = None
                        limit = None
                        hours = None
                        days = None
                elif param_clean.endswith('h') and range_start is None and time_range_start is None:
                    # Параметр часов
                    hours_val = int(param_clean.replace('h', ''))
                    if hours_val > 0:
                        hours = hours_val
                elif param_clean.endswith('d') and range_start is None and time_range_start is None:
                    # Параметр дней
                    days_val = int(param_clean.replace('d', ''))
                    if days_val > 0:
                        days = days_val
                elif param_clean.isdigit() and range_start is None and time_range_start is None:
                    # Это количество сообщений
                    limit = int(param_clean)
        
        # Если ничего не указано, по умолчанию 24 часа
        if hours is None and days is None and limit is None and range_start is None and time_range_start is None:
            hours = 24
        
        # Удаляем команду из чата (для приватности)
        await event.delete()
        
        # Получаем или создаем тему для этого чата
        topic_id = await get_or_create_topic(chat_name)
        
        # Формируем сообщение о начале
        action = "анализ" if use_ai else "экспорт"
        if time_range_start and time_range_end:
            status_msg = f"🔄 Начинаю {action} сообщений за диапазон {format_timedelta_short(time_range_start)}-{format_timedelta_short(time_range_end)} назад из чата '{chat_name}'..."
        elif range_start and range_end:
            status_msg = f"🔄 Начинаю {action} сообщений {range_start}-{range_end} от конца чата '{chat_name}'..."
        elif limit:
            status_msg = f"🔄 Начинаю {action} последних {limit} сообщений из чата '{chat_name}'..."
        else:
            status_msg = f"🔄 Начинаю {action} чата '{chat_name}' за последние {days or 0} дней и {hours or 0} часов..."
        
        await telegram_client.send_message(
            RESULTS_DESTINATION, 
            status_msg,
            reply_to=topic_id
        )
        
        await run_analysis(
            chat_id=event.chat_id,
            chat_name=chat_name,
            hours=hours,
            days=days,
            limit=limit,
            range_start=range_start,
            range_end=range_end,
            time_range_start=time_range_start,
            time_range_end=time_range_end,
            use_ai=use_ai,
            post_to_source=post_to_source,
            scheduled=False
        )
    
    except Exception as e:
        error_msg = f"❌ Ошибка при выполнении команды: {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        
        try:
            chat = await event.get_chat()
            chat_name = chat.title if hasattr(chat, 'title') else "чата"
            topic_id = await get_or_create_topic(chat_name)
            await telegram_client.send_message(RESULTS_DESTINATION, error_msg, reply_to=topic_id)
        except:
            await telegram_client.send_message(RESULTS_DESTINATION, error_msg)




# ──────────────────────────────────────────────
# Планировщик ежедневных саммари
# ──────────────────────────────────────────────
scheduler = AsyncIOScheduler()


async def scheduled_analysis_job(chat_id, period, post_to_source):
    """Job wrapper для запланированного анализа — разрешает chat_name и вызывает run_analysis."""
    try:
        chat_entity = await telegram_client.get_entity(chat_id)
        chat_name = chat_entity.title if hasattr(chat_entity, 'title') else f"чат {chat_id}"
    except Exception as e:
        print(f"❌ Не удалось получить информацию о чате {chat_id}: {e}")
        return

    hours = None
    days = None
    period_clean = period.lower().strip()
    if period_clean.endswith('h'):
        hours = int(period_clean[:-1])
    elif period_clean.endswith('d'):
        days = int(period_clean[:-1])
    else:
        days = 1

    await run_analysis(
        chat_id=chat_id,
        chat_name=chat_name,
        hours=hours,
        days=days,
        post_to_source=post_to_source,
        use_ai=True,
        scheduled=True
    )


def reload_schedule():
    """Перезагружает расписание из SCHEDULE.txt в планировщик."""
    scheduler.remove_all_jobs()
    entries = load_schedule(SCHEDULE_FILE)
    for entry in entries:
        scheduler.add_job(
            scheduled_analysis_job,
            trigger=CronTrigger(hour=entry['hour'], minute=entry['minute'], timezone=MSK),
            args=[entry['chat_id'], entry['period'], entry['post_to_source']],
            id=str(entry['chat_id']),
            replace_existing=True
        )
        print(f"   📅 Запланирован анализ чата {entry['chat_id']} на {entry['hour']:02d}:{entry['minute']:02d}, период {entry['period']}" + (" (в чат)" if entry['post_to_source'] else ""))


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/config'))
async def handle_config_command(event):
    """Показывает текущую конфигурацию"""
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    print(f"\n📥 Команда: /config | Чат: {chat_name}")
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
    
    topic_id = await get_or_create_topic(chat_name)
    
    await telegram_client.send_message(RESULTS_DESTINATION, config_text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/show_excluded'))
async def handle_show_excluded_command(event):
    """Показывает список исключенных пользователей"""
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    print(f"\n📥 Команда: /show_excluded | Чат: {chat_name}")
    text = f"📝 **Исключенные пользователи** ({len(EXCLUDED_USERS)}):\n\n"
    if EXCLUDED_USERS:
        for i, user in enumerate(EXCLUDED_USERS, 1):
            text += f"{i}. {user}\n"
    else:
        text += "Список пуст"
    
    await event.delete()
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/show_priority'))
async def handle_show_priority_command(event):
    """Показывает список приоритетных пользователей"""
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    print(f"\n📥 Команда: /show_priority | Чат: {chat_name}")
    text = f"⭐ **Приоритетные пользователи** ({len(PRIORITY_USERS)}):\n\n"
    if PRIORITY_USERS:
        for i, user in enumerate(PRIORITY_USERS, 1):
            text += f"{i}. {user}\n"
    else:
        text += "Список пуст"
    
    await event.delete()
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/show_prompt'))
async def handle_show_prompt_command(event):
    """Показывает текущий промпт"""
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    print(f"\n📥 Команда: /show_prompt | Чат: {chat_name}")
    prompt_preview = ANALYSIS_PROMPT[:1000] + "..." if len(ANALYSIS_PROMPT) > 1000 else ANALYSIS_PROMPT
    text = f"📄 **Текущий промпт** ({len(ANALYSIS_PROMPT)} символов):\n\n{prompt_preview}\n\n"
    text += f"💡 Полный промпт в файле: {PROMPT_FILE}"
    
    await event.delete()
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/add_excluded\s+(.+)'))
async def handle_add_excluded_command(event):
    """Добавляет пользователя в список исключенных"""
    global EXCLUDED_USERS
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    username = event.pattern_match.group(1).strip()
    print(f"\n📥 Команда: /add_excluded {username} | Чат: {chat_name}")
    
    async with config_lock:
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
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/remove_excluded\s+(.+)'))
async def handle_remove_excluded_command(event):
    """Удаляет пользователя из списка исключенных"""
    global EXCLUDED_USERS
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    username = event.pattern_match.group(1).strip()
    print(f"\n📥 Команда: /remove_excluded {username} | Чат: {chat_name}")
    
    async with config_lock:
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
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/add_priority\s+(.+)'))
async def handle_add_priority_command(event):
    """Добавляет пользователя в список приоритетных"""
    global PRIORITY_USERS
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    username = event.pattern_match.group(1).strip()
    print(f"\n📥 Команда: /add_priority {username} | Чат: {chat_name}")
    
    async with config_lock:
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
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/remove_priority\s+(.+)'))
async def handle_remove_priority_command(event):
    """Удаляет пользователя из списка приоритетных"""
    global PRIORITY_USERS
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    username = event.pattern_match.group(1).strip()
    print(f"\n📥 Команда: /remove_priority {username} | Чат: {chat_name}")
    
    async with config_lock:
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
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/show_model'))
async def handle_show_model_command(event):
    """Показывает текущую настройку модели"""
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    print(f"\n📥 Команда: /show_model | Чат: {chat_name}")
    export_mode = "HTML файлы 📄" if USE_HTML_EXPORT else "Telegraph 🌐"
    text = f"""
🤖 **Текущая модель для анализа**

**Модель:** `{CURRENT_MODEL}`
**Reasoning:** {'Включен ✅' if USE_REASONING else 'Выключен ❌'}
**Экспорт результатов:** {export_mode}

ℹ️ **Доступные модели Google Gemini** смотрите в Google AI Studio.
Модель задается строкой через `/set_model`.

💡 Текущая модель сохраняется в файле {MODEL_CONFIG_FILE}

📚 Альтернатива:
Если нужен Claude/GPT - используйте их напрямую через OpenAI API или Anthropic API.
"""
    
    await event.delete()
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/set_model\s+(.+)'))
async def handle_set_model_command(event):
    """Устанавливает модель для анализа"""
    global CURRENT_MODEL, GEMINI_DEFAULT_MODEL
    
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    model = event.pattern_match.group(1).strip()
    print(f"\n📥 Команда: /set_model {model} | Чат: {chat_name}")
    
    # Валидируем название модели
    if not model:
        text = f"⚠️ Не указано название модели.\n\nПример: `/set_model {GEMINI_DEFAULT_MODEL}`"
    else:
        async with config_lock:
            old_model = GEMINI_DEFAULT_MODEL or CURRENT_MODEL
            
            if update_env_value('private.txt', 'GEMINI_MODEL', model):
                GEMINI_DEFAULT_MODEL = model
                CURRENT_MODEL = model
                text = f"✅ Модель изменена: **{old_model}** → **{model}**\n\n"
                text += "Изменения вступят в силу для следующего анализа.\n"
                text += "Модель сохранена в `private.txt` (GEMINI_MODEL).\n"
                text += "Используйте `/show_model` для просмотра деталей."
            else:
                text = "❌ Ошибка при сохранении модели в private.txt"
    
    await event.delete()
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/reload_config'))
async def handle_reload_config_command(event):
    """Перезагружает конфигурацию из файлов"""
    global EXCLUDED_USERS, PRIORITY_USERS, ANALYSIS_PROMPT, CURRENT_MODEL, USE_REASONING, USE_HTML_EXPORT, GEMINI_DEFAULT_MODEL, GEMINI_REASONING_EFFORT, GEMINI_CHUNK_MAX_CHARS, GOOGLE_API_KEYS, google_analysis_counter
    
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    print(f"\n📥 Команда: /reload_config | Чат: {chat_name}")
    
    async with config_lock:
        load_dotenv('private.txt', override=True)
        GEMINI_DEFAULT_MODEL = os.getenv('GEMINI_MODEL', '').strip()
        GEMINI_REASONING_EFFORT = os.getenv('GEMINI_REASONING_EFFORT', '').strip().lower()
        GEMINI_CHUNK_MAX_CHARS = os.getenv('GEMINI_CHUNK_MAX_CHARS', '').strip()
        GOOGLE_API_KEYS = load_google_api_keys()
        google_analysis_counter = 0
        if GOOGLE_API_KEYS:
            set_google_api_key_index(0)
        EXCLUDED_USERS = load_users_from_file(EXCLUDED_USERS_FILE)
        PRIORITY_USERS = load_users_from_file(PRIORITY_USERS_FILE)
        ANALYSIS_PROMPT = load_prompt_from_file(PROMPT_FILE)
        CURRENT_MODEL, USE_REASONING, USE_HTML_EXPORT = load_model_config(MODEL_CONFIG_FILE)
        if GEMINI_DEFAULT_MODEL:
            CURRENT_MODEL = GEMINI_DEFAULT_MODEL
    
    reload_schedule()
    text = f"""
✅ **Конфигурация перезагружена из файлов**

📝 Исключенные пользователи: {len(EXCLUDED_USERS)}
⭐ Приоритетные пользователи: {len(PRIORITY_USERS)}
📄 Промпт: {len(ANALYSIS_PROMPT)} символов
🤖 Модель: {CURRENT_MODEL}
🔑 Google API keys: {len(GOOGLE_API_KEYS)}

💡 Используйте `/config` для просмотра деталей
"""
    
    await event.delete()
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(RESULTS_DESTINATION, text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/sum'))
async def handle_sum_command(event):
    """
    Обработчик команды /sum для анализа чата с AI
    
    Примеры:
    /sum 3h - анализ за 3 часа
    /sum 45 - анализ 45 сообщений
    /sum 600-800 - анализ сообщений с 600-го по 800-е от конца
    /sum 2d-3d - анализ сообщений от 3 до 2 дней назад
    """
    await process_chat_command(event, use_ai=True)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/copy'))
async def handle_copy_command(event):
    """
    Обработчик команды /copy для экспорта без AI
    
    Примеры:
    /copy 3h - экспорт за 3 часа
    /copy 45 - экспорт 45 сообщений
    /copy 600-800 - экспорт сообщений с 600-го по 800-е от конца
    /copy 2d-3d - экспорт сообщений от 3 до 2 дней назад
    """
    await process_chat_command(event, use_ai=False)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/help'))
async def handle_help_command(event):
    """Обработчик команды /help - показывает справку по командам"""
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    print(f"\n📥 Команда: /help | Чат: {chat_name}")
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
  • `/sum 600-800` - сообщения с 600-го по 800-е от конца
  • `/sum 2d-3d` - сообщения от 3 до 2 дней назад
  • `/sum 1d+` - анализ + публикация в исходном чате
  • `/sum1d+` - пробел после команды не обязателен

`/copy` - экспорт без анализа (для ручной обработки)
Примеры:
  • `/copy 3h` - экспорт за 3 часа
  • `/copy 50` - экспорт 50 сообщений
  • `/copy 600-800` - экспорт сообщений с 600-го по 800-е от конца
  • `/copy 2d-3d` - экспорт сообщений от 3 до 2 дней назад
  • Результат: JSON файл + текст для Google AI Studio

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

**📅 Расписание автоматического саммари:**

`/sch 09:00 1d` - запланировать саммари на 09:00 МСК каждый день
`/sch 21:30 12h` - запланировать на 21:30 МСК, период 12 часов
`/sch 09:00 1d+` - `+` публикует результат и в исходный чат
`/sch_list` - показать текущее расписание
`/unsch` - удалить текущий чат из расписания

**🤖 Модели AI (Google Gemini через API):**
• Модель задается в `MODEL_CONFIG.txt` или через `/set_model`
• Список моделей доступен в Google AI Studio

**Как это работает:**

**`/sum` (с AI анализом):**
1. Собирает сообщения (по времени или количеству)
2. Фильтрует шум и исключенных пользователей
3. Отправляет в Google Gemini (модель из конфигурации)
4. Получает структурированную выжимку по темам
5. Отправляет результат в ваш канал
6. Если добавить суффикс `+` (например `/sum 1d+`), результат также публикуется в исходном чате

**`/copy` (без AI, только экспорт):**
1. Собирает и фильтрует сообщения
2. Создает JSON файл с метаданными
3. Отправляет вам для ручного анализа
4. Удобно для копирования в Google AI Studio вручную

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
    topic_id = await get_or_create_topic(chat_name)
    
    await telegram_client.send_message(RESULTS_DESTINATION, help_text, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/sch\s+\d'))
async def handle_sch_command(event):
    """Добавляет текущий чат в расписание: /sch 09:00 1d или /sch 09:00 1d+"""
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    chat_name_display = chat.title if hasattr(chat, 'title') else "чата"
    print(f"\n📥 Команда: /sch | Чат: {chat_name}")

    parts = event.raw_text.split()
    if len(parts) < 3:
        await event.delete()
        topic_id = await get_or_create_topic(chat_name)
        await telegram_client.send_message(
            RESULTS_DESTINATION,
            "⚠️ **Формат:** `/sch 09:00 1d` или `/sch 09:00 1d+`\n"
            "`+` — публикация и в исходный чат",
            reply_to=topic_id
        )
        return

    time_str = parts[1]
    period_raw = parts[2].lower()

    time_match = re.fullmatch(r'(\d{1,2}):(\d{2})', time_str)
    if not time_match:
        await event.delete()
        topic_id = await get_or_create_topic(chat_name)
        await telegram_client.send_message(
            RESULTS_DESTINATION,
            f"⚠️ Неверный формат времени: `{time_str}`. Используйте `HH:MM`.",
            reply_to=topic_id
        )
        return

    hour = int(time_match.group(1))
    minute = int(time_match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        await event.delete()
        topic_id = await get_or_create_topic(chat_name)
        await telegram_client.send_message(
            RESULTS_DESTINATION,
            "⚠️ Время должно быть в диапазоне 00:00–23:59.",
            reply_to=topic_id
        )
        return

    post_to_source = period_raw.endswith('+')
    period = period_raw[:-1] if post_to_source else period_raw

    if not re.fullmatch(r'\d+[hd]', period):
        await event.delete()
        topic_id = await get_or_create_topic(chat_name)
        await telegram_client.send_message(
            RESULTS_DESTINATION,
            f"⚠️ Неверный формат периода: `{period_raw}`. Используйте `1d`, `12h`, `2d` и т.д.\n"
            f"`+` после периода — публикация в исходном чате.",
            reply_to=topic_id
        )
        return

    entries = load_schedule(SCHEDULE_FILE)
    entries = [e for e in entries if e['chat_id'] != event.chat_id]
    entries.append({
        'chat_id': event.chat_id,
        'hour': hour,
        'minute': minute,
        'period': period,
        'post_to_source': post_to_source
    })

    if not save_schedule(SCHEDULE_FILE, entries):
        await event.delete()
        topic_id = await get_or_create_topic(chat_name)
        await telegram_client.send_message(
            RESULTS_DESTINATION,
            "❌ Ошибка при сохранении расписания.",
            reply_to=topic_id
        )
        return

    reload_schedule()

    await event.delete()
    topic_id = await get_or_create_topic(chat_name)

    period_display = f"{period}h" if period.endswith('h') else f"{period}d"
    days = int(period[:-1]) if period.endswith('d') else 0
    hours = int(period[:-1]) if period.endswith('h') else 0
    if days == 1:
        period_text = "1 день"
    elif days in (2, 3, 4):
        period_text = f"{days} дня"
    elif days > 0:
        period_text = f"{days} дней"
    elif hours == 1:
        period_text = "1 час"
    else:
        period_text = f"{hours} часов"

    post_text = "да ✅" if post_to_source else "нет"
    confirm_msg = (
        f"✅ **Расписание добавлено**\n\n"
        f"Чат: **{chat_name_display}**\n"
        f"Время: `{hour:02d}:{minute:02d}`\n"
        f"Период: {period_text}\n"
        f"Публикация в чате: {post_text}"
    )
    await telegram_client.send_message(RESULTS_DESTINATION, confirm_msg, reply_to=topic_id)


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/sch_list$'))
async def handle_sch_list_command(event):
    """Показывает текущее расписание."""
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    print(f"\n📥 Команда: /sch_list | Чат: {chat_name}")

    entries = load_schedule(SCHEDULE_FILE)

    await event.delete()
    topic_id = await get_or_create_topic(chat_name)

    if not entries:
        await telegram_client.send_message(
            RESULTS_DESTINATION,
            "📅 **Расписание пусто**\n\nИспользуйте `/sch 09:00 1d` в нужном чате, чтобы добавить.",
            reply_to=topic_id
        )
        return

    lines = ["📅 **Текущее расписание:**\n"]
    for i, entry in enumerate(entries, 1):
        try:
            chat_entity = await telegram_client.get_entity(entry['chat_id'])
            display_name = chat_entity.title if hasattr(chat_entity, 'title') else f"чат {entry['chat_id']}"
        except:
            display_name = f"чат {entry['chat_id']}"
        post_suffix = " (в чат)" if entry['post_to_source'] else ""
        lines.append(f"{i}. **{display_name}**: {entry['hour']:02d}:{entry['minute']:02d}, {entry['period']}{post_suffix}")

    await telegram_client.send_message(
        RESULTS_DESTINATION,
        "\n".join(lines),
        reply_to=topic_id
    )


@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/unsch$'))
async def handle_unsch_command(event):
    """Удаляет текущий чат из расписания."""
    chat = await event.get_chat()
    chat_name = chat.title if hasattr(chat, 'title') else "Private"
    chat_name_display = chat.title if hasattr(chat, 'title') else "чата"
    print(f"\n📥 Команда: /unsch | Чат: {chat_name}")

    entries = load_schedule(SCHEDULE_FILE)
    before = len(entries)
    entries = [e for e in entries if e['chat_id'] != event.chat_id]
    removed = before - len(entries)

    if removed == 0:
        await event.delete()
        topic_id = await get_or_create_topic(chat_name)
        await telegram_client.send_message(
            RESULTS_DESTINATION,
            f"❌ Чат **{chat_name_display}** не найден в расписании.",
            reply_to=topic_id
        )
        return

    if not save_schedule(SCHEDULE_FILE, entries):
        await event.delete()
        topic_id = await get_or_create_topic(chat_name)
        await telegram_client.send_message(
            RESULTS_DESTINATION,
            "❌ Ошибка при сохранении расписания.",
            reply_to=topic_id
        )
        return

    reload_schedule()

    await event.delete()
    topic_id = await get_or_create_topic(chat_name)
    await telegram_client.send_message(
        RESULTS_DESTINATION,
        f"✅ **Чат {chat_name_display} удален из расписания**",
        reply_to=topic_id
    )


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
    
    # Показываем текущую Gemini-конфигурацию из private.txt
    print(f"\n🤖 Конфигурация Gemini (private.txt):")
    print(f"   • GEMINI_MODEL={GEMINI_DEFAULT_MODEL or 'не задан'}")
    print(f"   • GEMINI_REASONING_EFFORT={GEMINI_REASONING_EFFORT or 'не задан'}")
    print(f"   • GEMINI_CHUNK_MAX_CHARS={GEMINI_CHUNK_MAX_CHARS or 'не задан'}")
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
    print("    /sum 600-800 - сообщения с 600-го по 800-е от конца")
    print("    /sum 2d-3d - сообщения от 3 до 2 дней назад")
    print("    /sum 1d+ - анализ + публикация в исходном чате")
    print("  Экспорт:")
    print("    /copy - экспорт без AI (для ручного анализа)")
    print("    /copy 3h - экспорт за 3 часа")
    print("    /copy 50 - экспорт 50 сообщений")
    print("    /copy 600-800 - экспорт сообщений с 600-го по 800-е от конца")
    print("    /copy 2d-3d - экспорт сообщений от 3 до 2 дней назад")
    print("  Конфигурация:")
    print("    /config - показать конфигурацию")
    print("    /show_model - показать настройки модели AI")
    print("    /set_model - сменить модель AI")
    print("    /add_excluded, /remove_excluded - управление исключенными")
    print("    /add_priority, /remove_priority - управление приоритетными")
    print("    /reload_config - перезагрузить из файлов")
    print("  Расписание:")
    print("    /sch HH:MM период - запланировать ежедневное саммари")
    print("    /sch_list - показать расписание")
    print("    /unsch - удалить чат из расписания")
    print("  Справка:")
    print("    /help - полная справка по командам")
    print("\n💡 Отправьте команду /sum в любом чате для анализа с AI")
    print("💡 Используйте /copy для экспорта без затрат на API")
    print("=" * 60)
    print("\n👀 Ожидание команд...")
    print("💡 Нажмите Ctrl+C для остановки бота")
    
    try:
        # Запуск планировщика
        reload_schedule()
        scheduler.start()
        await telegram_client.run_until_disconnected()
    except KeyboardInterrupt:
        print("\n🔄 Завершение работы...")
    finally:
        # Остановка планировщика
        try:
            scheduler.shutdown(wait=False)
            print("✅ Планировщик остановлен")
        except Exception as e:
            print(f"⚠️ Ошибка при остановке планировщика: {e}")
        
        # Graceful shutdown: закрываем HTTP клиент и Telegram соединение
        try:
            await http_client.aclose()
            print("✅ HTTP клиент закрыт")
        except Exception as e:
            print(f"⚠️ Ошибка при закрытии HTTP клиента: {e}")
        
        try:
            await telegram_client.disconnect()
            print("✅ Соединение с Telegram закрыто")
        except Exception as e:
            print(f"⚠️ Ошибка при закрытии соединения Telegram: {e}")


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
