"""Resume the same campaign with four relay processes at a completed-stage boundary.

Never interrupt an active benchmark or simulation. Freeze/recheck the old controller
before replacement so that a state-file read cannot race with workload admission.
"""
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
import uuid


def eligible(state, boundary):
    return (state.get('status') == 'running' and boundary in state.get('completed', [])
            and not state.get('active') and not state.get('activeSimulations'))


def atomic_json(path, value):
    path = pathlib.Path(path)
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def storage_check(plan, root):
    mount = subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE', '-T', str(root)], text=True).split()
    if mount != [plan['archiveMount'], plan['archiveDevice'], 'ext4']:
        raise RuntimeError('External disk identity changed')
    if shutil.disk_usage(root).free < 20 * 1024**3 or shutil.disk_usage('/').free < 10 * 1024**3:
        raise RuntimeError('Insufficient disk space to resume')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--series', required=True)
    parser.add_argument('--boundary', default='Aedes/relay')
    args = parser.parse_args()
    root = pathlib.Path(args.series).resolve()
    audit = root / 'revision-transition/relay-four-workers'
    audit.mkdir(exist_ok=True)
    plan = read(root / 'series.json')
    service = read(root / 'controller-service.json')
    old_unit = service['unit']
    storage_check(plan, root)
    if not 1 <= int(plan['simulationFidelityWorkers']) <= 4:
        raise RuntimeError('Unexpected fidelity concurrency')
    if int(plan['simulationRelayWorkers']) != 4:
        atomic_json(audit / 'series.before.json', plan)
        plan['simulationRelayWorkers'] = 4
        plan['relayConcurrencyTransition'] = str(audit / 'status.json')
        atomic_json(root / 'series.json', plan)
    status = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'status': 'waiting-for-safe-boundary', 'boundary': args.boundary,
              'oldUnit': old_unit, 'relayWorkersBefore': 2, 'relayWorkersAfter': 4,
              'adapterSha256': plan['orchestrationAdapterSha256'],
              'handoffSourceSha256': digest(__file__),
              'policy': 'No interruption of active workloads; same model, profiles, seeds, windows and matrix'}
    atomic_json(audit / 'status.json', status)
    while True:
        state = read(root / 'state.json')
        if state.get('status') in ('technical-failure', 'completed'):
            status.update(status='not-switched', reason=state['status'])
            atomic_json(audit / 'status.json', status)
            return
        if read(root / 'controller-service.json')['unit'] != old_unit:
            raise RuntimeError('Another controller replacement already occurred')
        if not eligible(state, args.boundary):
            time.sleep(1)
            continue
        pid = int(subprocess.check_output(['systemctl', '--user', 'show', old_unit, '-p', 'MainPID', '--value'], text=True))
        if pid != state['controllerRuntime']['pid'] or pid <= 1:
            raise RuntimeError('Controller process identity mismatch')
        os.kill(pid, signal.SIGSTOP)
        frozen = True
        try:
            # Stop is asynchronous: wait for /proc to confirm before re-reading state.
            deadline = time.monotonic() + 5
            while (pathlib.Path('/proc') / str(pid) / 'stat').read_text().split(') ', 1)[1].split()[0] not in ('T', 't'):
                if time.monotonic() > deadline:
                    raise RuntimeError('Controller did not reach a stopped state')
                time.sleep(.02)
            state = read(root / 'state.json')
            children = (pathlib.Path('/proc') / str(pid) / 'task' / str(pid) / 'children').read_text().split()
            if not eligible(state, args.boundary) or children:
                os.kill(pid, signal.SIGCONT)
                frozen = False
                time.sleep(1)
                continue
            storage_check(plan, root)
            if digest(plan['orchestrationAdapter']) != plan['orchestrationAdapterSha256']:
                raise RuntimeError('Orchestration adapter changed')
            for broker, sha in state['profiles'].items():
                if digest(root / 'brokers' / broker / 'broker-calibration.v3.json') != sha:
                    raise RuntimeError('Frozen profile changed')
            atomic_json(audit / 'state.at-boundary.json', state)
            atomic_json(audit / 'controller-service.before.json', service)
            os.kill(pid, signal.SIGKILL)  # Only the owned, stopped controller with no children.
            frozen = False
            subprocess.run(['systemctl', '--user', 'stop', old_unit], check=True)
            break
        finally:
            if frozen:
                os.kill(pid, signal.SIGCONT)
    tools = pathlib.Path(plan['archiveMount']) / 'mqtty-evidence/tools'
    base = root.parent
    os.environ.update(TMPDIR=str(tools / 'tmp'), TMP=str(tools / 'tmp'), TEMP=str(tools / 'tmp'),
        MPLCONFIGDIR=str(base / 'matplotlib-cache'), DOTNET_CLI_HOME=str(base / 'dotnet-home'),
        DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1', MQB_STAND_SHELL=str(tools / 'pwsh/pwsh'),
        MQB_GUARD_SAMPLER_CLI=plan['benchmarkCli'])
    unit = 'mqttbenchmark-controller-' + uuid.uuid4().hex + '.service'
    argv = [plan['orchestrationAdapter'], '--series', str(root), '--resume']
    payload = {'unit': unit, 'description': 'MqttBenchmark campaign: four relay simulation processes',
        'workingDirectory': str(root), 'executable': str(tools / 'campaign-venv/bin/python'), 'args': argv,
        'stdout': str(root / 'relay-four-controller.log'), 'stderr': str(root / 'relay-four-controller.err.log'),
        'memoryLimit': plan['controllerMemoryMaxBytes'], 'cpus': [*range(1, 12), *range(13, 24)]}
    subprocess.run(['/bin/python3', str(pathlib.Path(plan['benchmarkRepository']) / 'scripts/LaunchNativeSystemd.py')],
        input=json.dumps(payload), text=True, check=True)
    atomic_json(root / 'controller-service.json', {'unit': unit, 'mode': 'run',
        'argv': [payload['executable']] + argv, 'cpuSet': plan['controllerCpuSet'],
        'memoryMaxBytes': plan['controllerMemoryMaxBytes'], 'adapterSha256': plan['orchestrationAdapterSha256']})
    status.update(status='switched', utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), newUnit=unit)
    atomic_json(audit / 'status.json', status)


if __name__ == '__main__':
    main()
