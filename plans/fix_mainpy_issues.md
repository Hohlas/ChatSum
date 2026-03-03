# План устранения ошибок в main.py

## Структура плана

- **Фаза 1**: Критические ошибки (безопасность, стабильность)
- **Фаза 2**: Утечки ресурсов
- **Фаза 3**: Алгоритмические оптимизации
- **Фаза 4**: Улучшение обработки ошибок
- **Фаза 5**: Рефакторинг

---

## Фаза 1: Критические ошибки (Высший приоритет)

### 1.1 Исправление IndexError в collect_messages() [строка 745]

**Проблема:** Доступ к `messages_data[0]` без проверки на пустоту.

**Решение:**
```python
# Было:
period_start_date = messages_data[0].get('date', '') if messages_data else ''
messages_data.reverse()
period_start_date = messages_data[0].get('date', '') if messages_data else ''  # строка 745

# Станет:
if not messages_data:
    print("⚠️ Нет текстовых сообщений за указанный период")
    return [], chat_id_str, ''
    
period_start_date = messages_data[0].get('date', '')
```

**Тестирование:**
- Запустить `/sum` в чате без сообщений за указанный период
- Запустить `/sum` в чате где все сообщения - медиа (без текста)

---

### 1.2 Добавление синхронизации для глобальных списков

**Проблема:** Race condition при одновременном изменении `EXCLUDED_USERS`, `PRIORITY_USERS`.

**Решение:**
```python
# Добавить в начало файла после импортов:
import asyncio
config_lock = asyncio.Lock()

# В обработчиках команд:
@telegram_client.on(events.NewMessage(outgoing=True, pattern=r'^/add_excluded\s+(.+)'))
async def handle_add_excluded_command(event):
    async with config_lock:
        global EXCLUDED_USERS
        # ... существующая логика
```

**Где применить:**
- `/add_excluded`
- `/remove_excluded`
- `/add_priority`
- `/remove_priority`
- `/set_model`
- `/reload_config`

**Тестирование:**
- Быстро отправить 5 команд `/add_excluded` подряд
- Проверить консистентность файла EXCLUDED_USERS.txt

---

### 1.3 Защита от None в sender

**Проблема:** Сравнение `msg['sender'] in EXCLUDED_USERS` при `None`.

**Решение:**
```python
# Было:
if msg['sender'] in EXCLUDED_USERS:

# Станет:
sender = msg.get('sender')
if sender and sender in EXCLUDED_USERS:
```

---

## Фаза 2: Утечки ресурсов (Высокий приоритет)

### 2.1 Graceful shutdown HTTP клиента

**Проблема:** `httpx.AsyncClient` не закрывается при завершении.

**Решение:**
```python
# Добавить глобальную переменную:
http_client = None

# В main():
async def main():
    global http_client
    http_client = httpx.AsyncClient(
        timeout=180.0,
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    )
    
    try:
        # ... существующий код
        await telegram_client.run_until_disconnected()
    finally:
        await http_client.aclose()
        await telegram_client.disconnect()
```

---

### 2.2 Закрытие файловых дескрипторов

**Проблема:** Потенциальная утечка при ошибках в `save_analysis()`.

**Решение:**
```python
# Было:
def save_analysis(messages_data, summary):
    filename = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return filename

# Уже используется with - проблемы нет, но добавить обработку ошибок:
def save_analysis(messages_data, summary):
    filename = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return filename
    except Exception as e:
        print(f"❌ Ошибка сохранения анализа: {e}")
        return None
```

---

## Фаза 3: Алгоритмические оптимизации (Средний приоритет)

### 3.1 Оптимизация подсчёта приоритетных пользователей [O(n) → O(n)]

**Проблема:** Квадратичная сложность при подсчёте.

**Решение:**
```python
# Было:
for priority_user in PRIORITY_USERS:
    if priority_user in unique_senders:
        priority_msg_count = sum(1 for msg in optimized if msg['sender'] == priority_user)

# Станет:
from collections import Counter

sender_counts = Counter(msg['sender'] for msg in optimized_messages)
for priority_user in PRIORITY_USERS:
    if priority_user in unique_senders:
        count = sender_counts.get(priority_user, 0)
        print(f"   ✅ {priority_user}: найдено {count} сообщений")
```

**Эффект:** При 1000 сообщениях и 10 приоритетных пользователей: 10,000 → 1,000 операций.

---

### 3.2 Использование скомпилированных регулярных выражений

**Проблема:** Повторная компиляция regex в `convert_markdown_to_html()`.

**Решение:**
```python
# Было:
para_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', para_text)

# Станет (использовать уже скомпилированные константы):
para_text = MD_BOLD_RE.sub(r'<b>\1</b>', para_text)
para_text = MD_ITALIC_RE.sub(r'<i>\1</i>', para_text)
para_text = MD_LINK_RE.sub(r'<a href="\2">\1</a>', para_text)
```

---

### 3.3 Устранение двойного вызова get_chat()

**Проблема:** Два await для одного event.

**Решение:**
```python
# Было:
chat = await event.get_chat()  # строка 1898
# ...
chat = await event.get_chat()  # строка 1934

# Станет:
chat = await event.get_chat()
chat_name_log = chat.title if hasattr(chat, 'title') else "Private"
# ...
chat_name = chat.title if hasattr(chat, 'title') else "чата"  # использовать ту же переменную
```

---

## Фаза 4: Улучшение обработки ошибок (Средний приоритет)

### 4.1 Убрать бесполезную проверку кодировки

**Проблема:** `encode('utf-8')` для Python 3 строк никогда не даёт ошибки.

**Решение:**
```python
# Было:
try:
    system_content.encode('utf-8')
    user_content.encode('utf-8')
except UnicodeEncodeError as ue:
    print(f"⚠️  Ошибка кодировки в контенте: {ue}")
    user_content = user_content.encode('utf-8', errors='ignore').decode('utf-8')

# Станет:
# Удалить весь блок, он не нужен
# Если нужна санитизация - использовать другой подход:
user_content = user_content.replace('\x00', '')  # NULL bytes могут быть проблемой
```

---

### 4.2 Исправить проверку is_valid_summary()

**Проблема:** Ненадёжная эвристика для определения JSON.

**Решение:**
```python
# Было:
def is_valid_summary(text):
    text_stripped = text.strip()
    if text_stripped.startswith('{') and '"metadata"' in text_stripped:
        return False
    return True

# Станет:
def is_valid_summary(text):
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
```

---

### 4.3 Защита от деления на ноль

**Проблема:** Деление при малом размере сообщений.

**Решение:**
```python
# Было:
ratio = max_chars / len(messages_json)

# Станет:
if len(messages_json) < 100:  # минимальный размер для анализа
    print("⚠️ Слишком мало данных для анализа")
    return "❌ Недостаточно данных", None
    
ratio = max_chars / len(messages_json)
```

---

## Фаза 5: Рефакторинг и улучшения (Низкий приоритет)

### 5.1 Добавить LRU-кэш для get_sender_name()

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def _get_sender_name_cached(sender_id, first_name, last_name, title):
    # ... логика
    pass
```

---

### 5.2 Использовать aiofiles для файловых операций

```python
# Было:
with open(filename, 'r', encoding='utf-8') as f:
    content = f.read()

# Станет:
import aiofiles

async def load_users_from_file(filename):
    async with aiofiles.open(filename, 'r', encoding='utf-8') as f:
        content = await f.read()
```

**Примечание:** Требует добавления `aiofiles` в requirements.txt.

---

### 5.3 Использовать pathlib вместо os.path

```python
# Было:
if os.path.exists(filename):

# Станет:
from pathlib import Path
if Path(filename).exists():
```

---

### 5.4 Исправить параметр model в create_summary()

```python
# Было:
async def create_summary(chunks, chat_id_str, model=GEMINI_DEFAULT_MODEL, ...):
    actual_model = GEMINI_DEFAULT_MODEL  # Игнорируем параметр!

# Станет:
async def create_summary(chunks, chat_id_str, model=None, ...):
    actual_model = model or GEMINI_DEFAULT_MODEL  # Используем параметр или дефолт
```

---

## Порядок выполнения

```
Неделя 1 (Критично):
├── 1.1 IndexError fix
├── 1.2 asyncio.Lock для конфигурации
├── 1.3 Защита от None
└── 2.1 Graceful shutdown HTTP клиента

Неделя 2 (Оптимизация):
├── 3.1 Counter для подсчёта
├── 3.2 Использование скомпилированных regex
├── 3.3 Убрать двойной get_chat()
└── 4.1 Убрать бесполезную проверку кодировки

Неделя 3 (Улучшения):
├── 4.2 Улучшить is_valid_summary()
├── 4.3 Защита от деления на ноль
└── 5.x Рефакторинг по желанию
```

---

## Тестирование после каждой фазы

### Минимальный тест-кейс:
1. `/sum` в чате с сообщениями - ✅ работает
2. `/sum 1h` в чате без сообщений за 1 час - ✅ не падает
3. `/add_excluded test_user` × 5 быстро - ✅ консистентность
4. Ctrl+C для остановки - ✅ нет ошибок закрытия
5. `/sum` на 1000+ сообщений - ✅ производительность

---

## Связанные файлы

- [main.py](../main.py) - основной файл для исправлений
- [requirements.txt](../requirements.txt) - добавить aiofiles (опционально)
- [test_bot.py](../test_bot.py) - обновить тесты
