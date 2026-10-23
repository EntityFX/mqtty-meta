# Обработчик данных теста

0. Установить зависимости

```
pip install -r requirements.txt
```

или

```
python -m pip install -r requirements.txt
```

1. Заменить названия входных файлов

```
# файлы с данными нагрузки на CPU из Prometheus
cpu_usage_files = [
    "cpu.nb.m256.2024-03-13T03_07_03__10.114.22.10.json",
    "cpu.nb.m256.2024-03-13T03_07_03__10.114.22.50.json",
]

# файлы с данными теста
result_files = [
    "results.nb.m256.2024-03-13T03_07_03.csv",
]
```

2. Запустить `datamerge.py` или `datamerge.ipynb`

3. Результаты обработки сохраняются в файлы `q_#.csv`, где `#` - уровень QoS

4. Для последующей обработки импортировать файлы `q_#.csv` в таблицу Excel `MqttBenchmarkResults.xlsx`
