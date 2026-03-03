---
name: file-handling
description: Технические правила работы с файлами проекта (кодировки, большие CSV)
globs:
  - "**/*.mqh"
  - "**/*.mq4"
  - "**/*.csv"
  - "**/*.parquet"
alwaysApply: false
---

# Правило: Работа с файлами

## MQL4 файлы (*.mqh, *.mq4)

**Кодировка**: UTF-16LE (НЕ UTF-8)

**Чтение**:
```python
with open('file.mqh', 'r', encoding='utf-16-le') as f:
    content = f.read()
```



## CSV файлы (*.csv)
Проблема: Файлы проекта могут быть >10 MB (Nero.csv, train/test выборки)

❌ НЕ открывай целиком:

```python
df = pd.read_csv('Nero.csv')  # НЕ делай так
```

✅ Используй sampling:

```bash
# В терминале
head -n 100 Nero.csv
tail -n 100 Nero.csv

# В Python
df = pd.read_csv('Nero.csv', nrows=100)
```

## Parquet файлы
При работе с большими данными предпочитай .parquet вместо .csv:

```python
df.to_parquet('output.parquet')
df = pd.read_parquet('input.parquet')
```

## Examples

### ✅ Read MQL4 file with UTF-16LE encoding
```python
with open('MT/MQL4/Include/lib_PIC.mqh', 'r', encoding='utf-16-le') as f:
    content = f.read()
```

### ✅ Sample large CSV for exploration
```python
df = pd.read_csv('Nero.csv', nrows=100, sep=';')
print(df.head())
print(df.describe())
```
