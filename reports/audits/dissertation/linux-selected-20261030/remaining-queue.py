"""Prepare OR launch one missing variant. Each mode is one-time, no retry."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

REPO=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
LOCAL=Path('C:/temp/mqtty-evidence/queue-main-remaining-20261030-inputs')
REMOTE='/run/media/entityfx/hd250/mqtty-evidence/queue-main-remaining-20261030-inputs'
SSH=['ssh','-i','C:/Users/entityfx/.ssh/mqtty_two_machine','-o','BatchMode=yes','-o','ConnectTimeout=12','entityfx@192.168.5.1']


def remote(code):
    r=subprocess.run([*SSH,'python3 -B -'],input=code,text=True,capture_output=True,timeout=45)
    if r.returncode:raise RuntimeError(r.stderr)
    return json.loads(r.stdout)


def prepare():
    index_path=Path('C:/temp/mqtty-evidence/network-final-20261028/file-index.json')
    assert hashlib.sha256(index_path.read_bytes()).hexdigest()=='b1a775d2f93921610db678f50104a674f67a1bf1cc04c22a74867c502305bee6'
    index=json.loads(index_path.read_bytes())
    row=next(r for r in index['files'] if r['path']=='rows/matrix.star.federation.q2.r5000.w10.s42.attempt-001.json')
    assert row['sha256']=='eb9b1aa1f93d618f42d123ba05cf5ace6a41832c359d4525b36f645d48611bd0'
    LOCAL.mkdir(parents=True,exist_ok=False)
    for name,path in {
        'run_queue_remaining.py':REPO/'tools/dissertation/run_queue_remaining.py',
        'test_queue_remaining.py':REPO/'tools/dissertation/test_queue_remaining.py',
        'run_acceleration_pilot.py':REPO/'tools/dissertation/run_acceleration_pilot.py',
        'protocol.md':REPO/'docs/plans/Queue-main-remaining-20261030.md'}.items():
        (LOCAL/name).write_bytes(path.read_bytes())
    expected={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in LOCAL.iterdir()}
    remote(f"from pathlib import Path\nimport json\nPath({REMOTE!r}).mkdir(exist_ok=False)\nprint(json.dumps({{'created':True}}))\n")
    subprocess.run(['scp','-i','C:/Users/entityfx/.ssh/mqtty_two_machine','-o','BatchMode=yes',
                    *map(str,LOCAL.iterdir()),'entityfx@192.168.5.1:'+REMOTE+'/'],check=True,timeout=30)
    code=f'''import json,sys,subprocess
from pathlib import Path
root=Path({REMOTE!r});sys.path.insert(0,str(root))
from run_acceleration_pilot import read,digest,verify_files
verify_files(root,{expected!r})
source=root.parent/'network-full-3h-v1';selected=root.parent/'linux-selected-20261030-inputs'
(root/'config.json').write_bytes((selected/'queue4096.json').read_bytes())
from run_queue_remaining import admit,require_success,BASELINE_ID,BASELINE_SHA,IDENTITY
baseline_path=source/'rows'/f'{{BASELINE_ID}}.attempt-001.json'
assert digest(baseline_path)==BASELINE_SHA
baseline=read(baseline_path);require_success(baseline)
base=read(source/baseline['attempt']/'output/inputs/config.json')
admit(base,read(root/'config.json'),baseline['processWallSeconds'],1800)
program={{'programIdentity':IDENTITY,'source':str(source),'baselineId':BASELINE_ID,'baselineRowSha256':BASELINE_SHA,
 'localAcceptedIndexSha256':'b1a775d2f93921610db678f50104a674f67a1bf1cc04c22a74867c502305bee6',
 'modelTimeoutSeconds':1800,'serviceHardWallSeconds':1860,'onlyNewCapacity':4096,'retryAllowed':False}}
(root/'program.json').write_text(json.dumps(program,indent=2)+'\\n')
tests=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(root),'-p','test_queue_remaining.py','-v'],capture_output=True,text=True,timeout=10)
assert tests.returncode==0,tests.stderr
sys.path.insert(0,str(source/'tools/dissertation'))
from network_study_inputs import verify_snapshot
manifest=verify_snapshot(source)
validation=subprocess.run([manifest['dotnet'],str(source/'runtime/EntityFX.MqttY.Federation.App.dll'),'validate','--config',str(root/'config.json')],capture_output=True,text=True,check=True,timeout=15)
seal={{'programIdentity':IDENTITY,'files':{{p.name:digest(p) for p in root.iterdir() if p.is_file()}}}}
(root/'seal.json').write_text(json.dumps(seal,indent=2)+'\\n')
print(json.dumps({{'program':program,'seal':seal,'sealSha256':digest(root/'seal.json'),'tests':tests.stderr,
 'validation':json.loads(validation.stdout),'knownBaselineSeconds':baseline['processWallSeconds'],'sourceFilesVerified':len(manifest['files'])}}))
'''
    result=remote(code)
    (HERE/'remaining-admission.json').write_bytes((json.dumps(result,indent=2)+'\n').encode())
    print(json.dumps({k:result[k] for k in ('sealSha256','tests','knownBaselineSeconds')}))


def launch():
    admission=json.loads((HERE/'remaining-admission.json').read_bytes())
    code=f'''import json,sys,time,subprocess,urllib.request,os,re,shutil
from pathlib import Path
root=Path({REMOTE!r});sys.path.insert(0,str(root))
from run_acceleration_pilot import read,digest,verify_files
assert digest(root/'seal.json')=={admission['sealSha256']!r}
verify_files(root,read(root/'seal.json')['files'])
unit='mqtty-queue-main-remaining-20261030.service';output=root.parent/'queue-main-remaining-20261030'
assert not output.exists()
assert subprocess.check_output(['systemctl','--user','show',unit,'-p','LoadState','--value'],text=True).strip()=='not-found'
old=subprocess.check_output(['systemctl','--user','show','mqtty-linux-selected-20261030.service','-p','MainPID'],text=True)
assert 'MainPID=0' in old
health=json.load(urllib.request.urlopen('http://127.0.0.1:5088/healthz',timeout=4))
assert health['runningJobs']==health['queuedJobs']==0
assert shutil.disk_usage(root.parent.parent).free>10*1024**3
assert next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))>=6*1024**3
processes=subprocess.check_output(['ps','-eo','pid=,args='],text=True)
assert not [line for line in processes.splitlines() if re.search(r'EntityFX.MqttY.Federation.App.dll|TwinValidationProbe.dll|GraphExecutionProbe.dll|run_network_parallel.py|run_linux_selected.py|run_queue_remaining.py',line)]
for p in root.iterdir():p.chmod(0o444)
root.chmod(0o555)
argv=['systemd-run','--user','--unit='+unit,'--property=RuntimeMaxSec=1855','--property=TimeoutStopSec=5',
 '--property=MemoryMax=6G','--property=MemorySwapMax=0','--property=CPUQuota=200%','--property=KillMode=control-group',
 '/usr/bin/taskset','-c','2,3','/usr/bin/python3','-B',str(root/'run_queue_remaining.py'),'--inputs',str(root),'--output',str(output)]
started=subprocess.run(argv,capture_output=True,text=True,check=True)
time.sleep(4)
state=subprocess.check_output(['systemctl','--user','show',unit,'-p','MainPID','-p','ActiveState','-p','SubState','-p','Result'],text=True)
progress=read(output/'progress.json') if (output/'progress.json').exists() else None
print(json.dumps({{'unit':unit,'state':state,'progress':progress,'argv':argv,'sealSha256':digest(root/'seal.json'),
 'newJobs':1,'baselineRecomputed':False,'output':str(output),'healthAtAdmission':health}}))
'''
    result=remote(code);result['observedAtMsk']=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).isoformat()
    (HERE/'remaining-launch.json').write_bytes((json.dumps(result,indent=2)+'\n').encode())
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--launch',action='store_true')
    if parser.parse_args().launch:launch()
    else:prepare()
