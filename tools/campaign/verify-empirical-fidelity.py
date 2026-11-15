"""Bounded independent-process verification of the empirical revision, preserving the baseline cohort."""
import argparse
import collections
import concurrent.futures
import hashlib
import json
import pathlib
import subprocess
import time


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def write(path, value):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    temporary = pathlib.Path(str(path) + '.new')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'histograms', 'runtime', 'mqtty-repository', 'benchmark-repository', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--broker', default='Mosquitto')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--scope', choices=['smoke', 'full'], default='smoke')
    args = parser.parse_args()
    root, output = pathlib.Path(args.baseline), pathlib.Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    profile = output / (args.broker + '-broker-calibration.v4.json')
    observations = root / 'brokers' / args.broker / 'calibration/broker-observations.v3.json'
    if not profile.exists():
        result = subprocess.run(['dotnet', args.runtime, 'calibrate', '--observations', str(observations),
            '--latency-histograms', args.histograms, '--output', str(profile), '--mqtty-repository', args.mqtty_repository,
            '--broker', args.broker, '--coverage-protocol', 'compact-v1'], capture_output=True, text=True, timeout=120)
        (output / 'calibration.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(result.stderr)
    frozen = digest(profile)
    commit = subprocess.check_output(['git', '-C', args.mqtty_repository, 'rev-parse', 'HEAD'], text=True).strip()
    manifest = {'scope': args.scope, 'excludedFromBaselineCampaign': True, 'modelCommit': commit,
        'runtimeSha256': digest(args.runtime), 'profileSha256': frozen, 'histogramsSha256': digest(args.histograms),
        'nativeCalibrationObservationsSha256': digest(observations), 'loadMode': 'closedLoopPerPublisher',
        'tickMicroseconds': 10, 'workers': args.workers,
        'transportTiming': 'calibratedRttBudget',
        'validationStatus': 'Regression against already collected/inspected observations; not a new blind native holdout'}
    if (output / 'verification-manifest.json').exists() and read(output / 'verification-manifest.json') != manifest:
        raise RuntimeError('Frozen verification inputs changed')
    write(output / 'verification-manifest.json', manifest)
    rows = read(output / 'index.json')['runs'] if (output / 'index.json').exists() else []
    existing = {row['key'] for row in rows}
    calibration = read(observations)
    propagation = max(1, round(sum(p['rttBaselineMs'] for p in calibration['points']) / len(calibration['points']) / 2 / .01))
    jobs = []
    for stage in ('fidelity', 'holdout'):
        reference = read(root / 'brokers' / args.broker / ('validation' if stage == 'holdout' else 'calibration') / 'broker-observations.v3.json')
        for p in reference['points']:
            size, qos, publishers = (p[name] for name in ('messageBytes', 'qos', 'publishers'))
            if args.scope == 'smoke' and (size, qos, publishers) not in ((16, 1, 1), (256, 2, 1)):
                continue
            for seed in (42, 43, 44):
                key = f'{stage}.m{size}.q{qos}.p{publishers}.s{seed}'
                if key in existing:
                    continue
                template = root / f'brokers/{args.broker}/simulations/{stage}-2s-compact-v1/m{size}.q{qos}.p{publishers}.s{seed}.json'
                if not template.exists():
                    template = root / f'brokers/{args.broker}/simulations/fidelity-2s-compact-v1/m{size}.q{qos}.p{publishers}.s{seed}.json'
                config = read(template)
                config.update(brokerProfilePath=str(profile.resolve()), mqttYRepository=args.mqtty_repository,
                              mqttBenchmarkRepository=args.benchmark_repository)
                config['experiment'].update(loadMode='closedLoopPerPublisher', offeredRps=p['attemptedRps']['mean'],
                    transportTiming='calibratedRttBudget', warmupTicks=100000, measurementTicks=200000, drainTimeoutTicks=500000)
                config['ticks'].update(tickPeriod='00:00:00.0000100', outgoingWaitTicks=1)
                config['network'].update(transferTicks=propagation, throughputWindowTicks=1000)
                if stage == 'holdout':
                    config.update(referenceObservationsPath=str((root / 'brokers' / args.broker / 'validation/broker-observations.v3.json').resolve()), referenceCoverageProtocol='compact-v1')
                path = output / 'inputs' / (key + '.json')
                if path.exists() and read(path) != config:
                    raise RuntimeError('Existing experiment config differs')
                write(path, config)
                jobs.append((key, path))

    def execute(job):
        key, path = job
        started = time.monotonic()
        process = subprocess.run(['dotnet', args.runtime, 'experiment', '--config', str(path), '--output', str(output / 'runs' / key)],
            capture_output=True, text=True, timeout=300)
        (output / (key + '.log')).write_text(process.stdout + process.stderr, encoding='utf-8')
        if process.returncode not in (0, 3):
            raise RuntimeError(key + ': ' + process.stderr)
        directory = max((output / 'runs' / key).iterdir())
        summary = read(directory / 'summary.json')
        if summary['status'] != 'completed' or digest(profile) != frozen:
            raise RuntimeError('Experiment failed or frozen profile changed')
        return {'key': key, 'directory': str(directory), 'elapsedSeconds': time.monotonic() - started,
            'summary': summary, 'configSha256': digest(path)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in concurrent.futures.as_completed([pool.submit(execute, job) for job in jobs]):
            row = future.result()
            rows.append(row)
            rows.sort(key=lambda r: r['key'])
            write(output / 'index.json', {'runs': rows})
            print(row['key'], row['summary']['simulated']['completedRps'], row['summary']['simulated']['latencyP99Ms'], flush=True)
    failures = collections.Counter(c['name'] for row in rows for c in row['summary']['acceptanceCriteria'] if c['status'] == 'failed')
    write(output / 'completion.json', {'status': 'completed', 'runs': len(rows), 'failedCriteria': dict(failures), 'profileSha256': frozen})


if __name__ == '__main__':
    main()
