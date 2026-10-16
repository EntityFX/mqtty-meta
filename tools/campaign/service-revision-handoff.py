"""Switch to a separate empirical-service cohort at an idle controller boundary."""
import argparse
import collections
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

BROKERS = ('Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX')


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = pathlib.Path(path)
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def sha(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def idle(state):
    return state.get('status') == 'running' and not state.get('active') and not state.get('activeSimulations')


def full_native(results, broker):
    expected = {f'{broker}.m{size}.q{qos}.p{publishers}.r{repeat}' for size in (16, 256)
                for qos in (0, 1, 2) for publishers in (1, 16, 64, 128) for repeat in (1, 2, 3)}
    actual = collections.Counter(r['key']['key'] for r in results if r.get('status') == 'success')
    return set(actual) == expected and all(count == 1 for count in actual.values())


def storage(plan):
    actual = subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE', '-T', plan['archiveMount']], text=True).split()
    if actual != [plan['archiveMount'], plan['archiveDevice'], 'ext4']:
        raise RuntimeError('Evidence mount identity changed')
    if shutil.disk_usage(plan['archiveMount']).free < 20 * 1024**3 or shutil.disk_usage('/').free < 10 * 1024**3:
        raise RuntimeError('Free disk space guard')


def adopt(old, new):
    """Borrow only whole native matrices and matching profiles, never old simulations."""
    old, new = pathlib.Path(old).resolve(), pathlib.Path(new).resolve()
    plan, previous = read(new / 'series.json'), read(old / 'series.json')
    if read(new / 'state.json')['status'] != 'prepared' or plan['deadlineUtc'] != previous['deadlineUtc']:
        raise RuntimeError('Need prepared cohort with the unchanged absolute deadline')
    storage(plan)
    evidence_mount = pathlib.Path(plan['archiveMount']).resolve()
    if not new.is_relative_to(evidence_mount) or new.parent == old.parent:
        raise RuntimeError('Need a separate destination on the evidence mount')
    state = read(new / 'state.json')
    old_state = read(old / 'state.json')
    origins = []
    native_windows = {}
    for broker in BROKERS:
        if sha(new / 'inputs' / (broker + '-stand.json')) != sha(old / 'inputs' / (broker + '-stand.json')):
            raise RuntimeError('Borrowed native stand differs: ' + broker)
        # Observation reserve changes only observer coverage, not the 20-second load.
        for stage in ('calibration', 'validation'):
            config_path = new / 'inputs' / f'{broker}-{stage}.json'
            config = read(config_path)
            config['telemetryCaptureReserveSeconds'] = 18
            write(config_path, config)
            native_windows[broker + '/' + stage] = 18
            source = pathlib.Path(previous['rawControllerRoot']) / f'{broker}-{stage}'
            result_paths = list(source.glob('attempts/*/attempt-*/result.json'))
            results = [read(p) for p in result_paths]
            if not full_native(results, broker):
                origins.append({'broker': broker, 'stage': stage, 'borrowed': False,
                    'successfulAttemptsRetainedAtSource': sum(r.get('status') == 'success' for r in results),
                    'reason': 'Not a whole 72-key native matrix; no identities or partial repeats are merged'})
                continue
            identity = read(source / 'campaign.json')
            original_config = old / 'inputs' / f'{broker}-{stage}.json'
            if identity['configSha256'] != sha(original_config) or identity['standSha256'] != sha(old / 'inputs' / (broker + '-stand.json')):
                raise RuntimeError('Borrowed journal/input identity differs')
            for result_path in result_paths:
                if read(result_path)['identity'] != identity:
                    raise RuntimeError('Borrowed result/journal identity differs')
                receipt = result_path.with_name('archive.json')
                metadata = read(receipt)
                if sha(metadata['archivePath']) != metadata['sha256']:
                    raise RuntimeError('Borrowed native archive digest differs')
            shutil.copy2(original_config, config_path)
            native_windows[broker + '/' + stage] = read(config_path)['telemetryCaptureReserveSeconds']
            target = pathlib.Path(plan['rawControllerRoot']) / source.name
            if target.exists():
                raise RuntimeError('Borrowed native target already exists')
            shutil.copytree(source, target)
            observed = old / 'brokers' / broker / stage
            if observed.exists():
                shutil.copytree(observed, new / 'brokers' / broker / stage)
            verification = observed / 'archive-verification.json'
            observations = observed / 'broker-observations.v3.json'
            verified = (verification.exists() and observations.exists()
                and read(verification).get('verified', False)
                and read(verification).get('originalSha256') == sha(observations))
            if verified:
                state['completed'].append(broker + '/' + stage)
            origins.append({'broker': broker, 'stage': stage, 'borrowed': True,
                'rawSource': str(source), 'identity': identity, 'verifiedAtSource': verified,
                'note': 'Unverified aggregate will be verified by normal matrix resume/aggregate/restore before profiling'})
        if broker + '/pilot' in old_state.get('completed', []):
            state['completed'].append(broker + '/pilot')
            pilot = pathlib.Path(previous['rawControllerRoot']) / (broker + '-pilot')
            if pilot.exists():
                shutil.copytree(pilot, pathlib.Path(plan['rawControllerRoot']) / pilot.name)
            origins.append({'broker': broker, 'stage': 'pilot', 'borrowed': True, 'rawSource': str(pilot)})
        profile = old / 'brokers' / broker / 'broker-calibration.v3.json'
        if (any(x.get('borrowed') and x['broker'] == broker and x['stage'] == 'calibration' for x in origins)
                and profile.exists() and broker in old_state.get('profiles', {})):
            digest = sha(profile)
            if old_state.get('profiles', {}).get(broker) != digest:
                raise RuntimeError('Borrowed frozen profile changed')
            destination = new / 'brokers' / broker / profile.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(profile, destination)
            state.setdefault('profiles', {})[broker] = digest
    plan['borrowedNativeSource'] = str(old)
    # Calibration code is unchanged by the service revision. Retain its exact
    # pinned revision so copied and newly generated profiles remain compatible
    # for Mixed without rewriting the copied profile's provenance or digest.
    for name in ('calibratorCli', 'calibratorRepository', 'calibratorCliSha256'):
        plan[name] = previous[name]
    plan['nativeTelemetryCaptureReserveSeconds'] = native_windows
    plan['modelRevisionNote'] += ' Профили и полные native-матрицы predecessor заимствуются без изменения SHA/identity; старые simulations не заимствуются.'
    plan['programAmendmentNote'] += ' Новые native-этапы имеют reserve телеметрии 18 с (envelope 54 с) после ошибок completion fence; заимствованные матрицы сохраняют свой исходный reserve. Неполные серии не объединяются.'
    plan['programAmendmentNote'] = plan['programAmendmentNote'].replace('capture envelope 39 с.', 'capture envelope новых native 54 с; заимствованные сохраняют исходный envelope.')
    write(new / 'series.json', plan)
    environment = read(new / 'environment.json')
    environment['inputSha256'] = {str(p.relative_to(new)): sha(p) for p in (new / 'inputs').glob('*.json')}
    write(new / 'environment.json', environment)
    frozen = read(new / 'frozen-files.json')
    frozen.update({str(p): sha(p) for p in (new / 'inputs').glob('*.json')})
    frozen.update({str(p): sha(p) for p in pathlib.Path(plan['calibratorCli']).parent.glob('*.dll')})
    write(new / 'frozen-files.json', frozen)
    write(new / 'state.json', state)
    write(new / 'native-origins.json', origins)
    return origins


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', required=True)
    parser.add_argument('--series', required=True)
    args = parser.parse_args()
    old, new = pathlib.Path(args.previous).resolve(), pathlib.Path(args.series).resolve()
    plan = read(new / 'series.json')
    deadline = datetime.datetime.fromisoformat(plan['workloadDeadlineUtc'])
    old_unit = read(old / 'controller-service.json')['unit']
    def status(value, **extra):
        write(new / 'handoff-status.json', {'status': value, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'oldUnit': old_unit, **extra})
    status('waiting-for-idle-boundary')
    try:
        while True:
            if datetime.datetime.now(datetime.timezone.utc) >= deadline:
                status('not-started-deadline-exhausted')
                return
            state = read(old / 'state.json')
            if not idle(state):
                if state.get('status') not in ('running', 'prepared'):
                    pid = int(subprocess.check_output(['systemctl', '--user', 'show', old_unit, '-p', 'MainPID', '--value'], text=True).strip() or '0')
                    if pid == 0:
                        break
                time.sleep(.5)
                continue
            pid = int(subprocess.check_output(['systemctl', '--user', 'show', old_unit, '-p', 'MainPID', '--value'], text=True))
            if pid <= 1 or pid != state['controllerRuntime']['pid']:
                raise RuntimeError('Owned controller process identity differs')
            os.kill(pid, signal.SIGSTOP)
            frozen = True
            try:
                until = time.monotonic() + 5
                while (pathlib.Path('/proc') / str(pid) / 'stat').read_text().split(') ', 1)[1].split()[0] not in ('T', 't'):
                    if time.monotonic() >= until:
                        raise RuntimeError('Controller did not freeze')
                    time.sleep(.02)
                check = read(old / 'state.json')
                children = (pathlib.Path('/proc') / str(pid) / 'task' / str(pid) / 'children').read_text().split()
                if not idle(check) or children:
                    os.kill(pid, signal.SIGCONT)
                    frozen = False
                    time.sleep(.2)
                    continue
                storage(plan)
                write(new / 'previous-state.at-boundary.json', check)
                status('switching-at-idle-boundary', oldPid=pid)
                os.kill(pid, signal.SIGKILL)  # Owned stopped controller; no active children.
                frozen = False
                subprocess.run(['systemctl', '--user', 'stop', old_unit], check=True)
                break
            finally:
                if frozen:
                    os.kill(pid, signal.SIGCONT)
        previous_state = read(old / 'state.json')
        previous_state.update(status='superseded', supersededBy=str(new),
            supersededReason='Explicit model-revision deployment at an idle boundary; source evidence retained')
        write(old / 'state.json', previous_state)
        adopt(old, new)
        tools = pathlib.Path(plan['archiveMount']) / 'mqtty-evidence/tools'
        os.environ.update(MQB_STAND_SHELL=str(tools / 'pwsh/pwsh'), MQB_GUARD_SAMPLER_CLI=plan['benchmarkCli'],
            TMPDIR=str(new / 'tmp'), DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1')
        (new / 'tmp').mkdir(exist_ok=True)
        unit = 'mqttbenchmark-service-' + uuid.uuid4().hex + '.service'
        python = str(tools / 'campaign-venv/bin/python')
        runner = plan['orchestrationAdapter']
        payload = {'unit': unit, 'description': 'MqttBenchmark empirical-service cohort; stop 08:00 MSK',
            'workingDirectory': str(new), 'memoryMaxBytes': plan['controllerMemoryMaxBytes'],
            'cpus': list(range(1, 12)) + list(range(13, 24)), 'deadlineUtc': plan['deadlineUtc'],
            'argv': [python, runner, '--series', str(new), '--resume'],
            'stdout': str(new / 'logs/controller.stdout.log'), 'stderr': str(new / 'logs/controller.stderr.log'),
            'cleanupArgv': [python, runner, 'cleanup', '--series', str(new)]}
        write(new / 'controller-service.json', payload)
        subprocess.run(['/bin/python3', str(new / 'source/launch-owned-job.py'), str(new / 'controller-service.json')], check=True)
        status('switched', newUnit=unit)
    except BaseException as error:
        status('failed', reason=repr(error))
        raise


if __name__ == '__main__':
    main()
