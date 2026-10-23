"""Publish a small, reproducible report snapshot without changing a running campaign.

Use --source and --evidence-root to collect completed results. Without them, render
the saved snapshot again. Raw measurement archives remain on the evidence disk.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from datetime import datetime, timezone

BROKERS = ('Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX')
STAGES = {'calibration': 'Натурная калибровка', 'fidelity': 'Проверка калибровки (Fidelity)',
          'validation': 'Независимые замеры', 'holdout': 'Контрольные симуляции',
          'relay': 'Сеть MqttRelay'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='utf-8', newline='\n')


def dump(path, value):
    write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def collect(source, evidence, out):
    started = datetime.now(timezone.utc).isoformat()
    plan = read(source / 'series.json')
    if (plan.get('coverageProtocol') != 'overnight-v1' or
            plan.get('expectedRunsPerStage') != 72 or
            plan.get('empiricalTransportTiming') != 'empiricalServiceBudget' or
            any(plan.get('nativeMeasurementSecondsByBroker', {}).get(b) != 20 for b in BROKERS)):
        raise ValueError('This publication template requires the overnight-v1 20-second native protocol')
    files = {}

    def capture(path, relative):
        data = path.read_bytes()
        target = out / 'data' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[relative] = {'source': str(path), 'sha256': sha(data)}
        return json.loads(data)

    snapshot = {'collectionStartedUtc': started, 'source': str(source), 'brokers': {}}
    for name in ('series.json', 'environment.json', 'state.json', 'native-origins.json',
                 'repair-transition.json', 'archive-registry.json', 'frozen-files.json'):
        if (source / name).exists():
            capture(source / name, name)
    for broker in BROKERS:
        record = {'native': {}, 'model': {}}
        for path in sorted((source / 'inputs').glob(broker + '-*.json')):
            capture(path, 'inputs/' + path.name)
        for path in sorted((source / 'brokers' / broker).glob('**/broker-calibration*.json')):
            capture(path, f'{broker}/profile/{path.name}')
        for stage in ('calibration', 'validation'):
            root = source / 'brokers' / broker / stage
            path = root / 'broker-observations.v3.json'
            attempts = []
            for attempt in sorted((source.parent / 'raw' / f'{broker}-{stage}' / 'attempts').glob('*/attempt-*')):
                result = attempt / 'result.json'
                if result.exists():
                    value = read(result)
                    attempts.append({'path': str(attempt), 'resultSha256': sha(result.read_bytes()),
                                     'status': value.get('status'), 'failure': value.get('failure')})
                else:
                    attempts.append({'path': str(attempt), 'status': 'interrupted'})
            record['native'][stage] = {'attempts': attempts}
            if path.exists():
                obs = capture(path, f'{broker}/{stage}/observations.json')
                assert obs['runCount'] == 72 and len(obs['points']) == 24
                assert len({(p['messageBytes'], p['qos'], p['publishers']) for p in obs['points']}) == 24
                assert all(p['repeats'] == 3 for p in obs['points'])
                receipt = capture(root / 'archive-verification.json', f'{broker}/{stage}/archive-verification.json')
                assert receipt['verified'] is True
                assert receipt['originalSha256'] == sha(path.read_bytes())
                record['native'][stage]['runs'] = obs['runCount']
        for index in sorted((source / 'brokers' / broker / 'simulations').glob('*/index.json')):
            value = capture(index, f'{broker}/{index.parent.name}/index.json')
            assert value['measurementSeconds'] == 2
            stage = value['stage']
            rows = []
            for run in value['runs']:
                directory = run['directory']
                suffix = directory.split('/mqtty-evidence/', 1)[1]
                summary = capture(evidence / suffix / 'summary.json', f'{broker}/{stage}/{run["key"]}.json')
                assert summary['status'] == 'completed'
                rows.append({'key': run['key'], 'summary': summary})
            assert len({r['key'] for r in rows}) == len(rows)
            record['model'][stage] = rows
        snapshot['brokers'][broker] = record
    snapshot['collectionFinishedUtc'] = datetime.now(timezone.utc).isoformat()
    snapshot['files'] = files
    dump(out / 'data' / 'snapshot.json', snapshot)


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]) + '\n'


def number(value):
    return '—' if value is None else f'{value:.6g}'


def coverage(record):
    rows = []
    for stage, title in STAGES.items():
        if stage in ('calibration', 'validation'):
            data = record['native'][stage]
            count = data.get('runs', 0)
            errors = sum(a['status'] == 'failed' for a in data['attempts'])
            warnings = 'неприменимо'
        else:
            data = record['model'].get(stage, [])
            count = len(data)
            # Index contains completed jobs, not a history of failed process launches.
            errors = 'см. журнал'
            warnings = sum(any(c['status'] == 'failed' for c in r['summary']['acceptanceCriteria']) for r in data)
            if stage == 'relay':
                warnings = 'нет натурного эталона' if count else 'ещё не проверено'
            elif not count:
                warnings = 'ещё не проверено'
        rows.append([title, f'✅ {count}/72', f'❌ {errors}', f'⚠ {warnings}'])
    return table(['Этап', 'Завершено', 'Технические ошибки', 'Проверка точности'], rows)


def render_partial(out, broker, record, snapshot):
    text = [f'# {broker} — частичный отчёт',
            f'Срез данных: {snapshot["collectionFinishedUtc"]}. Серия `overnight-20261018-instrumentation-v2`. '
            'Показаны только завершённые и сохранённые результаты. Активная серия продолжает работу; этот документ является снимком, а не онлайн-статусом.',
            '## Что выполнено', coverage(record),
            '❌ у натурных этапов — число технически неуспешных попыток, а не ошибок отдельных сообщений. '
            'Индекс симуляций содержит только завершённые задания; отсутствие в нём ошибок не доказывает отсутствие прежних неудачных запусков процессов.',
            '## Условия опыта',
            'Контроллер и брокер работают на одном Linux-хосте через loopback. Брокер закреплён за одним логическим CPU; '
            'контроллер использует другие CPU. Общие память, диск и фоновые процессы могут влиять на результат. '
            'Сведения об оборудовании и версиях сохранены в [описании окружения](data/environment.json), '
            f'параметры брокера — в [описании стенда](data/inputs/{broker}-stand.json).',
            'Сетка: 16/256 байт × QoS 0/1/2 × 1/16/64/128 отправителей. В каждой точке три натурных повтора; '
            'модель выполняется с seed 42, 43 и 44. Реальная нагрузка длится 20 с, прогрев — 5 с, ожидание завершений — до 10 с. '
            'Модель: прогрев 1 с, нагрузка 2 с, ожидание до 5 с. Для одиночного двойника шаг 10 мкс, '
            '`closedLoopPerPublisher` и `empiricalServiceBudget`. Для Relay — 100 мкс, `openLoopRate` и `fixed`.',
            'MqttBenchmark: `3a689466009a0d5410f5dc88dc1282d72c27f7dd`; MqttY: '
            '`115885e012053d0112413e07743b061369ebea93`; калибратор: `f61daa47ec581ff88f20ee70882722085d1a0a10`. '
            'Изменения управляющих скриптов записаны отдельно в [программе серии](data/series.json).']
    if broker == 'Mosquitto':
        text += ['Независимые натурные замеры выполнялись в двух сеансах с паузой и перезагрузкой: '
                 'сохранённые 50 успешных запусков были дополнены оставшимися 22. Это не непрерывное измерение. '
                 'Повторное восстановление архивов завершено; прерванная попытка сохранена отдельно и не засчитана как успех.']
    profile = out / 'data' / broker / 'profile' / 'broker-calibration.v3.json'
    if profile.exists():
        text += [f'[Профиль двойника](data/{broker}/profile/broker-calibration.v3.json). '
                 f'SHA-256: `{sha(profile.read_bytes())}`. Профиль построен по калибровке; контрольные показатели его не изменяют.']
    for stage in ('calibration', 'validation'):
        path = out / 'data' / broker / stage / 'observations.json'
        if not path.exists():
            continue
        obs = read(path)
        rows = []
        csvrows = []
        for point in obs['points']:
            for metric in ('attemptedRps', 'completedRps', 'publishFailureRate', 'deliveryLossRate'):
                stat = point[metric]
                assert stat['count'] == 3
                assert math.isclose(stat['ci95HalfWidth'], 4.30265273 * stat['standardDeviation'] / math.sqrt(3), rel_tol=1e-7, abs_tol=1e-9)
            latency = point.get('observedLatencyQuantiles') or {}
            row = [point['messageBytes'], point['qos'], point['publishers'],
                   number(point['attemptedRps']['mean']),
                   f"{number(point['completedRps']['mean'])} ± {number(point['completedRps']['ci95HalfWidth'])}",
                   number(100 * point['publishFailureRate']['mean']), number(100 * point['deliveryLossRate']['mean']),
                   *[number(latency.get(k)) for k in ('p50Ms', 'p95Ms', 'p99Ms')], number(point['rttBaselineMs'])]
            rows.append(row)
            csvrows.append({**{k: point[k] for k in ('messageBytes', 'qos', 'publishers', 'repeats')},
                            **{k: point[k]['mean'] for k in ('attemptedRps', 'completedRps', 'publishFailureRate', 'deliveryLossRate')},
                            'completedRpsCi95HalfWidth': point['completedRps']['ci95HalfWidth'],
                            **{k: latency.get(k) for k in ('p50Ms', 'p95Ms', 'p99Ms')}, 'rttBaselineMs': point['rttBaselineMs']})
        with (out / f'{broker}-{stage}.csv').open('w', encoding='utf-8', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=csvrows[0].keys()); writer.writeheader(); writer.writerows(csvrows)
        text += [f'## {STAGES[stage]}',
                 'Средние по трём повторам. После ± показана полуширина 95%-го доверительного интервала Стьюдента для среднего RPS. '
                 'Доли ошибок и недоставки показаны в процентах; задержки — в миллисекундах. Для QoS 0 подтверждение публикации отсутствует, поэтому задержка неприменима.',
                 table(['Байт', 'QoS', 'Отправители', 'Попыток/с', 'Завершений/с ± CI95', 'Ошибки, %', 'Недоставка, %', 'p50, мс', 'p95, мс', 'p99, мс', 'RTT, мс'], rows),
                 f'[CSV]({broker}-{stage}.csv) · [Исходный агрегат](data/{broker}/{stage}/observations.json) · '
                 f'[Проверка восстановления архивов](data/{broker}/{stage}/archive-verification.json)',
                 f'![Производительность и недоставка {broker}](assets/{broker}-{stage}.png)']
    criteria_rows, failed = [], []
    for stage in ('fidelity', 'holdout'):
        runs = record['model'].get(stage, [])
        for metric in ('rps', 'latencyP99Ms', 'publishFailureRate', 'deliveryLossRate'):
            counts = {s: 0 for s in ('passed', 'failed', 'notApplicable')}
            for run in runs:
                for criterion in run['summary']['acceptanceCriteria']:
                    if criterion['name'] != metric:
                        continue
                    counts[criterion['status']] += 1
                    if criterion['status'] == 'failed':
                        failed.append([STAGES[stage], run['key'], metric, number(criterion['reference']),
                                       number(criterion['simulated']), number(criterion['error']), number(criterion['limit'])])
            criteria_rows.append([STAGES[stage], metric, *counts.values()])
    text += ['## Проверка двойника',
             'Пороги: относительная ошибка RPS ≤15%, p99 ≤20%; абсолютная разность долей ошибок и недоставки ≤0,05 '
             '(5 процентных пунктов). Порог не меняется при неудачном результате. `notApplicable` не считается прохождением.',
             table(['Этап', 'Критерий', 'Выполнен', 'Нарушен', 'Неприменим'], criteria_rows),
             '### Зарегистрированные нарушения',
             table(['Этап', 'Точка / seed', 'Критерий', 'Измерение', 'Модель', 'Отклонение', 'Допуск'], failed) if failed else 'В завершённых сценариях нарушений пока не обнаружено.',
             'Отклонение RPS и p99 в таблице — относительная доля; отклонение ошибок и недоставки — разность долей. '
             'Частичное прохождение контрольных симуляций не даёт заключения о всей сетке.']
    if broker == 'Mosquitto':
        text += ['## Что означают результаты',
                 'Производительность отправителей между натурными сериями сравнительно стабильна, но доставка при высокой нагрузке меняется заметно. '
                 'В некоторых точках недоставка достигает примерно 99%. Это результат данной конфигурации с конечным временем наблюдения, '
                 'а не универсальная характеристика Mosquitto. Отдельная диагностика выявила сбросы исходящей очереди; '
                 'полная роль получателя и TCP не установлена. [Разбор причин](history/mosquitto-fidelity-diagnosis.md). '
                 'На калибровочных данных нарушены два критерия RPS. Независимая проверка ещё идёт; точность на всей сетке пока не подтверждена.']
    else:
        text += ['## Что означают результаты',
                 'Все 72 проверки на калибровочных данных уложились в применимые допуски. Это подтверждает воспроизведение данных настройки, '
                 'но независимых измерений и контрольных симуляций ещё нет. Высокая недоставка в отдельных натурных точках '
                 '(до примерно 99,99%) описывает этот опыт; её причину нельзя установить по одной итоговой доле.']
    text += ['## Ограничения и проверяемость',
             'Подтверждение публикации не равно получению сообщения подписчиком. Недоставка означает отсутствие ID до окончания наблюдения. '
             'CPU и RSS относятся к настоящему процессу и не моделируются двойником. '
             'Табличный p99 — среднее p99 повторов, а модель использует объединённое распределение; эти оценки могут различаться. '
             'Три повтора и короткое модельное окно ограничивают устойчивость оценок хвоста. '
             'Benchmark использует фактическое время измерения, симулятор — фиксированное окно; завершения во время ожидания учитываются по правилам соответствующей программы.',
             '[Определения показателей](../docs/mqtt-benchmark/metrics.md) · [Как повторить опыт](../docs/mqtt-benchmark/runbook.md) · '
             '[Происхождение и SHA-256 файлов](data/snapshot.json) · [Реестр исходных архивов](data/archive-registry.json). '
             'Контрольные суммы подтверждают целостность, а повторное вычисление требует доступа к исходным архивам. '
             'При подготовке этого документа сохранены ранее полученные результаты проверки архивов; новая полная распаковка не выполнялась.',
             'Пересоздать таблицы, графики и HTML из опубликованного снимка: '
             '`python tools/campaign/publish-broker-reports.py --output reports`. Этот вызов не запускает брокеры и не изменяет серию.']
    write(out / f'{broker}.md', '\n\n'.join(text) + '\n')


def plots(out, snapshot):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for broker in ('Mosquitto', 'EMQX'):
        for stage in ('calibration', 'validation'):
            path = out / 'data' / broker / stage / 'observations.json'
            if not path.exists():
                continue
            points = read(path)['points']
            fig, axes = plt.subplots(2, 2, figsize=(11, 7))
            for col, size in enumerate((16, 256)):
                for qos in (0, 1, 2):
                    ps = sorted([p for p in points if p['messageBytes'] == size and p['qos'] == qos], key=lambda p: p['publishers'])
                    axes[0, col].errorbar([p['publishers'] for p in ps], [p['completedRps']['mean'] for p in ps],
                                         yerr=[p['completedRps']['ci95HalfWidth'] for p in ps], marker='o', label=f'QoS {qos}', capsize=3)
                    axes[1, col].plot([p['publishers'] for p in ps], [100*p['deliveryLossRate']['mean'] for p in ps], marker='o', label=f'QoS {qos}')
                axes[0, col].set_title(f'{size} байт'); axes[0, col].set_ylabel('Завершений/с; CI95 среднего')
                axes[1, col].set_ylabel('Недоставка, %')
                for ax in axes[:, col]:
                    ax.set_xlabel('Отправители'); ax.grid(alpha=.3); ax.legend()
            fig.suptitle(f'{broker}: {STAGES[stage]}'); fig.tight_layout()
            (out/'assets').mkdir(exist_ok=True)
            fig.savefig(out/'assets'/f'{broker}-{stage}.png', dpi=140, metadata={'Software': 'MqttBenchmark report publisher'})
            plt.close(fig)


def html_reports(out):
    import base64
    import html
    import re
    import markdown
    for broker in ('Aedes', 'Mosquitto', 'EMQX'):
        path = out / f'{broker}.md'
        if not path.exists():
            continue
        body = markdown.markdown(path.read_text(encoding='utf-8'), extensions=['tables', 'fenced_code'])
        def image(match):
            name = match.group(1)
            if not name.startswith('assets/'):
                return match.group(0)
            data = base64.b64encode((out/name).read_bytes()).decode('ascii')
            return f'src="data:image/png;base64,{data}"'
        body = re.sub(r'src="([^"]+)"', image, body)
        write(out / f'{broker}.html', '<!doctype html><html lang="ru"><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width"><title>' + html.escape(broker) + '</title>'
              '<style>body{max-width:1200px;margin:2rem auto;padding:0 1rem;font:16px/1.5 system-ui;color:#17212b}'
              'table{border-collapse:collapse;display:block;overflow:auto}td,th{border:1px solid #b6c3cf;padding:6px}'
              'img{max-width:100%}pre{overflow:auto;background:#f0f3f6;padding:1rem}a{color:#064ca5}</style>' + body + '</html>\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--evidence-root', type=Path)
    parser.add_argument('--output', type=Path, default=Path('reports'))
    args = parser.parse_args()
    if (args.output / 'data/final-evidence-sha256.json').exists():
        raise ValueError('This is a final publication; use render-final-reports.py to preserve full coverage and conclusions')
    if args.source:
        if not args.evidence_root:
            parser.error('--source requires --evidence-root')
        if (args.output/'data'/'snapshot.json').exists():
            parser.error('snapshot already exists; choose a new output directory to preserve it')
        collect(args.source, args.evidence_root, args.output)
    snapshot = read(args.output/'data'/'snapshot.json')
    for name, info in snapshot['files'].items():
        assert sha((args.output/'data'/name).read_bytes()) == info['sha256'], name
    for broker, record in snapshot['brokers'].items():
        for stage, runs in record['model'].items():
            for run in runs:
                assert run['summary'] == read(args.output/'data'/broker/stage/(run['key']+'.json'))
    for broker in ('Mosquitto', 'EMQX'):
        render_partial(args.output, broker, snapshot['brokers'][broker], snapshot)
    plots(args.output, snapshot)
    html_reports(args.output)
    dump(args.output / 'publication-sha256.json', {
        'note': 'Hashes of published report files; this manifest excludes itself. Raw archive verification is separate.',
        'files': {p.relative_to(args.output).as_posix(): sha(p.read_bytes())
                  for p in sorted(args.output.rglob('*'))
                  if p.is_file() and p.name != 'publication-sha256.json'}})
    print('Reports rendered from verified snapshot:', snapshot['collectionFinishedUtc'])


if __name__ == '__main__':
    main()
