---
name: generate-module-index
description: >
  Regenerate MODULE_INDEX.md from file headers across all code files in the project.
tags:
  - documentation
  - automation
  - index
triggers:
  - rebuild module index
  - refresh MODULE_INDEX.md
alwaysApply: false
---

**Команда**: `rebuild module index` или `refresh MODULE_INDEX.md`
**Назначение**: Автоматически пересоздать MODULE_INDEX.md из file headers

Шаги:
1. Найти все .py/.mqh/.ipynb в проекте
2. Извлечь file headers
3. Парсить секции: Назначение, Входные данные, Выходные данные, Зависимости
4. Сгенерировать MODULE_INDEX.md
5. Показать diff и запросить подтверждение

Полезно после массовых изменений или если MODULE_INDEX.md потерял синхронизацию.
