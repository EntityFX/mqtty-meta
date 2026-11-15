"""Prepare and launch a new full campaign without host-specific preparation scripts.

Uses the frozen NewNativeStand and D-Bus launcher. Does not install brokers or
modify source checkouts. All deployment-specific values come from arguments/spec.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid

BROKERS = ('Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX')


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def cpu_set(value):
    result = set()
    for part in value.split(','):
        bounds = [int(v) for v in part.split('-')]
        if len(bounds) == 1:
            result.add(bounds[0])
        elif len(bounds) == 2 and bounds[0] <= bounds[1]:
            result.update(range(bounds[0], bounds[1] + 1))
        else:
            raise ValueError('Invalid CPU set')
    if not result or min(result) < 0:
        raise ValueError('CPU set must be nonempty and nonnegative')
    return result


def capture(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True)
        return {'argv': argv, 'exitCode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    except OSError as error:
        return {'argv': argv, 'error': str(error)}


def mount_identity(mount):
    mount = pathlib.Path(mount).resolve()
    actual = subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE', '-T', str(mount)], text=True).split()
    if len(actual) != 3 or actual[0] != str(mount) or actual[2] != 'ext4' or str(mount) == '/':
        raise RuntimeError('An explicitly mounted ext4 filesystem is required; observed: ' + repr(actual))
    system_source = subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '-T', '/'], text=True).strip()
    if actual[1].split('[', 1)[0] == system_source.split('[', 1)[0]:
        raise RuntimeError('Evidence must not use the system filesystem')
    if shutil.disk_usage(mount).free < 20 * 1024**3 or shutil.disk_usage('/').free < 10 * 1024**3:
        raise RuntimeError('Need at least 20 GiB free on evidence disk and 10 GiB on system disk')
    return actual


def repository_commit(path):
    if subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise RuntimeError('Tracked source changes must be committed before preparing: ' + str(path))
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()


def prepare(args):
    if sys.version_info < (3, 11) or sys.platform != 'linux':
        raise RuntimeError('Preparation requires Linux and Python >=3.11')
    repo = pathlib.Path(args.repository).resolve()
    mount = pathlib.Path(args.archive_mount).resolve()
    actual = mount_identity(mount)
    base = pathlib.Path(args.series).resolve()
    if base == mount or not base.is_relative_to(mount):
        raise RuntimeError('Series must be a separate directory inside the evidence mount')
    root = base / 'controller'
    if root.exists():
        raise RuntimeError('Existing controller directory: use a new series or launch --resume')
    assets_root = pathlib.Path(args.broker_assets_root).resolve() if getattr(args, 'broker_assets_root', None) else None
    if assets_root and (not assets_root.is_dir() or not assets_root.is_relative_to(base)):
        raise RuntimeError('Broker asset snapshot must be an existing directory inside this series')
    broker_cpu = args.broker_cpu
    controller_cpus = cpu_set(args.controller_cpus)
    allowed = os.sched_getaffinity(0)
    if not 0 <= broker_cpu <= 63 or broker_cpu not in allowed or not controller_cpus.issubset(allowed):
        raise RuntimeError('Requested CPUs are unavailable; broker CPU must be 0..63')
    siblings_path = pathlib.Path(f'/sys/devices/system/cpu/cpu{broker_cpu}/topology/thread_siblings_list')
    siblings = cpu_set(siblings_path.read_text().strip())
    if controller_cpus & siblings:
        raise RuntimeError('Controller must exclude broker CPU and its SMT siblings')
    if max(args.fidelity_workers, args.relay_workers) > len(controller_cpus):
        raise RuntimeError('Worker count must not exceed controller logical CPUs')
    sources = {name: repository_commit(repo / name) for name in ('MqttBenchmark', 'mqtty')}
    sources['meta'] = repository_commit(repo)
    spec = read(args.spec)
    if len(spec['brokers']) != 4 or {b['name'] for b in spec['brokers']} != set(BROKERS):
        raise RuntimeError('Spec must contain exactly Aedes, Mosquitto, ActiveMQ and EMQX')
    by_name = {b['name']: b for b in spec['brokers']}
    import urllib.parse
    for name, port in zip(BROKERS, (1883, 2883, 3883, 4883)):
        entry = by_name[name]
        uri = urllib.parse.urlparse(entry['mqttUri'])
        if uri.scheme != 'mqtt' or uri.hostname != '127.0.0.1' or uri.port != port:
            raise RuntimeError(f'{name} must use mqtt://127.0.0.1:{port}')
        if not entry.get('brokerVersion'):
            raise RuntimeError('Record the actual installed brokerVersion: ' + name)
        entry['linuxLaunchMode'] = 'systemdUser'
        for field in ('executable', 'launcherExecutable'):
            if entry.get(field) and (not pathlib.Path(entry[field]).is_absolute() or not pathlib.Path(entry[field]).is_file()):
                raise RuntimeError('Absolute existing path required: ' + str(entry[field]))
    cli = pathlib.Path(args.benchmark_cli).resolve()
    simulator = pathlib.Path(args.simulator_cli).resolve()
    pwsh = pathlib.Path(args.pwsh).resolve()
    for path in (cli, simulator, pwsh, repo / 'tools/campaign/accelerated-resume.py'):
        if not path.is_file():
            raise RuntimeError('Missing deployment input: ' + str(path))
    root.mkdir(parents=True)
    inputs = root / 'inputs'
    inputs.mkdir()
    for name in BROKERS:
        sp = inputs / (name + '-spec.json')
        write(sp, {'brokers': [by_name[name]]})
        stand = inputs / (name + '-stand.json')
        calibration = inputs / (name + '-calibration.json')
        subprocess.run([str(pwsh), '-NoProfile', '-File', str(repo / 'MqttBenchmark/scripts/NewNativeStand.ps1'),
            '-BrokerSpecPath', str(sp), '-StandPath', str(stand), '-CampaignPath', str(calibration),
            '-CpuMode', 'singleCorePinned', '-CpuSet', str(broker_cpu), '-Lifecycle', 'managed'], check=True)
        campaign = read(calibration)
        campaign.update(seed=20260909, messageBytes=[16, 256], qos=[0, 1, 2], publishers=[1, 16, 64, 128],
                        repeats=3, warmupSeconds=5, measurementSeconds=30, cooldownSeconds=5,
                        drainTimeoutSeconds=30, cpuThresholdPercent=args.cpu_guard)
        write(calibration, campaign)
        write(inputs / (name + '-validation.json'), dict(campaign, seed=20260910))
        write(inputs / (name + '-pilot.json'), dict(campaign, seed=20260908, messageBytes=[16], repeats=1, publishers=[128]))
    storage = base / 'evidence'
    (storage / 'attempts').mkdir(parents=True)
    adapter = repo / 'tools/campaign/accelerated-resume.py'
    plan = {'schemaVersion': 1, 'seriesId': base.name, 'controllerMode': 'linuxLocal',
        'createdAtUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'benchmarkRepository': str(repo / 'MqttBenchmark'), 'mqttYRepository': str(repo / 'mqtty'),
        'benchmarkCli': str(cli), 'simulatorCli': str(simulator),
        'simulatorExample': str(repo / 'mqtty/doc/examples/broker-fidelity.v3.json'),
        'calibratorCli': str(simulator), 'calibratorCliSha256': digest(simulator),
        'calibratorRepository': str(repo / 'mqtty'), 'archiveStorageRoot': str(storage),
        'archiveLinuxRoot': str(storage), 'archiveMount': actual[0], 'archiveDevice': actual[1],
        'systemDiskRoot': '/', 'rawControllerRoot': str(base / 'raw'), 'cpuGuardPercent': args.cpu_guard,
        'controllerCpuSet': ','.join(map(str, sorted(controller_cpus))), 'brokerCpuSet': str(broker_cpu),
        'excludedControllerSmtSibling': ','.join(map(str, sorted(siblings - {broker_cpu}))),
        'controllerMemoryMaxBytes': 8 * 1024**3, 'brokerMemoryMaxBytes': 4 * 1024**3,
        'loadGeneratorSampler': 'separate .NET process; shared OS monotonic clock; strict maximum interval 1.25s',
        'brokerOrder': list(BROKERS), 'expectedRealRuns': 576, 'expectedFidelityRuns': 576, 'expectedRelayRuns': 360,
        'simulationMeasurementSeconds': args.simulation_seconds, 'simulationFidelityWorkers': args.fidelity_workers,
        'simulationRelayWorkers': args.relay_workers, 'standShell': str(pwsh),
        'orchestrationAdapter': str(adapter), 'orchestrationAdapterSha256': digest(adapter),
        'loadGeneratorSamplerCli': str(cli), 'modelCapacityNote': 'declared model capacity; loopback capacity is not measured',
        'reportMachineDescription': f'Linux {os.uname().nodename}: контроллер и брокеры на одном хосте; MQTT через 127.0.0.1. '
            f'Брокер использует логический CPU {broker_cpu}, MemoryMax 4 GiB; контроллер — CPU '
            f'{",".join(map(str, sorted(controller_cpus)))}, MemoryMax 8 GiB. SMT siblings брокера '
            f'{",".join(map(str, sorted(siblings - {broker_cpu}))) or "отсутствуют"} исключены из контроллера. '
            'Фоновые процессы не перемещаются, эксклюзивность физического ядра не заявляется. '
            'Native managed lifecycle, singleHostSequential, singleCorePinned, без Docker. '
            'Windows при наличии используется только для управления. Модельная ёмкость 1 Gbit/s — допущение, а не измерение loopback.'}
    write(root / 'series.json', plan)
    write(root / 'transport.json', {'network': {'capacityBytesPerSecond': 125000000,
        'throughputWindowTicks': 100, 'queueCapacity': 100000, 'networkType': 'ethernet'},
        'transferTicksByBroker': {}, 'method': 'nearest integer one-way propagation ticks from half mean calibration MQTT echo RTT; tick=0.1ms; no control-data tuning',
        'capacityProvenance': 'explicit model assumption: 1 Gbit/s Ethernet; loopback capacity was not measured',
        'queueProvenance': 'explicit assumption: 100000 queued packets'})
    captures = [capture(command) for command in [['uname', '-a'], ['cat', '/etc/os-release'], ['lscpu'],
        ['free', '-h'], ['df', '-h'], ['ip', '-details', 'link'], ['ip', 'route'], ['timedatectl', 'status'],
        ['ps', '-eo', 'pid,comm,pcpu,pmem'], ['dotnet', '--list-runtimes'], [str(pwsh), '-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()']]]
    for entry in spec['brokers']:
        if entry.get('versionArgs'):
            captures.append(capture([entry['executable']] + entry['versionArgs']))
    published = {str(path): digest(path) for directory in (cli.parent, simulator.parent)
                 for path in sorted(directory.rglob('*')) if path.is_file()}
    host = {'captures': captures, 'smtSiblings': sorted(siblings), 'governor': capture(['cat', f'/sys/devices/system/cpu/cpu{broker_cpu}/cpufreq/scaling_governor']),
            'boostControls': {str(p): p.read_text().strip() if p.exists() else None for p in
                [pathlib.Path('/sys/devices/system/cpu/cpufreq/boost'), pathlib.Path('/sys/devices/system/cpu/intel_pstate/no_turbo')]}}
    interfaces = {}
    for interface in pathlib.Path('/sys/class/net').glob('*'):
        fields = {}
        for field in ('mtu', 'speed', 'operstate'):
            try:
                fields[field] = (interface / field).read_text().strip()
            except OSError:
                fields[field] = None
        interfaces[interface.name] = fields
    host['networkInterfaces'] = interfaces
    distributions = {p.relative_to(assets_root).as_posix(): digest(p) for p in sorted(assets_root.rglob('*'))
                     if p.is_file() and not any(part in ('data', 'log', 'logs', 'tmp') for part in p.relative_to(assets_root).parts)} if assets_root else {}
    write(root / 'environment.json', {'controller': host, 'brokerHost': host, 'sameHost': True,
        'sourceCommits': sources, 'publishedCliSha256': published,
        'brokerDistributionSha256': distributions,
        'brokerDistributionScope': 'Supplied series asset root; runtime directories data/log/logs/tmp excluded. System runtime executable hashes are in stand pins.',
        'reportPackages': subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True),
        'inputSha256': {p.name: digest(p) for p in sorted(inputs.glob('*.json'))}})
    print(str(root))


def launch(args):
    root = pathlib.Path(args.series).resolve()
    plan = read(root / 'series.json')
    actual = mount_identity(plan['archiveMount'])
    if actual[1] != plan['archiveDevice']:
        raise RuntimeError('Evidence disk SOURCE changed')
    if not root.is_relative_to(pathlib.Path(actual[0])):
        raise RuntimeError('Controller bundle must be on the evidence disk')
    previous = root / 'controller-service.json'
    if previous.exists() and subprocess.run(['systemctl', '--user', 'is-active', '--quiet', read(previous)['unit']]).returncode == 0:
        raise RuntimeError('A controller for this series is already active')
    if digest(plan['orchestrationAdapter']) != plan['orchestrationAdapterSha256']:
        raise RuntimeError('Orchestration adapter changed')
    base = root.parent
    temporary = pathlib.Path(plan['archiveMount']) / 'mqtty-evidence/tools/tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    os.environ.update(TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary),
        MPLCONFIGDIR=str(base / 'matplotlib-cache'), DOTNET_CLI_HOME=str(base / 'dotnet-home'),
        DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1', MQB_STAND_SHELL=plan['standShell'],
        MQB_GUARD_SAMPLER_CLI=plan['benchmarkCli'])
    unit = 'mqttbenchmark-controller-' + uuid.uuid4().hex + '.service'
    argv = [plan['orchestrationAdapter'], '--series', str(root), '--resume']
    flag = {'preflight': '--preflight-only', 'pilot': '--pilot-only', 'verify': '--verify-only'}.get(args.mode)
    if flag:
        argv.append(flag)
    payload = {'unit': unit, 'description': 'MqttBenchmark portable Linux campaign: ' + args.mode,
        'workingDirectory': str(root), 'executable': sys.executable, 'args': argv,
        'stdout': str(root / ('portable-' + args.mode + '.log')), 'stderr': str(root / ('portable-' + args.mode + '.err.log')),
        'memoryLimit': plan['controllerMemoryMaxBytes'], 'cpus': sorted(cpu_set(plan['controllerCpuSet']))}
    subprocess.run(['/bin/python3', str(pathlib.Path(plan['benchmarkRepository']) / 'scripts/LaunchNativeSystemd.py')],
        input=json.dumps(payload), text=True, check=True)
    write(previous, {'unit': unit, 'mode': args.mode, 'argv': [sys.executable] + argv,
                     'cpuSet': plan['controllerCpuSet'], 'memoryMaxBytes': plan['controllerMemoryMaxBytes']})
    print(unit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    for name in ('repository', 'series', 'spec', 'archive-mount', 'benchmark-cli', 'simulator-cli', 'pwsh', 'controller-cpus'):
        prep.add_argument('--' + name, required=True)
    prep.add_argument('--broker-cpu', type=int, required=True)
    prep.add_argument('--broker-assets-root', help='Optional immutable distribution/dependency snapshot under this series')
    prep.add_argument('--fidelity-workers', type=int, choices=range(1, 5), default=4)
    prep.add_argument('--relay-workers', type=int, choices=range(1, 5), default=4)
    prep.add_argument('--simulation-seconds', type=int, choices=(10, 30), default=10)
    prep.add_argument('--cpu-guard', type=float, default=99)
    start = sub.add_parser('launch')
    start.add_argument('--series', required=True, help='Path to the controller directory')
    start.add_argument('--mode', choices=('run', 'preflight', 'pilot', 'verify'), default='run')
    args = parser.parse_args()
    if args.command == 'prepare':
        if not 0 < args.cpu_guard <= 100:
            parser.error('CPU guard must be in (0,100]')
        prepare(args)
    else:
        launch(args)


if __name__ == '__main__':
    main()
