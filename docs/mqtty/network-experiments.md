# Воспроизведение сетей симулируемых брокеров

Нужны внешний [calibration v3](calibration-v3.md), .NET 6 и чистые tracked файлы MqttY/MqttBenchmark. Эти команды работают в PowerShell 7 на Windows/Linux. Рабочий каталог — корень mqtty. Все относительные пути в JSON разрешаются относительно **текущего каталога процесса**, не расположения config.

## BrokerFidelity: сравнение одной точки

Используйте [broker-fidelity.v3.json](../../mqtty/doc/examples/broker-fidelity.v3.json), скопировав в `results/fidelity.local.json`. Профиль/репозитории укажите абсолютными путями либо путями от mqtty. `brokers: 1`, topologyMode=brokerFidelity, profileClientCountMode=publishers, clientsPerBroker=N; subscriber создаётся дополнительно, всего N+1 connections. В examples по умолчанию Mosquitto, payload16, QoS1, N=1; offeredRps=10000 — **заменяемый пример**, не измеренный target.

Скрипт [NewSimulationConfig.ps1](../../mqtty/doc/examples/NewSimulationConfig.ps1) берёт offeredRps из selected calibration point, чтобы не перепутать нагрузку:

```powershell
./doc/examples/NewSimulationConfig.ps1 -CalibrationPath results/broker-calibration.v3.json `
  -OutputPath results/fidelity.local.json -Broker Mosquitto -MessageBytes 16 -Qos 1 `
  -Publishers 1 -Seed 42
```

Ожидается новый JSON с нагрузкой attemptedRps выбранной точки. Скрипт не подбирает сеть под RTT: `network.transferTicks`, capacity/queues и tickPeriod остаются отдельными параметрами. Их надо согласовать с эталонным транспортом и проверить измерением в модели; универсального равенства одного transfer tick одному RTT нет.

Ручной запуск:

```powershell
dotnet run -c Release --project src/EntityFX.MqttSimulator/EntityFX.MqttY.MqttRelay.App -- `
  experiment --config results/fidelity.local.json --output results/fidelity-runs
```

Или полный маршрут скриптом с уже готовой calibration:

```powershell
./doc/examples/RunSimulation.ps1 -CalibrationPath results/broker-calibration.v3.json `
  -ConfigPath results/fidelity-script.local.json -OutputDirectory results/fidelity-script-runs `
  -Broker Mosquitto -MessageBytes 16 -Qos 1 -Publishers 1 -Seed 42
```

ConfigPath — новый файл. Run output — общий parent; runner создаёт уникальный run subdirectory и печатает его путь. Для воспроизводимого сравнения сохраняйте точные config bytes, profile SHA-256, commits и seed. Остановка — Ctrl+C; partial bundle не считается завершённым экспериментом.

## MqttRelay: смешанная сеть

Для отдельных calibrated файлов задайте `brokerProfilePaths: ["<aedes.json>", "<mosquitto.json>", "<activemq.json>", "<emqx.json>"]`
вместо `brokerProfilePath`. Поля взаимоисключающие. Дубли брокеров и несовместимые
source commits/CPU/deployment modes отклоняются; manifest хранит SHA-256 и provenance
каждого исходного файла. Их campaign identities не объединяются.

Для независимого BrokerFidelity используйте `referenceObservationsPath` с полной
72/24 observations одного брокера другой кампании той же установки. Профиль
сохраняется неизменным; reference применяется только к критериям сравнения.
Для relay критерии натурной fidelity имеют статус notApplicable;
single-broker reference остаётся диагностическим. `counters.json` экспортирует
receipts каждого receiver после measurement reset через drain.

Скопируйте [mqtt-relay.v3.json](../../mqtty/doc/examples/mqtt-relay.v3.json) в `results/relay.local.json`, проверьте путь внешнего профиля и два source repositories. В примере четыре области с Mosquitto/ActiveMQ/Aedes/EMQX, по одному publisher; networkLength=2, topologyMode=mqttRelay, assignment=mixed. У mixedBrokerTypes ровно B элементов; имена с учётом регистра как в calibration. Для single assignment JSON enum использует camelCase, например mosquitto.

```powershell
New-Item -ItemType Directory -Force results | Out-Null
Copy-Item doc/examples/mqtt-relay.v3.json results/relay.local.json
dotnet run -c Release --project src/EntityFX.MqttSimulator/EntityFX.MqttY.MqttRelay.App -- `
  experiment --config results/relay.local.json --output results/relay-runs
```

Этот маршрут добавляет listener/forwarder/receiver приложения. Broker имеет N+B+3 connections, но lookup v3 по publishers использует N. `connectedClients` — другая lookup policy; она не переименовывает натурную ось N. Manifest сохраняет оба счётчика. OfferedRps — **суммарная нагрузка всего сценария**, publishers обслуживаются round-robin; это не RPS на каждый брокер. При разных targets четырёх профилей одно offeredRps не воспроизводит одновременно все локальные исходные точки.

Смешанная сеть проверяет доставку и взаимодействие в выбранной модели. Натурные single-broker observations не являются measurements этой сети. Aggregated reference latency для mixed не равна p99 смеси; успешный smoke не подтверждает точность реальной сети. Для собственной топологии вместо штатного relay builder используйте [public API пример](custom-topology.md).

## Время и выходы

В examples tickPeriod=0.1 ms: warmup50000 ticks=5 s, measurement300000=30 s, drain100000=10 s **модельного времени**. Wall-clock runtime другой. RPS нормируется на measurement ticks × TickPeriod; завершения in-flight во время drain остаются в числителе. В benchmark знаменатель — actualMeasurementSeconds с завершением issued publishes. Это различие окон учитывают при сопоставлении перегруженных точек. CapacityBytesPerSecond — bytes/s, не bit/s; 100000000 bytes/s =800 Mbit/s. QueueCapacity задаёт ограничение очереди; transferTicks и throughputWindowTicks задаются в ticks.

Каждый run сохраняет experiment-manifest.json, summary.json, counters.json, latency histogram, GraphML и PlantUML. Manifest включает exact source commits, profile SHA-256, seed, конфигурацию и connection/lookup policy. Profile bytes читаются один раз: hash и repository потребляют тот же snapshot. Summary связывает phases, measurement, drain и acceptance. Не смешивайте processingLatencyQuantiles профиля с observed completion p99 reference.

Exit0: сценарий завершён без failed criteria; exit1: invalid input/Git/output; exit2: phase failure; exit3: fidelity criterion failed. Наличие notApplicable требует отдельной интерпретации — exit0 не означает применимость всех критериев. [Критерии и deterministic replay](validation.md).
