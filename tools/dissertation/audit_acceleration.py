"""Recompute historical table ratios; never execute simulator or infer raw evidence."""
import argparse
import csv
import hashlib
import io
import json
import pathlib
import re
import subprocess


def seconds(minutes, whole, fraction):
    return int(minutes) * 60 + int(whole) + float('0.' + fraction)


def table_rows(text):
    table = text.split(r'\label{tab:AllCompare}', 1)[1].split(r'\label{tab:FinalCompare}', 1)[0]
    rows = []
    machines = [('Xeon E5-2680v3', 'Core i7-2600'), ('Elbrus 8C v4', 'Dual Core E5400')]
    for part in table.split(r'\hline'):
        workload = re.search(r'B=\d+[^&\n]+', part)
        times = re.findall(r'(\d\d):(\d\d),(\d+)', part)
        if workload is None or not times:
            continue
        if len(times) != 4 or len(rows) >= 32:
            raise ValueError('Unexpected table layout')
        group = len(rows) // 16
        values = [seconds(*item) for item in times]
        for index, machine in enumerate(machines[group]):
            single, parallel = values[index * 2:index * 2 + 2]
            if single <= 0 or parallel <= 0:
                raise ValueError('Nonpositive timing')
            rows.append(dict(machine=machine, workload=workload.group().strip(),
                             singleSeconds=single, parallelSeconds=parallel,
                             ratio=single / parallel, evidence='published-table-only'))
    if len(rows) != 32:
        raise ValueError('Expected sixteen workloads and thirty-two comparisons')
    return rows


def audit(root, output):
    rows = table_rows((root / 'diss/Dissertation/chapter4.tex').read_text(encoding='utf-8'))
    paths = [
        'diss/Dissertation/chapter4.tex', 'diss/listings/mqtty.mqttrelay.result.json',
        'reports/audits/dissertation/publications.json',
        'mqtty/utils/parseResults.py', 'mqtty/utils/parseResults.ipynb',
        'mqtty/utils/parseResultsNext.ipynb',
        'mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.MqttRelay.App/Program.cs',
        'tools/campaign/pilot-parallel-memory.py',
        'tools/documentation/layout.json', 'tools/dissertation/audit_acceleration.py',
    ]
    payload = dict(step33Accepted=False, benchmarkLaunched=False,
                   scope='Local checked-out repositories and accessible Git history; no external archive reconstruction',
                   sourceFiles={p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths},
                   commits={name: subprocess.check_output(['git', '-C', str(root / name), 'rev-parse', 'HEAD'], text=True).strip()
                            for name in ['mqtty', 'MqttBenchmark', 'diss']},
                   historicalFinalCompare=dict(oldSeconds=40.39, newSingleSeconds=5.99,
                       newParallelSeconds=3.19, oldTicks=6886, newTicks=6890,
                       rawRatio=40.39 / 3.19, tickNormalizedRatio=(6886 / 6890) * (40.39 / 3.19),
                       currentParallelRatio=5.99 / 3.19),
                   perMachine={machine: dict(comparisons=len(selected),
                       minimumRatio=min(x['ratio'] for x in selected),
                       maximumRatio=max(x['ratio'] for x in selected),
                       arithmeticMeanRatio=sum(x['ratio'] for x in selected) / len(selected),
                       pooledTimeRatio=sum(x['singleSeconds'] for x in selected) / sum(x['parallelSeconds'] for x in selected))
                       for machine in sorted({r['machine'] for r in rows})
                       for selected in [[r for r in rows if r['machine'] == machine]]})
    output.mkdir(parents=True, exist_ok=True)
    (output / 'evidence.json').write_bytes((json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode())
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    (output / 'historical-ratios.csv').write_bytes(buffer.getvalue().encode('utf-8'))
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root.resolve(), args.output.resolve())['perMachine'], ensure_ascii=False, indent=2))
