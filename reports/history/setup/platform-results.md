# Сохранённые результаты и ограничения

Здесь разделены исторические измерения, функциональная проверка нового запуска и ещё не выполненная натурная fidelity. Экспериментальные JSON/CSV/raw и evidence registries сохранены; переписанная документация не меняет их статус.

## Локальный Mosquitto без Docker: 72/72, не полный calibration dataset

Источник: [broker-observations.v3.json](../../../MqttBenchmark/results/aggregated/mosquitto-local-native/broker-observations.v3.json), SHA-256 `c010fe9aad8876292068f36f890e85e8588596e1c0e762198a70a7d89bd02a7b`. Это 24 точки, три повтора, один Mosquitto 2.0.15 на ALT Linux11.1/kernel6.12.85 x64; runtime .NET6.0.36, PowerShell7.4.6, singleHostSequential/singleCorePinned. Binary SHA `34cee46d71bd97aca34c75fcb844674ab787434468c06423a3a1f2825a213dea`; source commit `c9df71ea5afde649ec93fee366c76e01a16559ea`. Config SHA `58c56ba6aa4aa9d290f31d9a5d207bbac675d03dd2eee7b413e24580ca835c3b`, stand SHA `be2508db2a758ad6338d24d0c8042ba92df3fe16ca474728483a16461b0dfafa`. Эти значения взяты из сохранённой provenance; raw seals перечислены в observations, полные raw не повторно проверялись здесь.

Completed RPS (среднее по 3 повторам; CI95 — полуширина):

| payload | QoS | publishers | completedRps | CI95 ± | failRate | lossRate |
| --- | --- | --- | --- | --- | --- | --- |
| 16 | 0 | 1 | 28 852 | 4 122 | 0 | 0 |
| 16 | 0 | 16 | 49 764 | 3 671 | 0 | 0 |
| 16 | 0 | 64 | 49 814 | 6 838 | 0 | 0.151 |
| 16 | 0 | 128 | 48 424 | 8 970 | 0 | 0.096 |
| 16 | 1 | 1 | 6 762 | 116 | 0 | 0 |
| 16 | 1 | 16 | 34 292 | 3 525 | 0 | 0.901 |
| 16 | 1 | 64 | 40 971 | 3 226 | 0 | 0.852 |
| 16 | 1 | 128 | 44 045 | 7 100 | 0 | 0.877 |
| 16 | 2 | 1 | 5 007 | 73 | 0 | 0 |
| 16 | 2 | 16 | 24 357 | 2 964 | 0 | 0.917 |
| 16 | 2 | 64 | 22 794 | 2 204 | 0 | 0.977 |
| 16 | 2 | 128 | 23 072 | 2 108 | 0 | 0.988 |
| 256 | 0 | 1 | 27 210 | 5 633 | 0 | 0 |
| 256 | 0 | 16 | 51 052 | 3 932 | 0 | 0 |
| 256 | 0 | 64 | 52 148 | 1 902 | 0 | 0.598 |
| 256 | 0 | 128 | 50 525 | 3 336 | 0 | 0.525 |
| 256 | 1 | 1 | 6 664 | 180 | 0 | 0 |
| 256 | 1 | 16 | 35 256 | 5 879 | 0 | 0.908 |
| 256 | 1 | 64 | 40 019 | 4 334 | 0 | 0.877 |
| 256 | 1 | 128 | 43 856 | 2 794 | 0 | 0.879 |
| 256 | 2 | 1 | 4 912 | 52 | 0 | 0 |
| 256 | 2 | 16 | 22 638 | 2 009 | 0 | 0.914 |
| 256 | 2 | 64 | 22 276 | 1 200 | 0 | 0.977 |
| 256 | 2 | 128 | 22 722 | 1 253 | 0 | 0.988 |

Латентность публикации QoS 1/2, мс (среднее по 3 повторам):

| payload | QoS | publishers | p50 | p99 | max |
| --- | --- | --- | --- | --- | --- |
| 16 | 1 | 1 | 0.114 | 0.179 | 59.5 |
| 16 | 1 | 16 | 0.374 | 0.810 | 64.3 |
| 16 | 1 | 64 | 0.923 | 4.371 | 89.9 |
| 16 | 1 | 128 | 1.421 | 12.515 | 96.7 |
| 256 | 1 | 1 | 0.114 | 0.184 | 61.6 |
| 256 | 1 | 16 | 0.384 | 0.841 | 64.5 |
| 256 | 1 | 64 | 1.065 | 4.253 | 71.2 |
| 256 | 1 | 128 | 1.398 | 12.725 | 79.5 |
| 16 | 2 | 1 | 0.162 | 0.269 | 47.7 |
| 16 | 2 | 16 | 0.634 | 1.386 | 52.0 |
| 16 | 2 | 64 | 2.878 | 5.871 | 71.9 |
| 16 | 2 | 128 | 5.698 | 11.757 | 81.1 |
| 256 | 2 | 1 | 0.163 | 0.268 | 48.2 |
| 256 | 2 | 16 | 0.654 | 1.414 | 52.9 |
| 256 | 2 | 64 | 2.905 | 5.933 | 58.5 |
| 256 | 2 | 128 | 5.770 | 12.119 | 80.8 |


Во всех 72 successful runs сохранено publishFailureRate=0; это факт этих наблюдений, не доказательство безошибочности брокера при любой нагрузке. Большие deliveryLossRate при нескольких publishers относятся к **всей установке и окну drain**. Единственный subscriber, синхронный per-message log, broker queues и observer overhead — возможные причины; имеющиеся данные не изолируют их вклад. Нельзя по одной постоянной скорости доставки объявить доказанным дефект только подписчика или только брокера. Модель, калиброванная на такие observations, воспроизводила бы установку с её ограничениями.

Исходная серия отклонялась от стандартной методики: maximumGuardIntervalSeconds=3 вместо1.25 и retry budget8 вместо3, общий host с desktop/background workload, network informational на loopback, memoryLimitEnforcement=not-enforced-native. Payload16/256 дают разные численные значения; статистическая неразличимость/причина плато не доказываются одним сравнением means. QoS0 имеет валидные throughput/failure/loss наблюдения, но ACK latency — null; он не исключается из полной calibration матрицы.

**72/24 не принимаются текущим четырёхброкерным calibrator v3.** Ни отдельные хорошие поля, ни сохранённая provenance не заменяют недостающие 216 runs. Исходные таблицы сохранены; прежние категоричные причинные выводы не используются как научное доказательство.

## Исторический контейнерный стенд: Windows контроллер → ALT broker host

Источники: [registry шести raw roots](../../data/platform-smoke-v1/dissertation-readiness/evidence/task-8-raw-roots.json), [summary генератора15610](../../data/platform-smoke-v1/dissertation-readiness/evidence/task-8-15610-run-summary.json) и [исходный исторический отчёт](https://github.com/EntityFX/diss/blob/c59d8afae7f59bca5b9605cb6220cdabc4389822/doc/dissertation-readiness/task-8-real-broker-validation-report.md). Raw paths относятся к прежним машинам; текущая workspace не содержит эти Windows raw roots.

Broker host: ALT Workstation11.1/i5-6400T, четыре CPU, Linux Engine29.2.1, singleCorePinned CPU0 и 4GiB cgroup/container. Брокеры Aedes1.1.2/Node20.20.2, Mosquitto2.1.2, EMQX6.3.0, Classic6.3.2/Java21.0.12; ports1883/2883/4883/3883. Controller первоначально i7-2600, 4 физических/8 логических CPU, физический1GbE. Это иные версии/ресурсы, чем новый native smoke.

| Серия / исходный срез | Сохранённый результат | Ограничение |
|---|---|---|
| Final preflight003 | 4/4 brokers | Проверка доступности, не load baseline |
| Guarded короткие pilots001 | Aedes4, Mosquitto4, EMQX3, Classic7 успешных первых attempts; 864 sealed references,0 mismatches по исходному отчёту | 1-секундное measurement, не288 ключей |
| Baseline singlecore001 | 0/288; первая attempt CPU max84.659% при gate70; следующая cancelled/unsealed;39/39 seals первой attempt | Неполная серия; aggregate/calibration отсутствуют |
| Контроллер15610/Xeon,4 физических CPU,100MbE | Preflight4/4; pilots Mosquitto4, EMQX3, Classic7; Aedes исчерпал3 attempts | В summary18 seals/911 references,0 mismatches; baseline0/288 |
| Позднее Aedes network gate85% | По историческому отчёту отдельная baseline приняла7 ключей, следующий исчерпал3 attempts при92.40–92.54% RX+TX/link | Это новая методика/серия; её counts не подменяют более ранний registry с0/288 |

Ранние gates70% сети, поздние85% Aedes и текущий informational Aedes — разные методики. Не корректируйте старые manifests под новый default. Поздний отчёт также сохраняет Aedes pilot loss0.983141 и проверку443 ссылок baseline; это не completed calibration и не naturnaya fidelity. Loss не стирают потому, что она высока.

Исторические BrokerFidelity0/288 и MqttRelay0/24 отражают отсутствие выполненной натурной проверки по полной матрице. Успешные synthetic unit tests относятся к коду. Порог15% throughput/20% p99/0.05 failure/loss оценивается в [отдельной validation серии](../../../docs/mqtty/validation.md).

## Новый пакет native: проверка работоспособности

На текущей Simply Linux11.1 проверены Aedes1.1.2/Node22.13.1, Mosquitto2.0.15, Classic6.3.2/Java17 и EMQX6.3.0 portable Ubuntu22.04 archive. Preflight прошёл все QoS0/1/2; сокращённая кампания по одной QoS1/payload16/publisher1 точке каждого брокера прошла4/4 и aggregate4/4. Это hostAllCores, короткие timings, **не288/96** и не профиль для calibrate.

Compact [native smoke evidence](../../data/platform-smoke-v1/verification/native-smoke-summary.json) фиксирует scope/identities/hashes и проверку seals. Подробные tests/платформенные ограничения находятся в [отчёте проверки документации](platform-validation.md). Windows/WSL и несколько отдельных физических hosts не проверялись этим smoke. Новый рабочий маршрут не переоценивает завершённость прежних научных экспериментов.
