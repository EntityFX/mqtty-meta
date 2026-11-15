"""Isolated two-process simulator pilot; outputs never enter campaign indices."""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import time

GIB = 1024**3


def scientific_output(directory):
    directory = pathlib.Path(directory)
    counters = json.loads((directory / 'counters.json').read_text())
    # graph is a human-readable dump containing RealTime and wall-clock rates.
    # Compare every structured broker/receiver counter and preserve the raw dump.
    if 'graph' in counters and not isinstance(counters['graph'], str):
        raise ValueError('Unexpected diagnostic graph format')
    counters.pop('graph', None)
    return {'summary': json.loads((directory / 'summary.json').read_text()),
            'histogram': json.loads((directory / 'latency-histogram.json').read_text()), 'counters': counters}


def run(root):
    root = pathlib.Path(root).resolve()
    plan = json.loads((root / 'pilot-plan.json').read_text())
    cgroup = next(line.split(':', 2)[2] for line in pathlib.Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    group = pathlib.Path('/sys/fs/cgroup' + cgroup)
    jobs = []
    records = []
    start = time.time()
    result = {'status': 'running', 'startedUtc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        for job in plan['jobs']:
            stream = (root / (job['name'] + '.log')).open('w')
            process = subprocess.Popen(['dotnet', plan['cli'], 'experiment', '--config', job['config'],
                                        '--output', str(root / job['name'])], stdout=stream, stderr=subprocess.STDOUT)
            jobs.append((job, process, stream))
        while any(p.poll() is None for _, p, _ in jobs):
            stat = dict(line.split() for line in (group / 'memory.stat').read_text().splitlines())
            available = int(next(line.split()[1] for line in pathlib.Path('/proc/meminfo').read_text().splitlines()
                                 if line.startswith('MemAvailable:'))) * 1024
            current = int((group / 'memory.current').read_text())
            records.append({'elapsedSeconds': time.time() - start, 'current': current,
                            'anon': int(stat['anon']), 'file': int(stat['file']), 'available': available})
            if current > 4 * GIB or available < 2 * GIB:
                raise RuntimeError('Pilot memory guard; preserve outputs and keep main campaign sequential')
            if shutil.disk_usage(root).free < 20 * GIB:
                raise RuntimeError('Pilot disk guard')
            time.sleep(1)
        checks = []
        for job, process, _ in jobs:
            if process.returncode not in (0, 3):
                raise RuntimeError(f'{job["name"]}: CLI exit {process.returncode}')
            directory = max((root / job['name']).iterdir())
            summary = json.loads((directory / 'summary.json').read_text())
            if summary['status'] != 'completed':
                raise RuntimeError('Incomplete pilot: ' + job['name'])
            baseline = job.get('baseline')
            equal = None
            if baseline:
                equal = scientific_output(directory) == scientific_output(baseline)
                if not equal:
                    raise RuntimeError('Parallel pilot differs from sequential scientific baseline')
            checks.append({'name': job['name'], 'directory': str(directory), 'baselineIdentical': equal,
                           'configSha256': hashlib.sha256(pathlib.Path(job['config']).read_bytes()).hexdigest()})
        result.update(status='passed', checks=checks,
                      excludedDiagnosticFields=['counters.json:graph (wall-clock diagnostic text)'])
    except BaseException as error:
        result.update(status='failed', error=str(error))
        raise
    finally:
        for _, process, _ in jobs:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        deadline = time.monotonic() + 20
        for _, process, stream in jobs:
            try:
                process.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            stream.close()
        result.update(elapsedSeconds=time.time() - start,
                      peakMemoryBytes=max((row['current'] for row in records), default=0),
                      minimumAvailableBytes=min((row['available'] for row in records), default=0))
        (root / 'memory-samples.json').write_text(json.dumps(records, indent=2))
        (root / 'result.json').write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    run(parser.parse_args().root)
