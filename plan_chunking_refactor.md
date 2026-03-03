# План рефакторинга: Разбиение по символам вместо сообщений

## Цель
Заменить разбиение по количеству сообщений на разбиение по символам с сохранением целостности сообщений и перехлёстом.

## Ограничения
- Лимит контекста: 128,000 токенов
- Целевое использование: ≤35% (~45,000 токенов ≈ 90,000 символов для кириллицы)
- Минимальный перехлёст: 5%
- Сохранение целостности сообщений (не разрезать посередине)

## Алгоритм разбиения

### Шаг 1: Подготовка
```python
MAX_CHUNK_CHARS = 90000  # ~35% от лимита 128k токенов
OVERLAP_CHARS = int(MAX_CHUNK_CHARS * 0.05)  # 5% = 4500 символов
```

### Шаг 2: Оценка размера сообщения
Для каждого сообщения вычисляем его вклад в JSON:
```python
def estimate_json_size(msg):
    # Примерная оценка: {"id":X,"s":"...","t":"...","r":X}
    base = 30  # фиксированная часть JSON
    sender_len = len(msg['sender']) * 1.1  # +10% на экранирование
    text_len = len(msg['text']) * 1.1
    return base + sender_len + text_len
```

### Шаг 3: Построение чанков
```python
def build_chunks_by_chars(messages_data, max_chars=90000, overlap=4500):
    chunks = []
    current_chunk = []
    current_size = 0
    
    for msg in messages_data:
        msg_size = estimate_json_size(msg)
        
        # Если сообщение превышает лимит одного чанка — обрезаем
        if msg_size > max_chars:
            msg = truncate_message(msg, max_chars)
            msg_size = estimate_json_size(msg)
        
        # Проверяем, влезет ли в текущий чанк
        if current_size + msg_size > max_chars and current_chunk:
            # Сохраняем текущий чанк
            chunks.append(current_chunk)
            
            # Рассчитываем перехлёст
            overlap_messages = []
            overlap_size = 0
            
            # Идём с конца текущего чанка назад
            for prev_msg in reversed(current_chunk):
                prev_size = estimate_json_size(prev_msg)
                
                overlap_messages.insert(0, prev_msg)
                overlap_size += prev_size
                if overlap_size >= overlap:
                    break
            
            # Начинаем новый чанк с перехлёста
            current_chunk = overlap_messages + [msg]
            current_size = overlap_size + msg_size
        else:
            current_chunk.append(msg)
            current_size += msg_size
    
    # Добавляем последний чанк
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks
```

## Структура реализации

### 1. Новые константы (main.py)
```python
# Конфигурация разбиения на чанки (по символам)
CHUNK_MAX_CHARS = 90000      # Максимум символов в чанке (~35% контекста)
CHUNK_OVERLAP = 4500     # Минимум перехлёста (5%)

```

### 2. Функция оценки размера
```python
def estimate_message_json_size(message):
    """Оценивает размер сообщения в JSON представлении"""
    # Базовая структура: {"id":X,"s":"...","t":"..."}
    # +10% на экранирование кавычек и спецсимволов
    base_size = 25  # {"id":,"s":"","t":""}
    id_size = len(str(message['message_id']))
    sender_size = int(len(message['sender']) * 1.1)  # +10% на Unicode
    text_size = int(len(message['text']) * 1.1)      # +10% на экранирование
    reply_size = 15 if message.get('reply_to') else 0  # ,"r":X
    
    return base_size + id_size + sender_size + text_size + reply_size
```

### 3. Новая функция разбиения
Заменить `split_messages_into_chunks` на `split_messages_by_chars`

### 4. Валидация ответа API
```python
def is_valid_summary(text):
    """Проверяет, что ответ содержит саммари, а не сырой JSON"""
    if not text:
        return False
    # Если текст начинается с { или содержит "metadata": — это JSON
    text_stripped = text.strip()
    if text_stripped.startswith('{') and '"metadata"' in text_stripped:
        return False
    if text_stripped.startswith('```json'):
        return False
    return True
```

### 5. Retry логика
При невалидном ответе — повторить запрос с усиленным промптом.

## Интеграция с существующим кодом

### Изменения в create_summary()
1. Убрать старую логику проверки размера (строки 1071-1081)
2. Использовать новую функцию разбиения
3. Добавить валидацию после получения ответа
4. При необходимости — retry с дополнительной инструкцией

## Тестирование

### Тест 1: Размер чанков
- Проверить, что ни один чанк не превышает 90k символов

### Тест 2: Перехлёст
- Проверить, что чанки пересекаются на 5-10%

### Тест 3: Целостность сообщений
- Проверить, что сообщения не разрезаны

### Тест 4: Валидация
- Проверить обработку JSON-ответа
