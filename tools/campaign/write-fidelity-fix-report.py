"""Recompute a bounded revision-comparison report from retained summaries and provenance."""
import base64
import collections
import html
import json
import pathlib
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import markdown

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASELINE = ROOT / 'MqttBenchmark/artifacts/compact-20261016-6h-v1'
REVISION = ROOT / 'MqttBenchmark/artifacts/empirical-fidelity-20261016'


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def main():
    old = read(BASELINE / 'reports/Mosquitto-simulations.json')
    old_index = {(r['stage'], r['bytes'], r['qos'], r['publishers'], r['seed']): r['summary'] for r in old}
    windows = read(REVISION / 'windows-budget-smoke/index.json')['runs']
    linux = read(REVISION / 'linux-smoke/index.json')['runs']
    if len(windows) != 12 or len(linux) != 12:
        raise RuntimeError('Both complete 12-case verifications are required')
    win = {r['key']: r for r in windows}
    if any(r['summary'] != win[r['key']]['summary'] for r in linux):
        raise RuntimeError('Scientific summaries differ between Windows and Linux')
    rows = []
    for r in windows:
        m = re.fullmatch(r'(fidelity|holdout)\.m(\d+)\.q(\d+)\.p(\d+)\.s(\d+)', r['key'])
        stage, size, qos, publishers, seed = m.group(1), *map(int, m.groups()[1:])
        before = old_index[(stage, size, qos, publishers, seed)]
        after = r['summary']
        resolution = read(pathlib.Path(r['directory']) / 'transport-resolution.json')
        rows.append({'key': r['key'], 'stage': stage, 'bytes': size, 'qos': qos, 'seed': seed,
            'nativeRps': after['reference']['completedRps'], 'nativeP99Ms': after['reference']['latencyP99Ms'],
            'beforeRps': before['simulated']['completedRps'], 'beforeP99Ms': before['simulated']['latencyP99Ms'],
            'afterRps': after['simulated']['completedRps'], 'afterP99Ms': after['simulated']['latencyP99Ms'],
            'criteria': after['acceptanceCriteria'], 'transportResolution': resolution})
    reports = REVISION / 'reports'
    assets = reports / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    x = range(len(rows))
    for name, label, style in [('beforeP99Ms', 'Previous revision', 'o--'), ('afterP99Ms', 'Empirical revision', 's-'), ('nativeP99Ms', 'Native reference', 'x-')]:
        axes[0].plot(x, [r[name] for r in rows], style, label=label)
    axes[0].set(yscale='log', ylabel='p99, ms (log scale)')
    axes[0].legend()
    axes[0].grid(alpha=.3)
    for name, label, style in [('beforeRps', 'Previous revision', 'o--'), ('afterRps', 'Empirical revision', 's-')]:
        axes[1].plot(x, [(r[name] / r['nativeRps'] - 1) * 100 for r in rows], style, label=label)
    axes[1].axhspan(-15, 15, color='green', alpha=.1, label='RPS acceptance +/-15%')
    axes[1].set_ylabel('Completed RPS error, %')
    axes[1].set_xticks(list(x), [r['key'].replace('fidelity.', 'F.').replace('holdout.', 'H.') for r in rows], rotation=65, ha='right', fontsize=8)
    axes[1].legend()
    axes[1].grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(assets / 'Mosquitto-fixed.png', dpi=160)
    plt.close(fig)
    failures = sum(any(c['status'] == 'failed' for c in r['criteria']) for r in rows)
    table = ['| Сценарий | Натурный RPS | Модельный RPS | Натурный p99, мс | Прежний p99 | Новый p99 | Проваленных критериев |', '| --- | --- | --- | --- | --- | --- | --- |']
    for r in rows:
        values = [r['key'], *[f"{r[k]:.6g}" for k in ('nativeRps', 'afterRps', 'nativeP99Ms', 'beforeP99Ms', 'afterP99Ms')], sum(c['status'] == 'failed' for c in r['criteria'])]
        table.append('| ' + ' | '.join(map(str, values)) + ' |')
    text = '\n\n'.join(['# Mosquitto: проверка исправленной ревизии MqttY',
        f'Ревизия `f61daa47ec581ff88f20ee70882722085d1a0a10`. Проверка Windows: **12/12**, Linux: **12/12**. Сценариев с провалом критерия: **{failures}/12**. Все scientific summaries совпали между ОС; это повторение тех же сценариев, не 24 независимых статистических наблюдения.',
        'Проверены 16 байт/QoS1/N=1 и 256 байт/QoS2/N=1, seeds 42/43/44, fidelity и holdout. Точки выбраны для регрессии обнаруженных дефектов; результат нельзя переносить на всю матрицу, другие брокеры или Relay. Уже изученный holdout не является новым слепым контролем.',
        'Исправлены эмпирический хвост, publisher-side clock origin, warmup boundary и closed-loop. Tick 10 мкс; минимальное время протокола измеряется на идеальном графе, транспортный бюджет берётся только из калибровочного профиля. Критерии остаются RPS 15%, p99 20%, абсолютная разница failure/loss 0,05.',
        '\n'.join(table), '![До и после](assets/Mosquitto-fixed.png)',
        '## Ограничение временного бюджета',
        'Часть исходных наблюдений ниже минимального модельного времени QoS-протокола. Они остаются в эмпирическом профиле, а соответствующая processing delay равна нулю. Их количество (`clippedEmpiricalSamples`) и floor сохранены в transport-resolution.json каждого запуска и в JSON этого отчёта. Это ограничивает точность тела распределения, включая p50; прохождение p99 не подтверждает точность всех квантилей. MQTT echo RTT как proxy ACK budget остаётся явным допущением, не измерением физического времени распространения.',
        '[Числа, критерии и transport resolution](Mosquitto-fixed.json) · [Windows manifest](../windows-budget-smoke/verification-manifest.json) · [Linux manifest](../linux-smoke/verification-manifest.json)',
        'Натурная недоставка исходной серии этой проверкой не устранена и не приписывается автоматически дефекту брокера. Отдельная диагностика SYS/notice/TCP/in-flight/queue запускается после полного завершения исходной серии и имеет тот же предельный срок 18:54 МСК.'])
    (reports / 'Mosquitto-fixed.md').write_text(text + '\n', encoding='utf-8')
    (reports / 'Mosquitto-fixed.json').write_text(json.dumps({'rows': rows, 'crossPlatformScientificSummariesMatch': True}, indent=2), encoding='utf-8')
    body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
    body = body.replace('src="assets/Mosquitto-fixed.png"', 'src="data:image/png;base64,' + base64.b64encode((assets / 'Mosquitto-fixed.png').read_bytes()).decode() + '"')
    (reports / 'Mosquitto-fixed.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Mosquitto: исправленная модель</title><style>body{font:16px/1.5 system-ui;max-width:1200px;margin:30px auto;padding:20px}td,th{border:1px solid #bbb;padding:6px}table{border-collapse:collapse}img{max-width:100%}</style>' + body + '</html>', encoding='utf-8')
    print(reports / 'Mosquitto-fixed.md')


if __name__ == '__main__':
    main()
