"""Bounded A/A measurement calibration, before workers1/2 speed comparisons.

No resume/retry, no runtime/config changes, no automatic subsequent experiment.
GNU time owns CPU accounting; /proc samples simultaneous RSS independently.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
IDENTITY = 'acceleration-aa-20261030-v1'
MANIFEST_SHA = '24ae3654a610545f2a13ebc796eeddcaa898d9bde2103ee722592e211b8bffa7'
CONFIG_SHA = 'f84cc6ba86e6ead5c423ca21d5862c30e652f9a48e352245eec9740337f58ce5'
SCIENTIFIC = ('summary.json', 'histograms.json', 'publications.json',
              'delivery-receipts.json', 'transmissions.json', 'network-evidence.json')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n',
                         encoding='utf-8', newline='\n')
    temporary.replace(path)


def verify_files(root, files):
    root = Path(root).resolve()
    for name, expected in files.items():
        path = root / name
        if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(root) or path.is_symlink() or digest(path) != expected:
            raise ValueError('Sealed input changed or unsafe: ' + name)


def claim_output(root):
    Path(root).mkdir(parents=True, exist_ok=False)


def check_equivalence(baseline, current):
    if baseline != current:
        raise ValueError('scientificEquivalenceFailed')


def aa_classification(first, second):
    if min(first, second) <= 0:
        raise ValueError('Invalid completion wall')
    return 'measurementReady' if abs(first-second)/min(first, second) < .10 else 'inconclusiveAaSpread'


def process_tree_rss(pid):
    """Sample RSS of GNU time and its descendants, not sum of historical peaks."""
    try:
        status = Path(f'/proc/{pid}/status').read_text()
        value = next((int(line.split()[1])*1024 for line in status.splitlines()
                      if line.startswith('VmRSS:')), 0)
        children = Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
        return value + sum(process_tree_rss(int(child)) for child in children)
    except (FileNotFoundError, ProcessLookupError):
        return 0


def kill_owned(process):
    # Kill group even if wrapper just exited: a surviving descendant is still owned.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def measure_jobs(commands, root, workers, deadline, job_timeout=180, check=lambda: None):
    """Execute identical work schedules; return every attempted child including failures.

    Linux-only. Scientific verification is done by caller after this model makespan.
    Each batch preserves CPU/raw logs and timestamped simultaneous RSS samples.
    """
    if not sys.platform.startswith('linux') or workers not in (1, 2):
        raise ValueError('Linux and workers1/2 required')
    root = Path(root)
    claim_output(root)
    started = time.monotonic()
    active, jobs, next_job = [], [], 0
    peak, error = 0, None
    samples = (root / 'rss-samples.jsonl').open('x', encoding='utf-8')

    def finish(item):
        process = item['process']
        code = process.wait()
        item['stdout'].close(); item['stderr'].close()
        fields = {}
        resource_path = item['attempt'] / 'gnu-time.txt'
        if resource_path.exists():
            fields = dict(line.split('=', 1) for line in resource_path.read_text().splitlines()
                          if line.startswith(('user=', 'system=', 'maxrssKiB=', 'exit=')))
        row = {'job': item['job'], 'pid': process.pid, 'returnCode': code,
               'wallSeconds': time.monotonic()-item['started'],
               'sampledTreeRssPeakBytes': item['peak'],
               'userSeconds': float(fields['user']) if 'user' in fields else None,
               'systemSeconds': float(fields['system']) if 'system' in fields else None,
               'maxRssBytes': int(fields['maxrssKiB'])*1024 if 'maxrssKiB' in fields else None}
        save(item['attempt'] / 'process.json', row)
        jobs.append(row)
        return row

    try:
        while next_job < len(commands) or active:
            check()
            if time.monotonic() >= deadline:
                error = 'deadlineExceeded'; break
            while next_job < len(commands) and len(active) < workers:
                attempt = root / f'job-{next_job+1}'
                attempt.mkdir()
                argv = ['/usr/bin/time', '-f', 'user=%U\nsystem=%S\nmaxrssKiB=%M\nexit=%x',
                        '-o', str(attempt / 'gnu-time.txt'), '--', *commands[next_job]]
                save(attempt / 'command.json', {'argv': argv, 'startedEpoch': time.time()})
                stdout = (attempt / 'stdout.log').open('xb')
                stderr = (attempt / 'stderr.log').open('xb')
                try:
                    process = subprocess.Popen(argv, cwd=attempt, stdout=stdout, stderr=stderr,
                                               start_new_session=True)
                except BaseException:
                    stdout.close(); stderr.close(); raise
                active.append({'process': process, 'attempt': attempt, 'job': next_job+1,
                               'started': time.monotonic(), 'stdout': stdout, 'stderr': stderr, 'peak': 0})
                next_job += 1
            current = 0
            for item in list(active):
                process = item['process']
                rss = process_tree_rss(process.pid)
                item['peak'] = max(item['peak'], rss); current += rss
                if rss > 2*1024**3:
                    error = 'childRssLimit'; break
                if time.monotonic()-item['started'] >= job_timeout:
                    error = 'childTimeout'; break
                if process.poll() is not None:
                    row = finish(item); active.remove(item)
                    if row['returnCode'] != 0:
                        error = 'childFailed'; break
                    if row['userSeconds'] is None or row['systemSeconds'] is None or row['maxRssBytes'] is None:
                        error = 'missingCpuAccounting'; break
            peak = max(peak, current)
            samples.write(json.dumps({'elapsedSeconds': time.monotonic()-started, 'rssBytes': current})+'\n')
            samples.flush()
            if error:
                break
            if active:
                time.sleep(.05)
    except BaseException as failure:
        error = type(failure).__name__ + ': ' + str(failure)
    finally:
        for item in active:
            kill_owned(item['process']); finish(item)
        samples.close()
    result = {'workers': workers, 'jobs': sorted(jobs, key=lambda row: row['job']),
              'error': error, 'modelMakespanSeconds': time.monotonic()-started,
              'simultaneousSampledRssPeakBytes': peak,
              'sumIndividualMaxRssBytes': sum(row['maxRssBytes'] or 0 for row in jobs),
              'cpuUserSeconds': sum(row['userSeconds'] or 0 for row in jobs),
              'cpuSystemSeconds': sum(row['systemSeconds'] or 0 for row in jobs),
              'attemptedJobs': next_job, 'expectedJobs': len(commands)}
    save(root / 'measurement.json', result)
    return result


def cgroup_snapshot(group):
    values = dict(line.split() for line in (group / 'cpu.stat').read_text().splitlines())
    return {'cpuUsec': int(values['usage_usec']), 'memoryCurrentBytes': int((group/'memory.current').read_text()),
            'memoryPeakBytes': int((group/'memory.peak').read_text()),
            'memoryEvents': (group/'memory.events').read_text()}


def run(source, inputs, output):
    import resource
    source, inputs, output = source.resolve(), inputs.resolve(), output.resolve()
    started, epoch = time.monotonic(), time.time()
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Linux required')
    seal = read(inputs / 'seal.json')
    if seal['programIdentity'] != IDENTITY:
        raise ValueError('Wrong program identity')
    verify_files(inputs, seal['files'])
    if digest(source/'execution-manifest.json') != MANIFEST_SHA or digest(inputs/'fixture.json') != CONFIG_SHA:
        raise ValueError('Wrong source/fixture')
    if output.is_relative_to(source) or output.is_relative_to(inputs) or inputs.is_relative_to(output):
        raise ValueError('Separate immutable input and new output required')
    sys.path.insert(0, str(source/'tools/dissertation'))
    from network_study_inputs import StorageGuard, verify_snapshot, lock
    from network_study_verification import verify_loaded_run
    from network_review import check_graph
    guard = StorageGuard(source.parent.parent, output, 10*1024**3)
    group_name = next(line.split(':', 2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    group = Path('/sys/fs/cgroup'+group_name)
    if not group.name.endswith('.service') or set(os.sched_getaffinity(0)) != {4, 6}:
        raise ValueError('Require bounded service and declared CPU affinity4,6')
    properties = dict(line.split('=', 1) for line in subprocess.check_output(
        ['systemctl', '--user', 'show', group.name, '-p', 'RuntimeMaxUSec', '-p', 'TimeoutStopUSec'], text=True).splitlines())
    if properties != {'RuntimeMaxUSec': '9min 55s', 'TimeoutStopUSec': '5s'}:
        raise ValueError('Require exactly595+5s service budget: '+str(properties))
    quota, period = (group/'cpu.max').read_text().split()
    if (group/'memory.max').read_text().strip() != str(6*1024**3) or quota == 'max' or int(quota) != 2*int(period) or (group/'memory.swap.max').read_text().strip() != '0':
        raise ValueError('Require6GiB/200%/swap0')
    with lock(guard.mount/'.mqtty-network-study.lock'):
        claim_output(output)
        batches = []
        initial_cpu = time.process_time()
        initial_cgroup = cgroup_snapshot(group)
        try:
            guard.save(output/'progress.json', {'programIdentity': IDENTITY, 'phase': 'preflight',
                       'finishedModels': 0, 'total': 4, 'deadlineEpoch': epoch+600})
            manifest = verify_snapshot(source)
            (output/'runtime').mkdir()
            for path in (source/'runtime').iterdir():
                if path.is_file():
                    shutil.copy2(path, output/'runtime'/path.name)
            shutil.copy2(inputs/'fixture.json', output/'fixture.json')
            pinned = {p.relative_to(output).as_posix(): digest(p) for p in (output/'runtime').iterdir() if p.is_file()}
            pinned['fixture.json'] = digest(output/'fixture.json')
            guard.save(output/'preflight.json', {'programIdentity': IDENTITY, 'manifestSha256': MANIFEST_SHA,
                'sourceFilesVerified': len(manifest['files']), 'pinned': pinned,
                'sealSha256': digest(inputs/'seal.json'), 'cpuAffinity': sorted(os.sched_getaffinity(0)),
                'cpuTopology': subprocess.check_output(['lscpu', '-J'], text=True),
                'uname': list(os.uname()), 'unitProperties': properties,
                'dotnetSha256': digest(manifest['dotnet']), 'gnuTimeSha256': digest('/usr/bin/time'),
                'cgroupBefore': initial_cgroup, 'startedEpoch': epoch, 'deadlineEpoch': epoch+600,
                'cachePolicy': 'Cold process/JIT, ordinary unmanaged filesystem cache; same policy, not cold disk'})
            command = [manifest['dotnet'], str(output/'runtime/EntityFX.MqttY.Federation.App.dll'),
                       'run', '--config', str(output/'fixture.json'), '--output', 'output']
            baseline = None
            for batch in ('aa-1', 'aa-2'):
                batch_start = time.monotonic()
                guard.save(output/'progress.json', {'programIdentity': IDENTITY, 'phase': batch,
                           'finishedModels': 2*len(batches), 'total': 4, 'deadlineEpoch': epoch+600,
                           'activeBatchPath': str(output/batch), 'note': 'Per-child logs and RSS samples available in batch directory'})
                measurement = measure_jobs([command, command], output/batch, 1, started+480,
                                           check=guard.check)
                if measurement['error'] or len(measurement['jobs']) != 2:
                    raise RuntimeError('measurementFailed: '+str(measurement['error']))
                for job in (1, 2):
                    if time.monotonic() >= started+570:
                        raise TimeoutError('verificationBudget')
                    raw = output/batch/f'job-{job}'/'output'
                    summary = verify_loaded_run(raw)
                    config = read(raw/'run-config.json')
                    check_graph(raw/'topology.graphml', config)
                    if summary['status'] != 'completed' or any(config.get(k) != v for k, v in read(output/'fixture.json').items()) or digest(raw/'inputs/config.json') != CONFIG_SHA:
                        raise ValueError('Incomplete model or changed executed fixture')
                    hashes = {name: digest(raw/name) for name in SCIENTIFIC}
                    if baseline is None:
                        baseline = hashes
                    check_equivalence(baseline, hashes)
                    guard.save(output/batch/f'job-{job}'/'verification.json',
                               {'rawArithmeticPassed': True, 'summary': summary, 'scientificSha256': hashes})
                measurement['primaryWallSeconds'] = time.monotonic()-batch_start
                measurement['cgroupAfter'] = cgroup_snapshot(group)
                guard.save(output/batch/'measurement.json', measurement)
                batches.append(measurement)
            verify_snapshot(source)
            verify_files(inputs, seal['files']); verify_files(output, pinned)
            first, second = [batch['primaryWallSeconds'] for batch in batches]
            final = {'programIdentity': IDENTITY, 'status': 'completed', 'phase': 'completed',
                'classification': aa_classification(first, second), 'finishedModels': 4, 'total': 4,
                'aaRelativeSpread': abs(first-second)/min(first, second), 'batchPrimaryWallSeconds': [first, second],
                'scientificSha256': baseline, 'sourceUnchanged': True, 'pinnedInputsUnchanged': True,
                'controllerCpuSeconds': time.process_time()-initial_cpu,
                'controllerMaxRssBytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                'cgroupBefore': initial_cgroup, 'cgroupAfter': cgroup_snapshot(group),
                'wallSeconds': time.monotonic()-started, 'retryPerformed': False,
                'speedupMeasured': False, 'automaticNextExperiment': False}
            guard.save(output/'result.json', final); guard.save(output/'progress.json', final)
            return True
        except BaseException as failure:
            final = {'programIdentity': IDENTITY, 'status': 'technicalFailure', 'phase': 'stopped',
                     'error': repr(failure), 'traceback': traceback.format_exc(),
                     'finishedBatches': len(batches), 'total': 4, 'retryPerformed': False,
                     'wallSeconds': time.monotonic()-started, 'automaticNextExperiment': False}
            guard.save(output/'failure.json', final); guard.save(output/'progress.json', final)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.exit(0 if run(args.source, args.inputs, args.output) else 1)
