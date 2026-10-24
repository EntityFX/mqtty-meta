"""One bounded local attempt; no source edits, package substitutions or global install."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location('build_audit', root / 'tools/dissertation/check_dissertation_build.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
base = root / 'MqttBenchmark/artifacts/dissertation-build-v1'
second = '--attempt-002' in sys.argv
snapshot = base / ('main-source-002-retry' if second else 'main-source')
snapshot.mkdir(exist_ok=False)
source = root / 'diss'
paths = audit.tracked_inputs(source)
before = {p.relative_to(source).as_posix(): audit.sha(p) for p in paths}
for path in paths:
    if second and path.relative_to(source).parts[0] == 'latex':
        continue  # Unreferenced alternative entrypoint; Windows upstream issue #1344.
    target = snapshot / path.relative_to(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
env = os.environ.copy()
env['TECTONIC_CACHE_DIR'] = str(base / 'cache')
env['TEMP'] = env['TMP'] = str(base / 'temp')
(base / 'temp').mkdir(exist_ok=True)
command = [str(base / 'portable/tectonic.exe'), '--untrusted', '--keep-logs',
           '--keep-intermediates', '--print', '--color', 'never', 'dissertation.tex']
record = {'command': command, 'cwd': str(snapshot), 'cache': env['TECTONIC_CACHE_DIR'],
          'sourceManifest': before, 'timeLimitSeconds': 300, 'watchdogBytes': 240 * 1024**2,
          'totalBudgetBytes': 300 * 1000**2, 'status': 'running',
          'sourceEdits': False, 'bibliographyBackendOverride': False,
          'fontOverride': False, 'step37Accepted': False}
record['excludedAlternativeTree'] = 'latex/' if second else None
record['excludedInputs'] = [p.relative_to(source).as_posix() for p in paths
                           if second and p.relative_to(source).parts[0] == 'latex']
evidence = Path(__file__).parent
started = time.monotonic()
suffix = '-002' if second else ''
with (evidence / f'portable{suffix}.stdout.log').open('wb') as output:
    process = subprocess.Popen(command, cwd=snapshot, env=env,
                               stdout=output, stderr=subprocess.STDOUT)
    while process.poll() is None:
        size = sum(p.stat().st_size for p in base.rglob('*') if p.is_file())
        elapsed = time.monotonic() - started
        if elapsed > 296 or size > 240 * 1024**2:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            record['status'] = 'watchdogTimeout' if elapsed > 296 else 'watchdogStorage'
            break
        time.sleep(.5)
    record['exitCode'] = process.wait(timeout=10)
if record['status'] == 'running':
    record['status'] = 'processExited'
record['elapsedSeconds'] = time.monotonic() - started
record['totalLocalBytes'] = sum(p.stat().st_size for p in base.rglob('*') if p.is_file())
record['sourceUnchanged'] = all(audit.sha(p) == before[p.relative_to(source).as_posix()] for p in paths)
record['freshPdfExists'] = (snapshot / 'dissertation.pdf').is_file()
record['outputFiles'] = {p.relative_to(snapshot).as_posix(): {'sha256': audit.sha(p), 'bytes': p.stat().st_size}
                         for p in snapshot.iterdir() if p.is_file() and p.name not in before}
(evidence / f'portable{suffix}-verification.json').write_bytes((json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode())
print(json.dumps({k: record[k] for k in ('status', 'exitCode', 'elapsedSeconds', 'totalLocalBytes',
                                      'sourceUnchanged', 'freshPdfExists', 'step37Accepted')}))
