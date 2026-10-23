# Контракт кампании v3

Практические команды находятся в [локальном запуске](local-stand.md) и [двухмашинном запуске](two-machine-stand.md). Этот документ задаёт единую методику для обоих маршрутов и ОС.

## Матрица и входы

Стандарт: Aedes/Mosquitto/ActiveMQ/EMQX × payload 16/256 bytes × QoS 0/1/2 × publishers 1/16/64/128 × 3 repeats = **288 успешных ключей**, **96 агрегированных точек**. ActiveMQ означает Classic. Каждый запуск создаёт N publishers и одного subscriber. Seed 20260909 определяет порядок SHA-256 от seed/key; брокеры группируются для последовательного исполнения.

Campaign JSON (`schemaVersion: 3`) описывает нагрузку и gates; stand JSON (`schemaVersion: "broker-stand.v1"`) описывает endpoints, executable/launcher и ресурсы. `NewNativeStand.ps1` создаёт native-входы и задаёт `customImageBrokers: []`. Исходный `config/broker-stand.v1.json` — контейнерный инвентарь, его не используют в маршруте без Docker. Поля dockerContext/serviceName/containerName остаются в native schema как совместимые имена; Docker-процесс не вызывается.

| Параметр | Стандарт |
|---|---|
| warmup / measurement / cooldown | 5 / 30 / 5 секунд |
| drainTimeout / quietPeriod | 30 / 2 секунды |
| maxAttempts | 3 на ключ |
| preflightRttProbes / preflightSysWaitSeconds | 20 / 10 секунд ожидания SYS |
| transportTimeoutSeconds | 10 |
| telemetryCaptureReserveSeconds | 20 |
| telemetryReadinessTimeoutSeconds | 60 |
| CPU gate / maximum guard interval | 70% / 1.25 секунды |
| Network gate | 80% для удалённого физического NIC; Aedes informational в текущей методике; loopback всегда null |

Источники значений: [CampaignDefaults](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/CampaignDefaults.cs), [CampaignDefinition](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/CampaignDefinition.cs). Генератор записывает network policy явно. Изменение политики — новая campaign identity; старый Aedes gate 85% относится только к [исторической серии](../../reports/history/setup/platform-results.md).

CLI позволяет другие размеры матрицы/длительности, aggregate требует покрытие именно заданного config. Калибратор MqttY строже: один брокер (72 ключа, 24 точки), короткий pilot или иное число repeats не принимаются как стандартная калибровка. Минимальное время 288×40 секунд фаз — 3.2 часа; сюда не входят preflight, запуск каждого процесса, reserve/capture, drain и retries.

## Идентичность и возобновление

Запуск требует чистых tracked файлов MqttBenchmark; `--benchmark-repo` указывает репозиторий исходников, не просто каталог published bin. Campaign manifest фиксирует ID, точные config/stand SHA-256, deployment/CPU mode и commit. Парсинг и хэш используют один byte snapshot. Stand snapshot проверяется до действий контроллера; его изменённые bytes не могут незаметно войти в новую попытку.

`matrix --resume` с теми же `--config`, `--stand`, `--campaign` проверяет seals и observations, пропускает только валидные успехи. Незавершённая unsealed попытка остаётся в истории и расходует retry budget. Удалять её ради повторного счётчика нельзя. Третья неудача останавливает кампанию, последующий resume не снимает этот предел. CLI `--max-attempts` меняет budget явно. Изменение tracked исходников, config или stand запрещает продолжение; новая машина/версии/конфигурации требуют новой кампании даже там, где CLI не может автоматически обнаружить их изменение.

Native identity проверяет фактический listener PID, стабильную process start identity, resolved executable SHA-256 и launcher SHA-256. Конфигурационные файлы хэшируются при старте и проверяются во время действий этого run. Это не полная фиксация всех библиотек, env или машины: оператор дополнительно сохраняет зависимости и состояние окружения. Не обновляйте бинарные файлы, конфигурации или node_modules в ходе серии. External процесс принадлежит оператору; его state/reset policy также фиксируется.

## Условия успешной попытки

1. Validate → start/attach → MQTT health CONNECT/CONNACK → clock. Native local имеет один host clock; SSH проверяет remote clock с консервативной uncertainty. Default stand bound: abs(offset)+uncertainty <=3000 ms, допустимый wall-clock step <=100 ms. Это не сертификация синхронизации до миллисекунды.
2. Protocol preflight: отдельные CONNECT/SUBSCRIBE/PUBLISH/доставка для QoS 0/1/2, RTT echo. `$SYS/broker/version` может отсутствовать: null/unavailable с сохранённой диагностикой, версия executable записывается отдельно.
3. Capture сохраняет первый sample и атомарно публикует telemetry-ready **до workload**. Конец sampler публикуется отдельно. Workload-end-fence использует monotonic timestamp того же listener/runtime; последний sample должен быть строго позже fence.
4. Load-generator guard на Windows/Linux получает baseline и интервалы, покрывающие actual measurement, включая in-flight completion. Loopback использует whole-system CPU и informational network; remote IPv4 требует физический routed NIC с положительной скоростью. Windows проверяет adapter metadata, Linux — procfs/sysfs. Виртуальный NIC контроллера не проходит remote guard.
5. Каждый пересекающий measurement интервал оценивается целиком. CPU и RX+TX/link должны быть в заданных пределах; counter reset, route/link change, разрывы, overlapping reads и неполное покрытие отклоняют попытку. Нет bypass guard. One-second averages не видят все субсекундные пики.
6. Result и SHA-256 seal сохраняются после завершения writers/cleanup. QoS1/2 без completed publishes не имеют требуемой latency и не считаются успешными. Отказы/partial bytes сохраняются.

Телеметрия брокера и guard генератора — разные источники. Формулы нагрузки и состава артефактов: [метрики](artifact-format.md). Aggregate проверяет полное уникальное покрытие, identity/modes и seals; не смешивает разные кампании. Команда обязательно содержит `--stand`, даже если aggregate не запускает брокер.

## Ошибки и остановка

| Ошибка | Действие |
|---|---|
| Занят MQTT-порт | Проверить listener на broker host; остановить принадлежащий эксперименту процесс либо выбрать новый port/inventory до новой кампании |
| Неверный executable/launcher SHA-256 | Проверить путь/дистрибутив; пересоздать входы для новой серии, не менять pin существующей кампании |
| Изменены входы при resume | Восстановить точные исходные bytes/commit либо начать отдельную кампанию |
| Неполная матрица | Завершить недостающие ключи в той же допустимой identity; не достраивать observations выдуманными значениями |
| Output существует | Выбрать новый output; для raw campaign использовать только поддержанный resume |
| Guard/clock/readiness failure | Сохранить failed attempt, устранить причину; не объявлять протокольную доступность успешным измерением |

Остановка CLI — Ctrl+C. Managed cleanup прекращает только проверенный процесс; external оставляет его оператору. При потерянном SSH применяются правила lease recovery [двухмашинного руководства](two-machine-stand.md). Default v3 retry/resume и sealed results одинаковы при запуске через RunNativeCampaign.ps1 и отдельные CLI-команды.
