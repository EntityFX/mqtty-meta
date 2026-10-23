# Балансировка подключений в MqttY

Приложение сравнивает один брокер, заранее распределённый пул, Round Robin и Least Connections. Backend выбирается перед CONNECT и сохраняется до окончания опыта. Нового solution нет: проект включён в существующий `EntityFX.Mqtty.sln`. Старые приложения, протокол и ядро не изменяются.

## Кто что делает

| Часть | Обязанность |
| --- | --- |
| Сотрудник | Выбирает демонстрацию или опыт с профилем, параметры нагрузки и при необходимости диск результата |
| MqttBenchmark и calibrate | Заранее измеряют настоящий брокер и создают профиль; это приложение их не запускает |
| LoadBalancing.App | Проверяет входы, назначает подключения, создаёт сеть, выполняет фазы, сохраняет результаты |
| Ядро MqttY и MQTT plugin | Исполняют модельное время, сетевые очереди и MQTT; дают готовые метрики backend |
| plot_results.py | Читает сохранённые таблицы и строит SVG; не запускает симуляцию |

## Демонстрация без конфига и реального профиля

Из корня репозитория **mqtty**:

```text
dotnet run --project src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App -c Release -- demo
dotnet run --project src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App -c Release -- demo --brokers 3 --publishers 10 --mode leastConnections --qos 2
```

Первая команда использует встроенные настройки. Вторая создаёт три идеальных брокера и десять publishers, назначает соединения по Least Connections и выполняет MQTT-обмен с QoS 2. Файлы конфигурации, профили настоящих брокеров и Python не нужны. `dotnet run` при необходимости сначала собирает приложение; после сборки те же аргументы можно передавать непосредственно DLL.

В консоли отображаются две диаграммы: число назначенных publisher-соединений и число уникальных сообщений, полученных receiver каждого брокера. Например, распределение десяти publishers между тремя брокерами:

```text
broker0  [####################] 4
broker1  [###############.....] 3
broker2  [###############.....] 3
```

Длина полосы сравнивается с максимумом в своей диаграмме; точное число стоит справа. Далее выводятся заданная и фактически отправленная нагрузка, goodput, недоставка, p99, получения во время drain и статус. Это результаты симуляции идеального механизма, а не измерения производительности конкретного настоящего брокера. Служебные receivers не участвуют в назначении publishers.

| Аргумент | По умолчанию | Значение |
| --- | --- | --- |
| `--brokers` | 4; для single — 1 | Число backend, от 1 до 64, не больше числа publishers |
| `--mode` | `roundRobin` | `single`, `directBalanced`, `roundRobin`, `leastConnections`; регистр не важен |
| `--publishers` | 64 | Всего отправителей, от 1 до 4096 |
| `--qos` | 1 | QoS публикации и подписки: 0, 1 или 2 |
| `--rps` | 1000 | Общая заданная интенсивность, сообщений/с |
| `--seconds` | 0.1 | Длительность нагрузки в модельных секундах: больше 0 и до 10, шаг 0.0001 |
| `--seed` | 42 | Seed опыта |
| `--output` | Отсутствует | Новый каталог для сохранения полного пакета результатов |

Дробные числа задаются через точку независимо от языка ОС. Прогрев составляет 0.01 модельной секунды, drain — до 1 секунды. Остальные сетевые настройки встроены и одинаковы для режимов. Действуют общие ограничения объёма опыта, описанные ниже; реальное время расчёта зависит от параметров и оборудования.

По умолчанию опыт выполняется в памяти и **не записывает файлы**. Для сохранения и последующего воспроизведения добавьте `--output` с новым путём на диске данных:

```text
dotnet run --project src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App -c Release -- demo --brokers 3 --publishers 10 --mode leastConnections --output /data/mqtt/demo-new
```

В этом каталоге будут сгенерированный конфиг, JSON/CSV, топология, Markdown-отчёт и SHA-256. Повторить опыт можно обычной командой `run --config <result>/run-config.json --output <new-result>`. Для остальных параметров опыта используйте обычный `run` с конфигом. Справка по демонстрации: `demo --help`.

## Сборка и проверка механизма

Нужен .NET SDK с возможностью сборки net6.0 и .NET 6 runtime для запуска. Команды ниже выполняются **из корня репозитория mqtty**; путь результата замените новым каталогом на диске данных. В Linux команды dotnet такие же.

```text
dotnet build src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App/EntityFX.MqttY.LoadBalancing.App.csproj -c Release
dotnet src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App/bin/Release/net6.0/EntityFX.MqttY.LoadBalancing.App.dll validate --config doc/examples/load-balancing/ideal.json
dotnet src/EntityFX.MqttSimulator/EntityFX.MqttY.LoadBalancing.App/bin/Release/net6.0/EntityFX.MqttY.LoadBalancing.App.dll matrix --config doc/examples/load-balancing/ideal.json --output /data/mqtt/lb-smoke
dotnet test src/EntityFX.MqttSimulator/EntityFX.Tests.Integration/EntityFX.Tests.Integration.csproj -c Release --filter "TestCategory!=LongRunning"
```

Идеальная матрица: **12 опытов** — четыре режима × три QoS, seed 42. Профили не используются; эти результаты проверяют механизм и учёт, а не точность двойника. `run` вместо `matrix` выполняет один опыт. `validate` только проверяет входы и показывает назначения.

## Реальные профили

Путь профиля разрешается относительно JSON конфигурации. При `ideal=false` нужен внешний профиль v3/v4; встроенный старый эталон не используется. Для каждого backend необходима точная измеренная точка `(brokerType, payloadBytes, publishQos, publishersOnBackend)`. Receivers не входят в число publishers. Подмена ближайшим N и интерполяция не допускаются.

Пример одной точки — JSON со следующими полями; сетевые числа являются допущениями опыта:

```json
{
  "schemaVersion": 1,
  "mode": "roundRobin",
  "brokerType": "Aedes",
  "backendCount": 4,
  "ideal": false,
  "brokerProfilePath": "profiles/aedes.json",
  "totalPublishers": 64,
  "payloadBytes": 16,
  "publishQos": 1,
  "subscribeQos": 1,
  "offeredRps": 20179.382680945022,
  "seed": 42,
  "warmupSeconds": 1,
  "measurementSeconds": 2,
  "drainTimeoutSeconds": 5,
  "tickPeriod": "00:00:00.0001000",
  "networkLength": 2,
  "network": {
    "capacityBytesPerSecond": 125000000,
    "throughputWindowTicks": 100,
    "queueCapacity": 100000,
    "transferTicks": 2
  }
}
```

Для профилированной `matrix` вместо одиночного `brokerProfilePath` укажите `brokerProfiles`: четыре объекта с `brokerType`, `brokerProfilePath`, `calibrationObservationsPath`, в порядке Aedes, Mosquitto, ActiveMQ, EMQX. В мета-репозитории готовые примеры с сохранёнными измерениями находятся в `docs/mqtty/examples/load-balancing/`.

Матрица: **144 опыта** = четыре типа × четыре режима × три QoS × seeds 42/43/44. Payload 16 байт; 64 publishers на single либо по 16 на каждом из четырёх backend. Общая offeredRps берётся из средней attemptedRps калибровочной точки N=64 и одинакова для четырёх режимов. SHA observations проверяется по provenance профиля; контрольные измерения для выбора нагрузки не используются. Длительности и фиксированная сеть берутся из конфигурации; последовательно обновляются и граф, и опыты. Реальное время расчёта зависит от оборудования и трафика; модельные секунды не равны реальным.

Проверяется совпадение commits источников, CPU/deployment modes, протокола покрытия и метода калибровки четырёх профилей. Это необходимая проверка совместимости; она сама по себе не доказывает равенство аппаратных условий. Исходные campaign identities сохраняются раздельно. Защита v1 ограничивает один опыт миллионом запланированных публикаций, 4096 publishers, 64 backend и 1024 сетевыми участками backend; payload разрешён только 16 или 256 байт. Это ограничения входов, а не гарантия потребления RSS; перед большим опытом всё равно нужен pilot.

## Что сохраняется и как повторить

Команды `run`, `matrix` и `demo --output` пишут отдельный новый каталог: `summary.json`, `manifest.json`, `inputs/`, переносимый `run-config.json`, `assignments.csv`, `backend-metrics.csv`, `histograms.json`, `phases.json`, `topology.graphml`, `topology.puml`, `report.md`, `sha256.json`. Матрица также сохраняет `matrix-summary.json` после каждого опыта. Ошибки подключения, подписки и drain содержат причину и частичные данные. Существующий каталог не перезаписывается. Обычный `demo` работает без записи на диск; при отказе причина видна в консоли.

Для повторения укажите `run --config <result>/run-config.json --output <new-result>`. Профиль читается из сохранённого snapshot. Сравнивайте `summary.json`: служебные сведения о компьютере, commit и бинарях вынесены в manifest. Одинаковые входы, сборка и seed должны давать одинаковый научный summary. Хэши подтверждают целостность сохранённых файлов; независимая проверка требует повторения расчёта.

Графики строятся отдельно стандартным Matplotlib:

```text
python -m pip install matplotlib
python doc/examples/load-balancing/plot_results.py /data/mqtt/lb-smoke
```

Скрипт строит графики backend из CSV. Для матрицы он также создаёт `comparison.csv` и графики по типам; столбец — среднее, точки — отдельные seeds. Это описание разброса, а не доверительный интервал. После добавления SVG скрипт обновляет реестры SHA-256.

## Метрики и ограничения

- `scheduledPublishes` — заданное расписание; `apiAttempts` — вызовы Publish; `sendRejected` — немедленные отказы метода. Разность scheduled−apiAttempts отражает непереданную нагрузку при ограничении подачи. Для QoS 1/2 один publisher ждёт завершения предыдущего обмена; исчезновение pending не считается само по себе доказательством успеха.
- Слоты подачи распределены поровну между publishers с сохранением общей дробной части интенсивности. У каждого собственный ограниченный запас слотов: `ceil(offeredRps × tickSeconds / publishers) + 1`. Занятый publisher не передаёт нагрузку свободному. Остаток и вытесненные слоты входят в scheduled−apiAttempts. Указанная доля offeredRps backend — план, а фактические попытки и получения показаны отдельно.
- `backendCompletedRps` — завершения backend / длительность measurement. `uniqueGoodput` — уникально полученные измерительные ID / ту же длительность. Поздние завершения и доставки в drain входят в когорту и показаны отдельно. Прогрев исключён.
- `attemptDeliveryLoss = 1−uniqueReceived/apiAttempts`; backend failure = `(rateRejected+publishFailed)/attempted`; backend loss = `1−delivered/expectedDeliveries`. Эти знаменатели различаются; нельзя смешивать показатели. Нулевой знаменатель даёт null/notApplicable.
- p50/p95/p99 завершения — объединённая гистограмма backend, а не среднее их p99; QoS 0 ACK latency неприменима. Receiver latency измеряется от вызова Publish до первого корректного получения ID.
- Один receiver подключён напрямую к каждому backend. Независимые брокеры **не объединяют подписки**. Результат относится к сумме локальных доставок, а не к единому кластерному подписчику.
- Моделируется выбор соединения, без стоимости TCP proxy, health checks, failover, миграции сессий и ресурсов CPU/RSS. Транспорт фиксирован; специальный EmpiricalServiceBudget одиночного BrokerFidelity не переносится на пул. Это исследовательская модель; её точность для настоящего балансировщика не подтверждена натурным эталоном.
- В постоянных одинаковых сессиях DirectBalanced, Round Robin и Least Connections имеют одинаковые назначения. Они должны давать одинаковый результат при одинаковых прочих условиях. Увеличение числа брокеров само по себе не доказывает преимущество алгоритма.

Коды завершения: 0 — предусмотренные фазы завершены; 1 — неверные входы; 2 — техническая ошибка опыта. Код 0 не означает прохождение проверки натурной точности.

## Разделение кода

`ConnectionPolicies` только назначает backend; `LoadBalancingOptions` проверяет параметры; `ExperimentInput` загружает и фиксирует входные байты; `LoadBalancingTopologyBuilder` строит сеть через публичный builder; `LoadBalancingExperimentRunner` управляет фазами; `DeliveryLedger` различает уникальные ID, прогрев, дубликаты и неверного получателя; `ExperimentResultWriter` пишет артефакты и формулы отчёта. Новых слоёв ядра, DI-контейнера и отдельного framework политик не требуется.

`Cli/DemoCommandOptions` разбирает аргументы демонстрации и создаёт идеальный опыт; `Results/DemoConsoleView` отображает его числа и полосы. Демо использует тот же runner; `RunInMemory` отключает только экспорт файлов, сохраняя модель и правила учёта.

Каждый объявленный тип находится в отдельном файле. Каталоги отражают обязанности:

```text
Cli/            разбор команд
Configuration/  настройки опыта, сети и путей профилей
Inputs/         чтение и проверка неизменяемых снимков входов
Policies/       режимы и назначение соединений
Topology/       граф, клиенты и создание backend через публичный builder
Execution/      подготовленный опыт и управление фазами
Delivery/       реестр сообщений и снимки учёта доставок
Results/        типы результатов, экспорт и реестр целостности
Program.cs      точка входа
```
