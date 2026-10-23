# Архитектура запуска и сбора метрик

Стенд разделяет жизненный цикл брокера, протокольную нагрузку и доказательства измерения. Один компьютер совмещает роли; на двух компьютерах worker работает на broker host, а CLI и MQTTnet — на контроллере. Все маршруты используют один контракт v3. При 3+ машинах nativeHost/dockerContext выбираются по broker entry; matrix остаётся последовательной. [Варианты размещения](deployment-layouts.md).

```mermaid
flowchart LR
    CLI[Контроллер: CLI .NET] --> Session[StandSession и BrokerStand]
    Session -->|локально или SSH| Worker[PowerShell worker на стенде]
    Worker -->|managed: start/stop; external: attach| Broker[Native broker process]
    CLI --> Load[MQTTnet: N publishers и 1 subscriber]
    Load <-->|MQTT TCP| Broker
    Worker -->|stream readiness, samples, fence| Raw[Raw attempts на контроллере]
    Load --> Raw
    Guard[CPU/NIC генератора] --> Raw
    Raw --> Aggregate[aggregate]
    Aggregate --> Observations[broker-observations.v3.json]
    Observations --> Calibrate[MqttY calibrate]
    Calibrate --> Profile[broker-calibration.v3.json]
    Profile --> Simulation[MqttY experiment]
```

## Последовательность одного измерения

```mermaid
sequenceDiagram
    participant C as CLI
    participant W as Worker
    participant B as Broker
    C->>W: validate, start или attach
    W->>B: проверить executable/launcher/PID/config/CPU
    C->>W: health и clock
    C->>B: protocol preflight, RTT
    C->>W: capture
    W-->>C: первая sample, затем telemetry-ready
    C->>B: warmup → measurement → drain → cooldown
    C->>W: completion fence
    W-->>C: fence на монотонных часах стенда
    W-->>C: последняя sample, sampler-ended, версии и логи
    C->>C: проверить coverage и seal SHA-256
    C->>W: stop или detach
```

Warmup исключён из измеряемых счётчиков. Drain даёт доставкам завершиться; итоговое окно и его причина записываются, а не восстанавливаются из номинальных 30 секунд. Fence должен предшествовать последней sample **на тех же монотонных часах broker host** и иметь тот же unitId. Синхронизация realtime и доказательство monotonic coverage — разные проверки.

## Локально и по SSH

StandSession хранит исходный инвентарь и создаёт snapshot выбранного брокера. BrokerStand управляет процессом и читает его ресурсы. RemoteNativeStand передаёт snapshot worker по зашифрованному SSH; большой payload идёт через stdin, компактный bootstrap — через EncodedCommand, чтобы учитывать ограничения Windows command line.

Телеметрия передаётся потоком: первая строка сохраняется до публикации local readiness. Лишь копирование файлов после завершения capture не обеспечило бы этот барьер. Worker сохраняет копии evidence в workRoot; frame receiver принимает безопасные относительные пути и не подменяет уже опубликованные байты. Capture и fence могут выполняться параллельно, но пишут разные итоговые артефакты.

На одном контроллере lease привязан к context; на SSH host дополнительно используется owner lease в workRoot. У каждого автоматического StandSession свой owner даже при одинаковых путях на разных контроллерах. Managed-процесс остановится при штатном выходе; external-процесс остаётся оператору. После потери SSH требуется recovery по [runbook](two-machine-stand.md).

## Где находится реализация

| Ответственность | Источник |
|---|---|
| CLI и выбор схемы | [Program.cs](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Cli/Program.cs) |
| Команды, identity, параметры | [Commands](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Commands) |
| Session, lifecycle, telemetry barriers | [Stand](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Stand) |
| Локальный process controller | [BrokerStand.ps1](../../MqttBenchmark/scripts/BrokerStand.ps1) |
| SSH transport, streaming и host lease | [RemoteNativeStand.ps1](../../MqttBenchmark/scripts/RemoteNativeStand.ps1) |
| Нагрузка, ledger и drain | [Workload](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Workload) |
| CPU/NIC контроллера | [Guard](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Guard) |
| Агрегация | [CampaignAggregator.cs](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/CampaignAggregator.cs) |

Установка и режим запуска не определяют качество профиля сами по себе. Топология, observer overhead, subscriber, resource enforcement и сетевой путь остаются частью установки. Формулы и scopes — в [метриках](artifact-format.md), точные CLI/options — в [контракте кампании](campaign-v3.md).
