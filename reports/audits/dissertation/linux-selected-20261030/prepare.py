"""One-time staging and admission of explicitly selected items2,4,5."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

REPO=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
LOCAL=Path('C:/temp/mqtty-evidence/linux-selected-20261030-inputs')
REMOTE='/run/media/entityfx/hd250/mqtty-evidence/linux-selected-20261030-inputs'
SSH=['ssh','-i','C:/Users/entityfx/.ssh/mqtty_two_machine','-o','BatchMode=yes','-o','ConnectTimeout=12','entityfx@192.168.5.1']


def remote(script):
    result=subprocess.run([*SSH,'python3 -B -'],input=script,text=True,capture_output=True,timeout=50)
    if result.returncode:raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


if __name__=='__main__':
    LOCAL.mkdir(parents=True,exist_ok=False)
    for name in ('run_linux_selected.py','run_acceleration_pilot.py','test_linux_selected.py','test_acceleration_pilot.py','prepare_graph_execution_probe.py'):
        shutil.copy2(REPO/'tools/dissertation'/name,LOCAL/name)
    shutil.copy2(REPO/'docs/plans/Linux-items-2-4-5-20261030.md',LOCAL/'protocol.md')
    graph=Path('C:/temp/mqtty-evidence/graph-execution-probe-20261030')
    shutil.copytree(graph/'runtime',LOCAL/'graph-runtime')
    shutil.copy2(graph/'adapter.json',LOCAL/'adapter.json')
    for name in ('FederationRunner.original.cs','Runner.generated.cs'):
        shutil.copy2(graph/name,LOCAL/name)
    core=json.loads((REPO/'reports/audits/dissertation/bounded-experiments-20261030/configs/q1024.json').read_bytes())
    core.update(warmupSeconds=.01,measurementSeconds=.05,drainTimeoutSeconds=1)
    (LOCAL/'core.json').write_bytes((json.dumps(core,indent=2)+'\n').encode())
    package=LOCAL.parent/'linux-selected-20261030-inputs.tar.gz'
    with tarfile.open(package,'x:gz') as archive:
        for path in sorted(LOCAL.rglob('*')):
            if path.is_file():archive.add(path,arcname=path.relative_to(LOCAL).as_posix(),recursive=False)
    package_sha=hashlib.sha256(package.read_bytes()).hexdigest()
    remote(f"from pathlib import Path\nimport json\nr=Path({REMOTE!r});r.mkdir(exist_ok=False)\nprint(json.dumps({{'directory':str(r)}}))\n")
    subprocess.run(['scp','-i','C:/Users/entityfx/.ssh/mqtty_two_machine','-o','BatchMode=yes',str(package),
                    'entityfx@192.168.5.1:'+REMOTE+'.tar.gz'],check=True,timeout=40)
    script=f'''import hashlib,json,sys,tarfile,shutil,subprocess
from pathlib import Path
root=Path({REMOTE!r}); package=Path(str(root)+'.tar.gz')
def sha(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
assert sha(package)=={package_sha!r}
with tarfile.open(package) as a:
 for member in a.getmembers():
  assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
 a.extractall(root,filter='data')
sys.path.insert(0,str(root))
import run_linux_selected as controller
source=Path('/run/media/entityfx/hd250/mqtty-evidence/network-full-3h-v1')
main=source.parent/'article1-matched-main-v1'
old=json.loads((main/'program.json').read_text())
freeze=json.loads((main/'profile-freeze.json').read_text())
(root/'twin-tools').mkdir()
for name,expected in old['files'].items():
 if name.startswith('launcher/') and name.endswith('.py'):
  assert sha(main/name)==expected
  shutil.copy2(main/name,root/'twin-tools'/Path(name).name)
base=json.loads((source/'program/configs/matrix/matrix.star.federation.q2.r5000.w10.s42.json').read_text())
assert base['queueCapacity']==1024 and base['measurementSeconds']==10 and base['offeredRps']==5000
for q in (1024,4096):
 config=dict(base,queueCapacity=q)
 (root/f'queue{{q}}.json').write_text(json.dumps(config,indent=2)+'\\n')
pinned=dict(old['pinnedFiles'])
for name,expected in old['files'].items():pinned[str(main/name)]=expected
for broker,profile in freeze['profiles'].items():pinned[profile['path']]=profile['sha256']
for name in ('program.json','profile-freeze.json','runtime-manifest.json'):pinned[str(main/name)]=sha(main/name)
previous={{}}
for broker in controller.BROKERS:
 previous[broker]=[]
 for r in (1,2,3):
  path=main/f'attempt-001/jobs/control.{{broker}}.r{{r}}/review.json'
  pinned[str(path)]=sha(path)
  previous[broker].append(json.loads(path.read_text())['metrics'])
network_manifest=json.loads((source/'execution-manifest.json').read_text())
program={{'programIdentity':controller.IDENTITY,'networkSource':str(source),'twinSource':str(main),
 'dotnet':network_manifest['dotnet'],'profiles':freeze['profiles'],'pinnedAbsoluteFiles':pinned,
 'previousControlMetrics':previous,'selectedItems':[2,4,5],'stageBudgetsSeconds':[420,600,1800],
 'totalBudgetSeconds':3000,'mqttyCoreSourceCommit':'f5c0a4715b555c4348d2a2843177251f62c640ff','retryAllowed':False}}
controller.verify_absolute(pinned)
(root/'program.json').write_text(json.dumps(program,indent=2)+'\\n')
tests=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(root),'-p','test_*pilot.py','-v'],capture_output=True,text=True,timeout=15)
selected=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(root),'-p','test_linux_selected.py','-v'],capture_output=True,text=True,timeout=15)
assert tests.returncode==selected.returncode==0,(tests.stderr,selected.stderr)
controller.check_queue_pair(controller.read(root/'queue1024.json'),controller.read(root/'queue4096.json'))
validations={{}}
for name in ('core.json','queue1024.json','queue4096.json'):
 runtime=root/'graph-runtime/EntityFX.MqttY.Federation.App.dll' if name=='core.json' else source/'runtime/EntityFX.MqttY.Federation.App.dll'
 checked=subprocess.run([program['dotnet'],str(runtime),'validate','--config',str(root/name)],capture_output=True,text=True,timeout=15)
 assert checked.returncode==0,checked.stderr
 validations[name]=json.loads(checked.stdout)
seal={{'programIdentity':controller.IDENTITY,'files':{{p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*')) if p.is_file()}}}}
(root/'seal.json').write_text(json.dumps(seal,indent=2)+'\\n')
print(json.dumps({{'inputSealSha256':sha(root/'seal.json'),'sealedFiles':len(seal['files']),'program':program,
 'seal':seal,'tests':{{'processTests':tests.stderr,'selectedTests':selected.stderr}},'cliValidations':validations,
 'sourcePackageSha256':sha(package)}}))
'''
    receipt=remote(script)
    (HERE/'admission.json').write_bytes((json.dumps(receipt,indent=2)+'\n').encode())
    print(json.dumps({k:receipt[k] for k in ('inputSealSha256','sealedFiles','tests')}))
