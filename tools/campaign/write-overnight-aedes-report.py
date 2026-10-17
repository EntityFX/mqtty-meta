"""Recompute the completed service-budget Aedes report without running workloads."""
import argparse
import base64
import collections
import csv
import hashlib
import json
import math
import pathlib
import statistics

import markdown


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def table(headers, rows):
    def line(values):
        return '| ' + ' | '.join(str(v).replace('|', '\\|').replace('\n', ' ') for v in values) + ' |'
    return '\n'.join([line(headers), line(['---'] * len(headers)), *(line(r) for r in rows)])


def num(v):
    return 'неприменимо' if v is None else f'{v:.6g}'


def dump(p, d):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--series', required=True)
    args = parser.parse_args()
    root = pathlib.Path(args.series).resolve()
    out = root / 'reports'
    plan, state, env = (read(root / p) for p in ('series.json', 'state.json', 'environment.json'))
    assert state['brokers']['Aedes']['status'] == 'завершён'
    profile = root / 'brokers/Aedes/broker-calibration.v3.json'
    assert sha(profile) == state['profiles']['Aedes']
    observations = {stage: read(root / f'brokers/Aedes/{stage}/broker-observations.v3.json') for stage in ('calibration', 'validation')}
    verifications = {stage: read(root / f'brokers/Aedes/{stage}/archive-verification.json') for stage in observations}
    expected = {(b, q, n) for b in (16, 256) for q in range(3) for n in (1, 16, 64, 128)}
    native = []
    for stage, data in observations.items():
        assert data['runCount'] == 72 and len(data['points']) == 24
        assert {(p['messageBytes'], p['qos'], p['publishers']) for p in data['points']} == expected
        assert verifications[stage]['verified'] is True
        assert sha(root / f'brokers/Aedes/{stage}/broker-observations.v3.json') == verifications[stage]['originalSha256']
        for p in data['points']:
            assert p['repeats'] == 3
            for metric in ('attemptedRps', 'completedRps', 'publishFailureRate', 'deliveryLossRate'):
                m = p[metric]
                assert m['count'] == 3
                assert math.isclose(m['ci95HalfWidth'], 4.302652729911275 * m['standardDeviation'] / math.sqrt(3), rel_tol=1e-7, abs_tol=1e-7)
            lat = p['observedLatencyQuantiles'] or {}
            native.append(dict(stage=stage, bytes=p['messageBytes'], qos=p['qos'], publishers=p['publishers'],
                attemptedRps=p['attemptedRps']['mean'], completedRps=p['completedRps']['mean'], ci95=p['completedRps']['ci95HalfWidth'],
                failure=p['publishFailureRate']['mean'], loss=p['deliveryLossRate']['mean'],
                p50=lat.get('p50Ms'), p95=lat.get('p95Ms'), p99=lat.get('p99Ms'), rtt=p['rttBaselineMs']))
    attempts = read(out / 'Aedes-attempts.json')
    successful = [a for a in attempts if a['campaign'] != 'Aedes-pilot' and a['status'] == 'success']
    assert len(successful) == 144 and all(a['guard']['assessment']['success'] for a in successful)
    sims = read(out / 'Aedes-simulations.json')
    stages = {s: [r for r in sims if r['stage'] == s] for s in ('fidelity', 'holdout', 'relay')}
    criteria, failed, failures = {}, {}, []
    for stage, runs in stages.items():
        assert len(runs) == 72 and len({r['key'] for r in runs}) == 72
        assert all(r['summary']['status'] == 'completed' and r['measurementSeconds'] == 2 for r in runs)
        assert {(r['bytes'], r['qos'], r['publishers']) for r in runs} == expected
        assert {r['seed'] for r in runs} == {42, 43, 44}
        criteria[stage] = {name: dict(collections.Counter(c['status'] for r in runs for c in r['summary']['acceptanceCriteria'] if c['name'] == name))
            for name in ('rps', 'latencyP99Ms', 'publishFailureRate', 'deliveryLossRate')}
        failed[stage] = sum(any(c['status'] == 'failed' for c in r['summary']['acceptanceCriteria']) for r in runs)
        for r in runs:
            for c in r['summary']['acceptanceCriteria']:
                if c['status'] == 'failed':
                    failures.append(dict(stage=stage, key=r['key'], **c))
    resources = []
    for stage in observations:
        rows = [a for a in successful if a['campaign'] == f'Aedes-{stage}']
        resources.append([stage, len(rows),
            '–'.join(num(f(a['resources']['captureCpuMeanPercent'] for a in rows)) for f in (min, max)),
            '–'.join(num(f(a['resources']['captureRssMaxBytes'] / 2**20 for a in rows)) for f in (min, max)),
            num(max(i['cpuPercent'] for a in rows for i in a['guard']['assessment']['intervals']))])
    archives = [r for r in read(root / 'archive-registry.json') if '/Aedes-' in r['archivePath']]
    dump(out / 'Aedes-archives.json', archives)
    with (out / 'Aedes-native-results.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(native[0])); writer.writeheader(); writer.writerows(native)
    relay = []
    for b, q, n in sorted(expected):
        runs = [r for r in stages['relay'] if (r['bytes'], r['qos'], r['publishers']) == (b, q, n)]
        assert len({r['offeredRps'] for r in runs}) == 1
        cal = next(p for p in native if (p['stage'], p['bytes'], p['qos'], p['publishers']) == ('calibration', b, q, n))
        assert math.isclose(runs[0]['offeredRps'], 4 * cal['attemptedRps'])
        receipts = [sum(r['receivers'].values()) for r in runs]
        relay.append(dict(bytes=b, qos=q, publishersPerBroker=n, offeredTotalRps=runs[0]['offeredRps'],
            offeredPerBrokerRps=runs[0]['offeredRps']/4,
            completedRpsMean=statistics.mean(r['summary']['simulated']['completedRps'] for r in runs),
            failureMean=statistics.mean(r['summary']['simulated']['publishFailureRate'] for r in runs),
            lossMean=statistics.mean(r['summary']['simulated']['deliveryLossRate'] for r in runs),
            receiverReceiptsMean=statistics.mean(receipts), receiverReceiptsMin=min(receipts), receiverReceiptsMax=max(receipts)))
    with (out / 'Aedes-relay-results.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(relay[0])); writer.writeheader(); writer.writerows(relay)
    summary = dict(seriesId=plan['seriesId'], profileSha256=sha(profile), nativeRuns=144, fidelityRuns=144, relayRuns=72,
        criteria=criteria, failedScenarios=failed, failedCriteria=failures, resources=resources,
        archiveVerification=verifications, generatorSha256=sha(pathlib.Path(__file__)))
    dump(out / 'Aedes-summary.json', summary)
    docs = ['# Aedes — полный отчёт ночной серии 16–17 октября 2026 года',
        f'Серия `{plan["seriesId"]}`. Полный цикл Aedes завершён: **144 успешных натурных измерения, 144 BrokerFidelity-симуляции и 72 Relay-симуляции**. Общая кампания остановлена по временному бюджету; цикл Aedes выполнен полностью.',
        '## Результат проверки',
        f'Воспроизведение калибровки: **{72-failed["fidelity"]}/72** сценария проходят все применимые критерии. Независимый контроль: **{72-failed["holdout"]}/72**. '
        'Точность двойника на всей матрице **не подтверждена**. Содержательные провалы сохранены, пороги не менялись. Технически успешное выполнение не означает прохождение научной проверки.',
        'Натурный publishFailureRate равен нулю во всех 48 агрегированных точках. Недоставка ID успешных публикаций достигает **92,02% в калибровке и 96,87% в независимом контроле** (256 байт, QoS 1, 128 publishers). '
        'Эту недоставку нельзя приписать исключительно брокеру без разделения влияния очередей, получателя, TCP и окна наблюдения.',
        '[Отдельный справочник метрик, единиц и формул](../../../../docs/campaign-metrics.md).',
        '## 1. Стенд и версии',
        table(['Параметр', 'Значение'], [
            ['Размещение', 'Контроллер и native-брокер на Linux 192.168.5.1; MQTT 127.0.0.1:1883; Windows — SSH и документы'],
            ['Брокер', 'Aedes 1.1.2, Node.js; без Docker; точный launcher и binary SHA в stand'],
            ['ОС / CPU', 'Linux 6.12.85-6.12-alt1; Xeon E5-2680 v3, 12 физических / 24 логических CPU, SMT=2'],
            ['Affinity / лимиты', 'Брокер CPU 0, 4 GiB; контроллер CPU 1–11,13–23, 8 GiB; SMT sibling 12 исключён'],
            ['Guard', '99% общесистемного CPU; отдельный sampler, maximum interval 1,25 с'],
            ['MqttBenchmark commit', env['benchmarkCommit']],
            ['MqttY simulator commit', env['simulatorCommit']],
            ['Калибратор commit', read(profile)['provenance']['mqttYCommitSha']],
            ['Профиль SHA-256', sha(profile)],
            ['Оркестратор commit', plan['orchestrationSourceCommit']]]),
        'CPU 0 не объявляется эксклюзивным физическим ядром. Контроллер, брокер и фоновые процессы делят RAM и I/O. Эта серия не измеряет двухмашинную Ethernet-конфигурацию. '
        '[Окружение](../environment.json), [stand и binary SHA](../inputs/Aedes-stand.json), [манифест](../series.json), [происхождение заимствованных измерений](../native-origins.json).',
        '## 2. Программа и покрытие',
        table(['Этап', 'Успешных', 'Технических failed attempts', 'Сценариев с failed-критериями'], [
            ['Калибровка', '72/72', 1, 'неприменимо'], ['Fidelity', '72/72', 0, failed['fidelity']],
            ['Независимые замеры', '72/72', 0, 'неприменимо'], ['Контрольные симуляции', '72/72', 0, failed['holdout']],
            ['Relay', '72/72', 0, 'натурные критерии notApplicable']]),
        'Матрица: payload 16/256 байт × QoS 0/1/2 × publishers 1/16/64/128 × 3 повтора или seed 42/43/44. '
        'Натурные warmup/measurement/cooldown/drain: **5/20/1/10 с**; quiet period 10 с. Seed порядка 20260909 / 20260910. '
        'Capture envelope калибровки 39 с (reserve 3 с), контроля 54 с (reserve 18 с). Три успешных pilot учитываются отдельно.',
        'Калибровка заимствована из `overnight-20261016-v1` с сохранением campaign identity, observations и профиля. '
        'Одна неуспешная калибровочная попытка: `Telemetry does not cover the remote workload completion fence`; retry обеспечил 72 успешных ключа. '
        'Прежний частичный контроль 24/72 и старые симуляции сюда не включены. Независимая серия новой кампании выполнена заново полностью.',
        'Симуляции: **warmup 1 с / measurement 2 с / drain до 5 с**. BrokerFidelity: tick 10 мкс, `closedLoopPerPublisher`, `empiricalServiceBudget`. '
        'Relay: tick 100 мкс, `openLoopRate`, `fixed` transport. Последовательный refresh внутри модели; до четырёх отдельных процессов. Натурные тесты с симуляциями не пересекались.',
        '## 3. Статистика и определение результата',
        'RPS benchmark рассчитывается по actualMeasurementSeconds. Доли failure и loss имеют разные знаменатели: попытки и завершённые публикации соответственно. '
        'p50/p95/p99 QoS 1/2 — время завершения публикации, не доставки подписчику. QoS 0 ACK latency неприменима. '
        'Натурные табличные квантили — средние квантилей трёх повторов; CI95 = mean ± 4,30265273 × SD / √3 (Student-t, df=2). '
        'Три повтора ограничивают статистическую мощность. Поправка на множество сравнений не применена.',
        'Симулятор делит завершения, включая допускаемые завершения во время drain, на фиксированные 2 с measurement. '
        'Эта семантика отличается от actualMeasurementSeconds benchmark. Короткое модельное окно делает границы и хвост распределения существенными; '
        'seeds не заменяют новые натурные повторы. Серию нельзя объявлять эквивалентной первоначальной программе 30-секундных симуляций.']
    for stage in observations:
        rows = [r for r in native if r['stage'] == stage]
        docs += [f'## 4. Натурные результаты — {stage}',
            table(['Байт/QoS/N', 'attempted RPS', 'completed RPS ± CI95', 'failure', 'loss', 'p50 мс', 'p95 мс', 'p99 мс', 'RTT мс'], [
                [f'{r["bytes"]}/{r["qos"]}/{r["publishers"]}', num(r['attemptedRps']), f'{num(r["completedRps"])} ± {num(r["ci95"])}',
                 *[num(r[k]) for k in ('failure','loss','p50','p95','p99','rtt')]] for r in rows]),
            f'![Completed RPS, {stage}](assets/Aedes-{stage}-completedRps.png)',
            f'![Недоставка, {stage}](assets/Aedes-{stage}-deliveryLossRate.png)',
            f'![Задержки, {stage}](assets/Aedes-{stage}-latency.png)']
    changes = []
    for k in sorted(expected):
        c, v = [next(r for r in native if (r['stage'],r['bytes'],r['qos'],r['publishers']) == (s,*k)) for s in observations]
        changes.append((k, v['completedRps']/c['completedRps']-1, v['loss']-c['loss']))
    docs += ['## 5. Воспроизводимость и ресурсы',
        f'Максимальное абсолютное изменение среднего native RPS между сериями: **{100*max(abs(r[1]) for r in changes):.2f}%**. '
        'Одновременно deliveryLossRate в точке 256/QoS1/N64 вырос с 32,15% до 70,30%: стабильный publisher RPS не гарантирует стабильную доставку.',
        table(['Серия', 'Запусков', 'CPU mean по capture, диапазон %', 'RSS max по capture, MiB', 'Максимум CPU guard, %'], resources),
        'Все 144 успешных основных запуска прошли guard. CPU/RSS относятся к полному capture envelope, а не только 20 с нагрузки. '
        'CPU процесса брокера и общесистемный CPU guard — разные метрики. CPU/RSS не моделируются двойником.',
        '![Ресурсы; рисунок также включает pilot](assets/Aedes-resources.png)',
        '## 6. Проверка точности двойника',
        'Пороги сохранены: относительная ошибка RPS ≤15%, p99 ≤20%; абсолютная разница failure/loss ≤0,05. '
        'Профиль построен только по калибровке и не менялся для независимого контроля.',
        table(['Критерий', 'Fidelity: passed / failed / N/A', 'Holdout: passed / failed / N/A'], [
            [name, *[' / '.join(str(criteria[s][name].get(st,0)) for st in ('passed','failed','notApplicable')) for s in ('fidelity','holdout')]] for name in criteria['fidelity']]),
        '### Все нарушенные критерии',
        table(['Этап', 'Точка / seed', 'Критерий', 'Натурный эталон', 'Модель', 'Ошибка', 'Порог'], [
            [r['stage'], r['key'], r['name'], num(r['reference']), num(r['simulated']), num(r['error']), num(r['limit'])] for r in failures]),
        'Ошибка RPS/p99 в таблице — относительная доля; ошибка failure/loss — абсолютная разность долей. '
        'Расхождения p99 16/QoS1/N64 частично связаны с разными статистиками: модель использует объединённую CDF, '
        'а эталон — среднее p99 повторов (около 3,799 против 4,791 мс). Это диагностическое объяснение, не отмена failed. '
        'Для 256/QoS2/N64 хвост существенно зависит от seed. Остаточные RPS-расхождения и дрейф loss требуют дальнейшей проверки; '
        'причина не объявляется доказанной по одним summaries.',
        'Исправленная модель уменьшила число предупреждений, но не устранила их полностью. Контрольная недоставка не использовалась для подгонки профиля.',
        '![Карта ошибок](assets/Aedes-fidelity-errors.png)', '![Измерения и модель](assets/Aedes-measured-vs-simulated.png)',
        '## 7. Однородная сеть MqttRelay',
        'Четыре двойника Aedes, networkLength=2; lookup по publishers. Суммарная внешняя нагрузка — 4 × attemptedRps калибровочной точки; '
        'на каждый узел приходится четверть. [Точная конфигурация примера](../inputs/Aedes-relay-example.json). '
        'Транспорт — модельная линия 1 Gbit/s, очередь 100000, transferTicks=2; это допущения, не измеренная loopback-линия.',
        '![Фактическая модельная топология](assets/Aedes-topology.png)',
        table(['Байт/QoS/N на узел', 'offered всего / на узел, RPS', 'broker completed RPS', 'failure', 'loss', 'Receiver arrivals mean [min;max]'], [
            [f'{r["bytes"]}/{r["qos"]}/{r["publishersPerBroker"]}', f'{num(r["offeredTotalRps"])} / {num(r["offeredPerBrokerRps"])}',
             num(r['completedRpsMean']), num(r['failureMean']), num(r['lossMean']),
             f'{num(r["receiverReceiptsMean"])} [{r["receiverReceiptsMin"]};{r["receiverReceiptsMax"]}]'] for r in relay]),
        'Broker completed/attempted сети суммируют брокерные события, включая внутренние пересылки; они не равны числу уникальных внешних сообщений. '
        'Модельная failure включает rateRejected. Receiver arrivals — экспортированные счётчики конечных модельных receivers, включая drain и возможные warmup-переходы границы. '
        'Это не физические удалённые получатели и не доказательство уникальной сквозной доставки без ID-атрибуции.',
        '**Все 72 сетевых запуска завершены; все четыре критерия в каждом имеют статус notApplicable. Натурная точность Relay-сети не подтверждена.**',
        '## 8. Архивы и научная проверяемость',
        table(['Серия', 'Восстановление verified', 'Observations SHA-256', 'Restored aggregate SHA-256'], [
            [s, v['verified'], v['originalSha256'], v['restoredAggregateSha256']] for s,v in verifications.items()]),
        'Обе стадии ранее восстановлены из архивов и повторно агрегированы: scientific points и campaign identity совпали. '
        'Различие полного SHA aggregate связано с картой inputSha256 разных представлений файлов; оно явно сохранено в verification receipts. '
        'Эта подготовка отчёта проверяет исходные receipts и формулы, но не объявляется новым восстановлением raw.',
        '[Архивы Aedes с точными путями и SHA-256](Aedes-archives.json). Заимствованная калибровка лежит в `overnight-20261016-v1/evidence/attempts/Aedes-calibration/`; '
        'новый контроль — в `overnight-20261016-service-v1/evidence/attempts/Aedes-validation/`. Корень Linux: `/run/media/entityfx/hd250/mqtty-evidence/`. '
        'Неуспешные попытки сохранены. SHA подтверждает целостность; проверяемость обеспечивают raw, версии, точные входы и повторный расчёт.',
        '## 9. Воспроизведение и приложения',
        'Пересоздать этот документ из синхронизированной серии, без нагрузки на стенд:',
        '```powershell\npython tools/campaign/write-overnight-aedes-report.py --series MqttBenchmark/artifacts/overnight-20261016-service-v1\n```',
        'Повторять эксперименты следует в новом output-каталоге на hd250, сохраняя версию runtime и исходные конфиги. '
        'Точные experiment-конфиги находятся в Linux `controller/brokers/Aedes/simulations/{fidelity,holdout,relay}-2s-overnight-v1/`. '
        'Натурные входы: [calibration](../inputs/Aedes-calibration.json), [validation](../inputs/Aedes-validation.json). '
        'Пошаговое развёртывание: [runbook](../../../../docs/full-campaign-runbook.md).',
        '[Натурные CSV](Aedes-native-results.csv) · [Relay CSV](Aedes-relay-results.csv) · [Все 216 summaries](Aedes-simulations.json) · '
        '[Попытки и guard](Aedes-attempts.json) · [Сводка JSON](Aedes-summary.json) · [SHA-256 отчёта и приложений](Aedes-report-manifest.json) · '
        '[Подробный исходный автоотчёт](Aedes-detailed.md).']
    output = out / 'Aedes.md'
    # Preserve the original exporter output before replacing the human-readable report.
    detailed = out / 'Aedes-detailed.md'
    if not detailed.exists():
        detailed.write_bytes(output.read_bytes())
    output.write_text('\n\n'.join(docs) + '\n', encoding='utf-8')
    body = markdown.markdown(output.read_text(encoding='utf-8'), extensions=['tables','fenced_code'])
    for p in sorted((out / 'assets').glob('Aedes*.png')):
        body = body.replace('src="assets/' + p.name + '"', 'src="data:image/png;base64,' + base64.b64encode(p.read_bytes()).decode() + '"')
    (out / 'Aedes.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Aedes — ночная серия</title>'
        '<style>body{font:16px/1.55 system-ui;max-width:1400px;margin:auto;padding:24px}table{border-collapse:collapse;display:block;overflow:auto}'
        'td,th{border:1px solid #bbb;padding:6px}img{max-width:100%}code{overflow-wrap:anywhere}pre{overflow:auto}</style>' + body + '</html>', encoding='utf-8')
    files = [output, out/'Aedes.html', out/'Aedes-summary.json', out/'Aedes-native-results.csv', out/'Aedes-relay-results.csv', out/'Aedes-archives.json',
        out/'Aedes-simulations.json', out/'Aedes-attempts.json', *sorted((out/'assets').glob('Aedes*.png'))]
    dump(out/'Aedes-report-manifest.json', dict(generatorSha256=sha(pathlib.Path(__file__)), files={p.name:sha(p) for p in files}))
    print(json.dumps(dict(report=str(output), native=len(native), failed=failed, archives=len(archives)), ensure_ascii=False))


if __name__ == '__main__':
    main()
