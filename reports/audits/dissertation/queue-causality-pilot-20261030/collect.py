"""Collect the completed pilot once; no model execution or source changes."""
from pathlib import Path
import hashlib
import json
import shlex
import subprocess
import sys
import tarfile

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
LOCAL=Path('C:/temp/mqtty-evidence/queue-causality-pilot-20261030/transfer-001')
SSH=['ssh','-i','C:/Users/entityfx/.ssh/mqtty_two_machine','-o','BatchMode=yes','-o','ConnectTimeout=12','entityfx@192.168.5.1']
REMOTE=r'''
import sys,json,hashlib,subprocess,tarfile
from pathlib import Path
sys.dont_write_bytecode=True
base=Path('/run/media/entityfx/hd250/mqtty-evidence')
raw=base/'queue-causality-pilot-20261030'
program=base/'bounded-experiments-20261030-inputs'
source=base/'network-full-3h-v1'
transfer=base/'queue-causality-pilot-20261030-transfer-001'
final=json.loads((raw/'result.json').read_text())
assert final['status']=='completed' and final['classification']=='queueCapacityContrast'
state=subprocess.check_output(['systemctl','--user','show','mqtty-queue-causality-20261030.service','--property=MainPID','--property=ActiveState','--property=Result','--property=ExecMainStatus'],text=True)
assert 'MainPID=0' in state and 'Result=success' in state and 'ExecMainStatus=0' in state
sys.path.insert(0,str(source/'tools/dissertation'))
from network_study_inputs import verify_snapshot
manifest=verify_snapshot(source)
assert len(manifest['files'])==281
def sha(path):
 with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
seal=json.loads((program/'program-seal.json').read_text())
for name,digest in seal['files'].items():assert sha(program/name)==digest,name
transfer.mkdir()
post={'sourceFilesVerified':281,'sourceManifestSha256':sha(source/'execution-manifest.json'),'programSealSha256':sha(program/'program-seal.json'),'unitState':state,'sourcePostflightPassed':True}
(transfer/'source-postflight.json').write_bytes((json.dumps(post,indent=2)+'\n').encode())
files={}
for path in raw.rglob('*'):
 if path.is_file():files['raw/'+path.relative_to(raw).as_posix()]=path
for name in [*seal['files'],'program-seal.json']:files['program/'+name]=program/name
files['metadata/source-postflight.json']=transfer/'source-postflight.json'
assert all(not p.is_symlink() for p in files.values())
index={'files':{name:{'bytes':p.stat().st_size,'sha256':sha(p)} for name,p in sorted(files.items())}}
(transfer/'index.json').write_bytes((json.dumps(index,indent=2)+'\n').encode())
with (transfer/'raw.tar.gz').open('xb') as stream:
 with tarfile.open(fileobj=stream,mode='w:gz') as tar:
  for name,path in sorted(files.items()):tar.add(path,arcname=name,recursive=False)
  tar.add(transfer/'index.json',arcname='metadata/index.json',recursive=False)
for name,p in files.items():assert sha(p)==index['files'][name]['sha256'],name
print(json.dumps({'remoteArchive':str(transfer/'raw.tar.gz'),'archiveSha256':sha(transfer/'raw.tar.gz'),'archiveBytes':(transfer/'raw.tar.gz').stat().st_size,'sourceIndexSha256':sha(transfer/'index.json'),'files':len(files),'bytes':sum(r['bytes'] for r in index['files'].values()),'sourcePostflight':post}))
'''

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    LOCAL.mkdir()
    result=subprocess.run(SSH+['python3 -B -'],input=REMOTE,text=True,capture_output=True,timeout=90)
    if result.returncode:
        (HERE/'collection-failure.json').write_bytes((json.dumps({'returnCode':result.returncode,'stderr':result.stderr},indent=2)+'\n').encode())
        raise RuntimeError(result.stderr)
    info=json.loads(result.stdout)
    archive=LOCAL/'raw.tar.gz'
    with archive.open('xb') as stream:
        subprocess.run(SSH+['cat '+shlex.quote(info['remoteArchive'])],stdout=stream,check=True,timeout=90)
    assert archive.stat().st_size==info['archiveBytes'] and sha(archive)==info['archiveSha256']
    extracted=LOCAL/'contents';extracted.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        assert all(not m.issym() and not m.islnk() and not Path(m.name).is_absolute() and '..' not in Path(m.name).parts for m in tar.getmembers())
        tar.extractall(extracted,filter='data')
    index_path=extracted/'metadata/index.json'
    assert sha(index_path)==info['sourceIndexSha256']
    index=json.loads(index_path.read_text())
    actual={p.relative_to(extracted).as_posix():p for p in extracted.rglob('*') if p.is_file()}
    assert set(actual)==set(index['files'])|{'metadata/index.json'}
    for name,row in index['files'].items():
        assert actual[name].stat().st_size==row['bytes'] and sha(actual[name])==row['sha256'],name
    (HERE/'source-file-index.json').write_bytes(index_path.read_bytes())
    receipt={**info,'localArchive':str(archive),'localRaw':str(extracted/'raw'),'localProgram':str(extracted/'program'),
             'status':'transferredVerified','newModelsExecuted':0}
    (HERE/'transfer.json').write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps({k:v for k,v in receipt.items() if k!='sourcePostflight'}))

if __name__=='__main__':main()
