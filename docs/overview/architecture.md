# Архитектура: измерение, профиль, модель, доказательства

MqttBenchmark измеряет реальные MQTT-брокеры. MqttY строит профиль по выбранным наблюдениям и рассчитывает модель клиентов, брокеров и сети. Двойник воспроизводит ограниченный набор наблюдаемых свойств; успешное выполнение не означает точность модели.

| Компонент | Ответственность | Основное руководство |
| --- | --- | --- |
| [MqttBenchmark](../../MqttBenchmark/) | Нагрузка на реальные брокеры, управление кампанией, первичные события и сводные показатели | [Запуск](../mqtt-benchmark/runbook.md) |
| [mqtty](../../mqtty/) | Ядро симуляции, профиль брокера, Relay, федерация и балансировка соединений | [Модель и режимы](../mqtty/README.md) |
| MqttY.Web | Редактирование поддерживаемой топологии, очередь и отдельные процессы моделей, мониторинг и экспорт | [Развёртывание](../mqtty/web-deployment.md) |
| [Метарепозиторий](../../README.md) | Фиксирует версии подмодулей, команды проверки, методику, данные и отчёты | [Навигация](../README.md) |
| [diss](../../diss/) | Авторский текст, библиография и сборка диссертации | [Карта материалов](../dissertation/README.md) |

## Поток данных и границы

1. Натурная кампания сохраняет конфигурацию, версии, первичные события и признаки ошибок. Определения attempted/completed/delivered и временных окон фиксирует [контракт измерений](measurement-contract.md).
2. Калибровка строит профиль из разрешённой обучающей части. Независимые контрольные наблюдения не используются для подбора этого же профиля. Формат и правила — [calibration-v3](../mqtty/calibration-v3.md).
3. Модель получает профиль, граф, нагрузку и seed. Relay пересылает между брокерами; федерация и балансировка имеют разные правила соединений и ресурсов. Режимы сравниваются при явно заданной полезной задаче и физической сети.
4. Сохраняются научные показатели, события и seals. Проверяющий независимо пересчитывает выбранные значения. [Формат артефактов](../mqtt-benchmark/artifact-format.md) и [воспроизводимость](../dissertation/reproducibility.md) определяют передачу результата.
5. Статья использует прошедшие и проваленные сравнения с ограничениями; техническое завершение процесса и научная приёмка учитываются раздельно.

Полной натурной кампанией управляют средства MqttBenchmark и сценарии экспериментов. Web управляет заданиями симуляции и не заменяет натурный контроллер. Runtime CPU/RSS отражают стоимость вычисления, а не CPU/память моделируемого брокера. Одинаковый seed проверяет повторяемость выбранной модели, но не доказывает её физическую точность.

## Инвентаризация кода

Ниже сохранён прежний автоматически сформированный срез. Он не является процентом готовности и не обновляется при редакции документации. Для отдельного обновления на согласованных версиях подмодулей используется `python tools/code-inventory.py`.

<!-- code-inventory:start -->

| Компонент / модуль | Вид | Файлов | Строк всего | Непустых строк |
| --- | --- | ---: | ---: | ---: |
| **Корневой репозиторий** | **Итого** | **83** | **13086** | **11859** |
| [tools](../../tools/) | Скрипты | 1 | 116 | 104 |
| [tools/campaign](../../tools/campaign/) | Скрипты | 34 | 6635 | 6078 |
| [tools/campaign](../../tools/campaign/) | Тестовые скрипты | 16 | 1244 | 1076 |
| [tools/dissertation](../../tools/dissertation/) | Скрипты | 9 | 2022 | 1836 |
| [tools/dissertation](../../tools/dissertation/) | Тестовые скрипты | 7 | 690 | 604 |
| [tools/documentation](../../tools/documentation/) | Скрипты | 4 | 275 | 240 |
| [tools/documentation](../../tools/documentation/) | Тестовые скрипты | 1 | 55 | 47 |
| [tools/federation](../../tools/federation/) | Скрипты | 4 | 890 | 822 |
| [tools/federation](../../tools/federation/) | Тестовые скрипты | 3 | 198 | 176 |
| [tools/load-balancing](../../tools/load-balancing/) | Скрипты | 3 | 861 | 787 |
| [tools/load-balancing](../../tools/load-balancing/) | Тестовые скрипты | 1 | 100 | 89 |
| **MqttBenchmark** | **Итого** | **177** | **14082** | **12782** |
| [EntityFX.MqttBenchmark](../../MqttBenchmark/src/EntityFX.MqttBenchmark/) | Модуль .NET | 28 | 1281 | 1139 |
| [EntityFX.MqttBenchmark.Bomber](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Bomber/) | Модуль .NET | 16 | 1338 | 1108 |
| [EntityFX.MqttBenchmark.Campaign](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/) | Модуль .NET | 64 | 3413 | 3183 |
| [EntityFX.MqttBenchmark.Cli](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Cli/) | Модуль .NET | 1 | 153 | 142 |
| [EntityFX.MqttBenchmark.Counters](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Counters/) | Модуль .NET | 4 | 254 | 199 |
| [EntityFX.MqttBenchmark.Scenario](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Scenario/) | Модуль .NET | 6 | 547 | 445 |
| [EntityFX.MqttBenchmark.StatAggregator](../../MqttBenchmark/src/EntityFX.MqttBenchmark.StatAggregator/) | Модуль .NET | 4 | 208 | 165 |
| [EntityFX.MqttBenchmark.Tests](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Tests/) | Тесты .NET | 19 | 3203 | 2996 |
| [data_processing](../../MqttBenchmark/data_processing/) | Скрипты | 1 | 102 | 78 |
| [docker](../../MqttBenchmark/docker/) | Скрипты | 2 | 64 | 60 |
| [scripts](../../MqttBenchmark/scripts/) | Скрипты | 26 | 3101 | 2888 |
| [scripts](../../MqttBenchmark/scripts/) | Тестовые скрипты | 3 | 299 | 266 |
| [src](../../MqttBenchmark/src/) | Скрипты | 2 | 14 | 8 |
| [src](../../MqttBenchmark/src/) | Тестовые скрипты | 1 | 105 | 105 |
| **mqtty** | **Итого** | **369** | **30876** | **26188** |
| [CustomNetwork](../../mqtty/doc/examples/custom-network/) | Пример .NET | 1 | 118 | 115 |
| [EntityFX.MqttY](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttSimulator/) | Модуль .NET | 57 | 6365 | 5110 |
| [EntityFX.MqttY.Cli](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Cli/) | Модуль .NET | 4 | 269 | 226 |
| [EntityFX.MqttY.Contracts](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttSimulator.Contracts/) | Модуль .NET | 52 | 1061 | 786 |
| [EntityFX.MqttY.Contracts.Mqtt](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Contracts.Mqtt/) | Модуль .NET | 40 | 1366 | 1154 |
| [EntityFX.MqttY.Contracts.Scenarios](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Contracts.Scenarios/) | Модуль .NET | 6 | 148 | 113 |
| [EntityFX.MqttY.Designer](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Designer/) | Модуль .NET | 39 | 5334 | 4692 |
| [EntityFX.MqttY.Federation.App](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/) | Модуль .NET | 37 | 1125 | 1085 |
| [EntityFX.MqttY.LoadBalancing.App](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App/) | Модуль .NET | 26 | 1099 | 1013 |
| [EntityFX.MqttY.MqttRelay.App](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.MqttRelay.App/) | Модуль .NET | 4 | 1071 | 896 |
| [EntityFX.MqttY.Plugin.Mqtt](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Mqtt/) | Модуль .NET | 57 | 6327 | 5253 |
| [EntityFX.MqttY.Plugin.Scenarios](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Scenarios/) | Модуль .NET | 8 | 388 | 311 |
| [EntityFX.Tests.Integration](../../mqtty/src/EntityFX.MqttSimulator/EntityFX.Tests.Integration/) | Тесты .NET | 33 | 5961 | 5221 |
| [doc](../../mqtty/doc/) | Скрипты | 3 | 165 | 154 |
| [tools](../../mqtty/tools/) | Скрипты | 1 | 34 | 29 |
| [utils](../../mqtty/utils/) | Скрипты | 1 | 45 | 30 |
| **diss** | **Итого** | **6** | **410** | **364** |
| [biblio](../../diss/biblio/) | Скрипты | 1 | 102 | 97 |
| [book](../../diss/book/) | Скрипты | 5 | 308 | 267 |
| **Всего** | | **635** | **58454** | **51193** |

<!-- code-inventory:end -->
