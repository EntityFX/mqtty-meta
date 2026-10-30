"""Collect closed attempts and A/A once, using the established transfer verifier."""
import importlib.util
from pathlib import Path
import sys

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
template=ROOT/'reports/audits/dissertation/queue-causality-pilot-20261030/collect.py'
spec=importlib.util.spec_from_file_location('transfer_verifier',template)
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
collector.HERE=HERE
collector.LOCAL=Path('C:/temp/mqtty-evidence/linux-selected-20261030/transfer-001')
collector.REMOTE=r'''
import sys,json,hashlib,subprocess,tarfile
from pathlib import Path
sys.dont_write_bytecode=True
base=Path('/run/media/entityfx/hd250/mqtty-evidence')
raw=base/'linux-selected-20261030'
program=base/'linux-selected-20261030-inputs'
source=base/'network-full-3h-v1'
transfer=base/'linux-selected-20261030-transfer-001'
state=subprocess.check_output(['systemctl','--user','show','mqtty-linux-selected-20261030.service','-p','MainPID','-p','ActiveState','-p','Result','-p','ExecMainStatus'],text=True)
assert 'MainPID=0' in state
assert json.loads((raw/'progress.json').read_text())['phase']=='completedWithFailures'
sys.path.insert(0,str(source/'tools/dissertation'))
from network_study_inputs import verify_snapshot
manifest=verify_snapshot(source)
def sha(path):
 with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
seal=json.loads((program/'seal.json').read_text())
for name,digest in seal['files'].items():assert sha(program/name)==digest,name
metadata=json.loads((program/'program.json').read_text())
for name,digest in metadata['pinnedAbsoluteFiles'].items():assert sha(Path(name))==digest,name
transfer.mkdir(exist_ok=False)
post={'sourceFilesVerified':len(manifest['files']),'sourceManifestSha256':sha(source/'execution-manifest.json'),
      'programSealSha256':sha(program/'seal.json'),'unitState':state,'sourcePostflightPassed':True,
      'excludedFromRaw':'broker-home executables/data/cache; conf/etc regular files retained',
      'pinnedAbsoluteFilesVerified':len(metadata['pinnedAbsoluteFiles'])}
(transfer/'source-postflight.json').write_bytes((json.dumps(post,indent=2)+'\n').encode())
files={}
for path in raw.rglob('*'):
 if not path.is_file() or path.is_symlink():continue
 rel=path.relative_to(raw)
 if 'broker-home' in rel.parts:
  tail=rel.parts[rel.parts.index('broker-home')+1:]
  if not tail or tail[0] not in ('conf','etc'):continue
 files['raw/'+rel.as_posix()]=path
for name in [*seal['files'],'seal.json']:files['program/'+name]=program/name
aa=base/'acceleration-aa-20261030'
for path in aa.rglob('*'):
 if path.is_file() and not path.is_symlink():files['aa/'+path.relative_to(aa).as_posix()]=path
aa_inputs=base/'acceleration-aa-20261030-inputs'
for path in aa_inputs.iterdir():
 if path.is_file():files['aa-program/'+path.name]=path
files['metadata/source-postflight.json']=transfer/'source-postflight.json'
index={'files':{name:{'bytes':p.stat().st_size,'sha256':sha(p)} for name,p in sorted(files.items())}}
(transfer/'index.json').write_bytes((json.dumps(index,indent=2)+'\n').encode())
with (transfer/'raw.tar.gz').open('xb') as stream:
 with tarfile.open(fileobj=stream,mode='w:gz') as tar:
  for name,path in sorted(files.items()):tar.add(path,arcname=name,recursive=False)
  tar.add(transfer/'index.json',arcname='metadata/index.json',recursive=False)
for name,path in files.items():assert sha(path)==index['files'][name]['sha256'],name
print(json.dumps({'remoteArchive':str(transfer/'raw.tar.gz'),'archiveSha256':sha(transfer/'raw.tar.gz'),
 'archiveBytes':(transfer/'raw.tar.gz').stat().st_size,'sourceIndexSha256':sha(transfer/'index.json'),
 'files':len(files),'bytes':sum(r['bytes'] for r in index['files'].values()),'sourcePostflight':post}))
'''

if __name__=='__main__':
    collector.LOCAL.parent.mkdir(parents=True,exist_ok=True)
    collector.main()
