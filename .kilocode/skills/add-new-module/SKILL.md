---
name: add-new-module
description: >
  Create a new module or add documentation to an existing module. Includes file header, markdown docs, module index and data flow entries.
tags:
  - documentation
  - automation
  - scaffold
triggers:
  - create module [name]
  - new script [name]
  - doc this [file]
  - document [file]
applies_to:
  - "*.py"
  - "*.mq4"
  - "*.mqh"
  - "*.ipynb"
alwaysApply: false
---

## Режим 1: Создание нового модуля

**Команда**: `create module [имя]` или `new script [имя]`
**Назначение**: Создать новый модуль со всей необходимой документацией

Шаги:
1. Спросить параметры:
   - Директория (processing/statistics/ML)
   - Назначение (одна строка)
   - Входные/выходные данные
2. Создать файл с file header
3. Создать docs/[категория]/[имя].md
4. Добавить в MODULE_INDEX.md
5. Добавить в DATA_FLOW.md (если нужно)
6. Открыть файл в редакторе

Пример:
> create module feature_engineering
Директория: processing
Назначение: Создание дополнительных признаков из фракталов
Входы: Nero_normalized.csv
Выходы: Nero_features.csv
✅ Создан processing/feature_engineering.py
✅ Создан docs/data_preprocessing/feature_engineering.py.md
✅ Обновлён MODULE_INDEX.md

## Режим 2: Документирование существующего модуля

**Команда**: `doc this [файл]` или `document [файл]`
**Назначение**: Создать полную документацию для существующего недокументированного модуля

Шаги:
1. Проверить наличие file header в коде
   - Если нет → создать по шаблону 000-documentation.md
2. Создать docs/[категория]/[модуль].md
   - Использовать шаблон из 000-documentation.md
3. Добавить запись в MODULE_INDEX.md
4. Обновить DATA_FLOW.md (если участвует в pipeline)
5. Показать diff и запросить подтверждение

Пример:
> doc this processing/normalize.py
Создаю документацию...
- ✅ File header добавлен
- ✅ docs/data_preprocessing/normalize.py.md создан
- ✅ MODULE_INDEX.md обновлён
