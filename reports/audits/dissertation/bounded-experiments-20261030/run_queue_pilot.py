"""One new q=1/1024 diagnostic. No retries, resume, matrix or runtime changes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
IDENTITY = 'queue-causality-pilot-20261030-v1'
SOURCE_MANIFEST = '24ae3654a610545f2a13ebc796eeddcaa898d9bde2103ee722592e211b8bffa7'
SCIENTIFIC = ('summary.json', 'histograms.json', 'publications.json', 'delivery-receipts.json', 'transmissions.json', 'network-evidence.json')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate_pair(small, reference):
    if small.get('queueCapacity') != 1 or reference.get('queueCapacity') != 1024:
        raise ValueError('Only the declared q=1/1024 pair is admitted')
    if set(small) != set(reference) or {k for k in small if small[k] != reference[k]} != {'queueCapacity'}:
        raise ValueError('Only queueCapacity may differ, including nested options')
    expected = {'warmupSeconds': .25, 'measurementSeconds': 2, 'drainTimeoutSeconds': 10,
                'mode': 'federation', 'topology': 'star', 'brokers': 4, 'publishers': 64,
                'receiverSites': [0, 1, 2, 3], 'payloadBytes': 16, 'qos': 2, 'offeredRps': 1000,
                'seed': 42, 'tickPeriod': '00:00:00.0001000', 'ideal': True,
                'brokerProfilePath': None, 'cacheCapacity': 100000, 'hopLimit': 4}
    if any(reference.get(k) != v for k, v in expected.items()):
        raise ValueError('Declared workload or common windows changed')


def claim_run(root, started_epoch):
    root.mkdir(parents=True, exist_ok=False)
    with (root / 'run-identity.json').open('x', encoding='utf-8') as stream:
        json.dump({'programIdentity': IDENTITY, 'startedEpoch': started_epoch,
                   'deadlineEpoch': started_epoch + 600, 'hardWallSeconds': 600,
                   'modelTimeoutSeconds': 240, 'workers': 2, 'retryAllowed': False}, stream, indent=2)


def remaining_model_budget(now, started):
    # Last 120 seconds are reserved for reaping, recount and postflight.
    return max(0, min(240, started + 480 - now))


def classify(summaries):
    if summaries[0]['queueDrops'] == 0:
        return 'noninformativeNoSmallQueueOverflow'
    if summaries[1]['queueDrops'] != 0:
        return 'noninformativeReferenceOverflow'
    return 'queueCapacityContrast'


def unit_budget(runtime, stop):
    def seconds(value):
        parts = re.findall(r'(\d+(?:\.\d+)?)(us|ms|min|h|s)', value.replace(' ', '').strip())
        if not parts or ''.join(number + unit for number, unit in parts) != value.replace(' ', '').strip():
            raise ValueError('Unrecognised or unlimited systemd duration: ' + value)
        return sum(float(number) * {'us': 1e-6, 'ms': .001, 's': 1, 'min': 60, 'h': 3600}[unit] for number, unit in parts)
    total = seconds(runtime) + seconds(stop)
    if total <= 0 or total > 600:
        raise ValueError('Unit runtime plus stop grace must fit the entire 600s budget')
    return total


def check_inputs():
    seal = load(HERE / 'program-seal.json')
    if seal['programIdentity'] != IDENTITY:
        raise ValueError('Wrong program identity')
    for name, expected in seal['files'].items():
        path = HERE / name
        if path.is_symlink() or not path.resolve().is_relative_to(HERE) or digest(path) != expected:
            raise ValueError('Program seal changed: ' + name)
    small, reference = [load(HERE / f'configs/q{capacity}.json') for capacity in (1, 1024)]
    validate_pair(small, reference)
    return small, reference


def run(source, root):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Execution requires Linux; --check-inputs is portable')
    source, root = source.resolve(), root.resolve()
    inputs = check_inputs()
    if digest(source / 'execution-manifest.json') != SOURCE_MANIFEST:
        raise ValueError('Wrong original scientific snapshot')
    sys.path.insert(0, str(source / 'tools/dissertation'))
    from network_study_inputs import StorageGuard, verify_snapshot, sha, read, lock
    from network_study_process import execute
    from network_study_verification import verify_loaded_run
    from network_review import check_graph
    guard = StorageGuard(source.parent.parent, root, 10 * 1024**3)
    manifest = verify_snapshot(source)
    original = read(source / 'program/configs/matrix/matrix.star.federation.q2.r1000.w10.s42.json')
    original.update(warmupSeconds=.25, measurementSeconds=2, drainTimeoutSeconds=10)
    if inputs[1] != original:
        raise ValueError('Reference differs from the declared source beyond common short windows')
    if root.is_relative_to(source) or HERE.is_relative_to(root):
        raise ValueError('Use a separate new output; never overwrite inputs or snapshot')
    # Authoritative cgroup hard wall/memory/CPU limits are required by the protocol command.
    # Refuse unisolated execution rather than relying solely on cooperative polling.
    group_name = next(line.split(':', 2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    group = Path('/sys/fs/cgroup' + group_name)
    if not group.name.endswith('.service'):
        raise ValueError('Run inside the bounded systemd service from the protocol')
    unit_properties = dict(line.split('=', 1) for line in subprocess.check_output(
        ['systemctl', '--user', 'show', group.name, '--property=RuntimeMaxUSec', '--property=TimeoutStopUSec'],
        text=True).splitlines() if '=' in line)
    hard_budget = unit_budget(unit_properties['RuntimeMaxUSec'], unit_properties['TimeoutStopUSec'])
    memory_max = (group / 'memory.max').read_text().strip()
    cpu_max = (group / 'cpu.max').read_text().split()
    if memory_max == 'max' or int(memory_max) > 6 * 1024**3 or cpu_max[0] == 'max' or int(cpu_max[0]) > 2 * int(cpu_max[1]) or (group / 'memory.swap.max').read_text().strip() != '0':
        raise ValueError('Require MemoryMax<=6GiB and CPUQuota<=200%')
    available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    if available < 6 * 1024**3:
        raise RuntimeError('Insufficient available RAM')
    with lock(guard.mount / '.mqtty-network-study.lock'):
        started_epoch, started = time.time(), time.monotonic()
        claim_run(root, started_epoch)
        results = []
        try:
            guard.save(root / 'progress.json', {'phase': 'preflight', 'finishedModels': 0, 'total': 2,
                'deadlineEpoch': started_epoch + 600, 'programIdentity': IDENTITY,
                'phaseVisibility': 'Model phases/ticks available only after completion'})
            (root / 'runtime').mkdir()
            for path in (source / 'runtime').iterdir():
                if path.is_file():
                    shutil.copy2(path, root / 'runtime' / path.name)
            (root / 'configs').mkdir()
            for capacity in (1, 1024):
                shutil.copy2(HERE / f'configs/q{capacity}.json', root / f'configs/q{capacity}.json')
            pinned = {p.relative_to(root).as_posix(): sha(p) for folder in ('runtime', 'configs') for p in (root / folder).iterdir() if p.is_file()}
            guard.save(root / 'preflight.json', {'programIdentity': IDENTITY, 'sourceManifestSha256': SOURCE_MANIFEST,
                'sourceSnapshotFilesVerified': len(manifest['files']), 'dotnetSha256': sha(manifest['dotnet']),
                'programSealSha256': digest(HERE / 'program-seal.json'), 'pinnedFiles': pinned,
                'cgroup': group_name, 'memoryMax': memory_max, 'cpuMax': cpu_max,
                'unitProperties': unit_properties, 'unitTotalBudgetSeconds': hard_budget, 'singleChangedFactor': 'queueCapacity'})

            def run_case(capacity):
                attempt = root / f'q{capacity}' / 'attempt-001'
                attempt.mkdir(parents=True, exist_ok=False)
                command = [manifest['dotnet'], str(root / 'runtime/EntityFX.MqttY.Federation.App.dll'),
                           'run', '--config', str(root / f'configs/q{capacity}.json'), '--output', 'output']
                guard.save(attempt / 'command.json', {'argv': command, 'queueCapacity': capacity})
                budget = remaining_model_budget(time.monotonic(), started)
                if budget <= 0:
                    result = {'technicalError': 'Shared budget exhausted before process start', 'returnCode': None}
                else:
                    result = execute(command, attempt, guard, budget, 2 * 1024**3, 1024**3,
                        lambda state: guard.save(attempt / 'process-state.json', state),
                        cancel_requested=lambda: time.monotonic() - started >= 480)
                result.update(queueCapacity=capacity, attempt=attempt.relative_to(root).as_posix())
                guard.save(attempt / 'result.json', result)
                return result

            guard.save(root / 'progress.json', {'phase': 'models', 'finishedModels': 0, 'total': 2,
                'deadlineEpoch': started_epoch + 600, 'programIdentity': IDENTITY})
            with ThreadPoolExecutor(max_workers=2) as pool:
                for future in as_completed([pool.submit(run_case, capacity) for capacity in (1, 1024)]):
                    results.append(future.result())
                    guard.save(root / 'progress.json', {'phase': 'models', 'finishedModels': len(results), 'total': 2,
                        'deadlineEpoch': started_epoch + 600, 'results': results})
            results.sort(key=lambda row: row['queueCapacity'])
            for result in results:
                if result['technicalError'] or result['returnCode'] != 0:
                    continue
                if time.monotonic() - started >= 580:
                    raise TimeoutError('Shared budget exhausted before recount')
                attempt = root / result['attempt']; output = attempt / 'output'
                summary = verify_loaded_run(output)
                check_graph(output / 'topology.graphml', read(output / 'run-config.json'))
                executed = read(output / 'run-config.json'); supplied = read(root / f"configs/q{result['queueCapacity']}.json")
                if summary['status'] != 'completed' or any(executed.get(k) != v for k, v in supplied.items()) or sha(output / 'inputs/config.json') != sha(root / f"configs/q{result['queueCapacity']}.json"):
                    raise ValueError('Incomplete model or executed inputs changed')
                result.update(rawArithmeticPassed=True, summary=summary,
                    scientificSha256={name: sha(output / name) for name in SCIENTIFIC})
                guard.save(attempt / 'result.json', result)
            verify_snapshot(source)
            if any(sha(root / name) != value for name, value in pinned.items()):
                raise ValueError('Pinned new inputs changed')
            check_inputs()
            accepted = len(results) == 2 and all(row.get('rawArithmeticPassed') for row in results)
            classification = classify([row['summary'] for row in results]) if accepted else 'technicalFailure'
            final = {'status': 'completed' if accepted else 'technicalFailure', 'classification': classification,
                     'results': results, 'sharedWallSeconds': time.monotonic() - started,
                     'sourceSnapshotUnchanged': True, 'pinnedInputsUnchanged': True, 'retryPerformed': False}
            guard.save(root / 'result.json', final)
            guard.save(root / 'progress.json', {k: v for k, v in final.items() if k != 'results'})
            return final['status'] == 'completed'
        except BaseException as error:
            guard.save(root / 'failure.json', {'status': 'technicalFailure', 'error': repr(error),
                'traceback': traceback.format_exc(), 'results': results, 'retryPerformed': False})
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-inputs', action='store_true')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.check_inputs:
        check_inputs(); print(json.dumps({'status': 'inputsValid', 'programIdentity': IDENTITY}))
    elif args.source is None or args.output is None:
        parser.error('--source and --output are required to execute')
    else:
        sys.exit(0 if run(args.source, args.output) else 1)
