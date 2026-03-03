---
name: module-index
description: Правило индексирования модулей в MODULE_INDEX.md при создании новых файлов
globs:
  - "*.py"
  - "*.mqh"
  - "*.ipynb"
alwaysApply: true
---

При создании нового .py/.mqh/.ipynb файла:
1. Добавь file header по стандарту 000-documentation.md
2. Добавь запись в MODULE_INDEX.md
3. Обнови DATA_FLOW.md, если файл участвует в pipeline

Формат записи в MODULE_INDEX:
## [путь/файл]
**Назначение**: [одна строка]
**Входы**: [файлы] | **Выходы**: [файлы]
**Использует**: [библиотеки] | **Используется в**: [скрипты]

## Examples

### ✅ Complete module entry
```markdown
## processing/label_main.py
**Назначение**: CLI оркестратор для полного конвейера обработки данных
**Входы**: MT/MQL4/Files/Nero.csv | **Выходы**: data/Nero_train_labeled.csv
**Использует**: pandas, numpy | **Используется в**: ML training pipeline
```
