---
name: create-eda-report
description: >
  Run automated project-specific exploratory data analysis using statistics.py and EDA.ipynb.
tags:
  - analysis
  - eda
  - data
triggers:
  - "analyze"
  - "analyze [file]"
  - "run eda"
applies_to:
  - "statistics/Nero.csv"
alwaysApply: false
---

**Команда**: `run eda` или `analyze`
**Назначение**: Запустить полный цикл статистического анализа и EDA для датасета Nero.

Шаги:
1. Запустить сбор статистики:
   `cd statistics && python statistics.py`
   * Генерирует: `statistics_summary.json`, `class_balance_report.csv`, `feature_distributions.csv`, `nero_sample_stratified.csv`, `class_statistics.json`

2. Сгенерировать EDA отчёт из ноутбука:
   `cd statistics && jupyter nbconvert --execute --to notebook --output EDA_executed --output-dir ./reports EDA.ipynb`
   * Генерирует: `reports/EDA_executed.ipynb` и графики в `plots/`

Выходные данные:
- JSON/CSV отчеты в `statistics/`
- Выполненный ноутбук в `statistics/reports/`
- Графики в `statistics/plots/`
