# Ограниченная проверка ускорения кампании — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** подготовить сопоставимую современную проверку скорости выполнения одного и того же маленького набора независимых симуляций с workers=1 и workers=2.

**Architecture:** один frozen MqttY runtime, одинаковые входы/выходы и набор из двух независимых заданий; меняется только число одновременно выполняемых процессов. Отдельный launcher должен измерять границы всего набора, CPU и память и проверять смысл научных файлов. Он пока отсутствует: этот документ не разрешает запуск непроверенного substitute.

**Tech Stack:** существующие Federation CLI и Python process supervisor, Linux cgroup v2; потребуются monotonic wall, wait4/getrusage либо GNU time и cgroup resource accounting.

**Spec:** [параллельный блок](../plans/Research-parallel-block-20261030.md), [аудит исторических доказательств](../../reports/audits/dissertation/acceleration-evidence-v1/Review.md), [evidence.json](../../reports/audits/dissertation/acceleration-evidence-v1/evidence.json).

**Состояние на новом этапе:** [отдельный A/A-блок](../plans/Acceleration-aa-20261030.md) разрешён пользователем. CPU-aware supervisor реализован; 7/7 проверок прошли на Linux, включая настоящее измерение CPU и остановку дерева по deadline. Сначала запускаются два одинаковых последовательных набора, без сравнения workers1/2. Исторический текст ниже описывает исходный пробел; его закрытие измерителем не означает измеренного ускорения. Шаг33 не закрыт.

## Global Constraints

- Это **ускорение кампании**, не parallel refresh одного графа, не оптимизация waiters и не сравнение первоначального/нового движка. Новый опыт не восстанавливает исторические 12,5×, 2,5–3× или их missing raw.
- Исходные публикации, получатели, payload, seed, модельные окна, tick, counters и правила завершения совпадают в обеих схемах. Одинаковый wall при разном объёме полезной работы несопоставим.
- Весь диагностический блок, включая очереди, не превышает **600 с**. В текущем launch-пакете весь срок зарезервирован за очередью; benchmark не запускается автоматически после неё. Для совместного запуска нужен заранее разделённый единый бюджет и readiness review; остаток времени не является разрешением на новый запуск.
- Нет подбора workload по observed speedup/accuracy, увеличения нагрузки, retry, продолжения после неинформативности, длинной матрицы или таймеров.

## Review Focus

- Ускорение одной симуляции смешано с makespan набора: первичная величина — завершение двух одинаковых jobs и их проверка.
- CPU отсутствует: wall/RSS из имеющегося supervisor не заменяют CPU seconds.
- Быстрее за счёт потерь/иного завершения: нужны completed-фазы, ID/payload/cohort/counters и шесть научных файлов каждого job.
- RSS суммируется некорректно: sum отдельных peaks не равна наблюдённому одновременному peak набора.
- Один timing принят за устойчивый speedup: сначала A/A measurement smoke, затем фиксированные AB/BA повторы; иначе только диагностическое отношение одного набора.

## Что именно можно проверить

Пользовательский путь — получить два законченных и проверенных результата фиксированного набора. В схеме A процессы стартуют последовательно (workers1); в B те же два процесса работают одновременно (workers2). Одинаковые cold process/JIT/file IO входят в end-to-end wall в обеих схемах. Цена копирования исходников/staging исключена и измеряется отдельно; raw validation входит в primary completion wall, а model-only makespan записывается дополнительно.

В качестве будущего fixture использовать **две независимые точные копии q1024** из [пакета](../../reports/audits/dissertation/bounded-experiments-20261030/configs/q1024.json), с разными output/job IDs, без смены seed. Такой fixture выбран из известной bounded стоимости и принятой корректности .25/2/10 (предыдущие процессы ~93–94 с при совместном выполнении, RSS~0,78 ГБ), а не из наибольшего observed ускорения. Это одна точка сложности; две копии позволяют измерить scheduling без смешивания топологий/QoS/нагрузок. Это **два jobs, не два seed** и не широкий workload domain.

Никаких `--parallel` к Federation CLI добавлять нельзя: `FederationRunner.Refresh()` явно вызывает `Graph.Refresh(false,0)`; соответствующего параметра FederationOptions/CLI нет. Legacy `MqttRelay.App original` выполняет три режима (single counters=false; parallel counters=false; parallel counters=true) в фиксированном порядке и пишет counters с общим prefix. Эти прогоны не имеют отдельной надёжной идентичности первичных ID/receipts и не годятся как краткая команда парного benchmark без нового адаптера. `Rep=131` там — число отправок, не число независимых timing повторов.

## Метрики и gate эквивалентности

| Величина | Определение |
| --- | --- |
| Primary wall, с | monotonic от перед первым child start до завершения обоих jobs и scientific verification |
| Model makespan, с | monotonic от первого child start до reap последнего child; отдельно от verify |
| CPU seconds | сумма user+system дочерних процессов; дополнительно controller CPU и cgroup cpu.stat delta; не CPU% из одного snapshot |
| RSS | sampled RSS и peak каждого процесса; simultaneous RSS набора по общим timestamps; cgroup memory.peak/current отдельно, поскольку включает иные категории памяти |
| Speedup | `wall_A / wall_B` для **того же двухjob набора**; CPU и память — обязательные countermetrics |
| Работа | два completed результата; публикации, expected ID–receiver пары, доставки/ошибки, ticks/phases, payload и scientific hashes |

Для каждого из двух jobs совпадают input SHA, executed config и `summary.json`, `histograms.json`, `publications.json`, `delivery-receipts.json`, `transmissions.json`, `network-evidence.json` между A и B. Требуется пересчёт `verify_loaded_run` обоих вариантов. Manifest, elapsed/RSS/process logs и wall-clock metadata сравниваются по смыслу, а не побайтно. Если scientific файлы не совпадают, сохранять raw и **не рассчитывать принятый speedup** до разбора причины; число ticks само по себе недостаточно. Нельзя исключать неудачные попытки из записи или приписывать timeout нулевой wall.

## Минимальная программа и ограничения выводов

- [x] Разделить четыре исторически смешиваемые гипотезы и сохранить source audit; старый аудит ускорения не перезаписывать.
- [x] Зафиксировать вопрос workers1/2 и candidate fixture до новых timings: [acceleration-program.json](../../reports/audits/dissertation/bounded-experiments-20261030/acceleration-program.json).
- [ ] Перед реализацией выбрать **отдельный** следующий диагностический блок либо явно разделить текущие 600 с. Никакой автоматической очереди после causal pilot.
- [ ] Минимальный measurement smoke: A/A на неизменённом наборе, проверить CPU/reaping/RSS и hashes. Один A/B набор после этого — только технический feasibility pilot, без доверительного интервала и устойчивого коэффициента.
- [ ] Для численного научного вывода отдельно принять минимум две чередующиеся пары **AB, BA** с одинаковым staging/cold policy и практическим порогом **10% end-to-end wall**. Записать все individual timings, paired ratios, CPU и memory movement; n=2 не даёт надёжного статистического обобщения. Если A/A spread либо разброс paired ratios перекрывает 10%, результат inconclusive. Не добирать повторы после просмотра результата.
- [ ] Если даже feasibility pilot нельзя закончить в заранее оставленном бюджете, классифицировать budgetNotAdmitted. Не сокращать только один вариант и не экстраполировать throughput по одному job.

Раньше предложенная в историческом аудите полная программа 2 workloads × 2 variants × 5 повторов + прогрев остаётся **отдельным расширением**. Её нельзя незаметно вписать в этот десятиминутный блок или заменить ограниченным pilot при закрытии исторического заявления.

## Аудит средств запуска и подтверждённый пробел

| Средство | Что даёт | Почему сейчас недостаточно |
| --- | --- | --- |
| `tools/dissertation/audit_acceleration.py` | пересчитывает 32 опубликованных отношения | не запускает симулятор, не восстанавливает первичные timings |
| `tools/campaign/pilot-parallel-memory.py` | два child, cgroup memory и сравнение структурных scientific outputs | нет последовательного matched schedule, CPU accounting и общего hard wall; другой CLI/schema |
| `tools/dissertation/run_network_parallel.py` | workers1…N, deadline, sealed snapshot, jobs/phase gates | привязан к полной network program/pilots/решениям; CPU seconds не пишет; изменение manifest для mini-fixture потребовало бы отдельного staging и контракта |
| `reports/audits/dissertation/queue-capacity-short-v2/run_short.py` | строгая пара1024/4096 и raw verification | меняет научный input, не сравнивает schedulers; cannot reuse as speed benchmark |
| Новый `run_queue_pilot.py` | только новый causal q1/1024 | не запускатель ускорения и не скрытый режим workers1/2 |

**Недостаёт** узкого `run_acceleration_pilot.py`: неизменяемая program identity; общий deadline без reset/retry; matched schedules; individual user/system CPU; primary wall границы; RSS набора; равенство raw по каждому job; квитанция каждого исхода. Его **не реализовывали** и команды запуска benchmark сейчас нет. Предлагать `dotnet ... --parallel`, запуск полного legacy `original` или сети162+6 вместо него было бы подменой вопроса.

При готовом launcher preflight должен закреплять source/binary/config/dotnet/controller/protocol SHA, CPU model/SMT/affinity, OS/.NET, cgroup policy и занятость машины. Не выполнять схемы A/B одновременно и не совмещать с очередью или Web jobs. При technical failure/equivalence failure, срабатывании любого ресурса или неразличимости относительно A/A завершить ограниченный блок и сохранить исход. Любой вывод относится только к современной кампании данного fixture и host.
