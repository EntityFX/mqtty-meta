"""Run once with python3 -B on Linux, after tests and source publication."""
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

sys.dont_write_bytecode = True
ROOT = Path('/run/media/entityfx/hd250/mqtty-evidence')
SOURCE = ROOT/'network-full-3h-v1'
INPUTS = ROOT/'acceleration-aa-20261030-inputs'
OUTPUT = ROOT/'acceleration-aa-20261030'
UNIT = 'mqtty-acceleration-aa-20261030.service'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


assert not OUTPUT.exists(), 'No retry/resume of an existing run'
assert os.path.ismount(ROOT.parent) and shutil.disk_usage(ROOT.parent).free > 10*1024**3
assert next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
            if line.startswith('MemAvailable:')) >= 6*1024**3
assert {4,6}.issubset(os.sched_getaffinity(0))
assert subprocess.check_output(['systemctl','--user','show',UNIT,'-p','LoadState','--value'],text=True).strip() == 'not-found'
health = json.load(urllib.request.urlopen('http://127.0.0.1:5088/healthz', timeout=4))
assert health['runningJobs'] == health['queuedJobs'] == 0
jobs = json.load(urllib.request.urlopen('http://127.0.0.1:5088/api/simulations', timeout=4))
assert not [j for j in jobs if j['state'] in ('Running','Queued','Paused','Cancelling')]
lines = subprocess.check_output(['ps','-eo','pid=,args='], text=True).splitlines()
assert not [line for line in lines if re.search(r'EntityFX.MqttY.Federation.App.dll|TwinValidationProbe.dll|EntityFX.MqttBenchmark.Campaign.dll|run_network_parallel.py|run_queue_pilot.py|run_acceleration_pilot.py', line)]
sys.path.insert(0, str(INPUTS))
import run_acceleration_pilot as controller
seal = controller.read(INPUTS/'seal.json')
controller.verify_files(INPUTS, seal['files'])
assert sha(INPUTS/'run_acceleration_pilot.py') == sha(Path(controller.__file__))
assert sha(SOURCE/'execution-manifest.json') == controller.MANIFEST_SHA
assert sha(INPUTS/'fixture.json') == controller.CONFIG_SHA
sys.path.insert(0,str(SOURCE/'tools/dissertation'))
from network_study_inputs import verify_snapshot, lock
with lock(ROOT.parent/'.mqtty-network-study.lock'):
    manifest = verify_snapshot(SOURCE)
validation = subprocess.run([manifest['dotnet'], str(SOURCE/'runtime/EntityFX.MqttY.Federation.App.dll'),
                             'validate','--config',str(INPUTS/'fixture.json')], capture_output=True, text=True, timeout=20, check=True)
topology = subprocess.check_output(['lscpu','-p=CPU,CORE,SOCKET'],text=True)
cores = {int(line.split(',')[0]): tuple(line.split(',')[1:]) for line in topology.splitlines() if not line.startswith('#')}
assert cores[4] != cores[6], 'Require separate physical cores'
for path in INPUTS.iterdir():
    if path.is_file(): path.chmod(0o444)
INPUTS.chmod(0o555)
argv = ['systemd-run','--user','--unit='+UNIT,
        '--property=RuntimeMaxSec=595','--property=TimeoutStopSec=5',
        '--property=MemoryMax=6G','--property=MemorySwapMax=0','--property=CPUQuota=200%',
        '--property=KillMode=control-group','/usr/bin/taskset','-c','4,6',
        '/usr/bin/python3','-B',str(INPUTS/'run_acceleration_pilot.py'),
        '--source',str(SOURCE),'--inputs',str(INPUTS),'--output',str(OUTPUT)]
launched = subprocess.run(argv,capture_output=True,text=True,check=True)
time.sleep(3)  # One startup allowance, no repeated polling.
state = subprocess.check_output(['systemctl','--user','show',UNIT,'-p','MainPID','-p','ActiveState',
                               '-p','SubState','-p','Result','-p','RuntimeMaxUSec','-p','TimeoutStopUSec'],text=True)
progress = json.loads((OUTPUT/'progress.json').read_text()) if (OUTPUT/'progress.json').exists() else None
receipt = {'unit': UNIT, 'argv': argv, 'state': state, 'progress': progress,
           'sourceFilesVerified': len(manifest['files']), 'fixtureCliValidation': json.loads(validation.stdout),
           'inputSealSha256': sha(INPUTS/'seal.json'), 'healthAtAdmission': health,
           'physicalCores': {'4': cores[4], '6': cores[6]}, 'output': str(OUTPUT),
           'launchStderr': launched.stderr, 'retriesAllowed': False, 'automaticNextExperiment': False}
(ROOT/'acceleration-aa-20261030-launch.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
