"""Recount a saved sensitivity snapshot without SSH or changing experiment inputs."""
import argparse
import csv
import copy
from collections import Counter, defaultdict
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
from statistics import mean, median


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_final(observation):
    """Independent coverage, experiment, histogram and acceptance-formula audit."""
    plan = observation['plan']
    result = analyse(observation)
    require(plan['criteriaUnchanged'] == {'rps': .15, 'p99': .2, 'failure': .05, 'loss': .05}, 'Criteria changed')
    expected = {f'{b}.m16.q{q}.p{p}.s{s}.{v}' for b, q, p, s, v in itertools.product(
        ('Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX'), (0, 1, 2), (1, 128), (42, 43, 44),
        ('baseline', 'halfTick', 'doubleWindow'))}
    actual = {j['id'] for j in plan['jobs'] if j['kind'] == 'matrix'}
    require(expected == actual, 'Final matrix is not the prescribed Cartesian product')
    expected_pilots = {f'pilot.{b}.m16.q1.p{p}.s42.baseline' for b, p in itertools.product(
        ('Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX'), (1, 128))}
    require({j['id'] for j in plan['jobs'] if j['kind'] == 'pilot'} == expected_pilots, 'Wrong pilot selection')
    expected_replays = {'replay.' + key for key in ('Aedes.m16.q0.p1.s42.baseline',
        'Mosquitto.m16.q1.p1.s42.baseline', 'ActiveMQ.m16.q2.p1.s42.baseline',
        'EMQX.m16.q1.p128.s42.baseline', 'Aedes.m16.q1.p128.s42.halfTick',
        'ActiveMQ.m16.q0.p128.s42.doubleWindow')}
    require({j['id'] for j in plan['jobs'] if j['kind'] == 'replay'} == expected_replays, 'Wrong replay selection')
    require(len(plan['jobs']) == 230, 'Expected 230 jobs')
    for kind, count in (('pilot', 8), ('matrix', 216), ('replay', 6)):
        stats = result['coverage'][kind]
        require(stats['planned'] == stats['completed'] == count and stats['technicalErrors'] == 0,
                'Final phase incomplete: ' + kind)
    require(observation['progress']['status'] == 'completed' and not observation['progress']['active'], 'Study not terminal')
    require(observation['sourceFiles']['plan.json'] == observation['progress']['planSha256'], 'Plan identity differs')
    immutable = {r['path']: r['sha256'] for r in observation['verifiedImmutableFiles']}
    require(immutable == plan['immutableFiles'], 'Immutable input receipt differs')
    jobs = {j['id']: j for j in plan['jobs']}
    rows = {r.get('data', r)['id']: r.get('data', r) for r in observation['rows']}
    verified = observation['verifiedRuns']
    require(set(verified) == set(jobs), 'Missing verified run')
    formula_count = 0
    for key, job in jobs.items():
        row, data = rows[key], verified[key]
        dims = identity(key)
        config = data['config']
        experiment = config['experiment']
        tick_seconds = .000005 if dims['variant'] == 'halfTick' else .00001
        duration = 4 if dims['variant'] == 'doubleWindow' else 2
        require(config['ticks']['tickPeriod'] == ('00:00:00.0000050' if dims['variant'] == 'halfTick' else '00:00:00.0000100'), 'Wrong tick: ' + key)
        require(experiment['measurementTicks'] * tick_seconds == duration, 'Wrong window: ' + key)
        require(experiment['warmupTicks'] * tick_seconds == 1 and experiment['drainTimeoutTicks'] * tick_seconds == 5, 'Wrong warmup/drain')
        require(experiment['clientsPerBroker'] == dims['publishers'] and experiment['payloadBytes'] == dims['payload'] and experiment['randomSeed'] == dims['seed'], 'Dimensions differ: ' + key)
        require(experiment['brokerAssignmentMode'].lower() == dims['broker'].lower(), 'Wrong broker')
        require(experiment['publishQos'] == ('atMostOnce', 'atLeastOnce', 'exactlyOnce')[dims['qos']], 'Wrong QoS')
        require(experiment['subscribeQos'] == experiment['publishQos'] and experiment['topologyMode'] == 'brokerFidelity', 'Wrong experiment mode')
        require(data['profileSha256'] == immutable['inputs/' + dims['broker'] + '-profile.json'], 'Wrong profile')
        require(row['configSha256'] == job['configSha256'], 'Wrong config hash')
        for field in ('summarySha256', 'histogramSha256', 'scientificCountersSha256'):
            require(data[field] == row[field], 'Output receipt differs: ' + key)
        counters_sha = hashlib.sha256(json.dumps(data['scientificCounters'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        require(counters_sha == row['scientificCountersSha256'], 'Scientific counters modified')
        summary = row['summary']
        simulated = summary['simulated']
        broker_counters = data['scientificCounters']['brokers']
        for broker in broker_counters.values():
            require(broker['measurementStartTick'] == summary['measurementStartTick'] and
                    broker['measurementEndTick'] == summary['measurementEndTick'], 'Counter interval differs')
        for field in ('attempted', 'admitted', 'completed', 'publishFailed', 'rateRejected',
                      'delivered', 'deliveryDropped', 'expectedDeliveries'):
            total = sum(qos[field] for broker in broker_counters.values() for qos in broker['byQos'].values())
            require(total == simulated[field], 'Counter/summary total differs: ' + field)
        require((summary['measurementEndTick'] - summary['measurementStartTick']) * tick_seconds == duration, 'Wrong measured interval')
        require(math.isclose(simulated['completedRps'], simulated['completed'] / duration, abs_tol=1e-9), 'RPS formula differs')
        failure = simulated['publishFailed'] / simulated['attempted'] if simulated['attempted'] else 0
        loss = 1 - simulated['delivered'] / simulated['expectedDeliveries'] if simulated['expectedDeliveries'] else 0
        require(math.isclose(simulated['publishFailureRate'], failure, abs_tol=1e-12), 'Failure denominator differs')
        require(math.isclose(simulated['deliveryLossRate'], loss, abs_tol=1e-12), 'Loss denominator differs')
        bins = {}
        for by_qos in data['histogram'].values():
            for qos_name, histogram in by_qos.items():
                if qos_name == experiment['publishQos']:
                    for ticks, count in histogram.items():
                        bins[int(ticks)] = bins.get(int(ticks), 0) + count
        p99 = None
        if dims['qos'] != 0 and bins:
            rank, running = math.ceil(sum(bins.values()) * .99), 0
            for ticks, count in sorted(bins.items()):
                running += count
                if running >= rank:
                    p99 = ticks * tick_seconds * 1000
                    break
        require((p99 is None and simulated['latencyP99Ms'] is None) or
            (p99 is not None and math.isclose(p99, simulated['latencyP99Ms'], abs_tol=1e-9)), 'Histogram p99 differs: ' + key)
        expected_names = {'rps', 'publishFailureRate', 'deliveryLossRate', 'latencyP99Ms'}
        require({c['name'] for c in summary['acceptanceCriteria']} == expected_names, 'Missing criterion')
        for criterion in summary['acceptanceCriteria']:
            name = criterion['name']
            metric = 'completedRps' if name == 'rps' else name
            a, b = summary['reference'][metric], simulated[metric]
            require(criterion['reference'] == a and criterion['simulated'] == b, 'Criterion values differ')
            limit = {'rps': .15, 'latencyP99Ms': .2, 'publishFailureRate': .05, 'deliveryLossRate': .05}[name]
            if a is None or b is None:
                require(criterion['status'] == 'notApplicable', 'Null metric not marked NA')
                require(criterion['limit'] is None and criterion['error'] is None, 'NA criterion has numeric limit/error')
            else:
                require(criterion['limit'] == limit, 'Criterion limit changed')
                error = abs(b - a) / abs(a) if name in ('rps', 'latencyP99Ms') and a else abs(b - a)
                require(math.isclose(error, criterion['error'], abs_tol=1e-12), 'Error formula differs')
                require(criterion['status'] == ('passed' if error <= limit else 'failed'), 'Acceptance status differs')
            formula_count += 1
        if job['expectedOriginalSummary'] is not None:
            require(summary == job['expectedOriginalSummary'], 'Original baseline changed')
        if dims['kind'] == 'matrix' and dims['variant'] != 'baseline':
            base = rows[key.rsplit('.', 1)[0] + '.baseline']
            require(summary['reference'] == base['summary']['reference'], 'Paired reference differs')
            base_config = verified[base['id']]['config']
            require(experiment['offeredRps'] == base_config['experiment']['offeredRps'], 'Paired offered load differs')
            expected_config = copy.deepcopy(base_config)
            if dims['variant'] == 'doubleWindow':
                expected_config['experiment']['measurementTicks'] *= 2
            else:
                expected_config['ticks']['tickPeriod'] = '00:00:00.0000050'
                expected_config['ticks']['outgoingWaitTicks'] *= 2
                for field in ('warmupTicks', 'measurementTicks', 'drainTimeoutTicks'):
                    expected_config['experiment'][field] *= 2
                for field in ('transferTicks', 'throughputWindowTicks'):
                    expected_config['network'][field] *= 2
            require(config == expected_config, 'Variant changed an unrelated input: ' + key)
    replay_receipts = observation['verifiedReplays']
    require(len(replay_receipts) == 6 and len({r['id'] for r in replay_receipts}) == 6, 'Missing replay receipts')
    for receipt in replay_receipts:
        key, original = receipt['id'], receipt['originalId']
        require(key in jobs and jobs[key].get('replayOf') == original, 'Wrong replay target')
        for field in ('summarySha256', 'histogramSha256', 'scientificCountersSha256'):
            require(rows[key][field] == rows[original][field] == receipt['hashes'][field], 'Replay bytes differ')
            require(receipt['checks'][field] is True, 'Replay check differs')
    return {'status': 'verified', 'matrix': 216, 'pilots': 8, 'exactReplays': 6,
        'acceptanceFormulaChecks': formula_count, 'immutableFiles': len(immutable),
        'verifiedRuns': len(verified), 'pairedComparisons': len(result['matchedPairs']),
        'limits': 'Counters, histogram and formulas audited; no new raw native measurement or blind validation'}


IDENTITY = re.compile(r"(?:(pilot|replay)\.)?(Aedes|Mosquitto|ActiveMQ|EMQX)\.m(\d+)\.q([012])\.p(\d+)\.s(\d+)\.(baseline|halfTick|doubleWindow)")
METRICS = ("completedRps", "latencyP99Ms", "publishFailureRate", "deliveryLossRate")


def identity(value):
    match = IDENTITY.fullmatch(value)
    if not match:
        raise ValueError("Unknown sensitivity identity: " + value)
    kind, broker, payload, qos, publishers, seed, variant = match.groups()
    return {"kind": kind or "matrix", "broker": broker, "payload": int(payload),
            "qos": int(qos), "publishers": int(publishers), "seed": int(seed), "variant": variant}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def analyse(observation):
    """Observed results only; missing or technically failed rows cannot form scientific pairs."""
    jobs = observation["plan"]["jobs"]
    planned = {j["id"]: identity(j["id"]) for j in jobs}
    if len(planned) != len(jobs):
        raise ValueError("Duplicate planned job")
    rows = {}
    for item in observation["rows"]:
        row = item.get("data", item)
        key = row["id"]
        if key not in planned or key in rows:
            raise ValueError("Unknown or duplicate result: " + key)
        dims = planned[key]
        if row.get("kind") != dims["kind"] or row.get("variant") != dims["variant"]:
            raise ValueError("Result identity differs from metadata: " + key)
        if not row.get("technicalError"):
            if row.get("returnCode") not in (0, 3) or row.get("summary", {}).get("status") != "completed":
                raise ValueError("Successful row lacks completed scientific output: " + key)
            failed = any(c["status"] == "failed" for c in row["summary"]["acceptanceCriteria"])
            if row.get("fidelityFailed") is not failed:
                raise ValueError("Failure flag differs from criteria: " + key)
        rows[key] = row

    def stats(keys):
        selected = [rows[k] for k in keys if k in rows]
        usable = [r for r in selected if not r.get("technicalError")]
        timings = [r["wallSeconds"] for r in usable if finite(r.get("wallSeconds"))]
        rss = [r["peakRssBytes"] for r in usable if finite(r.get("peakRssBytes"))]
        return {"planned": len(keys), "recorded": len(selected), "completed": len(usable),
                "scientificFailed": sum(r["fidelityFailed"] for r in usable),
                "technicalErrors": sum(bool(r.get("technicalError")) for r in selected),
                "failedCriteria": dict(sorted(Counter(c["name"] for r in usable
                    for c in r["summary"]["acceptanceCriteria"] if c["status"] == "failed").items())),
                "medianWallSeconds": median(timings) if timings else None,
                "totalWorkerWallSeconds": sum(timings), "peakRssBytes": max(rss, default=None)}

    by_phase = {phase: stats([k for k, d in planned.items() if d["kind"] == phase])
                for phase in ("pilot", "matrix", "replay")}
    dimensions = {}
    for field in ("broker", "qos", "publishers", "variant"):
        dimensions[field] = {str(value): stats([k for k, d in planned.items()
                           if d["kind"] == "matrix" and d[field] == value])
                             for value in sorted({d[field] for d in planned.values()})}

    pairs = []
    for key, dims in planned.items():
        if dims["kind"] != "matrix" or dims["variant"] == "baseline" or key not in rows:
            continue
        base_id = key.rsplit(".", 1)[0] + ".baseline"
        if base_id not in rows:
            continue
        base, variant = rows[base_id], rows[key]
        if base.get("technicalError") or variant.get("technicalError"):
            continue
        comparison = {}
        for metric in METRICS:
            a = base["summary"]["simulated"].get(metric)
            b = variant["summary"]["simulated"].get(metric)
            comparable = finite(a) and finite(b)
            comparison[metric] = {"baseline": a, "variant": b,
                "absoluteDelta": b - a if comparable else None,
                "relativeDelta": (b - a) / abs(a) if comparable and a != 0 else None,
                "status": "compared" if comparable else "notApplicable"}
        pairs.append({"baselineId": base_id, "variantId": key, **dims,
                      "metrics": comparison, "wallTimeRatio": variant["wallSeconds"] / base["wallSeconds"]
                        if finite(base.get("wallSeconds")) and base["wallSeconds"] > 0 else None,
                      "baselineFailedCriteria": [c["name"] for c in base["summary"]["acceptanceCriteria"] if c["status"] == "failed"],
                      "variantFailedCriteria": [c["name"] for c in variant["summary"]["acceptanceCriteria"] if c["status"] == "failed"]})
    paired_summary = {}
    for variant in ("halfTick", "doubleWindow"):
        selected = [p for p in pairs if p["variant"] == variant]
        paired_summary[variant] = {"pairs": len(selected), "metrics": {}}
        for metric in METRICS:
            deltas = [p["metrics"][metric]["relativeDelta"] for p in selected
                      if p["metrics"][metric]["relativeDelta"] is not None]
            absolute = [p["metrics"][metric]["absoluteDelta"] for p in selected
                        if p["metrics"][metric]["absoluteDelta"] is not None]
            paired_summary[variant]["metrics"][metric] = {
                "relativeDeltaPairs": len(deltas),
                "meanAbsoluteRelativeDelta": mean(abs(v) for v in deltas) if deltas else None,
                "maxAbsoluteRelativeDelta": max((abs(v) for v in deltas), default=None),
                "absoluteDeltaPairs": len(absolute),
                "maxAbsoluteDelta": max((abs(v) for v in absolute), default=None)}
    scientific_failures = [{"id": k, **planned[k], "criteria": [c for c in r["summary"]["acceptanceCriteria"]
                           if c["status"] == "failed"]} for k, r in sorted(rows.items())
                           if not r.get("technicalError") and r.get("fidelityFailed")]
    return {"observedAtUtc": observation.get("observedAtUtc"), "planSha256": observation.get("progress", {}).get("planSha256"),
            "coverage": by_phase, "matrixDimensions": dimensions,
            "pairedSummary": paired_summary, "matchedPairs": sorted(pairs, key=lambda p: p["variantId"]),
            "scientificFailures": scientific_failures,
            "technicalFailures": [{"id": k, "reason": r["technicalError"]} for k, r in sorted(rows.items()) if r.get("technicalError")],
            "limitations": ["Only saved completed results; no new server observation or archive verification",
                "Scientific criterion failures do not establish broker or simulator defect causes",
                "Variant comparisons use identical broker, payload, QoS, publishers and seed, matrix phase only",
                "Relative deltas with zero baseline and QoS0 ACK latency are not applicable",
                "Wall times describe this execution; ratios alone do not prove hardware-independent acceleration",
                "Worker wall-time sum is not elapsed campaign time; partial, ordered coverage cannot represent all brokers"]}


def render(result):
    lines = ["# Разбор сохранённых результатов проверки чувствительности", "",
             "Проверка чувствительности отвечает, меняются ли результаты при уменьшении шага модельного времени и увеличении окна нагрузки. Это разбор имеющихся результатов, а не новая серия и не текст статьи.", "",
             f"Источник: `{result.get('observationFile', 'snapshot')}`. SHA-256: `{result.get('observationSha256', 'not recorded')}`.", "",
             "Срез наблюдения: `" + str(result["observedAtUtc"]) + "`. Последующие результаты сюда автоматически не включаются.", "",
             "## Фактическое покрытие", "", "| Этап | Завершено / план | Не прошли критерии | Технические ошибки |",
             "| --- | ---: | ---: | ---: |"]
    for kind, stats in result["coverage"].items():
        lines.append(f"| {kind} | {stats['completed']}/{stats['planned']} | {stats['scientificFailed']} | {stats['technicalErrors']} |")
    lines += ["", "Столбец критериев показывает расхождение модели с натурным наблюдением. Код завершения 3 в этой программе означает такой результат; он не означает падение процесса. Технические ошибки считаются отдельно по сохранённым причинам.", "",
              "## Основная матрица по брокерам", "", "| Брокер | Завершено / план | Не прошли критерии | Какие критерии | Медиана времени, с |", "| --- | ---: | ---: | --- | ---: |"]
    for name, stats in result["matrixDimensions"]["broker"].items():
        criteria = ", ".join(f"{k}: {v}" for k, v in stats["failedCriteria"].items()) or "—"
        timing = f"{stats['medianWallSeconds']:.1f}" if stats["medianWallSeconds"] is not None else "—"
        lines.append(f"| {name} | {stats['completed']}/{stats['planned']} | {stats['scientificFailed']} | {criteria} | {timing} |")
    lines += ["", "Медиана для смешанных уровней нагрузки может скрывать дорогие точки. Поэтому ниже нагрузка показана отдельно. Эти числа относятся только к завершённой части основной матрицы.", "",
              "| Разрез | Значение | Завершено / план | Не прошли критерии | Медиана времени, с |", "| --- | --- | ---: | ---: | ---: |"]
    for dimension in ("qos", "publishers", "variant"):
        for name, stats in result["matrixDimensions"][dimension].items():
            timing = f"{stats['medianWallSeconds']:.1f}" if stats["medianWallSeconds"] is not None else "—"
            lines.append(f"| {dimension} | {name} | {stats['completed']}/{stats['planned']} | {stats['scientificFailed']} | {timing} |")
    lines += ["", "## Зафиксированные расхождения", "", "| Точка | Критерий | Натурное значение | Модель | Ошибка | Допуск |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for failure in result["scientificFailures"]:
        for c in failure["criteria"]:
            values = [f"{c.get(field):.6g}" if finite(c.get(field)) else "—" for field in ("reference", "simulated", "error", "limit")]
            lines.append(f"| `{failure['id']}` | {c['name']} | " + " | ".join(values) + " |")
    if not result["scientificFailures"]:
        lines.append("| Нет в сохранённой части | — | — | — | — | — |")
    lines += ["", "RPS и p99 проверяются по относительной ошибке; failure/loss — по абсолютной разнице долей. Уменьшение числа таких расхождений в одном варианте само по себе не объясняет причину и не разрешает выбрать параметры по контрольным данным."]
    lines += ["", "## Парные сравнения с исходным вариантом", "", "Сравниваются только одинаковые брокер, payload, QoS, число publishers и seed в основной матрице. Пилоты в пары не входят. Увеличение окна меняет число публикаций; сравниваются скорости и доли, а не абсолютные счётчики.", "",
              "| Вариант | Пар всего | Метрика | Пар с ненулевой базой | Среднее абсолютное относительное изменение | Максимум |", "| --- | ---: | --- | ---: | ---: | ---: |"]
    for name, data in result["pairedSummary"].items():
        for metric, values in data["metrics"].items():
            avg = f"{values['meanAbsoluteRelativeDelta'] * 100:.3f}%" if values["meanAbsoluteRelativeDelta"] is not None else "неприменимо"
            maximum = f"{values['maxAbsoluteRelativeDelta'] * 100:.3f}%" if values["maxAbsoluteRelativeDelta"] is not None else "неприменимо"
            lines.append(f"| {name} | {data['pairs']} | {metric} | {values['relativeDeltaPairs']} | {avg} | {maximum} |")
    lines += ["", "Для нулевой исходной доли ошибка относительного изменения не определена. Абсолютная разница сохранена для каждой пары в JSON. QoS 0 не имеет задержки публикационного ACK. Эти сравнения не заменяют независимую натурную проверку и не меняют исходные пороги.", "",
              "## Что делать дальше", "", "1. Довести запланированное покрытие и повторы до конца; не перезапускать завершённые точки только из-за непройденных критериев.",
              "2. При технической ошибке сохранить попытку, проверить причину и повторить только затронутую точку по отдельному решению.",
              "3. После полного покрытия проверить влияние шага и окна попарно для каждого QoS и числа publishers. До этого не переносить выводы Aedes на остальные брокеры.",
              "4. Ускорять управляющий планировщик и устранять простои между независимыми процессами; runtime, профили, численное расписание и пороги внутри этой серии сохранять.",
              "5. Проверку сети и подготовку таблиц выполнять независимо; конкурентные процессы ограничивать общей памятью и свободным диском.", "",
              "## Границы выводов", "", "Из среза нельзя установить причину расхождений, доказать точность всех брокеров или подтвердить натурную точность MqttRelay. Хэши файла позволяют повторить этот разбор, но сами по себе не подтверждают корректность исходных измерений.", "",
              "Рабочее время процесса не равно суммарному времени кампании. Это описательный анализ частичного, упорядоченного покрытия; доверительные интервалы для всей серии здесь не оцениваются.", ""]
    return "\n".join(lines)


def write_final_artifacts(observation, output, observation_sha):
    """Reproducible technical analysis; all scientific rows remain in its denominator."""
    audit = verify_final(observation)
    result = analyse(observation)
    result['verification'] = audit
    result['observationSha256'] = observation_sha
    result['limitations'] = [
        'Complete prescribed sensitivity subset, not a new blind native validation.',
        'Seed repeats describe this deterministic model; they do not estimate native measurement uncertainty.',
        'Broker counters and receiver receipts have different scopes; warmup arrivals can cross the reset boundary.',
        'Completion counts can include drain; completedRps divides those counts by the measurement window.',
        'Six exact replays establish only the selected summaries, histogram bytes and canonical scientific-counter bytes.',
        'No proof of convergence for arbitrary tick, payload, client count or hardware; no relay network native validation.']
    (output / 'analysis.json').write_bytes((json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode())
    (output / 'verification.json').write_bytes((json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode())
    pair_rows = []
    grouped = defaultdict(list)
    for pair in result['matchedPairs']:
        grouped[(pair['broker'], pair['variant'])].append(pair)
        for metric, values in pair['metrics'].items():
            pair_rows.append({'broker': pair['broker'], 'qos': pair['qos'], 'publishers': pair['publishers'],
                'seed': pair['seed'], 'variant': pair['variant'], 'metric': metric, **values,
                'baselineId': pair['baselineId'], 'variantId': pair['variantId']})
    with (output / 'paired-metrics.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(pair_rows[0]))
        writer.writeheader(); writer.writerows(pair_rows)
    raw_rows = [r.get('data', r) for r in observation['rows']]
    matrix = [r for r in raw_rows if r['kind'] == 'matrix']
    long_rows = []
    for row in matrix:
        d = identity(row['id'])
        for criterion in row['summary']['acceptanceCriteria']:
            long_rows.append({'id': row['id'], **d, **criterion})
    with (output / 'criteria.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(long_rows[0]))
        writer.writeheader(); writer.writerows(long_rows)
    point_groups = defaultdict(list)
    for row in matrix:
        d = identity(row['id'])
        point_groups[(d['broker'], d['qos'], d['publishers'], d['variant'])].append(row)
    point_rows = []
    for (broker, qos, publishers, variant), rows in sorted(point_groups.items()):
        for metric in METRICS:
            values = [r['summary']['simulated'][metric] for r in rows if finite(r['summary']['simulated'][metric])]
            point_rows.append({'broker': broker, 'qos': qos, 'publishers': publishers, 'variant': variant,
                'metric': metric, 'seeds': len(rows), 'applicableSeeds': len(values),
                'mean': mean(values) if values else None, 'min': min(values) if values else None,
                'max': max(values) if values else None})
    with (output / 'point-metrics.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(point_rows[0]))
        writer.writeheader(); writer.writerows(point_rows)

    def pct(value):
        return '—' if value is None else f'{value * 100:.3f}%'

    def pair_metric(pairs, metric, field='relativeDelta'):
        return [abs(p['metrics'][metric][field]) for p in pairs if p['metrics'][metric][field] is not None]

    failed_matrix = result['coverage']['matrix']['scientificFailed']
    lines = ['# Итог проверки чувствительности двойников MqttY', '',
        'Серия полностью выполнена: **216 основных симуляций, 8 пилотов и 6 точных повторов; технических ошибок нет**. '
        f'**{216-failed_matrix}/216** основных запусков прошли все применимые критерии, **{failed_matrix}/216** не прошли хотя бы один. '
        'Это исследование чувствительности уже зафиксированной модели, а не новая независимая натурная проверка и не текст статьи.', '',
        '## Что сравнивалось', '',
        'Четыре двойника: Aedes, Mosquitto, ActiveMQ Classic и EMQX. Для каждого: payload 16 байт, QoS 0/1/2, '
        '1 или 128 publishers, seeds 42/43/44 и три варианта. Итого 4 × 1 × 3 × 2 × 3 × 3 = 216. '
        'Каждый вариант имеет 72 запуска, каждый брокер — 54. В каждой паре сохраняются профиль, натурное наблюдение, '
        'нагрузочная точка, offeredRps и seed.', '',
        '| Вариант | Шаг | Окно нагрузки | Прогрев | Предельный drain |',
        '| --- | ---: | ---: | ---: | ---: |',
        '| baseline | 10 мкс | 2 с | 1 с | 5 с |',
        '| halfTick | 5 мкс | 2 с | 1 с | 5 с |',
        '| doubleWindow | 10 мкс | 4 с | 1 с | 5 с |', '',
        'Время здесь модельное. Увеличение окна увеличивает количество публикаций; сравниваются скорости, доли и задержки, '
        'а не только абсолютные счётчики. Это сокращённая матрица по двум уровням publishers и одному payload, '
        'её нельзя распространить на все режимы брокера.', '',
        '## Проверка доказательств', '',
        f"SHA-256 плана: `{observation['sourceFiles']['plan.json']}`.", '',
        f'SHA-256 перенесённого снимка: `{observation_sha}`.', '',
        'Программа чтения на Linux независимо перечитала 230 файлов строк и исходные summary, histogram, counters и manifest; '
        'проверила SHA конфигураций, 27 замороженных файлов входов/runtime и профили каждого запуска. '
        'После переноса локальный анализ проверил полное декартово покрытие, параметры каждого опыта и неизменность эталона в парах; '
        'пересчитал RPS, failure/loss и p99 по гистограммам, а также 920 состояний критериев. Ошибок этих проверок нет.', '',
        'У шести заранее выбранных повторов совпали байты summary и histogram и канонические байты научной части counters '
        '(поля brokers, receivers, receiverScope). Полный counters с графом и manifest с runId/временем таким требованием не охватываются. '
        'Это подтверждает детерминированность выбранных запусков на данном runtime, а не безусловную точность модели реального брокера.', '',
        '## Соответствие натурному эталону', '',
        'Пороги сохранены: относительная ошибка RPS ≤15%, p99 ≤20%, абсолютная разница publishFailureRate и deliveryLossRate ≤0,05. '
        'Для QoS 0 публикационного ACK нет; p99 ACK отмечен notApplicable. '
        'Код завершения 3 означает научное расхождение; он не является техническим падением.', '',
        '| Брокер | baseline: прошли / 18 | halfTick: прошли / 18 | doubleWindow: прошли / 18 | Всего прошли / 54 |',
        '| --- | ---: | ---: | ---: | ---: |']
    brokers = ('Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX')
    variants = ('baseline', 'halfTick', 'doubleWindow')
    pass_counts = {}
    for broker in brokers:
        values = []
        for variant in variants:
            selected = [r for r in matrix if identity(r['id'])['broker'] == broker and r['variant'] == variant]
            passed = sum(not r['fidelityFailed'] for r in selected)
            pass_counts[(broker, variant)] = passed
            values.append(passed)
        lines.append(f'| {broker} | {values[0]}/18 | {values[1]}/18 | {values[2]}/18 | {sum(values)}/54 |')
    lines += ['', '| Критерий | baseline: не прошли | halfTick: не прошли | doubleWindow: не прошли |', '| --- | ---: | ---: | ---: |']
    for criterion in ('rps', 'latencyP99Ms', 'publishFailureRate', 'deliveryLossRate'):
        values = [sum(c['status'] == 'failed' for r in matrix if r['variant'] == v for c in r['summary']['acceptanceCriteria'] if c['name'] == criterion) for v in variants]
        lines.append(f'| {criterion} | {values[0]} | {values[1]} | {values[2]} |')
    lines += ['', 'Один опыт может нарушить несколько критериев. Поэтому сумма нарушений отдельных критериев не равна числу непройденных запусков. '
        'Пилоты и повторы не добавлены в знаменатель 216. Все провалы сохранены в criteria.csv; успешные точки не отобраны отдельно.', '',
        '![Число прошедших запусков по брокеру и варианту](pass-counts.svg)', '',
        '## Парное влияние шага и окна', '',
        'Для каждого брокера и варианта есть 18 пар с baseline, из них 12 имеют p99 ACK (QoS 1/2). '
        'Ниже: среднее абсолютное относительное изменение / наибольшее изменение. '
        'Это изменение **между двумя симуляциями**, а не ошибка относительно натурного наблюдения. '
        'Среднее берётся по нагрузочным точкам и seeds с равными весами; точные пары сохранены в paired-metrics.csv.', '',
        '| Брокер | Вариант | RPS: среднее / максимум | p99: среднее / максимум | Максимальная абсолютная разница loss |',
        '| --- | --- | ---: | ---: | ---: |']
    for broker in brokers:
        for variant in ('halfTick', 'doubleWindow'):
            pairs = grouped[(broker, variant)]
            rps, latency = [pair_metric(pairs, metric) for metric in ('completedRps', 'latencyP99Ms')]
            loss = pair_metric(pairs, 'deliveryLossRate', 'absoluteDelta')
            lines.append(f'| {broker} | {variant} | {pct(mean(rps))} / {pct(max(rps))} | {pct(mean(latency))} / {pct(max(latency))} | {max(loss):.6f} |')
    half = [p for p in result['matchedPairs'] if p['variant'] == 'halfTick']
    double = [p for p in result['matchedPairs'] if p['variant'] == 'doubleWindow']
    pass_order = '/72, '.join(str(72-result['matrixDimensions']['variant'][v]['scientificFailed']) for v in ('baseline', 'halfTick', 'doubleWindow')) + '/72'
    loss_order = ', '.join(str(result['matrixDimensions']['variant'][v]['failedCriteria'].get('deliveryLossRate', 0)) for v in ('baseline', 'halfTick', 'doubleWindow'))
    lines += ['', f"Уменьшение шага до 5 мкс изменило RPS максимум на {pct(max(pair_metric(half, 'completedRps')))}; "
        f"максимальное изменение p99 — {pct(max(pair_metric(half, 'latencyP99Ms')))}. Это свидетельство малой чувствительности к данному уменьшению шага в выбранной матрице. "
        f"Переход с 2 до 4 с изменил RPS до {pct(max(pair_metric(double, 'completedRps')))}, а p99 ActiveMQ — до {pct(max(pair_metric(grouped[('ActiveMQ', 'doubleWindow')], 'latencyP99Ms')))}. "
        'Поэтому считать двухсекундное окно универсально достаточным для хвостовой задержки нельзя. '
        f'Число прошедших запусков ({pass_order}, соответственно baseline, halfTick, doubleWindow) не доказывает устранение систематической ошибки модели.', '',
        f'Число нарушений loss для baseline, halfTick и doubleWindow: {loss_order}. '
        'Это основание отдельно разбирать модель доставки и соответствие определения loss; это не доказательство конкретной программной ошибки. '
        'Подбирать профиль или допуски по этим наблюдениям без новой независимой серии нельзя.', '',
        '## Значение метрик и границы достоверности', '',
        'completedRps = completed / длительность окна; completion может происходить во время drain. '
        'publishFailureRate = publishFailed / attempted; deliveryLossRate = 1 − delivered / expectedDeliveries. '
        'p99 восстановлен как ближайший ранг ceil(0,99 × число ACK) из гистограммы тиков с переводом в мс. '
        'QoS 0 исключён из анализа ACK. Фактические receiver receipts имеют иной охват: после сброса учитываются поступления '
        'до конца drain, включая сообщения прогрева, пересёкшие границу. Их нельзя подставлять вместо broker.delivered '
        'или выдавать за проверенную полноту пар «публикация, получатель».', '',
        'Три seeds показывают вариацию этой модели, но не заменяют повторные натурные измерения. '
        'В point-metrics.csv даны среднее и диапазон трёх seeds; доверительные интервалы точности реального брокера по ним не заявляются. '
        'CPU/RSS относятся к стоимости вычисления, не к моделируемым свойствам двойника. EST здесь не вычисляется: '
        'интегральный рейтинг брокеров не является вероятностью достоверности модели.', '',
        'Результаты используют ранее полученные профили и контрольные observations. '
        'Базовые summary совпали с зафиксированной прежней кампанией, однако аудит истории не подтвердил слепую независимость '
        'всех контрольных данных от предшествующей разработки. Называть эту серию независимой натурной валидацией нельзя. '
        'Также она не подтверждает натурную точность сети MqttRelay или федерации.', '',
        '## Воспроизведение анализа', '',
        '```powershell',
        'python tools/dissertation/analyse_sensitivity.py --observation reports/audits/dissertation/sensitivity-final-v1/observation.json --output <новый-каталог> --final',
        '```', '',
        'analysis.json содержит 144 парных сравнения; paired-metrics.csv — каждую метрику каждой пары; '
        'point-metrics.csv — 72 группы нагрузочных точек с тремя seeds; criteria.csv — все 864 критерия основной матрицы. '
        'verification.json фиксирует проверки, transfer.json — совпадение SHA снимка на Linux и локально, '
        'collect_read_only.py позволяет повторить чтение сохранённого стенда без перезапуска опытов. '
        'Полные исходные файлы остаются на диске доказательств Linux; в Git архивы не добавляются.', '',
        '## Что требуется дальше', '',
        '1. Для режима с заметным изменением p99 проверить достаточность окна по заранее заданному плану; текущие результаты сохранить.',
        '2. Отдельно проверить определения потерь и влияние границы прогрев/измерение/drain.',
        '3. Если модель будет исправлена, зафиксировать новую версию и проверять на новом контрольном наборе, не меняя эту серию.',
        '4. Перед основной сетевой матрицей отдельно разобрать результаты её pilots; данная серия не является допуском сетевого опыта.', '']
    report = '\n'.join(lines)
    (output / 'Sensitivity-final.md').write_bytes(report.encode())
    colors = ('#2563eb', '#059669', '#d97706')
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="880" height="400" viewBox="0 0 880 400" role="img" aria-label="Количество запусков, прошедших все критерии, из 18 для каждого брокера и варианта">',
        '<rect width="880" height="400" fill="white"/>', '<g font-family="sans-serif" font-size="14" fill="#111827">',
        '<text x="30" y="27" font-size="19">Прошли все применимые критерии, из 18 запусков</text>']
    for count in (0, 6, 12, 18):
        y = 325 - count * 14
        svg.append(f'<path d="M60 {y} H850" stroke="#e5e7eb"/><text x="28" y="{y+5}">{count}</text>')
    for bi, broker in enumerate(brokers):
        x = 90 + bi * 195
        for vi, variant in enumerate(variants):
            count = pass_counts[(broker, variant)]
            h = count * 14
            svg.append(f'<rect x="{x+vi*43}" y="{325-h}" width="33" height="{h}" fill="{colors[vi]}"/><text x="{x+vi*43+8}" y="{319-h}">{count}</text>')
        svg.append(f'<text x="{x+15}" y="350">{broker}</text>')
    for vi, variant in enumerate(variants):
        x = 100 + vi * 240
        svg.append(f'<rect x="{x}" y="375" width="14" height="14" fill="{colors[vi]}"/><text x="{x+22}" y="388">{variant}</text>')
    svg += ['</g></svg>']
    (output / 'pass-counts.svg').write_bytes(('\n'.join(svg) + '\n').encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New result directory; never overwrite")
    parser.add_argument("--final", action="store_true", help="Require complete final evidence and produce final tables/report")
    args = parser.parse_args()
    raw = args.observation.read_bytes()
    observation = json.loads(raw.decode("utf-8-sig"))
    result = analyse(observation)
    result.update(observationFile=args.observation.as_posix(), observationSha256=hashlib.sha256(raw).hexdigest(),
                  analyserSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.mkdir(parents=True, exist_ok=False)
    if args.final:
        result = write_final_artifacts(observation, args.output, hashlib.sha256(raw).hexdigest())
        print(json.dumps({'verification': result['verification'], 'output': str(args.output)}, ensure_ascii=False))
        return
    (args.output / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (args.output / "Sensitivity-review.md").write_text(render(result), encoding="utf-8")
    print(json.dumps({"coverage": result["coverage"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
