"""Read-only scientific analysis of completed and partially completed broker cohorts."""
import argparse
import collections
import hashlib
import json
import pathlib

BROKERS = ('Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX')


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'] +
                     ['| ' + ' | '.join(str(v) for v in row) + ' |' for row in rows])


def build(root, broker):
    root = pathlib.Path(root)
    plan, state = read(root / 'series.json'), read(root / 'state.json')
    reports = root / 'reports'
    reports.mkdir(exist_ok=True)
    expected = plan.get('expectedRunsPerStage', 72)
    result = {'broker': broker, 'seriesId': plan['seriesId'], 'status': state['status'], 'native': {}, 'simulations': {}}
    document = [f'# {broker}: анализ эксперимента',
        f"Серия `{plan['seriesId']}`, протокол `{plan.get('coverageProtocol', 'full')}`, статус **{state['status']}**. "
        'Завершение запуска и прохождение критериев точности оцениваются раздельно.',
        f"MqttY commit: `{plan['simulatorRevision']}`. CLI SHA-256: `{sha(plan['simulatorCli'])}`.",
        f'[Полный документ, графики и таблицы]({broker}.md) · [Автономный HTML]({broker}.html)', '## Натурные измерения']
    rows = []
    for stage in ('calibration', 'validation'):
        path = root / 'brokers' / broker / stage / 'broker-observations.v3.json'
        raw = pathlib.Path(plan['rawControllerRoot']) / (broker + '-' + stage)
        failed = [read(p) for p in raw.glob('attempts/*/attempt-*/result.json') if read(p).get('status') != 'success']
        if not path.exists():
            rows.append([stage, f'0/{expected}', len(failed), 'нет агрегата', 'нет агрегата', 'не проверено'])
            continue
        data = read(path)
        points = data['points']
        verification = path.with_name('archive-verification.json')
        verified = verification.exists() and read(verification).get('verified', False)
        rates = [p['completedRps']['mean'] for p in points]
        loss = max(p['deliveryLossRate']['mean'] for p in points)
        result['native'][stage] = {'runs': data['runCount'], 'technicalFailedAttempts': len(failed),
            'minCompletedRps': min(rates), 'maxCompletedRps': max(rates), 'maxDeliveryLossRate': loss,
            'maxPublishFailureRate': max(p['publishFailureRate']['mean'] for p in points),
            'archiveReaggregationVerified': verified, 'observationsSha256': sha(path), 'points': points}
        rows.append([stage, f"{data['runCount']}/{expected}", len(failed), f'{min(rates):.1f}–{max(rates):.1f}', f'{loss:.2%}', verified])
    document.append(table(['Этап', '✅ Запуски', '❌ Попытки', 'Completed RPS, диапазон', 'Макс. loss', 'Повторная агрегация ZIP'], rows))
    document += ['Диапазон RPS относится к разным payload/QoS/publishers и не является одним сопоставимым рейтингом. '
        'ACK подтверждает обработку публикации; доставка подписчику проверяется отдельно по ID после drain. '
        'Loss здесь — доля недоставленных до конца наблюдения; без отдельной диагностики нельзя всю её объявлять сбросом брокера. '
        'CI95 Student-t по трём повторам, attempted RPS, p50/p95/p99 и RTT приведены в основном документе. QoS0 ACK latency неприменима.',
        '## Стабильность между двумя натурными сериями']
    comparison = []
    calibration = result['native'].get('calibration', {}).get('points', [])
    control = {(p['messageBytes'], p['qos'], p['publishers']): p for p in result['native'].get('validation', {}).get('points', [])}
    for p in calibration:
        key = (p['messageBytes'], p['qos'], p['publishers'])
        if key not in control:
            continue
        q = control[key]
        rates = p['completedRps']['mean'], q['completedRps']['mean']
        delta = 100 * (rates[1] - rates[0]) / rates[0] if rates[0] else None
        comparison.append([f'{key[0]}/{key[1]}/{key[2]}',
            f"{rates[0]:.1f} ±{p['completedRps']['ci95HalfWidth']:.1f}",
            f"{rates[1]:.1f} ±{q['completedRps']['ci95HalfWidth']:.1f}",
            f'{delta:+.2f}%' if delta is not None else 'неприменимо',
            f"{p['deliveryLossRate']['mean']:.2%} → {q['deliveryLossRate']['mean']:.2%}"])
    document.append(table(['Байт/QoS/N', 'Калибровка RPS ±CI95', 'Контроль RPS ±CI95', 'Изменение RPS', 'Loss'], comparison))
    document.append('Это оценка воспроизводимости натурного стенда, а не критерий Fidelity. '
        'Изменение между сериями может включать шум ОС, состояние приёмника и взаимодействие очередей. '
        'Три повтора не обеспечивают высокую мощность статистической проверки; пересечение CI само по себе не доказывает эквивалентность.')
    document.append('## Точность модели и сетевые симуляции')
    rows = []
    cohort = plan.get('simulationCohort', '')
    simulation_root = root / 'brokers' / broker / 'simulations'
    for stage in ('fidelity', 'holdout', 'relay'):
        indices = [p for p in simulation_root.glob(stage + '*/index.json') if not cohort or p.parent.name.endswith('-' + cohort)]
        if len(indices) > 1:
            raise ValueError('Ambiguous simulation cohort: ' + broker + '/' + stage)
        runs = read(indices[0]).get('runs', []) if indices else []
        counts, failed_scenarios, not_applicable = collections.Counter(), 0, 0
        for row in runs:
            summary = read(pathlib.Path(row['directory']) / 'summary.json')
            if summary['status'] != 'completed':
                raise ValueError('Index contains an incomplete simulation')
            criteria = summary['acceptanceCriteria']
            failed_scenarios += any(c['status'] == 'failed' for c in criteria)
            not_applicable += bool(criteria) and all(c['status'] == 'notApplicable' for c in criteria)
            counts.update(c['name'] for c in criteria if c['status'] == 'failed')
        result['simulations'][stage] = {'runs': len(runs), 'failedScenarios': failed_scenarios,
            'failedCriteria': dict(counts), 'allCriteriaNotApplicableScenarios': not_applicable}
        rows.append([stage, f'{len(runs)}/{expected}', failed_scenarios, '; '.join(f'{k}: {v}' for k, v in sorted(counts.items())) or 'нет', not_applicable])
    document.append(table(['Этап', '✅ Запуски', '⚠ Сценарии с провалом', 'Провалы по критериям', 'Все критерии notApplicable'], rows))
    document += ['Пороги BrokerFidelity: относительная ошибка RPS ≤15%, p99 ≤20%, абсолютная разность failure/loss ≤0,05. '
        'Числа по критериям могут пересекаться в одном сценарии. Fidelity воспроизводит калибровочные точки; '
        'holdout использует отдельные натурные observations при неизменном профиле. '
        'Relay показывает модельную доставку receivers; его notApplicable не означает прохождение проверки натурной точности.',
        '## Интерпретация']
    native_loss = max((r['maxDeliveryLossRate'] for r in result['native'].values()), default=0)
    model_failures = sum(result['simulations'][s]['failedScenarios'] for s in ('fidelity', 'holdout'))
    document.append(f'Максимальная наблюдаемая недоставка: **{native_loss:.2%}**. '
        f'Сценариев с провалом критериев Fidelity/holdout: **{model_failures}**. '
        'Технически завершённая программа не подтверждает точность двойника при наличии этих провалов.')
    if broker == 'Mosquitto':
        document.append('Отдельная 24-запусковая диагностика подтвердила сбросы исходящей очереди при 16 publishers. '
            'Увеличение конечной очереди, TCP_NODELAY и inflight не устранили недоставку. '
            'Это взаимодействие нагрузки, брокерной очереди и принимающей стороны, а не доказанный дефект MQTT-брокера. '
            'Исправленная модель прошла отдельные 12 проверок только для двух точек N=1 на обеих ОС; '
            'эти результаты не заменяют исходную матрицу и не доказывают точность при N=16/64/128.')
    if not plan.get('empiricalProfiles'):
        document.append('Эта серия использует прежнее восстановление распределения задержки и прежний режим нагрузки. '
            'Обнаруженные проблемы хвоста CDF, начала отсчёта latency, дискретизации и open-loop против native closed-loop '
            'ограничивают интерпретацию расхождений для всех брокеров; исправление проверяется отдельной новой серией.')
    document += ['## Воспроизведение и ограничения',
        f"`python {__file__} --series {root}`",
        '[Manifest](../series.json) · [Окружение](../environment.json) · [Реестр архивов](../archive-registry.json). '
        'SHA-256 подтверждает целостность; проверяемость обеспечивается raw, ID-журналами, seals и повторной агрегацией. '
        'CPU/RSS — натурная телеметрия, не моделируемые свойства. Контроллер и брокер размещены на одном Linux-хосте; '
        'закреплённое логическое CPU не является эксклюзивным физическим ядром. '
        'Сравнение короткой и полной серий диагностическое; разные окна и commits не объединяются в одну оценку.']
    (reports / (broker + '-Analysis.md')).write_text('\n\n'.join(document) + '\n', encoding='utf-8')
    (reports / (broker + '-Analysis.json')).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result


def analyse(root):
    results = [build(root, broker) for broker in BROKERS]
    overview = ['# Анализ завершения и точности по брокерам',
        table(['Брокер', 'Натурные запуски', '❌ Натурные попытки', '⚠ Fidelity', '⚠ Holdout', 'Relay'],
            [[f"[{r['broker']}]({r['broker']}-Analysis.md)",
              sum(v['runs'] for v in r['native'].values()), sum(v['technicalFailedAttempts'] for v in r['native'].values()),
              r['simulations']['fidelity']['failedScenarios'], r['simulations']['holdout']['failedScenarios'],
              r['simulations']['relay']['runs']] for r in results]),
        '✅ — завершённые запуски; ❌ — технически неудачные натурные попытки; ⚠ — завершённые сценарии с провалом критерия точности. '
        'Relay не имеет натурной проверки точности связанной сети.']
    (pathlib.Path(root) / 'reports/Broker-Analysis.md').write_text('\n\n'.join(overview) + '\n', encoding='utf-8')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--series', required=True)
    analyse(parser.parse_args().series)
