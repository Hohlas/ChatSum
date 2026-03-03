---
name: mql4-specifics
description: Специфика работы с MQL4 кодом (кодировка UTF-16LE, MetaEditor)
globs:
  - "**/*.mq4"
  - "**/*.mqh"
alwaysApply: false
---

При работе с .mq4/.mqh:
- Кодировка: UTF-16LE (не UTF-8)
- File header: используй //+--...--+ вместо #
- Документация: docs/mt4/ (отдельно от Python)
- Тестирование: только в MetaTrader 4 Strategy Tester

Не пытайся запускать MQL4-код в Python-окружении.

## Examples

### ✅ Read MQL4 file with correct encoding
```python
with open('MT/MQL4/Include/lib_PIC.mqh', 'r', encoding='utf-16-le') as f:
    content = f.read()
```
