# Проверка пакета документации и запуска

Срез проверки: 13.10.2026. Рабочее окружение — Simply Linux11.1 p11 x86_64, kernel6.12.85, .NET6.0.36/SDK10.0.200, PowerShell7.4.6. Реальные репозитории не коммитились: для CLI с требованием clean Git использован отдельный временный clean source snapshot, его commit указан в smoke evidence. Это не опубликованный commit продукта.

## Выполненные проверки

| Проверка | Свидетельство | Что подтверждается |
|---|---|---|
| Все4 native brokers, preflight | [Compact smoke summary](../../data/platform-smoke-v1/verification/native-smoke-summary.json); protocol QoS0/1/2 | Aedes/Mosquitto/Classic/EMQX запускаются без Docker на текущей ALT |
| Короткая matrix → aggregate4/4 | Тот же evidence,4 successful keys,4 points,176 seal references,0 mismatches | Managed lifecycle, guard, capture/fence, immutable artifacts и aggregate сокращённой матрицы |
| Linux-контроллер → физический Windows-стенд | [Windows smoke summary](../../data/platform-smoke-v1/verification/windows-native-smoke-summary.json); три отдельные короткие кампании,135 seal references,0 mismatches | Mosquitto/Aedes/Classic: managed start, preflight, clock round trips, telemetry/fence, stop и aggregate1/1 для каждого |
| Windows-контроллер → физический Linux-стенд | [Controller smoke summary](../../data/platform-smoke-v1/verification/windows-controller-smoke-summary.json);45 seal references,0 mismatches | Windows load-generator/NIC counters, Git SSH transport, Mosquitto lifecycle и aggregate1/1 |
| Локальный Windows managed/external | [Local Windows smoke summary](../../data/platform-smoke-v1/verification/windows-local-smoke-summary.json);182 seal references,0 mismatches | Три managed broker campaigns; operator Mosquitto остаётся после external; CPU0/CPU2 разделены при guard70% |
| Mixed distributed native inventory | [Mixed smoke summary](../../data/platform-smoke-v1/verification/mixed-native-smoke-summary.json);2/2 successful keys | MergeNativeStands, per-broker SSH routing между Linux/Windows, shared campaign aggregate;2 физических hosts |
| Local managed/external и wrong hash | NativeBrokerStandTests | Native telemetry/stop; external process остаётся оператору; mismatch отвергается |
| SSH streaming / per-broker target | RemoteNativeStandTests | Ready до workload,7 samples, later fence, выбранный nativeHost имеет приоритет; foreign-OS inactive paths не мешают |
| Linux route/link counters | UnixLoadGeneratorCountersTests | Tabs/default/specific route, sysfs Mbit/s→bit/s |
| MqttY calibration/experiment contract |46 focused Release tests | Полнота288/96, формулы/provenance, invalid inputs, deterministic tests; fixtures synthetic |
| MqttY published CLI на Linux/Windows | [Cross-platform synthetic summary](../../data/platform-smoke-v1/verification/mqtty-cross-platform-smoke-summary.json) | Calibration288/96 + brokerFidelity; manual/script Windows и Linux summaries совпадают; Windows custom ideal delivery1/errors0 |
| Public API topology | doc/examples/custom-network, --ideal | Компиляция и1 QoS1 delivery, errors0 |

Полная новая натурная288-run кампания и её fidelity в этот пакет не входят. Short4-run smoke не импортируется в calibrator, не заменяет исторические measurements и не является performance comparison.

## Платформенные статусы

Ubuntu broker/runtime installation подтверждены официальными источниками, но установка на чистую Ubuntu здесь не выполнялась. ALT использует обнаруженные зависимости; отдельный EMQX portable archive проверен фактически, без официального обещания поддержки ALT. На физической Windows LTSC2019 проверен native managed запуск Mosquitto/Aedes/Classic с Linux-контроллера. Обратное направление Windows → Linux проверено для Mosquitto; локальный Windows-контроллер проверен для трёх native brokers в singleCorePinned, ручной external lifecycle — для Mosquitto. WSL2 на имеющемся Windows build17763 недоступен; EMQX через WSL2 здесь не проверен. Для каждой ОС/брокера статус указан в [quick start](../../../docs/mqtt-benchmark/quick-start.md), для нескольких hosts/container — в [размещении](../../../docs/mqtt-benchmark/deployment-layouts.md).

Container режим опирается на существующие Linux контейнерные contracts и [исторические серии](platform-results.md); новая контейнерная кампания/Windows Desktop networking этим native smoke не проверены. Несколько SSH logical targets не следует выдавать за несколько отдельных физических машин.

## Критерии проверки изменений

Проверяются ссылки Markdown, JSON syntax, PowerShell AST, соответствие CLI options/code и meaningful tests; protected file hashes сопоставляются с начальным snapshot. В diss разрешена только doc; все LaTeX/PDF/диссертационные файлы вне doc защищены. Исторические JSON/CSV/evidence scripts не редактируются. Код/скрипты MqttBenchmark изменены в рамках последующего явного разрешения пользователя обеспечить Linux/Windows и remote native запуск; working configs и experiment data остаются исходными.

Сводный [индекс](../../../docs/dissertation/README.md) покрывает шесть исходных вопросов; основные инструкции имеют prerequisites, CWD, заменяемые параметры, commands/result/stop. Отдельные теоретические документы не содержат deployment commands, поэтому не требуют вымышленной процедуры остановки. Старая дублированная проза удалена без архива в репозитории; исторические числа и limitations перенесены в results-and-evidence.

## Итоговый аудит и очистка

[Audit summary](../../data/platform-smoke-v1/verification/package-audit-summary.json):938 protected tracked files проверены по начальному SHA256 snapshot, неразрешённых изменений0. В Windows удалены временный public key, копия приватного ключа контроллера и единственное созданное firewall rule; listener и stand lease отсутствуют. В Linux остановлены только собственные тестовые SSH daemons и оставшийся fixture broker. Переносимые инструменты и результаты сохранены в отдельном validation-каталоге. Научные данные и файлы diss вне doc остаются исходными.
