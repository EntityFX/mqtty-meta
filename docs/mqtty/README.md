# MqttY: симуляция брокеров и сетей

MqttY исполняет модель времени, сетей и MQTT-обмена. Профиль реального брокера поступает из натурных наблюдений MqttBenchmark. Идеальные демонстрации работают без такого профиля; их успешность не доказывает натурную точность двойника.

| Задача | Документ |
| --- | --- |
| Понять ядро, контракты и модель MQTT | [Архитектура MqttY](architecture.md) |
| Построить профиль по observations | [Калибровка](calibration-v3.md) |
| Разобраться в эмпирических распределениях v4 | [Эмпирическая модель](empirical-fidelity.md) |
| Разобраться в бюджете обслуживания | [EmpiricalServiceBudget](empirical-service-budget.md) |
| Выполнить BrokerFidelity или сеть | [Сетевые эксперименты](network-experiments.md) |
| Проверить точность и повторяемость | [Валидация](validation.md) |
| Создать собственный граф через API | [Своя топология](custom-topology.md) |
| Понять устройство ретрансляции | [MqttRelay](mqtt-relay.md) |
| Исследовать назначение MQTT-соединений | [Балансировка](load-balancing/README.md) и [план развития](load-balancing/design.md) |
| Сравнить single, Relay и статическую федерацию | [Федерация](federation/README.md) и [границы реализации](federation/design.md) |
| Работать с графическим редактором | [Designer](designer.md) |
| Разобраться в происхождении тестового fixture | [Тестовые данные](testing-fixtures.md) |

Сценарии серий: [балансировка](load-balancing/automation.md), [федерация](federation/automation.md). Результаты и ограничения находятся в [отчётах MqttY](../../reports/mqtty/README.md).

Основной solution находится в `mqtty/src/EntityFX.MqttSimulator`. Исполняемые примеры и сценарии, привязанные к структуре исходников, остаются в `mqtty/doc/examples`; их перемещение изменило бы пути сборки. Дополнительные конфигурации мета-репозитория находятся в [examples](examples/).
