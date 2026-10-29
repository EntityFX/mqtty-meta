"""One-time launch after publication. Do not rerun existing service/output."""
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
ROOT=Path('/run/media/entityfx/hd250/mqtty-evidence')
INPUTS=ROOT/'linux-selected-20261030-inputs'
OUTPUT=ROOT/'linux-selected-20261030'
UNIT='mqtty-linux-selected-20261030.service'
sys.path.insert(0,str(INPUTS))
from run_acceleration_pilot import digest, read, verify_files

assert digest(INPUTS/'seal.json')=='715016847b71507bfd75f5a601b6375749d2af499b61120d077ab3a5febe086b'
seal=read(INPUTS/'seal.json');verify_files(INPUTS,seal['files'])
assert os.path.ismount(ROOT.parent) and shutil.disk_usage(ROOT.parent).free>10*1024**3
assert not OUTPUT.exists()
assert {0,2,3}.issubset(os.sched_getaffinity(0))
assert next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))>=6*1024**3
assert subprocess.check_output(['systemctl','--user','show',UNIT,'-p','LoadState','--value'],text=True).strip()=='not-found'
aa_state=subprocess.check_output(['systemctl','--user','show','mqtty-acceleration-aa-20261030.service','-p','MainPID','-p','ActiveState','-p','Result'],text=True)
assert 'MainPID=0' in aa_state and 'ActiveState=inactive' in aa_state
health=json.load(urllib.request.urlopen('http://127.0.0.1:5088/healthz',timeout=4))
jobs=json.load(urllib.request.urlopen('http://127.0.0.1:5088/api/simulations',timeout=4))
assert health['runningJobs']==health['queuedJobs']==0
assert not [j for j in jobs if j['state'] in ('Running','Queued','Paused','Cancelling')]
processes=subprocess.check_output(['ps','-eo','pid=,args='],text=True)
assert not [p for p in processes.splitlines() if re.search(r'EntityFX.MqttY.Federation.App.dll|TwinValidationProbe.dll|EntityFX.MqttBenchmark.Campaign.dll|run_network_parallel.py|run_queue_pilot.py|run_acceleration_pilot.py|run_linux_selected.py',p)]
program=read(INPUTS/'program.json')
from run_linux_selected import verify_absolute
verify_absolute(program['pinnedAbsoluteFiles'])
sys.path.insert(0,str(Path(program['networkSource'])/'tools/dissertation'))
from network_study_inputs import verify_snapshot, lock
with lock(ROOT.parent/'.mqtty-network-study.lock'):
    source=verify_snapshot(Path(program['networkSource']))
for path in INPUTS.rglob('*'):
    if path.is_file():path.chmod(0o444)
for path in sorted([p for p in INPUTS.rglob('*') if p.is_dir()],reverse=True):path.chmod(0o555)
INPUTS.chmod(0o555)
argv=['systemd-run','--user','--unit='+UNIT,'--property=RuntimeMaxSec=2995','--property=TimeoutStopSec=5',
      '--property=MemoryMax=6G','--property=MemorySwapMax=0','--property=CPUQuota=300%',
      '--property=KillMode=control-group','/usr/bin/taskset','-c','0,2,3','/usr/bin/python3','-B',
      str(INPUTS/'run_linux_selected.py'),'--inputs',str(INPUTS),'--output',str(OUTPUT)]
launched=subprocess.run(argv,capture_output=True,text=True,check=True)
time.sleep(3)
state=subprocess.check_output(['systemctl','--user','show',UNIT,'-p','MainPID','-p','ActiveState','-p','SubState',
    '-p','Result','-p','RuntimeMaxUSec','-p','TimeoutStopUSec'],text=True)
progress=read(OUTPUT/'progress.json') if (OUTPUT/'progress.json').exists() else None
receipt=dict(unit=UNIT,argv=argv,state=state,progress=progress,sourceFilesVerified=len(source['files']),
             inputSealSha256=digest(INPUTS/'seal.json'),healthAtAdmission=health,previousAaState=aa_state,
             output=str(OUTPUT),automaticRetry=False,launchStderr=launched.stderr,
             allowedSelectedItems=[2,4,5],otherItemsAutomaticallyLaunched=False)
if progress is None:
    receipt['startupJournal']=subprocess.check_output(['journalctl','--user','-u',UNIT,'-n','20','--no-pager'],text=True)
(ROOT/'linux-selected-20261030-launch.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
