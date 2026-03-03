---
name: no-csv-context
description: Запрет загрузки CSV в контекст (использовать sampling/streaming)
globs:
  - "**/*.csv"
alwaysApply: true
---

НИКОГДА не помещай .csv файлы целиком в контекст.

Используй:
- head(10) для просмотра структуры
- describe() для статистики
- sample(100) для анализа паттернов

Для больших операций используй streaming/chunking.

## Examples

### ✅ Sample for exploration
```python
# Explore structure
df = pd.read_csv('Nero.csv', nrows=100)
print(df.head())
print(df.describe())

# For analysis: streaming
for chunk in pd.read_csv('Nero.csv', chunksize=1000):
    process(chunk)
```
