# Статическая федерация MQTT

Режим: **Federation**; модель: **идеальная**; QoS: **1**; seed: **42**. Статус: **failed**.

| Показатель | Значение |
| --- | ---: |
| Запланировано | 10000 |
| Попытки исходных Publish | 10000 |
| Исходные завершения/с | 1000 |
| Ожидаемые пары ID–получатель | 40000 |
| Уникальные доставки | 38469 |
| Goodput доставок/с | 3846.9 |
| Недоставка среди ожидаемых пар | 0.038275 |
| Получили все subscribers | 9489 |
| p99 ACK, мс | 0.8 |
| p99 получения, мс | 1488.4 |
| Подавлено повторов | 49148 |
| Дубликаты у subscribers | 0 |
| Попытки пересылки | 77627 |
| Байты тем и payload пересылок | 3453633 |
| Переполнения очередей | 0 |
| Исчерпания числа переходов | 0 |
| Доставки в drain | 2589 |

![Физическая сеть](topology-physical.svg)

![Логические пути](topology-logical.svg)

## Формулы и ограничения

RPS исходных завершений = завершения только publishers / длительность нагрузки. Goodput = число уникальных пар ID–получатель / ту же длительность. Ожидаемые пары = API-попытки × число subscribers; потери включают немедленные отказы исходного Publish. Backpressure = заданные слоты − API-попытки. Drain входит в измерительную когорту; прогрев исключён по ID. Квантили рассчитаны по объединённым гистограммам методом верхнего ранга. QoS 0 ACK latency и показатели доставки при отсутствии subscribers неприменимы.

Это статическая пересылка, не самоорганизующийся алгоритм статьи Ribas–Spohn. Федерация рассылает по соседям с подавлением повторов; Relay соединяет каждый брокер со всеми остальными; экспортирует только исходные локальные сообщения и не пересылает импорт повторно. Темы input/routing/delivered разделены. CPU/RSS, EST, выбор ядра и отказоустойчивость не моделируются. Профилированные результаты не подтверждают натурную точность при дополнительной пересылке и fanout. Короткое окно проверяет механизм, а не стационарный p99.

## Причина отказа

```text
System.TimeoutException: drain timed out; partial evidence retained.
   at EntityFX.MqttY.Experiment.ExperimentLoops.Until(Func`1 condition, Int64 limitTicks, Action refresh, String timeoutMessage) in /home/entityfx/projects/mqtty-meta/mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Engines/Execution/ExperimentLoops.cs:line 19
   at EntityFX.MqttY.Federation.FederationRunner.<>c__DisplayClass0_0.<Run>g__Until|8(Func`1 condition) in /home/entityfx/projects/mqtty-meta/mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Engines/Federation/Execution/FederationRunner.cs:line 76
   at EntityFX.MqttY.Federation.FederationRunner.Run(FederationInput input, String output, Boolean diagnostics, CancellationToken cancellationToken, Action`2 onProgress, Boolean parallel, Int32 refreshStrategy, IDictionary`2 phaseWallSeconds, Action`2 networkPackets) in /home/entityfx/projects/mqtty-meta/mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Engines/Federation/Execution/FederationRunner.cs:line 50
```

Повтор: `run --config run-config.json --output <новый-каталог>`. Входы сохранены в inputs; summary.json не содержит сведений о компьютере. Хэши подтверждают целостность, проверяемость требует пересчёта и повторного запуска. SVG/Mermaid построены после сверки площадок, связей и расположения клиентов с GraphML.
