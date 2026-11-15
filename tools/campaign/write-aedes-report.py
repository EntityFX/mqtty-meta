"""Reproduce the final Aedes Markdown report from sealed campaign summaries.

Run against a synchronized compact controller bundle. No native or simulator
execution, archive extraction, raw modification, or parameter fitting is performed.
"""
import argparse
import collections
import csv
import hashlib
import json
import math
import pathlib
import shlex
import statistics


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def number(value, precision=3):
    return 'неприменимо' if value is None else f'{value:.{precision}f}'


def table(headers, rows):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(map(cell, headers)) + ' |',
        '| ' + ' | '.join('---' for _ in headers) + ' |'] +
        ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows])


def key(point):
    return point['messageBytes'], point['qos'], point['publishers']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--series', required=True, help='Synchronized compact controller bundle')
    args = parser.parse_args()
    root = pathlib.Path(args.series).resolve()
    reports = root / 'reports'
    plan, state = read(root / 'series.json'), read(root / 'state.json')
    if state['brokers']['Aedes']['status'] != 'завершён':
        raise RuntimeError('Aedes has not completed its full cycle')
    observations = {stage: read(root / 'brokers/Aedes' / stage / 'broker-observations.v3.json')
                    for stage in ('calibration', 'validation')}
    verifications = {stage: read(root / 'brokers/Aedes' / stage / 'archive-verification.json')
                     for stage in observations}
    expected = {(b, q, n) for b in (16, 256) for q in range(3) for n in (1, 16, 64, 128)}
    for stage, data in observations.items():
        assert data['runCount'] == 72 and len(data['points']) == 24
        assert {key(p) for p in data['points']} == expected
        assert verifications[stage]['verified'] is True
        for point in data['points']:
            assert point['repeats'] == 3
            assert point['publishFailureRate']['mean'] == 0
            for name in ('attemptedRps', 'completedRps', 'publishFailureRate', 'deliveryLossRate'):
                metric = point[name]
                assert metric['count'] == 3
                assert math.isclose(metric['ci95HalfWidth'], 4.302652729911275 * metric['standardDeviation'] / math.sqrt(3), rel_tol=1e-7, abs_tol=1e-7)
    profile = root / 'brokers/Aedes/broker-calibration.v3.json'
    profile_sha = sha(profile)
    assert profile_sha == state['profiles']['Aedes']
    attempts = [r for r in read(reports / 'Aedes-attempts.json') if r['campaign'] in ('Aedes-calibration', 'Aedes-validation')]
    assert len(attempts) == 144 and all(r['status'] == 'success' for r in attempts)
    assert all(r['guard']['assessment']['success'] for r in attempts)
    sims = read(reports / 'Aedes-simulations.json')
    by_stage = {stage: [r for r in sims if r['stage'] == stage] for stage in ('fidelity', 'holdout', 'relay')}
    for stage, rows in by_stage.items():
        assert len(rows) == 72
        assert len({(r['bytes'], r['qos'], r['publishers'], r['seed']) for r in rows}) == 72
        assert all(r['summary']['status'] == 'completed' for r in rows)
        assert all(r['measurementSeconds'] == (30 if stage == 'fidelity' else 10) for r in rows)
    criteria = {}
    for stage in ('fidelity', 'holdout'):
        criteria[stage] = {name: dict(collections.Counter(c['status'] for r in by_stage[stage]
                           for c in r['summary']['acceptanceCriteria'] if c['name'] == name))
                           for name in ('rps', 'publishFailureRate', 'deliveryLossRate', 'latencyP99Ms')}
    failed = {stage: sum(any(c['status'] == 'failed' for c in r['summary']['acceptanceCriteria'])
                         for r in rows) for stage, rows in by_stage.items() if stage != 'relay'}
    cal = {key(p): p for p in observations['calibration']['points']}
    val = {key(p): p for p in observations['validation']['points']}
    changes = [{'messageBytes': k[0], 'qos': k[1], 'publishers': k[2],
                'rpsRelativeChange': val[k]['completedRps']['mean'] / cal[k]['completedRps']['mean'] - 1,
                'lossAbsoluteChange': val[k]['deliveryLossRate']['mean'] - cal[k]['deliveryLossRate']['mean']}
               for k in sorted(expected)]
    resources = []
    for stage in ('calibration', 'validation'):
        rows = [r for r in attempts if r['campaign'] == 'Aedes-' + stage]
        cpus = [r['resources']['captureCpuMeanPercent'] for r in rows]
        rss = [r['resources']['captureRssMaxBytes'] / 1024**2 for r in rows]
        max_guard = max(i['cpuPercent'] for r in rows for i in r['guard']['assessment']['intervals'])
        resources.append({'stage': stage, 'runs': len(rows), 'cpuMeanRange': [min(cpus), max(cpus)],
                          'rssMaxMiBRange': [min(rss), max(rss)], 'maximumGuardCpuPercent': max_guard})
    archives = [r for r in read(root / 'archive-registry.json') if '/Aedes-' in r['archivePath']]
    assert len(archives) >= 144
    (reports / 'Aedes-archives.json').write_text(json.dumps(archives, indent=2), encoding='utf-8')
    native_rows = []
    for stage, data in observations.items():
        for p in sorted(data['points'], key=key):
            latency = p['observedLatencyQuantiles']
            native_rows.append({'stage': stage, 'bytes': p['messageBytes'], 'qos': p['qos'], 'publishers': p['publishers'],
                'attemptedRps': p['attemptedRps']['mean'], 'completedRps': p['completedRps']['mean'],
                'rpsCi95HalfWidth': p['completedRps']['ci95HalfWidth'], 'failureRate': p['publishFailureRate']['mean'],
                'deliveryLossRate': p['deliveryLossRate']['mean'], 'p50Ms': latency['p50Ms'] if latency else None,
                'p95Ms': latency['p95Ms'] if latency else None, 'p99Ms': latency['p99Ms'] if latency else None,
                'p99Ci95HalfWidth': p['observedLatencyStatistics']['p99Ms']['ci95HalfWidth'] if latency else None,
                'rttBaselineMs': p['rttBaselineMs'], 'repeats': 3})
    with (reports / 'Aedes-native-results.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(native_rows[0])); writer.writeheader(); writer.writerows(native_rows)
    relay_points = []
    for k in sorted(expected):
        rows = [r for r in by_stage['relay'] if (r['bytes'], r['qos'], r['publishers']) == k]
        offered = {r['offeredRps'] for r in rows}; assert len(offered) == 1
        receipts = [sum(r['receivers'].values()) for r in rows]
        relay_points.append({'bytes': k[0], 'qos': k[1], 'publishersPerBroker': k[2], 'seeds': 3,
            'offeredTotalRps': rows[0]['offeredRps'], 'offeredPerBrokerRps': rows[0]['offeredRps'] / 4,
            'brokerCompletedRpsMean': statistics.mean(r['summary']['simulated']['completedRps'] for r in rows),
            'brokerCompletedRpsCi95HalfWidth': 4.302652729911275 * statistics.stdev(r['summary']['simulated']['completedRps'] for r in rows) / math.sqrt(3),
            'receiverReceiptsMean': statistics.mean(receipts), 'receiverReceiptsMin': min(receipts), 'receiverReceiptsMax': max(receipts),
            'receiverReceiptScope': 'measurement reset through drain; warmup in-flight arrivals may cross boundary',
            'measurementSeconds': 10})
    with (reports / 'Aedes-relay-results.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(relay_points[0])); writer.writeheader(); writer.writerows(relay_points)
    native_worst = {stage: max(data['points'], key=lambda p: p['deliveryLossRate']['mean']) for stage, data in observations.items()}
    assets = sorted((reports / 'assets').glob('Aedes*'))
    hashes = {str(p.relative_to(root)).replace('\\', '/'): sha(p) for p in [profile,
        root / 'inputs/Aedes-calibration.json', root / 'inputs/Aedes-validation.json', root / 'inputs/Aedes-stand.json',
        root / 'brokers/Aedes/calibration/broker-observations.v3.json', root / 'brokers/Aedes/validation/broker-observations.v3.json',
        reports / 'Aedes-attempts.json', reports / 'Aedes-simulations.json', reports / 'Aedes-native-results.csv',
        reports / 'Aedes-relay-results.csv', reports / 'Aedes-archives.json', *assets]}
    summary = {'seriesId': plan['seriesId'], 'broker': 'Aedes', 'nativeRuns': 144, 'brokerFidelityRuns': 144,
        'relayRuns': 72, 'profileSha256': profile_sha, 'criteria': criteria, 'runsWithFailedFidelityCriteria': failed,
        'independentNativeChanges': changes, 'resources': resources, 'relayPoints': relay_points,
        'archiveVerification': verifications, 'inputAndFigureSha256': hashes, 'reportGeneratorSha256': sha(__file__)}
    (reports / 'Aedes-summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    documents = ['# Aedes — итоговый отчёт об измерениях',
        f'Серия `{plan["seriesId"]}`. Полный цикл Aedes завершён **15 октября 2026 года, 11:40 МСК**. '
        'Фактическое размещение: контроллер и брокер на одной Linux-машине; Windows используется для управления и получения документов.',
        '## Основные результаты',
        '**Выполнены 144 успешных натурных измерения, 144 BrokerFidelity-симуляции и 72 сетевые симуляции MqttRelay. '
        'Количественная точность двойника Aedes не подтверждена.**',
        f'Критерии fidelity нарушены в **{failed["fidelity"]}/72** калибровочных воспроизведениях и **{failed["holdout"]}/72** '
        'независимых контрольных симуляциях. Это научный результат проверки, а не ошибка исполнения CLI.',
        f'Натурный `publishFailureRate` равен нулю во всех 48 агрегированных точках. При этом `deliveryLossRate` достигает '
        f'**{100 * native_worst["calibration"]["deliveryLossRate"]["mean"]:.2f}%** в калибровке и '
        f'**{100 * native_worst["validation"]["deliveryLossRate"]["mean"]:.2f}%** в независимом контроле '
        '(256 байт, QoS 1, 128 publishers). Успешное завершение публикации и получение сообщения подписчиком — разные события.',
        'Причины недоставки этим экспериментом не разделены между брокером, очередями, клиентом-получателем и инструментированием. '
        'Эти величины нельзя без дополнительной диагностики объявлять потерями исключительно внутри Aedes. '
        'Высокий publisher RPS сам по себе не означает высокую сквозную доставку.',
        '## 1. Установка и условия',
        table(['Параметр', 'Зафиксированное значение'], [
            ['Брокер / runtime', 'Aedes 1.1.2 / Node.js v22.13.1; native, без Docker'],
            ['ОС', 'Simply Linux 11.1; Linux 6.12.85-6.12-alt1, x86-64'],
            ['CPU / память', 'Xeon E5-2680 v3, 12 физических ядер / 24 логических CPU, SMT=2; RAM около 15,5 GiB'],
            ['Размещение', 'Linux 192.168.5.1; MQTT 127.0.0.1:1883; Windows — управление'],
            ['Режим', 'singleHostSequential / singleCorePinned'],
            ['Брокер', 'CPU 0; MemoryMax 4 GiB'],
            ['Контроллер', 'CPU 1–11, 13–23; CPU 12 как SMT sibling CPU 0 исключён; MemoryMax 8 GiB'],
            ['CPU guard', '99% общесистемного CPU; отдельный sampler; максимальный интервал 1,25 с'],
            ['Governor / модель линии', 'Зафиксированный ondemand; loopback в натурных тестах. 1 Gbit/s в симуляции — допущение'],
            ['Хранилище', '/run/media/entityfx/hd250, /dev/sdc1, ext4; raw и архивы на внешнем диске']]),
        'Закреплённый логический CPU не объявляется эксклюзивным физическим ядром. Фоновые процессы и общие RAM/I/O '
        'могут влиять на результат. Реальная двухмашинная Ethernet-конфигурация этой серией не измерялась. '
        'Подробности CPU/SMT, NIC/MTU, времени и фоновых процессов: [environment.json](../environment.json); '
        'точный запуск: [Aedes-stand.json](../inputs/Aedes-stand.json).',
        '## 2. Программа и фактическое покрытие',
        table(['Этап', 'Запуски', 'Нагрузка / повтор', 'Результат'], [
            ['Натурная калибровка', '72/72; технических отказов 0', '30 с; три повтора', '24 точки'],
            ['Калибровочный BrokerFidelity', '72/72; технических отказов 0', '30 модельных секунд; seeds 42/43/44', f'{failed["fidelity"]}/72 с failed-критериями'],
            ['Независимый натурный контроль', '72/72; технических отказов 0', '30 с; три новых повтора', 'Профиль не менялся'],
            ['Независимый BrokerFidelity', '72/72; технических отказов 0', '10 модельных секунд; seeds 42/43/44', f'{failed["holdout"]}/72 с failed-критериями'],
            ['Однородная MqttRelay-сеть', '72/72; технических отказов 0', '10 модельных секунд; seeds 42/43/44', 'Модельные результаты; натурная fidelity сети не проверялась']]),
        'Матрица каждого этапа: payload **16/256 байт × QoS 0/1/2 × publishers 1/16/64/128 × три повтора/seed**. '
        'Натурные warmup/measurement/cooldown — **5/30/5 с**, drain до 30 с; захват телеметрии — 90 с на попытку. '
        'Seed порядка калибровки — **20260909**, независимого контроля — **20260910**. '
        'Пилотные три натурных запуска и диагностические performance pilots не включены в 360 основных запусков.',
        'У симуляций warmup 5 с, drain до 30 с, tick **0,1 мс**, `parallel=false`, `refreshStrategy=0`. '
        'Первые 72 симуляции Aedes сохранены с окном 30 с; после решения пользователя новые окна стали 10 с. '
        'Эти выборки не объединены в одну статистику; короткое окно уменьшает число наблюдений и устойчивость p99. '
        'Калибровочный Fidelity шёл одним процессом, holdout — до четырёх, Aedes MqttRelay — двумя. '
        'Переход сети на четыре процесса относится к последующим брокерам. Натурные тесты с симуляциями не пересекались.',
        'Один первоначальный незавершённый запуск симулятора намеренно прерван перед оптимизацией; его каталог и диагностика '
        'сохранены, он не включён в успешное покрытие. Остановка сборки отчёта из-за декларации кодировки GraphML также '
        'не считается отказом натурного измерения. Исходный GraphML не переписывался.',
        '## 3. Метрики и статистика',
        '`attemptedRps = attemptedPublishes / actualMeasurementSeconds`; '
        '`completedRps = completedPublishes / actualMeasurementSeconds`; '
        '`publishFailureRate = failedPublishes / attemptedPublishes`; '
        '`deliveryLossRate = 1 − completedIdsDelivered / completedPublishes` '
        '(при нуле завершений реализация возвращает 0). Последняя метрика учитывает только доставку ID завершённых публикаций.',
        'Задержка QoS 1/2 — время до PUBACK/PUBCOMP, а не задержка доставки подписчику. '
        'Для QoS 0 ACK latency **неприменима**, а не равна нулю. '
        'Табличные p50/p95/p99 — средние соответствующих квантилей трёх запусков, а не квантили объединённой выборки.',
        'Для каждого натурного показателя сохранены три повтора, sample SD и **Student-t CI95: mean ± 4,30265273 × SD / √3**, df=2. '
        'Границы интервала не обрезаются до [0;1], поэтому CI для доли может формально выходить за физические границы. '
        'При n=3 статистическая мощность ограничена; поправка на множество сравнений не применялась. '
        'Формула CI проверена по исходным JSON при генерации этого документа.']
    for stage, title in [('calibration', '4. Натурная калибровка'), ('validation', '5. Независимый натурный контроль')]:
        points = sorted(observations[stage]['points'], key=key)
        documents += ['## ' + title, f'[Observations: {stage}](../brokers/Aedes/{stage}/broker-observations.v3.json). '
            'В каждой строке три новых натурных повтора. failure/loss ниже выражены в процентах.',
            table(['Байт', 'QoS', 'N', 'attempted RPS', 'completed RPS ± CI95', 'failure, %', 'loss, %'], [
                [p['messageBytes'], p['qos'], p['publishers'], number(p['attemptedRps']['mean'], 1),
                 number(p['completedRps']['mean'], 1) + ' ± ' + number(p['completedRps']['ci95HalfWidth'], 1),
                 number(100 * p['publishFailureRate']['mean'], 2), number(100 * p['deliveryLossRate']['mean'], 2)] for p in points]),
            f'![completed RPS: {stage}](assets/Aedes-{stage}-completedRps.png)',
            f'![Доля недоставленных сообщений: {stage}](assets/Aedes-{stage}-deliveryLossRate.png)',
            table(['Байт', 'QoS', 'N', 'p50, мс', 'p95, мс', 'p99 ± CI95, мс', 'RTT, мс'], [
                [p['messageBytes'], p['qos'], p['publishers'],
                 number(p['observedLatencyQuantiles']['p50Ms'] if p['observedLatencyQuantiles'] else None),
                 number(p['observedLatencyQuantiles']['p95Ms'] if p['observedLatencyQuantiles'] else None),
                 number(p['observedLatencyQuantiles']['p99Ms']) + ' ± ' + number(p['observedLatencyStatistics']['p99Ms']['ci95HalfWidth']) if p['observedLatencyQuantiles'] else 'неприменимо',
                 number(p['rttBaselineMs'])] for p in points]),
            f'![Задержка завершения публикации: {stage}](assets/Aedes-{stage}-latency.png)']
    max_change = max(changes, key=lambda p: abs(p['rpsRelativeChange']))
    documents += [f'Максимальное абсолютное изменение среднего native completed RPS между двумя сериями — '
        f'**{100 * abs(max_change["rpsRelativeChange"]):.2f}%** '
        f'({max_change["messageBytes"]} байт, QoS {max_change["qos"]}, N={max_change["publishers"]}). '
        'Это описательное сравнение; совпадение или пересечение CI не объявляется самостоятельным доказательством эквивалентности.',
        '## 6. CPU, RSS и guard',
        table(['Серия', 'Успешных', 'CPU mean по capture, диапазон %', 'RSS max по capture, диапазон MiB', 'Максимум CPU guard, %'], [
            [r['stage'], r['runs'], '–'.join(number(v, 1) for v in r['cpuMeanRange']),
             '–'.join(number(v, 1) for v in r['rssMaxMiBRange']), number(r['maximumGuardCpuPercent'], 1)] for r in resources]),
        'Все 144 основных натурных попытки прошли guard. CPU/RSS выше относятся к **полному 90-секундному envelope захвата**, '
        'включая warmup, drain и cooldown; это не среднее строго 30-секундного окна. '
        'CPU guard — общесистемная метрика контроллера, CPU/RSS в telemetry — метрики процесса брокера. '
        'CPU/RSS не являются моделируемыми свойствами цифрового двойника.',
        '![CPU и RSS всех повторов](assets/Aedes-resources.png)',
        'Ресурсный рисунок штатного генератора также включает три pilot; таблица выше рассчитана только по 144 основным попыткам.',
        '[Все 144 основные попытки и три pilot с guard и capture-метриками](Aedes-attempts.json).',
        '## 7. Двойник и проверка fidelity',
        'Профиль построен только по калибровочным observations; независимый контроль не использован для перекалибровки '
        'или подбора транспорта. Offered load каждой симуляции берётся из соответствующей натурной точки. '
        'Критерии: относительная ошибка completed RPS ≤15%; p99 QoS 1/2 ≤20%; '
        'абсолютная разница failure/loss ≤0,05, то есть 5 процентных пунктов.',
        table(['Критерий', 'Калибровка 30 с: passed / failed / N/A', 'Независимый контроль 10 с: passed / failed / N/A'], [
            [name, *[' / '.join(str(criteria[stage][name].get(status, 0)) for status in ('passed', 'failed', 'notApplicable'))
                     for stage in ('fidelity', 'holdout')]] for name in criteria['fidelity']]),
        '**Полный набор критериев одновременно пройден в ' + str(72 - failed['fidelity']) + '/72 калибровочных и ' + str(72 - failed['holdout']) + '/72 независимых симуляций.** '
        '`notApplicable` у QoS 0 не считается прохождением критерия задержки.',
        'Например, в калибровочной точке 256 байт / QoS 2 / N=64 симулятор даёт около 3369 completed RPS '
        'вместо 6631 натурных RPS: ошибка около 49,2%. В этой точке модель отвергла на допуске 97 874 из 198 939 попыток; '
        'все 101 065 принятых завершены и доставлены. Это не равнозначно потере половины сообщений реальным брокером. '
        'Другой пример: 16 байт / QoS 1 / N=1 / seed=43 — модельный p99 6,7 мс против натурных 0,1703 мс. '
        'Первопричина систематических расхождений этой кампанией не установлена.',
        '![Карта ошибок относительно порогов](assets/Aedes-fidelity-errors.png)',
        '![Натурные значения и симуляции](assets/Aedes-measured-vs-simulated.png)',
        'В benchmark знаменатель RPS — actualMeasurementSeconds с завершением in-flight операций. '
        'В simulator знаменатель — фиксированное measurement-окно; завершения во время drain входят в числитель. '
        'Эту разницу нужно учитывать при интерпретации расхождений. Калибровочная и контрольная симуляции '
        'также имеют разную длительность; снижение/рост числа passed не является чистой оценкой улучшения модели.',
        '[Все 216 summaries и receiver counters](Aedes-simulations.json) · [Машинная сводка](Aedes-summary.json).',
        '## 8. Однородная сеть MqttRelay',
        'Четыре двойника Aedes, `networkLength=2`, lookup профиля по publishers. '
        'В каждой точке суммарный внешний offered RPS равен **4 × attemptedRps калибровочной точки**; '
        'нагрузка каждого узла — четверть этой суммы. Транспорт зафиксирован до независимого контроля: '
        'transferTicks=2, capacity=125 000 000 байт/с, queueCapacity=100 000. Ёмкость 1 Gbit/s и размер очереди — '
        'явные модельные допущения, не результат измерения loopback.',
        '![Топология модели: четыре Aedes](assets/Aedes-topology.png)',
        '[Исходная GraphML-топология](assets/Aedes-topology.graphml). У исходного файла ошибочная декларация UTF-16 при UTF-8 bytes; '
        'при построении графика исправлена только интерпретация декларации в памяти, исходный файл сохранён.',
        table(['Байт', 'QoS', 'N на брокер', 'Внешний RPS всего', 'RPS на узел', 'Broker completed RPS', 'Receiver receipts, среднее [min; max]'], [
            [p['bytes'], p['qos'], p['publishersPerBroker'], number(p['offeredTotalRps'], 1), number(p['offeredPerBrokerRps'], 1),
             number(p['brokerCompletedRpsMean'], 1), f'{p["receiverReceiptsMean"]:.1f} [{p["receiverReceiptsMin"]}; {p["receiverReceiptsMax"]}]'] for p in relay_points]),
        'В каждой строке три seeds; CI95 для модельного completed RPS сохранён в сетевом CSV и JSON по той же формуле Student-t. '
        'Интервал по трём seeds характеризует повторяемость модели, а не её точность относительно реальной сети. '
        'Broker completed RPS — агрегат брокерных завершений, включающий внутреннюю пересылку; '
        'его нельзя приравнивать к внешнему полезному throughput. Receiver receipts — фактически зарегистрированные '
        'прикладными получателями **внутри симуляции** события, а не измерения физической relay-сети. '
        'Счётчики покрывают measurement reset → drain; сообщения warmup in-flight могут пересекать границу. '
        'Сохранённые receiver counters не содержат origin-ID для доказательства удалённого маршрута каждого сообщения; '
        'по-сообщенческая атрибуция удалённой доставки этим набором counters не подтверждается.',
        'Пороговые критерии BrokerFidelity к этой сети не применяются. Сравнение с одиночным брокером — диагностическое. '
        '**Натурная точность MqttRelay-сети не подтверждена; результаты относятся к текущей модели с выявленными ограничениями двойника.** '
        '[CSV сетевых результатов](Aedes-relay-results.csv).',
        '## 9. Версии, целостность и доказательная база',
        table(['Компонент', 'Версия / commit'], [
            ['MqttBenchmark / native', '3a689466009a0d5410f5dc88dc1282d72c27f7dd'],
            ['Калибратор MqttY', '55a87f1a284da62b97e884ff1e747dd9fdcc037a'],
            ['Движок выполненных симуляций MqttY', '0fc65a26c10fe0da651475d738843946c8ec6339'],
            ['Профиль Aedes SHA-256', profile_sha],
            ['Node binary SHA-256', read(root / 'inputs/Aedes-stand.json')['brokers'][0]['executableSha256']]]),
        'Перед первой завершённой симуляцией устранено сканирование фиктивных response waiters и установлен детерминированный '
        'порядок последовательного refresh. Старый движок проявлял межпроцессную невоспроизводимость, поэтому старые '
        'и новые summaries не объявляются идентичными. Профиль и native benchmark при этом сохранены. '
        '[Переход версии](../revision-transition/manifest.json), '
        '[сокращение окна и параллельный запуск](../revision-transition/acceleration-20261015/manifest.json), '
        '[проверка идентичности четырёх параллельных повторов](../revision-transition/parallel-pilot.json).',
        'Для обеих натурных серий выполнены восстановление ZIP и повторная агрегация: scientific points и campaign identity '
        'совпали. Хэш всего JSON восстановленной агрегации отличается, поскольку inputSha256 описывает другое представление '
        'дерева файлов: compact descriptors и восстановленный raw. Это различие не скрывается:',
        table(['Серия', 'Проверено', 'Observations SHA-256', 'Restored aggregate SHA-256'], [
            [stage, verifications[stage]['verified'], verifications[stage]['originalSha256'], verifications[stage]['restoredAggregateSha256']]
            for stage in ('calibration', 'validation')]),
        '[Проверка calibration](../brokers/Aedes/calibration/archive-verification.json) · '
        '[Проверка validation](../brokers/Aedes/validation/archive-verification.json) · '
        '[Пути и SHA-256 архивов Aedes](Aedes-archives.json) · [SHA-256 входов и рисунков](Aedes-summary.json).',
        'Полные evidence лежат на Linux: `/run/media/entityfx/hd250/mqtty-evidence/' + plan['seriesId'] + '/`. '
        'Архивы натурных попыток: `evidence/attempts/Aedes-calibration/` и `evidence/attempts/Aedes-validation/`; '
        'модельные run directories: `evidence/brokers/Aedes/simulations/`. '
        'С Windows доступны через `\\\\192.168.5.1\\mqtty-evidence\\' + plan['seriesId'] + '`. '
        'Raw удалялся только после проверенного архивирования; исходные архивы и неуспешные/прерванные evidence сохранены. '
        'Хэши подтверждают целостность; научная проверяемость обеспечивается raw и повторным вычислением, а не одним хэшем.',
        '## 10. Воспроизведение и файлы',
        'Натурное воспроизведение выполняется в отдельное время, без конкурирующих измерений текущей кампании. '
        'Исходные argv, версии и пути сохранены в [commands.ndjson](../commands.ndjson). Для новых результатов '
        'нужно использовать отдельные выходные каталоги, не изменяя sealed evidence.',
        'После завершения работающей кампании проверка запускается штатным launcher: он создаёт owned systemd unit '
        'с требуемой affinity и MemoryMax и отказывает при уже активном контроллере. Обычный SSH shell не удовлетворяет '
        'проверкам контекста контроллера.',
        '```sh\n# Повторная проверка архивов и агрегирование исходной серии (на Linux)\n'
        + str(pathlib.PurePosixPath(plan['archiveMount']) / 'mqtty-evidence/tools/campaign-venv/bin/python')
        + ' ' + str(pathlib.PurePosixPath(plan['benchmarkRepository']) / 'scripts/campaign/launch-linux.py')
        + ' --series ' + shlex.quote('/run/media/entityfx/hd250/mqtty-evidence/' + plan['seriesId'] + '/controller')
        + ' --mode verify\n```',
        'Повторить модельную точку можно её неизменённым конфигом, используя отдельный output:',
        '```sh\n' + shlex.join(['taskset', '-c', '1-11,13-23', 'dotnet', plan['simulatorCli'], 'experiment', '--config',
            '/run/media/entityfx/hd250/mqtty-evidence/' + plan['seriesId'] + '/controller/brokers/Aedes/simulations/holdout-10s/m16.q1.p1.s42.json',
            '--output']) + ' "$REPLAY_OUTPUT"\n```',
        '`REPLAY_OUTPUT` должен указывать на новый каталог на hd250. Профиль и referenceObservationsPath в конфиге '
        'должны остаться неизменными; версия engine — указанная выше.',
        '[Полные натурные таблицы CSV](Aedes-native-results.csv) · [Модельные summaries JSON](Aedes-simulations.json) · '
        '[Сетевая таблица CSV](Aedes-relay-results.csv) · [Сводка и хэши JSON](Aedes-summary.json) · '
        '[Хэши итогового отчёта и приложений](Aedes-report-manifest.json) · '
        '[Подробный автоматически сформированный отчёт](Aedes-detailed.md).',
        'Этот Markdown воспроизводится скриптом `tools/campaign/write-aedes-report.py --series <compact-controller-bundle>`. '
        'Рисунки взяты из штатного генератора по тем же observations и summaries; их точные SHA-256 сохранены в сводке.']
    output = reports / 'Aedes.md'
    output.write_text('\n\n'.join(documents) + '\n', encoding='utf-8')
    outputs = [output, reports / 'Aedes-summary.json', reports / 'Aedes-native-results.csv',
               reports / 'Aedes-relay-results.csv', reports / 'Aedes-archives.json', reports / 'Aedes-detailed.md']
    (reports / 'Aedes-report-manifest.json').write_text(json.dumps({
        'seriesId': plan['seriesId'], 'reportGeneratorSha256': sha(__file__),
        'outputsSha256': {p.name: sha(p) for p in outputs}}, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(output), 'bytes': output.stat().st_size, 'nativeRuns': len(attempts),
                      'simulationRuns': len(sims), 'failedFidelity': failed, 'maximumRpsChange': max_change}, ensure_ascii=True))


if __name__ == '__main__':
    main()
