# Причинный пилот очередей — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** проверить, различает ли снижение ёмкости выходной очереди федератора потери в одном заранее фиксированном коротком эпизоде.

**Architecture:** новая пара q=1/1024 использует неизменённый frozen runtime основной сети и отдельный узкий запускатель поверх существующих StorageGuard, execute и raw verifier. Старые программы, входы и принятые результаты сохраняются.

**Tech Stack:** MqttY Federation CLI, Python standard library, Linux cgroup v2/systemd.

**Spec:** [параллельный блок](../plans/Research-parallel-block-20261030.md), [прежний протокол](queue-capacity-short-protocol.md), [принятый короткий результат](../../reports/analysis/Queue-capacity-short.md).

**Состояние:** подготовка; ни одного нового запуска модели в рамках этого документа. Локальный admission smoke не является научным результатом.

## Global Constraints

- Весь диагностический блок имеет один предел **600 с**. В этой ревизии готовится только очередь; benchmark ускорения автоматически не следует за ней. Отдельный новый срок не увеличивает остаток общего блока.
- Два процесса максимум, каждому **240 с / 2 ГиБ RSS**; служба **595 с + до 5 с принудительного завершения / 6 ГиБ / 200% CPU / swap=0**, весь срок <=600 с. Внутренний запрет запуска после 480 с оставляет 120 с на завершение и пересчёт. Hard wall обеспечивает cgroup-служба, включая проверку данных.
- Нет retry, resume, повторного входа в существующий каталог, расширения нагрузки, матрицы, новых плановых таймеров или периодических опросов агентом.
- Не менять runtime, исходный manifest, старые конфиги/результаты, статью и основной diss. Новый каталог результатов расположен рядом с исходным snapshot, вне него.

## Review Focus

- Второй изменившийся фактор: строгий diff допускает только верхний `queueCapacity`; вложенный `network.queueCapacity` остаётся 100000.
- Подмена версии: исходный manifest **24ae3654a610545f2a13ebc796eeddcaa898d9bde2103ee722592e211b8bffa7**, все **281** файлов и dotnet проверяются до/после. `runtimeSourceCommit=12601f534f9da3673057b072f8bd6a0c67fd8aed` из manifest; это не текущий checkout и не ранний 279-файловый snapshot.
- Готовый процесс ошибочно равен принятому результату: нужны completed-фазы, sealed output, сверка входа и независимая арифметика по ID/payload/receipts/transmissions.
- Ёмкость уменьшается искусственно: эффект q=1 не объясняет задним числом потери основной десятисекундной сети при q=1024.
- Отсутствие переполнения или незаконченный процесс: отдельная классификация, без нулевых «измеренных» потерь и автоматического усиления нагрузки.

## Выбор минимального различающего воздействия

В принятой паре .25/2/10, q=1024/4096 оба варианта дали 2000 исходных попыток/завершений, 8000/8000 ожидаемых/уникальных доставок, queueDrops=0 и одинаковые шесть научных файлов. Общий wall составил 94,98 с. Повторять увеличение 1024→4096 при этом эпизоде бессмысленно для причинного вопроса.

Новая пара меняет ёмкость в противоположную сторону: **1 против 1024**. Она выбрана по механизму и допустимой границе, без поиска лучшего accuracy. В `StaticFederator.Enqueue` сообщение отбрасывается при `queue.Count >= QueueCapacity`; счётчик растёт только для measurement cohort. `Pump` отдаёт одному выходному MQTT-клиенту следующее сообщение, только когда `HasPendingPublish=false`. QoS 2 и один pending publish на направление создают ожидание при сближении поступлений. Очередь содержит ожидающие сообщения; уже извлечённое in-flight сообщение в её Count не входит. Ёмкость применяется **к каждому target**, включая локальный receiver, а summary суммирует QueueDrops всех федераторов. Это не очередь сетевого транспорта.

Validation разрешает q=1. Существующий integration test `HopLimitAndQueueOverflow_AreVisibleLoss_NotInfiniteExecution` даёт queueDrops>0 при q=1/QoS2, но при другом трафике (12 publishers, 20000/s, .002 с); он подтверждает работоспособность сигнала, **не гарантирует** переполнение нового эпизода. Поэтому прогноз фальсифицируемый: при прежнем трафике ожидаются q1.queueDrops>0 и q1024.queueDrops=0, меньше успешных доставок у q1 и иные forward/receipt события. Точное количество drops и p99 заранее неизвестно; меньшая p99 среди выживших доставок не означает улучшение.

## Закреплённые входы

| Фактор | Оба варианта |
| --- | --- |
| Модель | federation, star, 4 brokers, 64 publishers, receiverSites=[0,1,2,3] |
| Сообщения | 16 bytes, QoS2, 1000/s, seed42 |
| Окна | warmup .25 с, measurement 2 с, drain до 10 с |
| Tick / cache / hops | .1 мс / 100000 / 4 |
| Профиль | ideal=true, brokerProfilePath=null |
| Сеть | 125000000 bytes/s, throughputWindowTicks=100, queueCapacity=100000, transferTicks=2 |
| Единственное различие | выходная queueCapacity=**1 / 1024** |

Источник — основной `matrix.star.federation.q2.r1000.w10.s42.json`, общие короткие окна унаследованы от принятого короткого протокола. Это новый диагностический эпизод. Предыдущий результат q1024 — опорное наблюдение для выбора, **не новый контроль**: оба варианта выполняются заново на одном snapshot.

Входы: [q1.json](../../reports/audits/dissertation/bounded-experiments-20261030/configs/q1.json), [q1024.json](../../reports/audits/dissertation/bounded-experiments-20261030/configs/q1024.json), [program-seal.json](../../reports/audits/dissertation/bounded-experiments-20261030/program-seal.json), [source-audit.json](../../reports/audits/dissertation/bounded-experiments-20261030/source-audit.json).

## Исполнение и приёмка

- [x] Подготовить отдельный [run_queue_pilot.py](../../reports/audits/dissertation/bounded-experiments-20261030/run_queue_pilot.py), не ослабляя старый gate1024/4096. Переиспользуются frozen `network_study_process.execute`, `StorageGuard`, `verify_snapshot`, `verify_loaded_run`, `check_graph`; новый framework отсутствует.
- [x] Проверить gate (второй фактор, окна, повторный каталог/срок, отсутствие оставшегося времени, классификация); локальный `--check-inputs` не создаёт процесса модели или каталога опыта.
- [ ] Root однократно проверяет отсутствие исследовательских процессов/очереди Web jobs, свободные RAM>=6 ГиБ и диск>=10 ГиБ, исправность mount и отсутствие чужого scientific lock. Сам lock новый runner удерживает весь запуск. Занятость Web по API — внешний admission gate, не выводится из наличия host-процесса.
- [ ] Перенести **весь** sealed пакет в `/run/media/entityfx/hd250/mqtty-evidence/bounded-experiments-20261030-inputs`, проверить seal командой ниже. Старые runtime не пересобирать; новый runner копирует runtime и конфиги в новый output.
- [ ] Создать службу ровно одной командой, убедиться один раз в PID/progress; затем ждать отдельного продолжения без таймеров/циклических status-check. Runner проверяет свойства реальной службы (`RuntimeMaxUSec + TimeoutStopUSec <=600 с`), CPU/RAM/swap cgroup и сохраняет начальный progress0/2 до child start.
- [ ] По окончании сохранить все исходы и полный raw вне Git; сверить source/program seals. В Git переносить только компактные квитанции и обзор.

Из каталога нового пакета на Linux (два отдельных шага, второй только после admission):

```bash
python3 -B run_queue_pilot.py --check-inputs
systemd-run --user --unit=mqtty-queue-causality-20261030 --property=RuntimeMaxSec=595 --property=MemoryMax=6G --property=MemorySwapMax=0 --property=CPUQuota=200% --property=KillMode=control-group --property=TimeoutStopSec=5 /usr/bin/python3 -B /run/media/entityfx/hd250/mqtty-evidence/bounded-experiments-20261030-inputs/run_queue_pilot.py --source /run/media/entityfx/hd250/mqtty-evidence/network-full-3h-v1 --output /run/media/entityfx/hd250/mqtty-evidence/queue-causality-pilot-20261030
```

`RuntimeMaxSec=595` вместе с `TimeoutStopSec=5` — предел этой однократной службы, а не расписание нового опыта. Не применять `--collect` до сохранения результата службы. Отказ при существующем output сохраняет прежний срок; выбрать другой output для автоматического повтора запрещено.

## Заранее установленный вывод

1. Любой timeout, ошибка sealed input, crash, failed model phase, неверная арифметика, исчерпание RAM/диска → **technicalFailure**. Частичные raw сохраняются, причинное сравнение не принимается.
2. Оба приняты, q1.queueDrops=0 → **noninformativeNoSmallQueueOverflow**, независимо от совпадения/различия доставки. Трафик не повышать.
3. q1.queueDrops>0, q1024.queueDrops>0 → **noninformativeReferenceOverflow** для намеченной пары «переполнение/без переполнения»; результаты сохранить, второй опыт не добавлять.
4. q1.queueDrops>0, q1024.queueDrops=0 → **queueCapacityContrast**: получен сигнал влияния ёмкости на переполнение. Сопоставить apiAttempts/sourceCompleted/expectedDeliveries, исходные ID/payload/ticks, доставки по ID–receiver, fullyDeliveredPublications, forwardAttempts, suppression, hop drops, drain-фазы и обе p99. Если исходные cohorts различаются, эффект относится к системе с backpressure и не является фиксированной когортой. Если cohorts совпали и у q1 меньше уникальных доставок, это поддерживает связь ограниченной очереди с недоставкой в данном эпизоде. Если доставки одинаковы, сигнал переполнения есть, гипотеза влияния на конечную доставку не подтверждена.

QueueDrops — события отказа постановки в target queue, а не число потерянных исходных публикаций или недоставленных пар. Нельзя арифметически приравнивать эти величины. Один seed и искусственная q=1 не дают статистического обобщения, не выравнивают Relay/federation и не закрывают причинный вопрос основной серии.
