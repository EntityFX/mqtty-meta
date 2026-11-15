"""Remote one-time admission and launch; run with python3 -B on the Linux host."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

sys.dont_write_bytecode=True
source=Path('/run/media/entityfx/hd250/mqtty-evidence/network-full-3h-v1')
inputs=source.parent/'bounded-experiments-20261030-inputs'
output=source.parent/'queue-causality-pilot-20261030'
mount=source.parent.parent
unit='mqtty-queue-causality-20261030.service'
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

# Repair only the extra cache produced by the preceding admission import without -B.
# The diagnosed bytes are retained, and no manifest member may be removed.
cache=source/'tools/dissertation/__pycache__/network_study_inputs.cpython-312.pyc'
expected_cache='cc13bb685e36862811b307561c5dc7e81ab46c5a1a499ca6de285178693aaaec'
manifest=json.loads((source/'execution-manifest.json').read_text())
assert cache.relative_to(source).as_posix() not in manifest['files']
assert cache.resolve().is_relative_to(source.resolve()) and cache.is_file() and sha(cache)==expected_cache
preserved=source.parent/'queue-causality-pilot-20261030-admission/attempt-001'
preserved.mkdir(parents=True,exist_ok=False)
(preserved/cache.name).write_bytes(cache.read_bytes())
assert sha(preserved/cache.name)==expected_cache
cache.unlink()  # One verified generated file; no recursive cleanup.
repair={'cause':'Admission script imported the verifier without disabling bytecode',
        'removedGeneratedPath':str(cache),'preservedAt':str(preserved/cache.name),'sha256':expected_cache,
        'scientificManifestFilesChanged':0,'modelProcessesStartedBeforeRepair':0}
(preserved/'repair.json').write_text(json.dumps(repair,indent=2)+'\n')

assert os.path.ismount(mount) and shutil.disk_usage(mount).free>10*1024**3
assert not output.exists(),'Existing output: no retry admitted'
health=json.load(urllib.request.urlopen('http://127.0.0.1:5088/healthz',timeout=4))
jobs=json.load(urllib.request.urlopen('http://127.0.0.1:5088/api/simulations',timeout=4))
assert health['runningJobs']==health['queuedJobs']==0
assert not [j for j in jobs if j['state'] in ('Running','Queued','Paused','Cancelling')]
processes=subprocess.check_output(['ps','-eo','pid=,args='],text=True)
assert not [p for p in processes.splitlines() if re.search(r'EntityFX.MqttY.Federation.App.dll|TwinValidationProbe.dll|EntityFX.MqttBenchmark.Campaign.dll|run_network_parallel.py|run_queue_pilot.py',p)]
assert next(int(x.split()[1])*1024 for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))>=6*1024**3
with (mount/'.mqtty-network-study.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(lock,fcntl.LOCK_UN)
assert sha(source/'execution-manifest.json')=='24ae3654a610545f2a13ebc796eeddcaa898d9bde2103ee722592e211b8bffa7'
sys.path.insert(0,str(source/'tools/dissertation'))
from network_study_inputs import verify_snapshot
manifest=verify_snapshot(source)
assert len(manifest['files'])==281
subprocess.run(['python3','-B',str(inputs/'run_queue_pilot.py'),'--check-inputs'],capture_output=True,check=True)
validations={}
for capacity in (1,1024):
    result=subprocess.run([manifest['dotnet'],str(source/'runtime/EntityFX.MqttY.Federation.App.dll'),'validate','--config',str(inputs/f'configs/q{capacity}.json')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,(capacity,result.stderr)
    validations[str(capacity)]=json.loads(result.stdout)
for path in inputs.rglob('*'):
    if path.is_file():path.chmod(0o444)
for path in sorted((p for p in inputs.rglob('*') if p.is_dir()),reverse=True):path.chmod(0o555)
inputs.chmod(0o555)
assert subprocess.check_output(['systemctl','--user','show',unit,'--property=LoadState','--value'],text=True).strip()=='not-found'
argv=['systemd-run','--user','--unit='+unit,'--property=RuntimeMaxSec=595','--property=MemoryMax=6G','--property=MemorySwapMax=0','--property=CPUQuota=200%','--property=KillMode=control-group','--property=TimeoutStopSec=5','/usr/bin/python3','-B',str(inputs/'run_queue_pilot.py'),'--source',str(source),'--output',str(output)]
launched=subprocess.run(argv,capture_output=True,text=True,check=True)
time.sleep(3)  # One startup allowance; no polling loop.
state=subprocess.check_output(['systemctl','--user','show',unit,'--property=MainPID','--property=ActiveState','--property=SubState','--property=Result','--property=RuntimeMaxUSec','--property=TimeoutStopUSec'],text=True)
progress=json.loads((output/'progress.json').read_text()) if (output/'progress.json').exists() else None
receipt={'unit':unit,'argv':argv,'unitState':state,'progress':progress,'sourceFilesVerified':281,
         'frozenCliValidation':validations,'inputsSealSha256':sha(inputs/'program-seal.json'),
         'output':str(output),'launchStderr':launched.stderr,'cacheRepair':repair,
         'automaticModelRetry':False,'accelerationStarted':False}
(source.parent/'queue-causality-pilot-20261030-launch.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(receipt))
