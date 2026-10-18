"""Repair instrumentation at an owned idle boundary; never replace active inputs."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import subprocess
import time
import uuid


def read(p):
    return json.loads(pathlib.Path(p).read_text(encoding='utf-8-sig'))


def write(p, value):
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.with_suffix(p.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(p)


def sha(p):
    with pathlib.Path(p).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def complete_matrix(path):
    successes = []
    for p in path.glob('attempts/*/attempt-*/result.json'):
        row = read(p)
        if row.get('status') == 'success' and (p.parent / 'sha256.json').is_file() and (p.parent / 'archive.json').is_file():
            successes.append(row['key']['key'])
    broker = path.name.split('-')[0]
    expected = {f'{broker}.m{b}.q{q}.p{n}.r{rep}' for b in (16, 256) for q in (0, 1, 2)
                for n in (1, 16, 64, 128) for rep in (1, 2, 3)}
    return len(successes) == 72 and set(successes) == expected


def prepare(old, new, patches, source_state):
    plan = read(old / 'series.json')
    revision = read(patches / 'manifest.json')
    for name, expected in revision.get('files', {}).items():
        if sha(patches / name) != expected:
            raise RuntimeError('Repair input changed: ' + name)
    for p, expected in read(old / 'frozen-files.json').items():
        if sha(p) != expected:
            raise RuntimeError('Source frozen file changed: ' + p)
    if new.exists():
        raise RuntimeError('Repair destination already exists')
    new.mkdir(parents=True)
    # Everything here is a compact controller bundle; large model outputs remain
    # immutable in the original evidence directories referenced by each index.
    for name in ('inputs', 'brokers', 'mixed', 'source', 'reports'):
        if (old / name).exists():
            shutil.copytree(old / name, new / name)
    for name in ('environment.json', 'transport.json', 'native-origins.json'):
        if (old / name).exists():
            shutil.copy2(old / name, new / name)
    (new / 'source').mkdir(exist_ok=True)
    (new / 'logs').mkdir()
    state = json.loads(json.dumps(source_state))
    old_raw, new_raw = pathlib.Path(plan['rawControllerRoot']), new.parent / 'raw'
    excluded = []
    for raw in old_raw.iterdir():
        if not raw.is_dir():
            continue
        if raw.name == 'ActiveMQ-calibration' and not complete_matrix(raw):
            excluded.append({'source': str(raw), 'reason': 'Incomplete matrix; retained at source, rerun separately with fence-driven telemetry'})
            continue
        parts = raw.name.split('-')
        if len(parts) == 2 and parts[1] in ('calibration', 'validation') and '/'.join(parts) in state['completed'] and not complete_matrix(raw):
            raise RuntimeError('Completed source matrix is not whole and sealed: ' + str(raw))
        shutil.copytree(raw, new_raw / raw.name)
    # Unfinished config files refer to the old profile paths. Keep only inputs of
    # indexed successful jobs; new jobs receive new configs in the new cohort.
    for index in new.glob('**/simulations/**/index.json'):
        completed_keys = {r['key'] for r in read(index)['runs']}
        for config in index.parent.glob('m*.json'):
            if config.stem not in completed_keys:
                config.unlink()  # Only this newly created copy; source is intact.
    for broker, digest in state.get('profiles', {}).items():
        if sha(new / 'brokers' / broker / 'broker-calibration.v3.json') != digest:
            raise RuntimeError('Frozen profile changed: ' + broker)
    state['status'] = 'prepared'
    for key in ('active', 'activeSimulations', 'controllerRuntime', 'simulationResources', 'finishedAtUtc', 'budgetReason'):
        state.pop(key, None)
    # Old errors remain in source-state.json and the transition manifest. Stage
    # failures in the new cohort refer only to its own attempts.
    state.pop('stageFailures', None)
    state.pop('reportErrors', None)
    for broker in ('Mosquitto', 'ActiveMQ', 'EMQX', 'MqttRelay-Mixed'):
        if broker + '/relay' not in state['completed']:
            state['brokers'][broker] = {'stage': 'pending', 'status': 'не начат'}
    for name in ('compact-campaign.py', 'start-repaired-controller.py'):
        shutil.copy2(patches / name, new / 'source' / name)
    for name in ('run.py', 'BrokerStand.ps1', 'RemoteNativeStand.ps1'):
        shutil.copy2(patches / name, new / 'source' / name)
    plan.update(seriesId=new.parent.name, rawControllerRoot=str(new_raw),
        archiveStorageRoot=str(new.parent / 'evidence'), archiveLinuxRoot=str(new.parent / 'evidence'),
        orchestrationAdapter=str(new / 'source/compact-campaign.py'),
        orchestrationAdapterSha256=sha(new / 'source/compact-campaign.py'),
        campaignRunnerModule=str(new / 'source/run.py'), campaignRunnerModuleSha256=sha(new / 'source/run.py'),
        standScript=str(new / 'source/BrokerStand.ps1'),
        instrumentationRevision=revision,
        repairPredecessor=str(old))
    now = datetime.datetime.now(datetime.timezone.utc)
    plan['predecessorStartedAtUtc'] = plan.get('startedAtUtc')
    plan['createdAtUtc'] = plan['startedAtUtc'] = now.isoformat()
    plan['budgetSeconds'] = int((datetime.datetime.fromisoformat(plan['deadlineUtc']) - now).total_seconds())
    plan['workloadBudgetSeconds'] = int((datetime.datetime.fromisoformat(plan['workloadDeadlineUtc']) - now).total_seconds()) if plan.get('workloadDeadlineUtc') else plan['budgetSeconds'] - 900
    plan['programAmendmentNote'] += ' Separate instrumentation-repair cohort: native capture remains active until the workload fence, within a bounded maximum. Prior successful matrices/simulations retained with original identities; incomplete ActiveMQ calibration is not merged. Model, profiles and fidelity thresholds unchanged.'
    frozen = read(old / 'frozen-files.json')
    for p in (new / 'source').iterdir():
        if p.is_file():
            frozen[str(p)] = sha(p)
    for p in (new / 'inputs').iterdir():
        if p.is_file():
            frozen[str(p)] = sha(p)
    write(new / 'frozen-files.json', frozen)
    write(new / 'series.json', plan)
    write(new / 'state.json', state)
    write(new / 'source-state.json', source_state)
    write(new / 'repair-transition.json', {'source': str(old), 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'sourceStateSha256': sha(new / 'source-state.json'), 'excludedIncompleteMatrices': excluded,
        'originalDeadlineUtc': plan['deadlineUtc'], 'profileSha256': state.get('profiles', {}),
        'measurementCliUnchanged': True, 'simulatorUnchanged': True,
        'nativeControllerRevision': plan['instrumentationRevision'],
        'reusedSimulationScope': 'Same simulator/profile bytes and inputs; original indices and output paths retained'})
    return plan


def main():
    parser = argparse.ArgumentParser()
    for name in ('source', 'destination', 'patches'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    old, new, patches = [pathlib.Path(x).resolve() for x in (args.source, args.destination, args.patches)]
    original = read(old / 'series.json')
    mount = pathlib.Path(original['archiveMount']).resolve()
    if not new.parent.is_relative_to(mount) or new.parent == mount or new == old:
        raise RuntimeError('Destination escaped evidence mount')
    unit = read(old / 'controller-service.json')['unit']
    status_path = patches / 'handoff-status.json'
    def status(value, **extra):
        write(status_path, {'status': value, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), **extra})
    status('waiting-for-idle-boundary', sourceUnit=unit)
    frozen_pid = None
    try:
        while True:
            if datetime.datetime.now(datetime.timezone.utc) >= datetime.datetime.fromisoformat(original['workloadDeadlineUtc']):
                status('not-started-deadline-exhausted')
                return
            state = read(old / 'state.json')
            pid = int(subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'], text=True).strip() or 0)
            if not pid:
                break
            if state.get('active') or state.get('activeSimulations'):
                time.sleep(.5)
                continue
            if pid != state['controllerRuntime']['pid']:
                raise RuntimeError('Owned controller process identity changed')
            os.kill(pid, signal.SIGSTOP)
            frozen_pid = pid
            until = time.monotonic() + 5
            while (pathlib.Path('/proc') / str(pid) / 'stat').read_text().split(') ', 1)[1].split()[0] not in ('T', 't'):
                if time.monotonic() >= until:
                    raise RuntimeError('Owned controller did not enter stopped state')
                time.sleep(.02)
            check = read(old / 'state.json')
            children = (pathlib.Path('/proc') / str(pid) / 'task' / str(pid) / 'children').read_text().split()
            if check.get('active') or check.get('activeSimulations') or children:
                os.kill(pid, signal.SIGCONT)
                frozen_pid = None
                time.sleep(.2)
                continue
            state = check
            break
        actual = subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE', '-T', str(mount)], text=True).split()
        if actual != [str(mount), original['archiveDevice'], 'ext4'] or shutil.disk_usage(mount).free < 24 * 2**30:
            raise RuntimeError('Evidence mount missing or insufficient space')
        status('preparing-at-idle-boundary')
        plan = prepare(old, new, patches, state)
        if frozen_pid:
            os.kill(frozen_pid, signal.SIGKILL)  # Owned stopped parent, no child workload.
            frozen_pid = None
        subprocess.run(['systemctl', '--user', 'stop', unit], check=True)
        state.update(status='superseded', supersededBy=str(new), supersededReason='Instrumentation repair at an owned idle boundary')
        write(old / 'state.json', state)
        tools = mount / 'mqtty-evidence/tools'
        os.environ.update(MQB_STAND_SHELL=str(tools / 'pwsh/pwsh'), MQB_GUARD_SAMPLER_CLI=plan['benchmarkCli'],
            MQB_NATIVE_CAPTURE_UNTIL_FENCE='1', MQB_NATIVE_REPOSITORY_ROOT=plan['benchmarkRepository'],
            TMPDIR=str(new / 'tmp'), DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1')
        (new / 'tmp').mkdir()
        runner = str(new / 'source/compact-campaign.py')
        python = str(tools / 'campaign-venv/bin/python')
        payload = {'unit': 'mqttbenchmark-repaired-' + uuid.uuid4().hex + '.service',
            'description': 'MqttBenchmark instrumentation repair; original absolute deadline retained',
            'workingDirectory': str(new), 'memoryMaxBytes': plan['controllerMemoryMaxBytes'],
            'cpus': state['controllerRuntime']['affinity'], 'deadlineUtc': plan['deadlineUtc'],
            'argv': [python, str(new / 'source/start-repaired-controller.py'), '--series', str(new)],
            'stdout': str(new / 'logs/controller.stdout.log'), 'stderr': str(new / 'logs/controller.stderr.log'),
            'cleanupArgv': [python, runner, 'cleanup', '--series', str(new)]}
        write(new / 'controller-service.json', payload)
        subprocess.run(['/bin/python3', str(new / 'source/launch-owned-job.py'), str(new / 'controller-service.json')], check=True)
        status('switched', newUnit=payload['unit'], destination=str(new))
    except BaseException as error:
        status('failed', reason=repr(error))
        raise
    finally:
        if frozen_pid:
            os.kill(frozen_pid, signal.SIGCONT)


if __name__ == '__main__':
    main()
