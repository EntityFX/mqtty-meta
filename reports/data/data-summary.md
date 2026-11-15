# Данные исследований: агрегированные описания наборов

Объединяет README из `reports/data/*/`: доказательства приёмочной серии федерации, проверка полносвязного Relay, данные опыта с десятью брокерами и транзитной сетью.

---

## 1. Доказательства приёмочной серии федерации (federation-study-v1)

**Источник:** `reports/data/federation-study-v1/README.md`

Основной набор: **54 симуляции и 6 повторов**. Технических отказов нет. Полнота доставки оценивается отдельно от завершения процессов.

Опубликовано: study-plan.json, runs.json, runs.csv, aggregates.json, verification.json, warnings.json, validation-tests.json, reporting-verification.json, publication-sha256.json, archive-receipt.json, initial-archive-receipt.json, cross-version-comparison.json, SVG/Mermaid топологии.

Границы интерпретации: три seeds описывают только условный разброс при неизменном профиле. CI95 не рассчитывается для неполной группы. У федерации один служебный publisher на направление; у Relay отдельные экземпляры для разных источников. Все доставки, включая drain, делятся на длительность подачи нагрузки.

У профиля Mosquitto разные исходные точки N=16 и N=64. Дополнительные пересылки и fanout не калибровались.

Повторение: `python tools/federation/study.py run --work-dir <новый-каталог>`, проверка — `verify`, отчёт — `python tools/federation/report.py --work-dir <каталог>`.

---

## 2. Проверка полносвязного Relay (federation-full-mesh-v1)

**Источник:** `reports/data/federation-full-mesh-v1/README.md`

Relay соединяет каждый брокер со всеми остальными: 6 направлений для трёх брокеров, 12 — для четырёх. Каждый узел экспортирует только свой исходный поток; импорт доставляется локальным подписчикам без повторного экспорта. Физическая сеть остаётся кольцом.

Опубликовано: study-plan.json, runs.json, runs.csv, aggregates.json, verification.json, warnings.json, validation-tests.json (319 C# + 5 Python проверок), report-reproduction.json, reporting-verification.json, protocol-comparison.json, archive-receipt.json, publication-sha256.json, SVG/Mermaid топологии.

В идеальной модели доставка полная. В опытах с профилем Mosquitto условные потери исходной профильной точки применяются на нескольких этапах, поэтому возможна большая итоговая недоставка.

---

## 3. Данные опыта с десятью брокерами и транзитной сетью (federation-transit-10-v1)

**Источник:** `reports/data/federation-transit-10-v1/README.md`

Отчёт относится к отдельной программе Relay: 10 брокеров, 160 исходных publishers, 10 subscribers и один сетевой узел transit без MQTT-приложений. Физическая сеть — звезда; логическая — 90 направлений «каждый с каждым». Импортированные сообщения не экспортируются повторно.

Опубликовано: study-plan.json, runs.csv, runs.json, aggregates.json, verification.json, validation-tests.json (316 MqttY тестов + 7 Python-проверок), report-reproduction.json, archive-receipt.json, publication-sha256.json, SVG/Mermaid топологии.

Для идеальной модели проверяются все 2000 доставок и 1800 пересылок. Профиль Mosquitto используется строго в точке N=16; дополнительный fanout и пересылки натурно не калибровались.

---

## Связанные документы

- [Отчёты MqttY](../mqtty/mqtty-summary.md)
- [Отчёты MqttBenchmark](../mqtt-benchmark/README.md)
- [История кампаний](../history/history-summary.md)