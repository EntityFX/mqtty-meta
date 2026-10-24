"""Separate bounded attempt with official Biber 2.17; no global installation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[4]
evidence = Path(__file__).parent
spec = importlib.util.spec_from_file_location('build_audit', root / 'tools/dissertation/check_dissertation_build.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
base = root / 'MqttBenchmark/artifacts/dissertation-build-v1'
snapshot = base / 'main-source-002-retry'
output = base / 'output-003'
output.mkdir(exist_ok=False)
source = root / 'diss'
paths = audit.tracked_inputs(source)
before = {p.relative_to(source).as_posix(): audit.sha(p) for p in paths}
for name, digest in before.items():
    if name.startswith('latex/'):
        continue
    if audit.sha(snapshot / name) != digest:
        raise ValueError('Isolated input snapshot differs: ' + name)
env = os.environ.copy()
env['PATH'] = str(base / 'portable') + os.pathsep + env['PATH']
env['TECTONIC_CACHE_DIR'] = str(base / 'cache')
env['TEMP'] = env['TMP'] = str(base / 'temp')
env['PAR_GLOBAL_TMPDIR'] = str(base / 'temp')
command = [str(base / 'portable/tectonic.exe'), '--untrusted', '--keep-logs',
           '--keep-intermediates', '--print', '--color', 'never',
           '--outdir', str(output), 'dissertation.tex']
record = {'command': command, 'cwd': str(snapshot), 'output': str(output),
          'environmentOverrides': {k: env[k] for k in ('TECTONIC_CACHE_DIR', 'TEMP', 'TMP', 'PAR_GLOBAL_TMPDIR')},
          'processOnlyPathPrepend': str(base / 'portable'), 'sourceManifest': before,
          'sameInputSnapshotAsAttempt002': True, 'excludedAlternativeTree': 'latex/',
          'timeLimitSeconds': 300, 'watchdogBytes': 285_000_000,
          'totalBudgetBytes': 300_000_000, 'status': 'running', 'step37Accepted': False}
started = time.monotonic()
with (evidence / 'portable-003.stdout.log').open('wb') as stream:
    process = subprocess.Popen(command, cwd=snapshot, env=env,
                               stdout=stream, stderr=subprocess.STDOUT)
    while process.poll() is None:
        size = sum(p.stat().st_size for p in base.rglob('*') if p.is_file())
        elapsed = time.monotonic() - started
        if elapsed > 300 or size > 285_000_000:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            record['status'] = 'watchdogTimeout' if elapsed > 300 else 'watchdogStorage'
            break
        time.sleep(.1)
    record['exitCode'] = process.wait(timeout=10)
if record['status'] == 'running':
    record['status'] = 'processExited'
record['elapsedSeconds'] = time.monotonic() - started
record['totalLocalBytes'] = sum(p.stat().st_size for p in base.rglob('*') if p.is_file())
record['sourceUnchanged'] = all(audit.sha(p) == before[p.relative_to(source).as_posix()] for p in paths)
record['freshPdfExists'] = (output / 'dissertation.pdf').is_file()
record['outputFiles'] = {p.name: {'sha256': audit.sha(p), 'bytes': p.stat().st_size}
                         for p in output.iterdir() if p.is_file()}
(evidence / 'portable-003-verification.json').write_bytes((json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode())
print(json.dumps({k: record[k] for k in ('status', 'exitCode', 'elapsedSeconds', 'totalLocalBytes',
                                      'sourceUnchanged', 'freshPdfExists', 'step37Accepted')}))
