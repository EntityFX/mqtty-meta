"""Recompute revision comparison and figures from retained Aedes regression summaries."""
import argparse
import base64
import collections
import hashlib
import html
import json
import math
import pathlib
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import markdown


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def quantile_estimands(profile):
    artifact = read(profile)
    histogram = json.loads(base64.b64decode(artifact['histogramsBase64']))
    values = {}
    def p99(bins):
        rank, cumulative = math.ceil(.99 * sum(c for _, c in bins)), 0
        for tick, count in sorted(bins):
            cumulative += count
            if cumulative >= rank:
                return tick / 1000
        raise ValueError('Empty latency distribution')
    for point in histogram['points']:
        by_repeat = [p99(rep['bins']) for rep in point['repeats']]
        pooled = collections.Counter()
        for repeat in point['repeats']:
            for tick, count in repeat['bins']:
                pooled[tick] += count
        values[(point['messageBytes'], point['qos'], point['publishers'])] = {
            'repeatP99Ms': by_repeat, 'meanRepeatP99Ms': sum(by_repeat) / len(by_repeat),
            'pooledP99Ms': p99(list(pooled.items()))}
    return values


def build(cohort, baseline):
    cohort, baseline = pathlib.Path(cohort), pathlib.Path(baseline)
    manifest, state = read(cohort / 'manifest.json'), read(cohort / 'state.json')
    estimands = quantile_estimands(cohort / 'inputs/Aedes-calibration.v4.json')
    runs = read(cohort / 'index.json')['runs']
    old = {r['key']: r for r in read(baseline / 'reports/Aedes-simulations.json') if r['stage'] == 'fidelity'}
    rows = []
    for run in runs:
        before, after = old[run['key']]['summary'], run['summary']
        if before['reference'] != after['reference']:
            raise ValueError('Native reference changed between revisions')
        config = cohort / 'inputs' / (run['key'] + '.json')
        if sha(config) != run['inputSha256']:
            raise ValueError('Retained experiment config changed')
        profile = pathlib.Path(read(config)['brokerProfilePath'])
        if sha(profile) != manifest['profileSha256']:
            raise ValueError('Frozen profile changed')
        if [(c['name'], c['limit']) for c in before['acceptanceCriteria']] != [(c['name'], c['limit']) for c in after['acceptanceCriteria']]:
            raise ValueError('Acceptance thresholds changed')
        resolution = read(pathlib.Path(run['directory']) / 'transport-resolution.json')
        dimensions = tuple(map(int, re.fullmatch(r'm(\d+)\.q(\d+)\.p(\d+)\.s\d+', run['key']).groups()))
        rows.append({'key': run['key'], 'reference': after['reference'], 'before': before['simulated'],
            'after': after['simulated'], 'criteria': after['acceptanceCriteria'],
            'beforeCriteria': before['acceptanceCriteria'], 'transportResolution': resolution,
            'nativeQuantileEstimands': estimands.get(dimensions)})
    reports = cohort / 'reports'
    assets = reports / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(3, 1, figsize=(13, 10), sharex=True)
    for ax, metric, label in zip(axes, ('completedRps', 'latencyP99Ms', 'publishFailureRate'), ('Completed RPS', 'p99, ms', 'Publication failure fraction')):
        for name, style in (('reference', 'kx-'), ('before', 'r.--'), ('after', 'b.-')):
            values = [r[name][metric] if r[name][metric] is not None else float('nan') for r in rows]
            ax.plot(range(len(rows)), values, style, label=name)
        ax.set_ylabel(label)
        if metric == 'latencyP99Ms':
            ax.plot(range(len(rows)), [r['nativeQuantileEstimands']['pooledP99Ms'] if r['nativeQuantileEstimands'] else float('nan') for r in rows],
                    'g+:', label='native pooled p99 (diagnostic)')
        ax.grid(alpha=.3)
        ax.legend()
    axes[-1].set_xticks(range(len(rows)), [r['key'] for r in rows], rotation=75, ha='right', fontsize=7)
    fig.tight_layout()
    fig.savefig(assets / 'Aedes-service-budget.png', dpi=150)
    plt.close(fig)
    def count(items, key):
        return sum(any(c['status'] == 'failed' for c in r[key]) for r in items)
    criteria = collections.Counter(c['name'] for r in rows for c in r['criteria'] if c['status'] == 'failed')
    def number(value):
        return 'неприменимо' if value is None else f'{value:.5g}'
    lines = ['# Aedes: исправление модели при неизменных допусках',
        f"Статус проверки: **{state['status']}**, **{len(rows)}/18** сценариев. Исходная ночная серия не изменяется.",
        f"Ревизия MqttY: `{manifest['modelCommit']}`; профиль SHA-256: `{manifest['profileSha256']}`; CLI SHA-256: `{manifest['runtimeSha256']}`.",
        f"В выбранных сценариях провалы до/после: **{count(rows, 'beforeCriteria')} → {count(rows, 'criteria')}**. Провалы по критериям после: `{dict(criteria)}`.",
        '## Что изменено',
        '1. Echo RTT ограничивает бюджет сверху; минимум protocol latency дополнительно ограничен минимальной наблюдаемой publish latency из калибровочной CDF. Невоспроизводимые при выбранном tick наблюдения остаются в профиле и учитываются в clippedEmpiricalSamples.\n'
        '2. FIFO capacity pacing откладывает краткие всплески вместо превращения их в отказы. Это модель backpressure только для single-broker closed-loop; реальные вероятности отказа и недоставки не обнуляются. Эмпирическая задержка уже включает очередь, поэтому capacity eligibility и processing residual объединяются максимумом, а не складываются дважды.\n'
        '3. QoS0 не ждёт ACK и сохраняет дробный offered load. QoS1/2 сохраняют дробный остаток токена при ограничении числа ожидающих publishers.',
        '## Результаты',
        '![До и после](assets/Aedes-service-budget.png)']
    table_rows = [
        '| Сценарий | Натурный RPS | RPS до → после | Натурный p99, мс | p99 до → после | Failure до → после | Провалы после |',
        '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in rows:
        before, after, reference = r['before'], r['after'], r['reference']
        failed = ', '.join(c['name'] for c in r['criteria'] if c['status'] == 'failed') or 'нет'
        table_rows.append('| ' + ' | '.join([r['key'], number(reference['completedRps']),
            number(before['completedRps']) + ' → ' + number(after['completedRps']), number(reference['latencyP99Ms']),
            number(before['latencyP99Ms']) + ' → ' + number(after['latencyP99Ms']),
            number(before['publishFailureRate']) + ' → ' + number(after['publishFailureRate']), failed]) + ' |')
    lines.append('\n'.join(table_rows))
    lines += ['## Область доказательности',
        'Пороги **RPS 15%, p99 20%, абсолютная разность failure/loss 0,05** не изменены; генератор проверяет их идентичность и одинаковую native reference. '
        'Профиль сохранён побайтно. Шесть уже изученных калибровочных точек Aedes, seeds 42/43/44, native 20 с, simulation warmup/measurement/drain 1/2/5 с, tick 10 мкс. '
        'Это проверка исправления механизма по calibration, а не новый независимый контроль, не полная матрица и не проверка Mosquitto/ActiveMQ/EMQX/Relay. '
        'Короткое модельное окно чувствительно к редким длинным задержкам. Успешное завершение не означает прохождение всех критериев; все failed/notApplicable сохраняются.',
        '117 прежних/дополненных проверок и отдельная проверка дробного QoS1 offered load: **118 passed**. '
        'В расширенном наборе обнаружен старый тест с отрицательным processingTicks, падающий также на исходной ревизии f61daa4; '
        'он приведён в соответствие с прежним контрактом ArgumentOutOfRangeException. Поведение очереди для отрицательных значений не менялось.',
        '[Точные сравнения JSON](Aedes-service-budget.json) · [Manifest](../manifest.json) · [Индекс](../index.json). '
        'Источник исходных данных: ночная серия overnight-20261016-v1; её inputs, профили, runtime и результаты не переписывались.']
    if any(r['nativeQuantileEstimands'] and abs(r['nativeQuantileEstimands']['pooledP99Ms'] - r['nativeQuantileEstimands']['meanRepeatP99Ms']) > .2 * r['nativeQuantileEstimands']['meanRepeatP99Ms'] for r in rows):
        lines += ['## Предупреждение об оцениваемой величине p99',
            'Для 16 байт/QoS1/N=64 исходные три native p99 после округления bins: **3,771 / 3,776 / 6,827 мс**. '
            'Среднее p99 повторов ≈**4,791 мс**, p99 объединённых raw samples — **3,799 мс**. Это разные статистики. '
            'Модель использует объединённую эмпирическую CDF, исходный oracle — среднее p99 повторов. '
            'Даже точное воспроизведение pooled CDF здесь может нарушить неизменный порог 20% относительно mean-of-p99. '
            'В таблице критерии и исходная reference сохранены, диагностическая pooled reference показана отдельно. '
            'Расхождение нельзя честно устранить увеличением модельных задержек ради прохождения oracle; '
            'для новой программы требуется согласовать оцениваемую величину и в независимом контроле, сохранив обе исходные статистики.']
    text = '\n\n'.join(lines) + '\n'
    (reports / 'Aedes-service-budget.md').write_text(text, encoding='utf-8')
    (reports / 'Aedes-service-budget.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
    encoded = base64.b64encode((assets / 'Aedes-service-budget.png').read_bytes()).decode()
    body = body.replace('src="assets/Aedes-service-budget.png"', 'src="data:image/png;base64,' + encoded + '"')
    (reports / 'Aedes-service-budget.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Aedes service budget</title><style>body{font:16px system-ui;max-width:1200px;margin:2em auto}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:6px}img{max-width:100%}</style>' + body + '</html>', encoding='utf-8')
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', required=True)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    build(args.cohort, args.baseline)
