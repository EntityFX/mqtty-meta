# MqttY: модель и интерфейсы

MqttY рассчитывает модель брокеров, клиентов и сети. Реальные MQTT-измерения выполняет [MqttBenchmark](../mqtt-benchmark/README.md). Профиль задаёт наблюдаемые ограничения модели и не воспроизводит все внутренние механизмы исходного брокера.

| Задача | Документ |
| --- | --- |
| Построить и проверить профиль | [Калибровка v3](calibration-v3.md) |
| Настроить пересылку | [MqttRelay](mqtt-relay.md), [федерация](federation/README.md) |
| Распределить клиентские соединения | [Балансировка](load-balancing/README.md) |
| Редактировать и просматривать граф | [Designer](designer.md), [каталог топологий](../topologies/README.md) |
| Развернуть Web на Linux | [Руководство](web-deployment.md) |
| Проверить готовность Web | [Сохранённые результаты](../../reports/web/README.md), [критерии W1–W5](../plans/completion.md) |
| Повторить переносимую симуляцию | [Windows → Linux/e2k](../../reproducibility/README.md) |

Научные границы модели зафиксированы в [контракте сети](../dissertation/network-model-contract.md) и [карте метода](../dissertation/broker-twin-method-map.md). Итоги экспериментов находятся в [отчётах](../../reports/README.md), а не в нескольких параллельных планах новых запусков.
