"""Windows/offline timing sensitivity; never edits campaign inputs, profiles or native stand."""
import argparse
import copy
import hashlib
import json
import pathlib
import subprocess
import time


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('series', 'runtime', 'output', 'mqtty-repository', 'benchmark-repository'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    root, output = pathlib.Path(args.series), pathlib.Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    profile = root / 'brokers/Mosquitto/broker-calibration.v3.json'
    before = hashlib.sha256(profile.read_bytes()).hexdigest()
    observations = read(root / 'brokers/Mosquitto/calibration/broker-observations.v3.json')
    half_rtt_ms = sum(p['rttBaselineMs'] for p in observations['points']) / len(observations['points']) / 2
    rows = []
    for size, qos in ((16, 1), (256, 2)):
        for seed in (42, 43, 44):
            source = root / f'brokers/Mosquitto/simulations/fidelity-2s-compact-v1/m{size}.q{qos}.p1.s{seed}.json'
            for variant in ('baseline100us', 'fine10us-preserved-time', 'fine10us-calibrated-transport'):
                config = copy.deepcopy(read(source))
                config.update(brokerProfilePath=str(profile.resolve()), mqttYRepository=args.mqtty_repository,
                              mqttBenchmarkRepository=args.benchmark_repository)
                if variant != 'baseline100us':
                    config['ticks']['tickPeriod'] = '00:00:00.0000100'
                    config['ticks']['outgoingWaitTicks'] *= 10
                    config['network']['transferTicks'] *= 10
                    config['network']['throughputWindowTicks'] *= 10
                    for name in ('warmupTicks', 'measurementTicks', 'drainTimeoutTicks'):
                        config['experiment'][name] *= 10
                if variant == 'fine10us-calibrated-transport':
                    config['network']['transferTicks'] = max(1, round(half_rtt_ms / .01))
                    config['ticks']['outgoingWaitTicks'] = 1
                key = f'm{size}.q{qos}.p1.s{seed}-{variant}'
                path = output / (key + '.json')
                path.write_text(json.dumps(config, indent=2), encoding='utf-8')
                started = time.monotonic()
                result = subprocess.run(['dotnet', args.runtime, 'experiment', '--config', str(path),
                                         '--output', str(output / key)], capture_output=True, text=True, timeout=180)
                (output / (key + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
                if result.returncode not in (0, 3):
                    raise RuntimeError(key + ': ' + result.stderr)
                directory = max((output / key).iterdir())
                summary = read(directory / 'summary.json')
                row = {'bytes': size, 'qos': qos, 'seed': seed, 'variant': variant,
                    'elapsedSeconds': time.monotonic() - started, 'directory': str(directory),
                    'p99Ms': summary['simulated']['latencyP99Ms'], 'completedRps': summary['simulated']['completedRps'],
                    'criteria': summary['acceptanceCriteria']}
                rows.append(row)
                (output / 'results.json').write_text(json.dumps({'excludedFromCampaignCoverage': True,
                    'profileSha256': before, 'method': 'same frozen model/profile; timing sensitivity using calibration RTT only',
                    'rows': rows}, indent=2), encoding='utf-8')
                print(key, 'p99', row['p99Ms'], 'wall', round(row['elapsedSeconds'], 2), flush=True)
                if hashlib.sha256(profile.read_bytes()).hexdigest() != before:
                    raise RuntimeError('Frozen profile changed')


if __name__ == '__main__':
    main()
