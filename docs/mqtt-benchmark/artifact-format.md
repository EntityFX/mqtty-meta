# Метрики реального брокера и артефакты v3

Единицей raw является успешная попытка для broker × payload bytes × QoS × publishers × repeat. N — число publishers; один subscriber создаётся дополнительно. Метрики измеряются на стороне генератора; успешная публикация и доставка подписчику учитываются раздельно.

## Наблюдаемые величины

Обозначим A — attemptedPublishes, C — completedPublishes, F — failedPublishes, D — completedIdsDelivered, T — actualMeasurementSeconds. Источник формул: [MeasurementSummary](../../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Workload/MeasurementSummary.cs).

| Величина | Формула/источник | Единица | Применение в MqttY |
|---|---|---|---|
| attemptedRps | A/T | публикаций/с | observed offered load; исходная capacity при rateRejectionRate=0 |
| completedRps | C/T | публикаций/с | targetCompletedRps, reference для fidelity |
| publishFailureRate | F/A; при A=0 — 0 | доля 0..1 | условный отказ после rate gate |
| deliveryLossRate | 1−D/C; при C=0 — 0 | доля 0..1 | условная потеря после успешной публикации |
| publishLatencyMs | от PublishAsync до завершения PUBACK/PUBCOMP, nearest-rank knots | мс | observed latency; после RTT-поправки processing latency |
| rttBaselineMs | медиана QoS0 MQTT echo probes в одном запуске | мс | поправка latency; среднее RTT повторов в observations |
| duplicateDeliveries | повторные receipts message ID | сообщения | диагностика, отдельного параметра профиля нет |
| unexpectedDeliveries | receipt без ожидаемого ID | сообщения | диагностика |
| deliveredAfterFailedPublish | receipt ID публикации с конечным failed outcome | сообщения | диагностика неоднозначного завершения |
| CPU/RSS/disk/NIC | worker и load-generator reports | %, байты, счётчики | валидация стенда, автоматически в calibration не импортируются |

QoS 0 latency = null/notApplicable: успешный возврат публикации не является MQTT ACK. QoS 1/2 p99 — задержка завершения публикации, а не end-to-end latency подписчика. Когда C=0, нулевая условная loss — соглашение отчёта, не доказательство отсутствия потерь. QoS1/2 без completed publishes не дают пригодной latency evidence.

T измеряется монотонно, включает завершение выданных in-flight publish операций после номинального cutoff. Поэтому делить count на measurementSeconds из config вместо T неправильно. Warmup имеет отдельные topic/ID и не участвует в итогах.

Payload содержит детерминированный 16-byte message ID, зависящий от campaign/attempt/publisher/sequence. Ledger считает уникальные завершённые публикации и доставки их ID; duplicate/unexpected receipts не делают loss отрицательной. После завершения drain ledger замораживается. Причины drain: все завершённые ID получены, quiet period либо timeout. Потери характеризуют выбранную установку и окно наблюдения; причину высокой loss нельзя автоматически приписать брокеру.

## Телеметрия и ресурсы

| Источник | Scope | Что можно утверждать |
|---|---|---|
| Native process CPU | process-pinned | CPU конкретного listener PID, нормированная на число выбранных ядер |
| Native RSS | broker-process | working set/RSS процесса, не вся память хоста |
| Native disk I/O | broker-process | Linux /proc/PID/io; в Windows может отсутствовать |
| Network / connections | host-shared | общие counters хоста и подключения порта, не изолированный сетевой трафик брокера |
| Load-generator CPU | whole system | нагрузка всей машины контроллера, а не только CLI |
| Load-generator NIC | routed interface | RX+TX по выбранному физическому NIC; unrelated traffic также попадает в counters |

Заявленный memoryLimit=4 GiB не равен фактическому enforcement. Проверяйте `memoryLimitEnforcement`, affinity и effective configs в run-provenance/versions. При external lifecycle настройки процесса принадлежат оператору. Недостающий counter не заполняется вымышленным нулём.

На loopback нет физической link capacity: network threshold должен быть null. Для удалённого host guard выбирает маршрут к IPv4 endpoint, требует физический NIC, стабильную скорость и непрерывные интервалы. В Linux sysfs speed выражена в Mbit/s и переводится в bit/s множителем 1 000 000. По умолчанию CPU gate — 70%, network gate — 80% (Aedes informational), maximum guard interval — 1.25 с; конкретный config может изменить их. RX+TX сравниваются совместно. Изменение порогов меняет методику и должно отражаться в сохранённых входах.

## Что сохранять и переносить

```text
campaign/
  campaign.json
  attempts/<broker.m.q.p.r>/attempt-NN/
    started.json
    publishes.ndjson
    deliveries.ndjson
    protocol.json
    load-generator-*.json / *.ndjson
    stand/
      stand.selected.json
      run-provenance.json
      clock-alignment.json
      telemetry.ndjson
      telemetry-ready.json
      workload-end-fence.json
      sampler-ended.json
      telemetry-manifest.json
      versions.json
      config-hashes.json
      logs/
    result.json
    sha256.json
aggregate/
  broker-observations.v3.json
```

Capture durations включают фазовый envelope и reserve. Сэмплы обязаны покрывать workload: ни ready в конце захвата, ни просто одинаковые UTC timestamps этого не доказывают. Не редактируйте sealed attempts; hashes проверяются при resume/aggregate. Hash provenance идентифицирует входы, но не удостоверяет их независимой подписью.

Для пересылки Linux может выполнить `tar -czf campaign.tar.gz <campaign-directory>` и `sha256sum campaign.tar.gz`; Windows — `tar.exe -czf campaign.tar.gz <campaign-directory>` и `Get-FileHash campaign.tar.gz -Algorithm SHA256`. Сначала прекратите запись в серию и сохраните весь campaign, config, stand, aggregate и версии кода. На получателе сравните SHA-256 архива и file seals. SSH private keys и пароли не включаются в пакет результатов.

## Агрегирование и достоверность

Стандартная матрица даёт 96 точек, каждая имеет три повтора. Rate statistics содержат mean, sample standardDeviation, Student-t CI95 с df=2, min/max/count. Наблюдаемые latency knots агрегируются по повторам вместе со статистиками каждого knot; это не p99 объединённой выборки всех сообщений. rttBaseline — среднее трёх per-run median RTT.

CI95 — статистический интервал, не физический диапазон параметра; границы могут выйти за 0..1. Три повтора ограничивают статистическую мощность. Разные campaign identities, source/config/stand hashes, CPU/deployment modes не смешиваются. Полнота observations ещё не означает приемлемую точность модели. [Как преобразуются значения](../mqtty/calibration-v3.md), [как проверяется fidelity](../mqtty/validation.md).
