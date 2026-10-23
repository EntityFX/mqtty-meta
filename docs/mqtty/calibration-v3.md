# Перенос измерений в MqttY: raw → observations → calibration

Нужны завершённая кампания MqttBenchmark v3, .NET 6 runtime/SDK и чистые tracked исходники MqttY. Команды одинаковы в Linux/Windows; ниже PowerShell 7. Рабочий каталог — корень mqtty, sibling MqttBenchmark расположен в ../MqttBenchmark. [Установка инструментов](../mqtt-benchmark/machine-preparation.md).

## 1. Проверить и перенести исходные измерения

Для последовательной обработки одного брокера доступен явный `calibrate --broker <Name>`:
требуются все72 успешных ключа/24 точки этого брокера с тремя повторами.
Без `--broker` сохраняется требование полного набора288/96.
Single-broker calibration сохраняет schemaVersion3, runCount72 и собственную provenance;
runtime проверяет полную матрицу одного брокера, а не произвольный subset.

Стандартный калибратор требует **288 успешных запусков и ровно 96 точек**, по три повтора: Aedes/Mosquitto/EMQX/ActiveMQ × 16/256 bytes × QoS 0/1/2 × publishers 1/16/64/128. Raw не импортируется напрямую. Короткий pilot, 72 запуска одного Mosquitto или сокращённый smoke не дают калибровку четырёх брокеров.

Завершите запись, сохраните campaign manifest, исходные config/stand bytes, все attempts с seals, aggregate, версии кода и инструментов. Перенос архива и проверка SHA-256 описаны в [метриках](../mqtt-benchmark/artifact-format.md). Абсолютные старые пути в manifest — provenance исходного запуска, а не инструкция перезаписать файлы там. Private keys/пароли не переносятся в результаты.

На машине с распакованными результатами выполните aggregate повторно через CLI точной версии MqttBenchmark с исходными config/stand, новым output и чистым benchmark source checkout:

```powershell
# Из корня MqttBenchmark; затем вернуться в mqtty.
dotnet run -c Release --project src/EntityFX.MqttBenchmark.Cli -- aggregate `
  --config config/campaign.local.json --stand config/broker-stand.local.json `
  --raw artifacts/baseline-001 --output artifacts/aggregate-002 --benchmark-repo .
```

Aggregate проверяет покрытие заданного config, identities и sealed files. Для стандартной серии ожидаются runCount=288, points.Count=96. Калибратор затем дополнительно проверяет полную фиксированную матрицу и статистический контракт. Разные CPU/deployment/source/config/stand identities не объединяются. Оригинальные observations сохраняются отдельно от параметров модели.

## 2. Выполнить calibrate руками или скриптом

Из корня mqtty, после копирования проверенного observations в выбранный каталог:

```powershell
$Observations = '../MqttBenchmark/artifacts/aggregate-002/broker-observations.v3.json'
Get-FileHash $Observations -Algorithm SHA256
dotnet run -c Release --project src/EntityFX.MqttSimulator/EntityFX.MqttY.MqttRelay.App -- `
  calibrate --observations $Observations --output results/broker-calibration.v3.json `
  --mqtty-repository .
Get-FileHash results/broker-calibration.v3.json -Algorithm SHA256
```

Выход 0: создан новый файл schemaVersion=3, artifactType=broker-calibration, runCount=288, profileClientCountMode=publishers. Выход 1: invalid input, dirty Git или конфликт output. Calibrate не выполняет натурный benchmark. Для повторения выберите новый output, старый не перезаписывается. Ctrl+C прерывает команду; возможный незавершённый файл не используют как готовую калибровку.

Для запуска скриптом используйте [RunSimulation.ps1](../../mqtty/doc/examples/RunSimulation.ps1): с `-ObservationsPath` он вызывает эту же calibrate, генерирует brokerFidelity config выбранной точки и запускает experiment. Если calibration уже есть, задайте `-CalibrationPath` без ObservationsPath. Скрипт не восполняет отсутствующую матрицу и не ослабляет проверки.

Для машины только с runtime предварительно опубликуйте CLI на машине с SDK. Из корня чистого mqtty:

```powershell
dotnet publish src/EntityFX.MqttSimulator/EntityFX.MqttY.MqttRelay.App -c Release -o ../simulator-portable/bin
```

Перенесите **весь** каталог `simulator-portable/bin`, входные JSON и чистые Git checkout обоих проектов. На целевой Linux/Windows-машине нужны .NET6 runtime, Git и PowerShell7. Добавьте к вызову скрипта `-SimulatorCliPath ../simulator-portable/bin/EntityFX.MqttY.MqttRelay.App.dll`; сборка исходников на целевой машине тогда не выполняется. Без этого параметра скрипт использует `dotnet run` и требует SDK. Ручной эквивалент каждого вызова — `dotnet ../simulator-portable/bin/EntityFX.MqttY.MqttRelay.App.dll calibrate ...` или `experiment ...` с теми же аргументами ниже/в [руководстве сетей](network-experiments.md). Ожидаются тот же calibration artifact и новый run bundle; остановка длительного experiment — Ctrl+C, после неё неполный bundle не считается успешным результатом.

## 3. Что преобразуется

| Поле calibration | Источник / формула |
|---|---|
| attemptedRps | observations.attemptedRps.mean |
| targetCompletedRps | observations.completedRps.mean |
| targetPublishFailureRate | observations.publishFailureRate.mean |
| rateRejectionRate = r | явное допущение, default 0 |
| capacityRps | attemptedRps × (1−r) |
| conditionalPublishFailureRate | (targetPublishFailureRate−r)/(1−r) |
| conditionalDeliveryLossRate | observations.deliveryLossRate.mean, уже условная среди completed |
| observedLatencyQuantiles | исходные completion knots |
| processingLatencyQuantiles | max(0, observed knot−RTT) для QoS1; −2×RTT для QoS2 |
| rttBaselineMs | наблюдаемый baseline |
| latencyStatus | observed для QoS1/2; notApplicable для QoS0 |

Rate rejection отдельно не измеряется. Default r=0 означает capacity=offered load и отнесение наблюдаемого publish failure к условному gate, а не оценку физического максимума брокера. `--rate-rejection-rate <fraction>` задаёт одно допущение для всех точек; r>target failure или r=1 отклоняются. Фактический RateRejected/Attempted в симуляции зависит также от ticks и bursts; проверяйте exported counters.

Разность квантилей с постоянным RTT — детерминированное приближение, не точная декомпозиция распределений. Queues/ticks/сеть дополнительно влияют на итоговую ACK latency. Mean(completed RPS)/mean(attempted RPS) может отличаться от mean(completion fraction) трёх повторов: сохраняются оба target, ни один не подменяет другой.

**CPU/RSS/disk/NIC не импортируются автоматически.** Они валидируют измерительную установку, но не создают CPU/память/энергетическую модель MqttY. QoS0 не получает выдуманную задержку ACK; его latency поля null.

## 4. Строгая проверка и provenance

Вход проверяется на неизвестные/отсутствующие/дублированные поля и точки, finite значения, физические диапазоны, count=3, ordering min/max, Student-t CI95 (df=2) и null QoS0 latency. Допуск mean/min/max на roundoff — восемь binary64 machine epsilons в масштабе сравниваемых величин; calculated mean сохраняется, не clamp. CI может выходить за физический диапазон — это статистический интервал, не параметр модели.

Calibration сохраняет полную observation provenance, SHA-256 **точных bytes observations**, clean MqttY commit и method conditional-failure-rtt-v1. Wall-clock timestamp отсутствует: одинаковые bytes/commit/r воспроизводят одинаковые bytes calibration. Runtime повторно проверяет формулы и RTT-поправки. Hash обнаруживает изменение относительно registry, но не удостоверяет независимую подлинность измерений.

Не заменяйте embedded профиль и working configs. Новый внешний путь задаётся через brokerProfilePath в отдельном экспериментальном JSON. Дальнейшие команды: [сети](network-experiments.md); критерии приемлемости: [валидация](validation.md).
