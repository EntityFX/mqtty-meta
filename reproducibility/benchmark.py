"""Bounded, process-level MqttY benchmark. No automatic retries or remote access."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    # Windows readers may briefly open the destination without FILE_SHARE_DELETE.
    # Retry only this atomic metadata write, never an experiment.
    for attempt in range(41):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 40:
                raise
            time.sleep(0.05)


def command(argv):
    return subprocess.check_output(argv, text=True, encoding='utf-8', errors='replace').strip()


def verify(package):
    manifest = read(package / 'manifest.json')
    for name, expected in manifest['files'].items():
        path = (package / name).resolve()
        if not path.is_relative_to(package) or digest(path) != expected:
            raise ValueError('Package changed: ' + name)
    if digest(__file__) != manifest['files']['benchmark.py']:
        raise ValueError('Use the packaged benchmark.py')
    for name, commit in manifest['sourceCommits'].items():
        repo = package / name
        if command(['git', '-C', str(repo), 'rev-parse', 'HEAD']) != commit:
            raise ValueError('Wrong source commit: ' + name)
        if command(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=no']):
            raise ValueError('Dirty source: ' + name)
    return manifest


def windows_usage(process):
    from ctypes import wintypes as w
    class Memory(ctypes.Structure):
        _fields_ = [('cb', w.DWORD), ('faults', w.DWORD)] + [(n, ctypes.c_size_t) for n in
            ('peak', 'working', 'pagedPeak', 'paged', 'nonPagedPeak', 'nonPaged', 'pagefile', 'pagefilePeak')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(Memory), w.DWORD]
    creation, exit_time, system, user = (w.FILETIME() for _ in range(4))
    handle = w.HANDLE(int(process._handle))
    if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(system), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    memory = Memory(); memory.cb = ctypes.sizeof(memory)
    memory_ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb)
    seconds = lambda v: ((v.dwHighDateTime << 32) | v.dwLowDateTime) / 10_000_000
    return {'userSeconds': seconds(user), 'systemSeconds': seconds(system),
            'peakMemoryBytes': int(memory.peak) if memory_ok else None,
            'memoryMethod': 'Windows GetProcessMemoryInfo PeakWorkingSetSize'}


def measure(argv, cwd, output, timeout):
    output.mkdir()
    linux = sys.platform.startswith('linux')
    actual = argv
    if linux:
        actual = ['/usr/bin/time', '-f', 'user=%U\nsystem=%S\nmaxrssKiB=%M', '-o', str(output / 'resources.txt'), '--'] + argv
    started = time.perf_counter()
    with (output / 'stdout.log').open('wb') as log:
        process = subprocess.Popen(actual, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, start_new_session=linux)
        save(output / 'process.json', {'pid': process.pid, 'argv': actual, 'startedEpoch': time.time()})
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if linux:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                if process.poll() is None:
                    process.kill()
            process.wait()
        wall = time.perf_counter() - started
        usage = {'userSeconds': None, 'systemSeconds': None, 'peakMemoryBytes': None}
        if os.name == 'nt':
            usage.update(windows_usage(process))
        elif linux:
            usage['memoryMethod'] = 'GNU time maximum resident set, KiB converted to bytes'
            if (output / 'resources.txt').exists():
                fields = dict(line.split('=', 1) for line in (output / 'resources.txt').read_text().splitlines() if '=' in line)
                for target, source in [('userSeconds', 'user'), ('systemSeconds', 'system')]:
                    if source in fields: usage[target] = float(fields[source])
                if 'maxrssKiB' in fields: usage['peakMemoryBytes'] = int(fields['maxrssKiB']) * 1024
    result = dict(usage, wallSeconds=wall, returnCode=process.returncode, timedOut=timed_out)
    save(output / 'measurement.json', result)
    return result


def fingerprint(bundle):
    summary = read(bundle / 'summary.json')
    if summary['status'] != 'completed' or summary['apiAttempts'] <= 0:
        raise ValueError('Incomplete or empty simulation')
    fields = {}
    for relative, expected in read(bundle / 'sha256.json').items():
        path = (bundle / relative).resolve()
        if not path.is_relative_to(bundle.resolve()) or digest(path).lower() != expected.lower():
            raise ValueError('Raw seal mismatch: ' + relative)
        if path.parent == bundle and path.suffix == '.json' and path.name != 'manifest.json':
            fields[relative] = hashlib.sha256(json.dumps(read(path), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if path.parent == bundle and path.suffix == '.csv':
            fields[relative] = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    return fields


def validate_baseline(value, manifest_sha, case_names):
    if value.get('status') != 'completed' or value.get('packageSha256') != manifest_sha:
        raise ValueError('Completed baseline from this exact package required')
    if not {name + ':serial' for name in case_names}.issubset(value.get('fingerprints', {})):
        raise ValueError('Baseline must cover every case')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=['preflight', 'native', 'pilot', 'main', 'campaign'])
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--admission', type=Path, help='Main only: packageSha256 and timeoutSeconds, fixed after all pilots')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    manifest = verify(package)
    if args.mode == 'native' and manifest.get('nativeCliDefaultIsParallel'):
        raise ValueError('This CLI defaults to parallel strategy 1. Use pilot to explicitly select native serial/parallel strategy 0.')
    manifest_sha = digest(package / 'manifest.json')
    cases = read(package / 'cases.json')
    baseline = read(args.baseline) if args.baseline else None
    if baseline: validate_baseline(baseline, manifest_sha, cases)
    timeout = 600
    if args.mode in ('main', 'campaign'):
        if not baseline or not args.admission:
            raise ValueError('Main requires --baseline and --admission after all machine pilots')
        admission = read(args.admission)
        timeout = admission.get('timeoutSeconds')
        if admission.get('packageSha256') != manifest_sha or type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError('Admission must bind this package and an integer timeout of 1..600 seconds')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'mode': args.mode, 'status': 'running', 'startedEpoch': time.time(), 'packageSha256': manifest_sha, 'timeoutSeconds': timeout,
              'rows': [], 'fingerprints': {}, 'baselineProvided': baseline is not None}
    save(output / 'result.json', report)
    try:
        if not (os.name == 'nt' or sys.platform.startswith('linux')):
            raise ValueError('Only Windows and Linux are supported')
        if sys.platform.startswith('linux') and not Path('/usr/bin/time').is_file():
            raise ValueError('GNU time /usr/bin/time required')
        environment = {'os': platform.platform(), 'architecture': platform.machine(), 'processor': platform.processor(),
                       'logicalCpuCount': os.cpu_count(), 'python': platform.python_version(),
                       'dotnetInfo': command([args.dotnet, '--info']), 'freeDiskBytes': shutil.disk_usage(output).free,
                       'cachePolicy': 'New process/JIT each run; ordinary OS filesystem cache; pilot excluded',
                       'runtimeEnvironment': {k: v for k, v in os.environ.items() if k.startswith(('DOTNET_', 'COMPlus_'))}}
        if os.name == 'nt':
            environment['hardware'] = command(['powershell', '-NoProfile', '-Command',
                'Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress; (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory'])
        else:
            environment['hardware'] = command(['lscpu'])
            environment['meminfo'] = Path('/proc/meminfo').read_text()
            environment['loadavg'] = Path('/proc/loadavg').read_text()
        save(output / 'environment.json', environment)
        if environment['freeDiskBytes'] < 5 * 1024**3:
            raise ValueError('At least 5 GiB free space required')
        if args.mode != 'preflight':
            schedule = [('pilot', 1)] if args.mode in ('native', 'pilot') else [('main', n) for n in range(1, 6)]
            if args.mode == 'campaign': schedule.insert(0, ('pilot', 1))
            for phase, repeat in schedule:
                for name, spec in cases.items():
                    if shutil.disk_usage(output).free < 5 * 1024**3:
                        raise ValueError('Free space fell below 5 GiB')
                    config = spec
                    config_path = output / (name + '.json')
                    save(config_path, config)
                    executions = ['serial'] if args.mode == 'native' else (['serial', 'parallel'] if repeat % 2 else ['parallel', 'serial'])
                    for execution in executions:
                        key = name + ':' + execution
                        attempt = output / ('%s-r%d-%s-%s' % (phase, repeat, name, execution))
                        if args.mode == 'native':
                            app = 'EntityFX.MqttY.LoadBalancing.App.dll' if name == 'load-balancer' else 'EntityFX.MqttY.Federation.App.dll'
                            argv = [args.dotnet, str(package / 'app' / app), 'run', '--config', str(config_path), '--output', str(attempt / 'raw')]
                        else:
                            argv = [args.dotnet, str(package / 'app/BenchmarkProbe.dll'), name, execution, str(config_path), str(attempt / 'raw')]
                        report['activeAttempt'] = attempt.name
                        save(output / 'result.json', report)
                        row = dict(measure(argv, package / 'mqtty', attempt, timeout), case=name, execution=execution, phase=phase, repeat=repeat)
                        report['rows'].append(row)
                        save(output / 'result.json', report)
                        row['technicalSuccess'] = not row['timedOut'] and row['returnCode'] == 0
                        if not row['technicalSuccess']:
                            row.update(matchesLocalRepeat=False, matchesSerialScience=False, matchesBaseline=False)
                            save(output / 'result.json', report)
                            if execution == 'serial':
                                raise ValueError('Failed serial attempt: ' + attempt.name)
                            print('FAIL', attempt.name, round(row['wallSeconds'], 3), 'seconds; diagnostic only', flush=True)
                            continue
                        value = fingerprint(attempt / 'raw')
                        own_reference = report['fingerprints'].get(key, value)
                        serial_reference = (baseline or report)['fingerprints'].get(name + ':serial', value)
                        row['matchesLocalRepeat'] = value == own_reference
                        row['matchesSerialScience'] = value == serial_reference
                        row['matchesBaseline'] = None if not baseline or key not in baseline['fingerprints'] else value == baseline['fingerprints'][key]
                        if execution == 'serial' and (not row['matchesLocalRepeat'] or not row['matchesSerialScience']):
                            raise ValueError('Serial scientific mismatch: ' + name)
                        report['fingerprints'].setdefault(key, value)
                        row['outputBytes'] = sum(p.stat().st_size for p in attempt.rglob('*') if p.is_file())
                        save(output / 'result.json', report)
                        print('PASS', attempt.name, round(row['wallSeconds'], 3), 'seconds; serial science match=', row['matchesSerialScience'], flush=True)
            report['wallStatistics'] = {name + ':' + execution: {'median': statistics.median(values), 'min': min(values), 'max': max(values), 'unit': 'seconds'}
                for name in cases for execution in ('serial', 'parallel')
                for values in [[r['wallSeconds'] for r in report['rows'] if r['case'] == name and r['execution'] == execution and r['technicalSuccess'] and (r['phase'] == 'main' or args.mode in ('pilot', 'native'))]] if values}
            report['parallelScienceEquivalent'] = all(r['matchesSerialScience'] and r['matchesLocalRepeat'] for r in report['rows'])
        verify(package)
        report['status'] = 'completed'
    except Exception as error:
        report['status'] = 'failed'; report['error'] = str(error)
        raise
    finally:
        report['finishedEpoch'] = time.time()
        save(output / 'result.json', report)
    print('PASS', args.mode, output, flush=True)


if __name__ == '__main__':
    main()
