---
name: skill-before-manual
description: Проверка наличия skill перед ручным выполнением рутинной задачи
alwaysApply: true
---

Перед выполнением рутинной задачи проверь доступные skills.

Существующие команды:
- `sync docs` — синхронизация документации
- `doc this` — документирование файла
- `check docs` — проверка актуальности
- `create module` — создание нового модуля
- `check data impact` — анализ зависимостей данных

Если skill не реализован:
1. Выполни задачу вручную
2. Предложи создать skill для автоматизации

## Examples

### ✅ Check skills before manual work
```markdown
# Перед обновлением документации:
1. Проверь доступные skills
2. Найдена команда: `sync docs`
3. Выполни: sync docs processing/normalize.py
```
