"""Regression of a separate service-budget revision against frozen Aedes calibration data."""
import argparse
import collections
import concurrent.futures
import datetime
import hashlib
import json
import pathlib
import subprocess
import time


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'output', 'runtime', 'repository', 'benchmark-repository'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    root, output = pathlib.Path(args.baseline), pathlib.Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    source = root / 'brokers/Aedes/broker-calibration.v3.json'
    source_sha = sha(source)
    if source_sha != read(root / 'state.json')['profiles']['Aedes']:
        raise RuntimeError('Baseline frozen profile digest differs')
    profile = output / 'inputs/Aedes-calibration.v4.json'
    profile.parent.mkdir(exist_ok=True)
    if not profile.exists():
        with profile.open('xb') as stream:
            stream.write(source.read_bytes())
    if sha(profile) != source_sha:
        raise RuntimeError('Copied profile changed')
    commit = subprocess.check_output(['git', '-C', args.repository, 'rev-parse', 'HEAD'], text=True).strip()
    if subprocess.check_output(['git', '-C', args.repository, 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise RuntimeError('Source repository must be clean')
    manifest = {'modelCommit': commit, 'runtimeSha256': sha(args.runtime), 'profileSha256': source_sha,
        'transportTiming': 'empiricalServiceBudget', 'thresholdsUnchanged': True,
        'scope': '18 regression scenarios at six inspected calibration points; not independent validation',
        'baselineSeries': read(root / 'series.json')['seriesId'], 'workers': args.workers}
    if (output / 'manifest.json').exists() and read(output / 'manifest.json') != manifest:
        raise RuntimeError('Verification manifest changed')
    write(output / 'manifest.json', manifest)
    rows = read(output / 'index.json')['runs'] if (output / 'index.json').exists() else []
    keys = {r['key'] for r in rows}
    jobs = []
    base = root / 'brokers/Aedes/simulations/fidelity-2s-overnight-v1'
    for size, qos, publishers in ((16, 0, 1), (16, 1, 1), (256, 2, 1), (16, 1, 16), (16, 2, 16), (16, 1, 64)):
        for seed in (42, 43, 44):
            key = f'm{size}.q{qos}.p{publishers}.s{seed}'
            if key in keys:
                continue
            config = read(base / (key + '.json'))
            config.update(brokerProfilePath=str(profile.resolve()), mqttYRepository=args.repository,
                          mqttBenchmarkRepository=args.benchmark_repository)
            config['experiment']['transportTiming'] = 'empiricalServiceBudget'
            path = output / 'inputs' / (key + '.json')
            if path.exists() and read(path) != config:
                raise RuntimeError('Frozen experiment config changed')
            write(path, config)
            jobs.append((key, path))
    write(output / 'state.json', {'status': 'running', 'completed': len(rows), 'expected': 18})

    def run(job):
        key, path = job
        start = time.monotonic()
        result = subprocess.run(['dotnet', args.runtime, 'experiment', '--config', str(path), '--output', str(output / 'runs' / key)],
                                capture_output=True, text=True, timeout=1200)
        (output / (key + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode not in (0, 3):
            raise RuntimeError(key + ': ' + result.stderr)
        directory = max((output / 'runs' / key).iterdir())
        summary = read(directory / 'summary.json')
        if summary['status'] != 'completed' or sha(profile) != source_sha:
            raise RuntimeError('Failed experiment or changed frozen profile')
        return {'key': key, 'directory': str(directory), 'summary': summary, 'elapsedSeconds': time.monotonic() - start,
                'inputSha256': sha(path)}

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in concurrent.futures.as_completed([pool.submit(run, job) for job in jobs]):
                row = future.result()
                rows.append(row)
                rows.sort(key=lambda r: r['key'])
                write(output / 'index.json', {'runs': rows})
                write(output / 'state.json', {'status': 'running', 'completed': len(rows), 'expected': 18})
                print(row['key'], 'failed', [c['name'] for c in row['summary']['acceptanceCriteria'] if c['status'] == 'failed'], flush=True)
        failures = collections.Counter(c['name'] for row in rows for c in row['summary']['acceptanceCriteria'] if c['status'] == 'failed')
        write(output / 'completion.json', {'status': 'completed', 'runs': len(rows), 'failedCriteria': dict(failures),
            'failedScenarios': sum(any(c['status'] == 'failed' for c in row['summary']['acceptanceCriteria']) for row in rows),
            'profileSha256': source_sha, 'finishedUtc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        write(output / 'state.json', {'status': 'completed', 'completed': len(rows), 'expected': 18})
    except BaseException as error:
        write(output / 'state.json', {'status': 'failed', 'reason': repr(error), 'completed': len(rows), 'expected': 18})
        raise


if __name__ == '__main__':
    main()
