# File Mappings: Код → Документация

Справочник соответствий между файлами кода и их документацией для проекта SoSimple.

## Маппинги по директориям

### processing/

| Файл кода | Документация | Раздел DATA_FLOW.md |
|-----------|--------------|---------------------|
| processing/label_main.py | docs/data_preprocessing/label_main.py.md | Pipeline → Label |
| processing/label_signals.py | docs/data_preprocessing/label_signals.py.md | Pipeline → Label |
| processing/normalize.py | docs/data_preprocessing/normalize.py.md | Pipeline → Norm |

### statistics/

| Файл кода | Документация | Раздел DATA_FLOW.md |
|-----------|--------------|---------------------|
| statistics/statistics.py | docs/data_analysis/statistics.py.md | — |
| statistics/EDA.ipynb | docs/data_analysis/EDA.ipynb.md | — |

### ML/

| Файл кода | Документация | Раздел DATA_FLOW.md |
|-----------|--------------|---------------------|
| ML/train.py | docs/ml/train.py.md | — |
| ML/optimize.py | docs/ml/optimize.py.md | — |
| ML/data_loader.py | docs/ml/data_loader.py.md | — |
| ML/compare_architectures.py | docs/ml/compare_architectures.py.md | — |
| ML/baseline/baseline_experiments.py | docs/ml/baseline_experiments.py.md | — |
| ML/models/*.py | docs/ml/neural_networks.md | — |

### MT/MQL4/Include/

| Файл кода | Документация | Раздел DATA_FLOW.md |
|-----------|--------------|---------------------|
| MT/MQL4/Include/lib_PIC.mqh | docs/mql4/lib_PIC.mqh.md | Pipeline → MT4 Export |
| MT/MQL4/Include/*.mqh | docs/mql4/[имя].mqh.md | — |

## Шаблоны путей

### Python скрипты
```
{processing,statistics,ML}/[script].py → docs/{data_preprocessing,data_analysis,ml}/[script].md
```

### MQL4 заголовки
```
MT/MQL4/Include/[lib].mqh → docs/mql4/[lib].mqh.md
```

### Jupyter notebooks
```
statistics/[name].ipynb → docs/data_analysis/[name].ipynb.md
```

## Правила обработки

1. Если документация не существует → предложить создать
2. Если скрипт участвует в pipeline → обновить DATA_FLOW.md
3. Если изменения значительные → обновить CHANGELOG.md
