"""Wait for an explicit safe baseline boundary, then run one bounded verification command."""
import datetime
import json
import pathlib
import subprocess
import sys
import time

plan = json.loads(pathlib.Path(sys.argv[1]).read_text())
status = pathlib.Path(plan['statusPath'])
deadline = datetime.datetime.fromisoformat(plan['deadlineUtc'])
child = None
def save(value, **extra):
    temporary = status.with_suffix('.new')
    temporary.write_text(json.dumps({'status': value, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), **extra}, indent=2))
    temporary.replace(status)
try:
    save('waiting-for-baseline-boundary')
    while True:
        if datetime.datetime.now(datetime.timezone.utc) >= deadline:
            save('not-started-deadline-exhausted')
            sys.exit(0)
        source = json.loads(pathlib.Path(plan['baselineState']).read_text())
        completed = set(source['completed'])
        safe = all(broker + '/validation' in completed for broker in ('Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX'))
        if plan['boundary'] == 'complete':
            safe = source['status'] in ('completed', 'completed-with-failures', 'time-budget-exhausted')
        if safe:
            break
        time.sleep(10)
    save('running', argv=plan['argv'])
    child = subprocess.Popen(plan['argv'])
    while child.poll() is None:
        if datetime.datetime.now(datetime.timezone.utc) >= deadline:
            raise TimeoutError('Original deadline reached')
        time.sleep(1)
    save('completed' if child.returncode == 0 else 'failed', exitCode=child.returncode)
    if child.returncode:
        sys.exit(child.returncode)
except BaseException as error:
    if child is not None and child.poll() is None:
        child.send_signal(__import__('signal').SIGINT)
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    if not isinstance(error, SystemExit):
        save('interrupted', reason=str(error))
    raise
