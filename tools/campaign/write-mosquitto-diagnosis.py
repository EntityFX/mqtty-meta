"""Build the evidence-based diagnosis from independent recounts and timing probes."""
import collections
import base64
import datetime
import hashlib
import json
import math
import html
import pathlib
import re
import statistics
import markdown
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REPO = pathlib.Path(__file__).resolve().parents[2]
SERIES = REPO / 'MqttBenchmark/artifacts/compact-20261016-6h-v1'
DIAG = SERIES / 'diagnostics/mosquitto'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'] +
                     ['| ' + ' | '.join(str(cell) for cell in row) + ' |' for row in rows])


def histogram_quantile(directory, probability, tick_ms):
    raw = read(pathlib.Path(directory) / 'latency-histogram.json')
    histogram = collections.Counter()
    for broker in raw.values():
        for qos in broker.values():
            for ticks, count in qos.items():
                histogram[int(ticks)] += count
    rank, cumulative = math.ceil(sum(histogram.values()) * probability), 0
    for ticks, count in sorted(histogram.items()):
        cumulative += count
        if cumulative >= rank:
            return ticks * tick_ms


def main():
    audits = [(path, read(path)) for path in sorted(DIAG.glob('*.audit.json'))]
    probes = read(DIAG / 'timing-sensitivity/results.json')
    if len(probes['rows']) != 18:
        raise RuntimeError('All 18 planned offline sensitivity runs must finish first')
    if any(value['differences'] or value['unsettled'] or value['duplicateAttemptIds'] or value['duplicateOutcomes'] for _, value in audits):
        raise RuntimeError('Independent recount found a mismatch; revise diagnosis')
    original = read(SERIES / 'reports/Mosquitto-simulations.json')
    for row in probes['rows']:
        if row['variant'] == 'baseline100us':
            reference = next(r for r in original if r['stage'] == 'fidelity' and
                (r['bytes'], r['qos'], r['publishers'], r['seed']) == (row['bytes'], row['qos'], 1, row['seed']))
            if row['p99Ms'] != reference['summary']['simulated']['latencyP99Ms']:
                raise RuntimeError('Windows baseline p99 does not reproduce Linux result')
    assets = DIAG / 'assets'
    assets.mkdir(exist_ok=True)
    q2audits = [v for _, v in audits if v['key']['messageBytes'] == 256 and v['key']['qos'] == 2]
    baseline = next(row for row in probes['rows'] if row['bytes'] == 256 and row['seed'] == 42 and row['variant'] == 'baseline100us')
    quantiles = [.99, .999, .9999]
    native = [[v['p99Ms'], v['additionalLatencyQuantilesMs']['0.999'], v['additionalLatencyQuantilesMs']['0.9999']] for v in q2audits]
    simulated = [histogram_quantile(baseline['directory'], q, .1) for q in quantiles]
    fig, ax = plt.subplots(figsize=(8, 4))
    for index, values in enumerate(native):
        ax.plot(range(3), values, 'o-', label='Native repeat ' + str(index + 1))
    ax.plot(range(3), simulated, 's--', label='MqttY baseline, seed 42')
    ax.set_xticks(range(3), ['p99', 'p99.9', 'p99.99'])
    ax.set_yscale('log')
    ax.set_ylabel('Publish completion latency, ms (log scale)')
    ax.set_title('Mosquitto: 256 bytes, QoS 2, one publisher')
    ax.grid(alpha=.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(assets / 'latency-tail.png', dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    variants = ['baseline100us', 'fine10us-preserved-time', 'fine10us-calibrated-transport']
    for ax, (size, qos, reference) in zip(axes, [(16, 1, .15980000000005626), (256, 2, .20830000000023574)]):
        for variant in variants:
            rows = sorted([r for r in probes['rows'] if r['bytes'] == size and r['variant'] == variant], key=lambda r: r['seed'])
            ax.plot([r['seed'] for r in rows], [r['p99Ms'] for r in rows], 'o-', label=variant)
        ax.axhspan(reference * .8, reference * 1.2, color='green', alpha=.15, label='Native target +/-20%')
        ax.set(title=f'{size} bytes, QoS {qos}, one publisher', xlabel='Seed', ylabel='p99, ms', yscale='log')
        ax.set_xticks([42, 43, 44])
        ax.grid(alpha=.3)
    axes[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(assets / 'timing-sensitivity.png', dpi=160)
    plt.close(fig)
    evidence = {'capturedUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'archiveAudits': [v for _, v in audits], 'timingProbe': probes,
                'modelTailSeed42Ms': dict(zip(map(str, quantiles), simulated)), 'baselineP99MatchesLinux': 6}
    evidence['diagnosticSourceSha256'] = {name: hashlib.sha256((REPO / 'tools/campaign' / name).read_bytes()).hexdigest()
        for name in ('audit-delivery.py', 'probe-mosquitto-timing.py', 'write-mosquitto-diagnosis.py')}
    evidence['sourceCommits'] = {'MqttBenchmark': '3a689466009a0d5410f5dc88dc1282d72c27f7dd',
        'MqttY': '4caf41246ec6636acc53b82ff2a8d918b74a6dbe'}
    (DIAG / 'evidence-summary.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    source = REPO / 'docs/mosquitto-fidelity-diagnosis.md'
    prefix = '../MqttBenchmark/artifacts/compact-20261016-6h-v1/diagnostics/mosquitto/'
    grouped = []
    for size in (16, 256):
        for variant in variants:
            rows = [r for r in probes['rows'] if r['bytes'] == size and r['variant'] == variant]
            grouped.append([size, variant, ', '.join(f"{r['p99Ms']:.4g}" for r in rows),
                sum(next(c for c in r['criteria'] if c['name'] == 'latencyP99Ms')['status'] == 'passed' for r in rows),
                f"{statistics.mean(r['elapsedSeconds'] for r in rows):.1f}"])
    text = '''# Mosquitto / MqttY: диагностика недоставки и провалов fidelity

Проверка 16.10.2026. Вывод: обнаружены подтверждённые несоответствия модели/методики и реальная недоставка на измерительной цепочке. Дефект Mosquitto не установлен. Текущая compact-кампания, её входы, профиль и пороги не изменялись. Ниже отдельно указаны факты, гипотезы и предлагаемые действия.

## 1. Что проверено независимо

На Windows, вне нагрузки Linux, пересчитаны sealed raw шести натурных запусков. ZIP проверены по SHA-256 receipts; все 270 файловых seals совпали. Проверялись множества ID attempt/completed/failed/delivered, дубликаты, неожиданные доставки, незавершённые публикации и p99 по nearest-rank. Расхождений с сохранёнными summaries нет. Это проверка согласованности сохранённых данных, а не независимая фиксация пакетов на проводе.

AUDIT_TABLE

В проблемном запуске 16 байт /QoS1 /16 publishers /r1 отправитель подтверждает примерно 56,4 тыс. публикаций/с, callback подписчика регистрирует около 5 тыс. доставок/с. Дубликатов, неизвестных ID и незавершённых публикаций нет. Последняя зарегистрированная доставка — 15,104 с от начала run; измерение закончилось примерно на 15-й секунде, drain остановлен по quietPeriod через 1,853 с. Данных о доставках после остановки нет: это следует называть «не получено к завершению drain», а не автоматически доказанной безвозвратной потерей.

Эффект существовал до сокращения: полная 30-секундная калибровка Mosquitto при 16 publishers, 16 байтах даёт недоставку 90,77% для QoS1 и 91,98% для QoS2. Короткая — 91,15% и 91,87%. Поэтому сокращение до 10 с не является достаточным объяснением.

## 2. Подтверждённые проблемы MqttY

### 2.1. Неверно восстановлен хвост распределения

`DeterministicMqttSampler.InverseCdf` строит линейную обратную CDF по min/p50/p75/p95/p99/max. В результате верхний 1% вероятности растягивается от p99 до max. Это дополнительная гипотеза формы хвоста, а не форма, измеренная в raw.

Для Mosquitto 256 байт /QoS2 /1 publisher профиль содержит observed p99=0,2083 мс и max=196,5997 мс. Из этой интерполяции получается observed p99.9≈176,961 мс. Однако независимо пересчитанные натурные p99.9 трёх повторов — 0,2560; 0,2479; 0,2553 мс. Натурные p99.99 — 2,1197; 1,1544; 0,3236 мс. Это существенное искажение распределения, а не только округление tick.

В отдельном запуске неизменённого MqttY на Windows, seed 42, p99=14,1 мс, p99.9=179,3 мс, p99.99=194,5 мс. Исходный p99 воспроизводит Linux-результат. Даже когда модельный p99 выглядит приемлемо для другого seed, форма верхнего хвоста остаётся неверной. При короткой выборке положение nearest-rank относительно границы верхнего 1% дополнительно меняется с seed; точная доля влияния каждого механизма не выделялась экспериментом.

![Сравнение хвостов](PREFIXassets/latency-tail.png)

Исправление: новый версионированный профиль с эмпирической CDF или взвешенной гистограммой, включая редкий хвост. Не назначать целому интервалу p99..max вероятность 1% на основании одного max. Сохранять действительные редкие выбросы и их частоту; не обрезать их ради прохождения критерия. При необходимости использовать per-repeat histograms вместо усреднения отдельных квантилей и max. Сначала проверить sampler без MQTT на p50/p95/p99/p99.9 и частотах хвостовых интервалов.

### 2.2. Грубые и искусственно завышенные временные задержки

Натурный p99≈0,16 мс имеет допуск ±0,032 мс, но tick=0,1 мс. Ближайшие 0,2 мс уже дают около +25%. `TicksFromMs` округляет вверх и назначает минимум один tick даже нулевой processing latency. Транспорт Mosquitto также получает минимум один tick в одну сторону, хотя половина калибровочного MQTT echo RTT около 0,04 мс. MQTT echo RTT включает обработку брокера/клиента и не является чистой задержкой линии.

Проведено 18 дополнительных симуляций на Windows: два сочетания payload/QoS, один publisher, seeds 42/43/44, три варианта времени. Все исключены из покрытия кампании, профиль SHA-256 не менялся. Warmup/measurement/drain сохранены 1/2/5 модельных секунд.

* baseline100us — исходные настройки.
* fine10us-preserved-time — tick 10 мкс, физические transfer/outgoing/throughput-window времена сохранены пересчётом ticks.
* fine10us-calibrated-transport — tick 10 мкс, transfer рассчитан из того же калибровочного RTT (40 мкс), outgoing wait один новый tick (10 мкс). Это совместное изменение транспортных и служебных времён; его нельзя приписывать одному tick. Контрольные observations для выбора значений не использовались.

TIMING_TABLE

Последняя колонка — среднее реальное время одного диагностического запуска на Windows, а не прогноз Linux. Более мелкий tick увеличивает стоимость выполнения. Уменьшение шага с сохранением прежних физических задержек не устраняет проблему; изменение транспорта помогает части точек, но сохраняет ошибку хвоста. Это проверка чувствительности, а не готовая валидированная замена модели.

![Чувствительность времени](PREFIXassets/timing-sensitivity.png)

Исправление: согласовать временной бюджет протокола, использовать разрешение порядка 10 мкс для субмиллисекундных задержек и устранить необоснованный обязательный processing tick. Для сохранения скорости — планировщик событий/переход к следующему due tick с проверкой эквивалентности фиксированному шагу; не ускорять подменой метрик или уменьшением разрешения измеряемой latency.

### 2.3. Измеряются разные интервалы

В `CampaignWorkloadRunner` Stopwatch начинается перед MQTTnet PublishAsync и заканчивается после завершения PUBACK/PUBCOMP. В MqttY `MqttBroker.ProcessFromClientPublish` создаёт tracker со StartTick при получении PUBLISH брокером, а `CompletePublisher` завершает его при получении подтверждения клиентом. Первая пересылка издатель→брокер отсутствует в модельной latency. Это подтверждённое различие определения; само по себе оно не объясняет завышенный хвост, поскольку смещает время в другую сторону.

Исправление: единый интервал publisher Publish start→publisher PUBACK/PUBCOMP; переносить startTick и measurement generation/уникальный publish ID до первой отправки. Проверить QoS1/QoS2 на известной задержке каждого перехода, очередь отправки, повторную доставку и публикации на границе warmup/measurement/drain. Формулу observed−QoS×RTT пересмотреть после согласования метрики: вычитание среднего MQTT echo RTT из всех квантилей — модельное допущение, не идентификация внутреннего времени брокера.

### 2.4. Разные модели подачи нагрузки

Натурный генератор делает await PublishAsync внутри цикла каждого publisher: для QoS1/2 допускается одна незавершённая публикация на издателя. `DriveOfferedLoad` MqttY вызывает Publish по token schedule, а MqttClient сохраняет pending и сразу возвращается; ожидания завершения предыдущей публикации нет. Совпадение среднего offered RPS не делает такие процессы одинаковыми. Различие подтверждено кодом; его вклад в конкретные цифры ещё не отделён.

Исправление: явные `closedLoopPerPublisher` и `openLoopRate` режимы. BrokerFidelity для этой натурной программы должен использовать closed-loop с одним in-flight QoS1/2 на publisher. Offered RPS оставить отдельным ограничителем, а не заставлять модель выдать эталонный completed RPS. Показать независимые attempted/completed RPS и actual measurement duration. Для QoS0 определить собственное условие завершения отправки. Не переносить этот выбор автоматически на Relay, не указав сценарий.

## 3. Недоставка: что известно и чего не хватает

Все 16 publishers публикуют в один topic; подписчик один, SUBACK проверяется на запрошенный QoS. Это fan-in, а не 16 независимых пар. Подтверждение издателю и доставка подписчику — разные участки MQTT. Повторная проверка raw исключает обнаруживаемую ошибку ID/агрегации на выбранных шести запусках; она не исключает проблему клиентского приёма до callback.

Закреплённый конфиг Mosquitto содержит listener, allow_anonymous, persistence=false, sys_interval=2 и `log_dest none`; лимиты очереди и in-flight не заданы. В upstream Mosquitto 2.0.15 defaults — 20 исходящих in-flight и 1000 queued сообщений на клиент. В `database.c` этой версии при переполнении исходящей очереди сообщение отбрасывается, увеличивается счётчик и формируется notice. Поэтому наиболее вероятный механизм — насыщение пути одного subscriber и ограниченной очереди; историческое переполнение этими архивами не доказано: broker log отключён, SYS dropped/queued/sent не записывались. Источник узкого места (протокол/очередь, callback/lock, MQTTnet, TCP/Nagle) также не выделен.

Источники: [конфигурация версии 2.0.15](https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.15/mosquitto.conf), [ветвь переполнения очереди](https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.15/src/database.c), [документация SYS dropped](https://mosquitto.org/man/mosquitto-8.html). Defaults upstream нужно отличать от прямого наблюдения effective limits установленного RPM.

Дополнительные ограничения теста: delivery callback сериализует JSON под lock; публикации журналируются синхронно; BufferedEventLog использует неограниченный канал. Отдельный лёгкий CPU sampler и успешный guard не доказывают отсутствие bottleneck одного callback. `quietPeriod` может завершить drain до максимума при остающихся недоставленных ID; из этого нельзя сделать вывод об окончательной потере. При завершающемся drain не делается независимое сопоставление с очередями брокера.

## 4. Рекомендуемый вариант устранения

Первый этап — исправить методику наблюдения, сохранив исходный default-broker сценарий для сравнения. После окончания текущей серии провести отдельную ограниченную диагностику; не запускать её одновременно с натурными измерениями. Все файлы и временные данные — на hd250, с прежними disk guards и конечными лимитами RAM/очередей.

| Проверка | Что меняется относительно baseline | Что различает |
|---|---|---|
| A | Только лёгкая SYS-телеметрия и notice/warning/error broker log | Увеличивается ли dropped; queued/stored и sent на момент drain |
| B | Только минимальный subscriber callback, ID в компактный буфер, запись после окна | Ограничивает ли приёмник/журналирование доставку |
| C | Только `set_tcp_nodelay true`; те же лимиты | Вклад TCP batching, без одновременной смены очередей |
| D | Только inflight 200, остальные лимиты baseline | Ограничивает ли окно QoS handshake скорость выхода |
| E | Только очередь: queued 100000, queued bytes 67108864, inflight baseline | Вклад конечной очереди; лимиты — стартовая гипотеза, не готовая настройка |

Для начала: 16 байт, QoS1, publishers 1/16, три повтора — 30 коротких натурных запусков для пяти вариантов. Использовать ту же машину/affinity, порядок вариантов чередовать, записать commits/config hashes. Нагрузка 10 с; диагностический drain до 30 с. Ориентир отдельной диагностики — 25–35 минут, уточняется по pilot. Это дополнительная работа, не часть завершённого покрытия compact и не обещание уложить её в остаток первоначальных 6 часов.

Записывать `$SYS/broker/publish/messages/received`, `/sent`, `/dropped`, доступные stored/inflight/queued counters, callback receive/ACK times, состояние соединений, publisher и receiver CPU, ThreadPool и GC, high-watermark/ошибки буфера. SYS счётчики глобальны: отдельная диагностическая подписка и preflight/warmup должны быть учтены; сравнивать дельты одного чистого broker instance с корреляцией topic/ID, не требовать наивного равенства всех глобальных счётчиков. Для неясного остатка использовать короткий pcap только диагностического listener; pcap ограничить по размеру и хранить на hd250.

Правило разбора: рост broker dropped локализует отбрасывание в брокере; отправка broker без callback требует анализа TCP/client; рост callback без event-log ID — ошибка журналирования; остаток в очереди после drain — цензурирование по времени. Определять причину по совокупности счётчиков, логов и trace, поскольку они могут иметь разные границы учёта.

Затем определить два честно названных режима: admission stress (максимум приёма издателей с отчётом о недоставке) и delivery capacity (pacing/backpressure до устойчивой доставки одному receiver). Для delivery capacity скорость повышать ступенчато, оставляя запас относительно измеренной скорости подписчика и конечные очереди. Простое увеличение очереди не обеспечивает устойчивую систему, если входной поток быстрее выхода. Не включать unlimited queues и не менять QoS подписки на 0 ради исчезновения потерь.

Параллельно исправить MqttY в новой ревизии/профиле: (1) границы latency и режим нагрузки, (2) эмпирическое распределение, (3) временной бюджет и эффективное исполнение. Начать с двух воспроизведённых здесь точек и затем прогнать все 12 компактных точек. Критерии 15% RPS /20% p99 /0,05 failure-loss сохранить; дополнительно проверять p99.9, чтобы p99 не маскировал неверный хвост. После заморозки профиля провести новую независимую натурную проверку. Уже изученная validation не является новым слепым контролем исправленной модели.

## 5. Воспроизведение и доказательства

Замороженные версии: MqttBenchmark `3a689466009a0d5410f5dc88dc1282d72c27f7dd`, MqttY `4caf41246ec6636acc53b82ff2a8d918b74a6dbe`. SHA-256 профиля Mosquitto: `d21497ce6c27a8fc844904e8460a679741ac76a1f4b92c9fae291823dc7a9bdd`. SHA-256 диагностических скриптов сохранены в evidence-summary.json. Все шесть baseline p99 в диагностике совпали с исходными Linux-симуляциями; побитная идентичность всех файлов между ОС не заявляется.

Код проверенной ревизии: [натурный цикл](../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Workload/CampaignWorkloadRunner.cs), [drain](../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Workload/DrainPolicy.cs), [учёт доставок](../MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Workload/DeliveryLedger.cs), [самплер](../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Mqtt/BrokerProfile/DeterministicMqttSampler.cs), [подача нагрузки](../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Mqtt/Experiments/MqttRelayExperimentRunner.cs), [начало модельного tracker](../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Mqtt/MqttBroker.cs), [завершение tracker](../mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Mqtt/Counter/BrokerMeasurement.cs).

* Независимый проверяющий: [audit-delivery.py](../tools/campaign/audit-delivery.py), три теста на потери/дубликаты/неизвестные ID, подмену summary и seals.
* Чувствительность модели: [probe-mosquitto-timing.py](../tools/campaign/probe-mosquitto-timing.py). Все 18 запусков на Windows, одна задача за раз, исходные profile/observations только читаются.
* Полные результаты: [evidence-summary.json](PREFIXevidence-summary.json), [timing results](PREFIXtiming-sensitivity/results.json). Каждый audit хранит SHA-256 ZIP; рядом receipt, исходный сжатый ZIP и raw-пересчёт. Raw не извлекался на системный диск.
* Пример: `python tools/campaign/audit-delivery.py --archive <ZIP> --expected-sha <SHA256 receipt> --output <audit.json>`.
* Текущая Linux-кампания и её scientific coverage не включают эти диагностические симуляции. Source snapshots и native stand configs не менялись.
'''
    audit_rows = []
    for path, value in audits:
        m, k = value['recounted'], value['key']
        audit_rows.append([value['identity']['campaignId'].replace('Mosquitto-', ''),
            f"{k['messageBytes']}/{k['qos']}/{k['publishers']}/r{k['repeat']}", m['completedPublishes'], m['completedIdsDelivered'],
            f"{value['lossRate'] * 100:.3f}%", 'совпало'])
    text = text.replace('AUDIT_TABLE', table(['Серия', 'Байт /QoS /N /repeat', 'Подтверждено', 'Получено', 'Недоставка', 'Raw /summary'], audit_rows))
    text = text.replace('TIMING_TABLE', table(['Байт', 'Вариант', 'p99, мс: seeds 42/43/44', 'Прошло p99 /3', 'Wall, с'], grouped))
    text = text.replace('PREFIX', prefix)
    source.write_text(text, encoding='utf-8')
    exported = text.replace(prefix, '../diagnostics/mosquitto/').replace('../tools/campaign/', '../../../../tools/campaign/')
    exported = exported.replace('../MqttBenchmark/src/', '../../../src/').replace('../mqtty/src/', '../../../../mqtty/src/')
    (SERIES / 'reports/Mosquitto-diagnosis.md').write_text(exported, encoding='utf-8')
    body = markdown.markdown(exported, extensions=['tables', 'fenced_code'])
    def embed(match):
        path = SERIES / 'reports' / html.unescape(match.group(1))
        return 'src="data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode() + '"'
    body = re.sub(r'src="([^"]+\.png)"', embed, body)
    (SERIES / 'reports/Mosquitto-diagnosis.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<title>Mosquitto: диагностика</title><style>body{font:16px/1.5 system-ui;max-width:1200px;margin:30px auto;padding:20px}'
        'table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:6px}img{max-width:100%}code{overflow-wrap:anywhere}</style>'
        + body + '</html>', encoding='utf-8')
    print(source)


if __name__ == '__main__':
    main()
