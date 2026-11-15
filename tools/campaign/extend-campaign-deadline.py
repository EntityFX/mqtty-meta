"""Extend an owned running controller at an idle boundary; keep workload inputs intact.

The supervisor must be launched independently of the controller being replaced.
It never interrupts a benchmark or simulator child to apply the new deadline.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import uuid


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def idle(state):
    return state.get('status') == 'running' and not state.get('active') and not state.get('activeSimulations')


def extended_plan(plan, deadline, reason, now):
    deadline = dt.datetime.fromisoformat(deadline)
    old = dt.datetime.fromisoformat(plan['deadlineUtc'])
    if deadline.tzinfo is None or deadline <= old or deadline <= now:
        raise ValueError('New deadline must have a time zone and be later than the current deadline and now')
    reserve = old - dt.datetime.fromisoformat(plan['workloadDeadlineUtc'])
    if reserve.total_seconds() <= 0 or deadline - reserve <= now:
        raise ValueError('No time remains before the workload deadline')
    result = json.loads(json.dumps(plan))
    started = dt.datetime.fromisoformat(plan['startedAtUtc'])
    result.update(deadlineUtc=deadline.isoformat(), workloadDeadlineUtc=(deadline-reserve).isoformat(),
                  budgetSeconds=int((deadline-started).total_seconds()),
                  workloadBudgetSeconds=int((deadline-reserve-started).total_seconds()),
                  runtimeMaxSeconds=int((deadline-now).total_seconds())-60)
    result.setdefault('resumptions', []).append({
        'resumedAtUtc': now.isoformat(), 'previousDeadlineUtc': plan['deadlineUtc'],
        'newDeadlineUtc': result['deadlineUtc'], 'reason': reason,
        'kind': 'deadline-only-extension',
        'policy': 'Same workload, model, profiles, acceptance criteria and completed run indices; no active job interrupted'})
    return result


def storage(plan, root):
    observed = subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE', '-T', str(root)], text=True).split()
    if observed != [plan['archiveMount'], plan['archiveDevice'], 'ext4']:
        raise RuntimeError('Evidence mount identity changed')
    if shutil.disk_usage(root).free < 20*2**30 or shutil.disk_usage('/').free < 10*2**30:
        raise RuntimeError('Insufficient free space')


def pid_for(unit):
    return int(subprocess.check_output(['systemctl', '--user', 'show', unit, '-p', 'MainPID', '--value'], text=True).strip() or 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--series', type=Path, required=True)
    parser.add_argument('--deadline', required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--reason', required=True)
    args = parser.parse_args()
    root, audit = args.series.resolve(), args.audit.resolve()
    plan = read(root/'series.json')
    extended_plan(plan, args.deadline, args.reason, dt.datetime.now(dt.timezone.utc))
    if not audit.is_relative_to(root) or audit == root:
        raise RuntimeError('Audit directory must be inside the controller directory')
    storage(plan, root)
    audit.mkdir(parents=True, exist_ok=False)
    old_service = read(root/'controller-service.json')
    old_unit = old_service['unit']
    write(audit/'request.json', {'requestedUtc': dt.datetime.now(dt.timezone.utc).isoformat(),
          'oldDeadlineUtc': plan['deadlineUtc'], 'newDeadlineUtc': args.deadline,
          'reason': args.reason, 'supervisorSha256': digest(__file__), 'oldUnit': old_unit})

    def status(value, **extra):
        write(audit/'status.json', {'status': value, 'utc': dt.datetime.now(dt.timezone.utc).isoformat(), **extra})

    status('waiting-for-idle-boundary')
    stopped_pid = None
    try:
        while True:
            if dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(plan['workloadDeadlineUtc']):
                raise RuntimeError('Original workload deadline reached before an idle boundary')
            state = read(root/'state.json')
            if state.get('status') == 'completed':
                status('not-needed-already-completed')
                return
            if read(root/'controller-service.json')['unit'] != old_unit:
                raise RuntimeError('Another controller transition changed the owner')
            if not idle(state):
                if state.get('status') != 'running':
                    raise RuntimeError('Controller no longer running: ' + str(state.get('status')))
                time.sleep(.2)
                continue
            pid = pid_for(old_unit)
            if pid <= 1 or pid != state['controllerRuntime']['pid']:
                raise RuntimeError('Controller process identity differs from the owned unit')
            # Stop only the parent. Children keep running if workload admission raced.
            os.kill(pid, signal.SIGSTOP)
            stopped_pid = pid
            until = time.monotonic()+5
            while (Path('/proc')/str(pid)/'stat').read_text().split(') ', 1)[1].split()[0] not in ('T', 't'):
                if time.monotonic() > until:
                    raise RuntimeError('Controller did not stop')
                time.sleep(.02)
            state = read(root/'state.json')
            children = (Path('/proc')/str(pid)/'task'/str(pid)/'children').read_text().split()
            if not idle(state) or children:
                os.kill(pid, signal.SIGCONT)
                stopped_pid = None
                time.sleep(.2)
                continue
            if pid_for(old_unit) != pid or read(root/'series.json') != plan:
                raise RuntimeError('Owner or plan changed during transition')
            storage(plan, root)
            frozen = read(root/'frozen-files.json')
            if str(root/'series.json') in frozen:
                raise RuntimeError('Plan is sealed; explicit replacement protocol required')
            for path, expected in frozen.items():
                if digest(path) != expected:
                    raise RuntimeError('Frozen input changed: ' + path)
            for broker, expected in state.get('profiles', {}).items():
                if digest(root/'brokers'/broker/'broker-calibration.v3.json') != expected:
                    raise RuntimeError('Profile changed: ' + broker)
            # Preserve the running controller environment in memory only, never in evidence.
            environ = {}
            for item in (Path('/proc')/str(pid)/'environ').read_bytes().split(b'\0'):
                if b'=' in item:
                    key, value = item.split(b'=', 1)
                    environ[os.fsdecode(key)] = os.fsdecode(value)
            if environ.get('MQB_NATIVE_CAPTURE_UNTIL_FENCE') != '1':
                raise RuntimeError('Required native capture environment is absent')
            now = dt.datetime.now(dt.timezone.utc)
            updated = extended_plan(plan, args.deadline, args.reason, now)
            write(audit/'series.before.json', plan)
            write(audit/'state.at-boundary.json', state)
            write(audit/'controller-service.before.json', old_service)
            write(audit/'frozen-files.before.json', frozen)
            runner = root/'source/compact-campaign.py'
            python = old_service['argv'][0]
            payload = {**old_service, 'unit': 'mqttbenchmark-extended-'+uuid.uuid4().hex+'.service',
                       'description': 'MqttBenchmark user-authorized deadline extension',
                       'deadlineUtc': updated['deadlineUtc'],
                       'argv': [python, str(runner), '--series', str(root), '--resume'],
                       'cleanupArgv': [python, str(runner), 'cleanup', '--series', str(root)]}
            write(audit/'controller-service.after.json', payload)
            status('switching-at-idle-boundary', oldPid=pid)
            # All validation and reviewable inputs prepared before stopping the old unit.
            os.kill(pid, signal.SIGKILL)
            stopped_pid = None
            subprocess.run(['systemctl', '--user', 'stop', old_unit], check=True)
            write(root/'series.json', updated)
            write(root/'controller-service.json', payload)
            subprocess.run(['/bin/python3', str(root/'source/launch-owned-job.py'),
                            str(root/'controller-service.json')], env=environ, check=True)
            until = time.monotonic()+30
            while time.monotonic() < until:
                current = read(root/'state.json')
                new_pid = pid_for(payload['unit'])
                if new_pid > 1 and current.get('controllerRuntime', {}).get('pid') == new_pid and current.get('deadlineUtc') == updated['deadlineUtc']:
                    for broker, expected in state.get('profiles', {}).items():
                        if digest(root/'brokers'/broker/'broker-calibration.v3.json') != expected:
                            raise RuntimeError('Profile changed on resume: '+broker)
                    status('extended', oldUnit=old_unit, newUnit=payload['unit'],
                           oldDeadlineUtc=plan['deadlineUtc'], newDeadlineUtc=updated['deadlineUtc'],
                           workloadDeadlineUtc=updated['workloadDeadlineUtc'],
                           preservedCompletedStages=state['completed'], profileSha256=state.get('profiles', {}))
                    return
                time.sleep(.2)
            raise RuntimeError('New controller did not confirm the extended deadline; inspect owned service logs')
    except BaseException as error:
        status('failed', reason=repr(error))
        raise
    finally:
        if stopped_pid:
            os.kill(stopped_pid, signal.SIGCONT)


if __name__ == '__main__':
    main()
