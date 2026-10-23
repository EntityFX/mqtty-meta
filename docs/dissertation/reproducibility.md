# Воспроизводимость и состав исследовательского пакета

Перед запуском фиксируйте OS/kernel, hardware/виртуализацию, физические и логические CPU, affinity/governor, memory enforcement, NIC/link/MTU/route, NTP, версии runtime/брокеров, выбранный runtime и размещение. Windows host с WSL и Linux containers содержит Linux guest; NAT/forwarding записывается как часть стенда.

Campaign identity связывает campaign ID, config/stand bytes SHA-256, deployment/CPU modes и benchmark source commit. Clean Git означает отсутствие tracked modifications; незафиксированные входы/зависимости всё равно требуют собственного учёта. Published bin должен соответствовать сохранённому clean source checkout или доверенной build provenance. Hash bin/launcher не фиксирует автоматически весь Node/JDK/Erlang dependency graph.

## Что хранить

- Точные campaign/stand/spec bytes, commits и сведения об установленном окружении.
- Все attempts: успешные, отклонённые и unsealed после отмены; protocol, guard, telemetry, fence, versions/config hashes, logs и seals.
- Исходный aggregate observations и отдельную calibration с input hash и model assumption r.
- Experiment JSON, calibration bytes/hash, seed/ticks/lookup modes, manifests/summaries/counters/histograms и topology exports.
- Краткий отчёт с покрытием, отклонениями методики и применимостью критериев.

Инструкции архивации/переноса через tar/scp для обеих ОС: [метрики](../mqtt-benchmark/artifact-format.md). Перед упаковкой остановите запись. Сверяйте archive checksum и внутренние file seals; не переносите SSH private keys и пароли в scientific bundle. SHA-256 доказывает совпадение с registry, не авторство или независимую истинность данных.

## Что нельзя объединять

Разные кампании, source/config/stand identities, режимы CPU/deployment, разные runtimes и машины не склеиваются ради 288/96. Замена input bytes при resume отвергается; смена машины/зависимостей также требует новой серии, даже если конкретный fingerprint не проверяется CLI автоматически. Raw результаты не редактируют после seal. Новое допущение rate rejection и новая topology сохраняются отдельными model artifacts.

## Повторение

Исходная повторяемость benchmark — три измерительных повтора каждой точки. CI95 с df=2 имеет ограниченную статистическую мощность. Seed гарантирует порядок/случайные решения соответствующего кода, не одинаковое физическое время брокера. Deterministic replay симуляции сравнивает содержательные counters/summary/histogram, не случайный run directory ID.

Проверка сохранённых исторических Windows raw roots выполняется read-only verifier из корня мета-репозитория в PowerShell 7. Скрипт содержит прежние значения каталогов; передавайте фактические `WorkspaceRoot` и `BaselineRoot` явно:

```powershell
./tools/dissertation/legacy/dissertation-readiness/evidence/verify-task-8-evidence.ps1 `
  -WorkspaceRoot <каталог-исходного-стенда> -BaselineRoot <исходный-каталог-baseline> `
  -VerifyRegistry ./reports/data/platform-smoke-v1/dissertation-readiness/evidence/task-8-raw-roots.json
```

Нужны **сами raw roots на машине хранения** по registry путям. Отсутствие raw нельзя считать успешной проверкой. Ожидается совпадение tree hashes и seals; Ctrl+C отменяет read-only проверку. Алгоритм tree digest указан в registry. Исторические provisioning scripts сохранены в `tools/dissertation/legacy`; они не являются инструкцией подготовки нового native стенда и не запускаются автоматически.

## Независимый пересчёт основной натурной кампании

`tools/dissertation/run_native_audit.py` проверяет реестр архивов и полную матрицу 576 выбранных успешных запусков. На Linux архивы читаются прямо со смонтированного диска доказательств, без распаковки. Проверяются SHA-256, внутренние seals, ID, счётчики, все сохранённые публикационные квантили и монотонная длительность. Неудачные попытки учитываются отдельно. Точные границы measurement/drain не выдумываются: [контракт измерений](../overview/measurement-contract.md) описывает доступные пределы проверки.

Результаты одного аудита хранятся в новом каталоге. Для resume должны совпасть хэши исходников проверки и входов. Сохраняются все попытки проверки, причины технических отказов, компактные результаты и команды. Работающий процесс проверяется по службе/PID; один файл статуса не доказывает, что процесс жив.
