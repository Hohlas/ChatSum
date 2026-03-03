---
name: check-data-impact
description: >
  Analyze downstream dependencies when changing data formats to prevent breaking changes in the pipeline.
tags:
  - analysis
  - impact
  - data
triggers:
  - check data impact [file]
  - impact analysis [file]
applies_to:
  - "*.csv"
  - "*.parquet"
alwaysApply: false
---

**Команда**: `check data impact [файл]` или `impact analysis [файл]`
**Назначение**: Показать, какие скрипты будут затронуты при изменении формата данных

Шаги:
1. Прочитать file header файла (секция "Выходные данные")
2. Найти в MODULE_INDEX.md все скрипты, использующие эти файлы
3. Рекурсивно найти downstream-зависимости
4. Вывести граф зависимостей

Пример:
> check data impact processing/normalize.py

Изменение processing/normalize.py повлияет на:
⬇️ Nero_normalized.csv
  └─ processing/label_main.py
      ├─ Nero_train_labeled.csv
      │   └─ ML/train.py (когда будет создан)
      ├─ Nero_val_labeled.csv
      │   └─ ML/validate.py (когда будет создан)
      └─ Nero_test_labeled.csv
          └─ ML/test.py (когда будет создан)
