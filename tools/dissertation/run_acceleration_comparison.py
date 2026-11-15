"""Separate sealed workers1/2 pilot or admitted five-pair study; no retry/resume."""
import argparse
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
from run_acceleration_pilot import (MANIFEST_SHA, CONFIG_SHA, SCIENTIFIC, read, save,
    digest, verify_files, claim_output, check_equivalence, measure_jobs, cgroup_snapshot)

IDENTITY = 'acceleration-workers-20261102-v1'


def schedule(mode):
    if mode == 'pilot':
        return [2]
    if mode == 'main':
        return [1, 2, 2, 1, 1, 2, 2, 1, 1, 2]
    raise ValueError('Unknown mode')


def main_budget(pilot):
    if (pilot.get('programIdentity') != IDENTITY or pilot.get('mode') != 'pilot'
            or pilot.get('status') != 'completed' or pilot.get('equivalencePassed') is not True
            or pilot.get('finishedModels') != 2 or len(pilot.get('primaryWalls', [])) != 1):
        raise ValueError('Verified completed pilot required')
    wall = pilot['primaryWalls'][0]
    if not isinstance(wall, (int, float)) or not math.isfinite(wall) or wall <= 0:
        raise ValueError('Invalid pilot wall')
    budget = math.ceil(1.35*5*(179.007+wall)+120)
    if budget > 2400:
        raise ValueError('Estimated main budget exceeds 2400s')
    return budget


def analyse(rows):
    if [r['workers'] for r in rows] != schedule('main'):
        raise ValueError('All five fixed pairs required')
    ratios = []
    for index in range(0, 10, 2):
        pair = {r['workers']: r['primaryWallSeconds'] for r in rows[index:index+2]}
        if not all(math.isfinite(v) and v > 0 for v in pair.values()):
            raise ValueError('Invalid pair timing')
        ratios.append(pair[1]/pair[2])
    logs = [math.log(r) for r in ratios]
    mean = sum(logs)/5
    sd = math.sqrt(sum((x-mean)**2 for x in logs)/4)
    radius = 2.7764451051977987*sd/math.sqrt(5)
    interval = [math.exp(mean-radius), math.exp(mean+radius)]
    return {'pairedSpeedups': ratios, 'geometricMean': math.exp(mean),
            'ci95StudentLogPairs': interval, 'pairs': 5,
            'practicalGainThreshold': 1.10, 'practicalGainConfirmed': interval[0] > 1.10,
            'scope': 'Two fixed independent q1024 jobs on this host; not kernel speedup'}


def run(source, inputs, output, mode, admission=None):
    import os
    import resource
    started, epoch = time.monotonic(), time.time()
    source, inputs, output = [p.resolve() for p in (source, inputs, output)]
    seal = read(inputs/'seal.json')
    if seal['programIdentity'] != IDENTITY or sys.platform != 'linux':
        raise ValueError('Linux and matching input identity required')
    verify_files(inputs, seal['files'])
    if digest(Path(__file__)) != seal['files']['run_acceleration_comparison.py']:
        raise ValueError('Execute sealed controller')
    import run_acceleration_pilot as meter
    if digest(meter.__file__) != seal['files']['run_acceleration_pilot.py']:
        raise ValueError('Execute sealed measurement helper')
    if digest(source/'execution-manifest.json') != MANIFEST_SHA or digest(inputs/'fixture.json') != CONFIG_SHA:
        raise ValueError('Frozen source/fixture mismatch')
    if any(a.is_relative_to(b) or b.is_relative_to(a) for a, b in [(source, output), (inputs, output)]):
        raise ValueError('Separate immutable source, inputs and output required')
    budget = 600
    admission_record = None
    if mode == 'main':
        if admission is None:
            raise ValueError('Admission file required')
        admission_record = read(admission)
        pilot_path = Path(admission_record['pilotResult'])
        if digest(pilot_path) != admission_record['pilotSha256']:
            raise ValueError('Pilot result changed')
        pilot = read(pilot_path)
        budget = main_budget(pilot)
        if (admission_record['hardWallSeconds'] != budget
                or pilot['inputSealSha256'] != digest(inputs/'seal.json')
                or pilot['sourceManifestSha256'] != MANIFEST_SHA):
            raise ValueError('Admission inputs/budget mismatch')
    workers_list = schedule(mode)
    sys.path.insert(0, str(source/'tools/dissertation'))
    from network_study_inputs import StorageGuard, lock, verify_snapshot
    from network_study_verification import verify_loaded_run
    from network_review import check_graph
    guard = StorageGuard(source.parent.parent, output, 10*1024**3)
    group_name = next(line.split(':', 2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines()
                      if line.startswith('0::'))
    group = Path('/sys/fs/cgroup'+group_name)
    props = dict(line.split('=', 1) for line in subprocess.check_output(
        ['systemctl', '--user', 'show', group.name, '-p', 'RuntimeMaxUSec', '-p', 'TimeoutStopUSec',
         '-p', 'KillMode'], text=True).splitlines())
    # systemctl renders durations differently at minute boundaries; use microseconds via dbus-independent suffix parsing.
    def seconds(value):
        units = {'h': 3600, 'min': 60, 's': 1, 'ms': .001, 'us': .000001}
        import re
        parts = re.findall(r'([0-9.]+)(min|ms|us|h|s)', value)
        if not parts:
            raise ValueError('Unrecognised systemd duration')
        return sum(float(n)*units[u] for n, u in parts)
    if (not group.name.endswith('.service') or set(os.sched_getaffinity(0)) != {4, 6}
            or seconds(props['RuntimeMaxUSec']) != budget-5 or props['TimeoutStopUSec'] != '5s'
            or props['KillMode'] != 'control-group'):
        raise ValueError('Wrong bounded service or CPU affinity')
    quota, period = (group/'cpu.max').read_text().split()
    if ((group/'memory.max').read_text().strip() != str(6*1024**3)
            or quota == 'max' or int(quota) != 2*int(period)
            or (group/'memory.swap.max').read_text().strip() != '0'):
        raise ValueError('Require6GiB/200%/swap0')
    reference = read(inputs/'aa-result.json')
    if reference['status'] != 'completed' or reference['classification'] != 'measurementReady':
        raise ValueError('Accepted A/A required')
    baseline = reference['scientificSha256']
    if set(baseline) != set(SCIENTIFIC):
        raise ValueError('Incomplete scientific reference')
    with lock(guard.mount/'.mqtty-network-study.lock'):
        claim_output(output)
        batches = []
        def progress(phase):
            return {'programIdentity': IDENTITY, 'mode': mode, 'phase': phase,
                    'finishedModels': 2*len(batches), 'total': 2*len(workers_list),
                    'deadlineEpoch': epoch+budget, 'hardWallSeconds': budget,
                    'inputSealSha256': digest(inputs/'seal.json'), 'sourceManifestSha256': MANIFEST_SHA}
        try:
            guard.save(output/'progress.json', progress('preflight'))
            manifest = verify_snapshot(source)
            shutil.copytree(source/'runtime', output/'runtime')
            shutil.copyfile(inputs/'fixture.json', output/'fixture.json')
            pinned = {p.relative_to(output).as_posix(): digest(p) for p in (output/'runtime').iterdir() if p.is_file()}
            pinned['fixture.json'] = digest(output/'fixture.json')
            initial_cpu, initial_cgroup = time.process_time(), cgroup_snapshot(group)
            guard.save(output/'preflight.json', dict(progress('preflight'),
                sourceFilesVerified=len(manifest['files']), pinned=pinned, unitProperties=props,
                uname=list(os.uname()), cpuTopology=subprocess.check_output(['lscpu', '-J'], text=True),
                dotnetSha256=digest(manifest['dotnet']), gnuTimeSha256=digest('/usr/bin/time'),
                admission=admission_record, cgroupBefore=initial_cgroup,
                controllerSha256=digest(__file__), measurementHelperSha256=digest(meter.__file__),
                cachePolicy='Cold process/JIT, ordinary unmanaged filesystem cache'))
            command = [manifest['dotnet'], str(output/'runtime/EntityFX.MqttY.Federation.App.dll'),
                       'run', '--config', str(output/'fixture.json'), '--output', 'output']
            for index, workers in enumerate(workers_list, 1):
                batch = output/f'batch-{index:02d}-w{workers}'
                batch_start = time.monotonic()
                guard.save(output/'progress.json', dict(progress('running'), activeBatchPath=str(batch)))
                measurement = measure_jobs([command, command], batch, workers,
                    min(started+budget-35, batch_start+(480 if mode == 'pilot' else 360)),
                    job_timeout=240, check=guard.check)
                if measurement['error'] or len(measurement['jobs']) != 2:
                    raise RuntimeError('measurementFailed: '+str(measurement['error']))
                for job in (1, 2):
                    if time.monotonic() >= started+budget-15:
                        raise TimeoutError('verificationBudget')
                    raw = batch/f'job-{job}'/'output'
                    summary = verify_loaded_run(raw)
                    config = read(raw/'run-config.json')
                    check_graph(raw/'topology.graphml', config)
                    if (summary['status'] != 'completed'
                            or any(config.get(k) != v for k, v in read(output/'fixture.json').items())
                            or digest(raw/'inputs/config.json') != CONFIG_SHA):
                        raise ValueError('Incomplete model or changed executed config')
                    hashes = {name: digest(raw/name) for name in SCIENTIFIC}
                    check_equivalence(baseline, hashes)
                    guard.save(batch/f'job-{job}'/'verification.json',
                        {'rawArithmeticPassed': True, 'summary': summary, 'scientificSha256': hashes})
                measurement['primaryWallSeconds'] = time.monotonic()-batch_start
                measurement['cgroupAfter'] = cgroup_snapshot(group)
                guard.save(batch/'measurement.json', measurement)
                batches.append(measurement)
            verify_snapshot(source)
            verify_files(inputs, seal['files']); verify_files(output, pinned)
            final = dict(progress('completed'), status='completed', equivalencePassed=True,
                primaryWalls=[b['primaryWallSeconds'] for b in batches],
                batches=batches, scientificSha256=baseline, sourceUnchanged=True,
                pinnedInputsUnchanged=True, controllerCpuSeconds=time.process_time()-initial_cpu,
                controllerMaxRssBytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                cgroupBefore=initial_cgroup, cgroupAfter=cgroup_snapshot(group),
                wallSeconds=time.monotonic()-started, retryPerformed=False, automaticNextExperiment=False)
            if mode == 'main':
                final['analysis'] = analyse(batches)
            guard.save(output/'result.json', final); guard.save(output/'progress.json', final)
        except BaseException as failure:
            final = dict(progress('stopped'), status='failed', error=repr(failure),
                         traceback=traceback.format_exc(), retryPerformed=False,
                         wallSeconds=time.monotonic()-started, automaticNextExperiment=False)
            guard.save(output/'failure.json', final); guard.save(output/'progress.json', final)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'inputs', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--mode', choices=('pilot', 'main'), required=True)
    parser.add_argument('--admission', type=Path)
    args = parser.parse_args()
    run(args.source, args.inputs, args.output, args.mode, args.admission)
