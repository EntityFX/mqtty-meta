"""Transfer one closed censored run and its accepted reference; never rerun either."""
import importlib.util
from pathlib import Path
import sys

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
template=REPO/'reports/audits/dissertation/queue-causality-pilot-20261030/collect.py'
spec=importlib.util.spec_from_file_location('transfer_verifier',template)
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
collector.HERE=HERE/'remaining-result';collector.HERE.mkdir(exist_ok=False)
collector.LOCAL=Path('C:/temp/mqtty-evidence/queue-main-remaining-20261030/transfer-001')
collector.REMOTE=r'''
import json,hashlib,subprocess,sys,tarfile
from pathlib import Path
sys.dont_write_bytecode=True
base=Path('/run/media/entityfx/hd250/mqtty-evidence')
raw=base/'queue-main-remaining-20261030';program=base/'queue-main-remaining-20261030-inputs'
source=base/'network-full-3h-v1';transfer=base/'queue-main-remaining-20261030-transfer-001'
def sha(path):
 with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
state=subprocess.check_output(['systemctl','--user','show','mqtty-queue-main-remaining-20261030.service','-p','MainPID','-p','Result','-p','ExecMainStatus'],text=True)
assert 'MainPID=0' in state
summary=json.loads((raw/'attempt-001/output/summary.json').read_text())
assert summary['status']=='failed' and 'drain timed out' in summary['error']
sys.path.insert(0,str(source/'tools/dissertation'))
from network_study_inputs import verify_snapshot
manifest=verify_snapshot(source)
seal=json.loads((program/'seal.json').read_text())
for name,digest in seal['files'].items():assert sha(program/name)==digest,name
baseline_row=source/'rows/matrix.star.federation.q2.r5000.w10.s42.attempt-001.json'
assert sha(baseline_row)=='eb9b1aa1f93d618f42d123ba05cf5ace6a41832c359d4525b36f645d48611bd0'
reference=json.loads(baseline_row.read_text());baseline=source/reference['attempt']/'output'
for name,digest in reference['scientificSha256'].items():assert sha(baseline/name)==digest,name
transfer.mkdir(exist_ok=False)
post={'sourceFilesVerified':len(manifest['files']),'sourceManifestSha256':sha(source/'execution-manifest.json'),
      'programSealSha256':sha(program/'seal.json'),'unitState':state,'sourcePostflightPassed':True,
      'classification':'modelDrainDeadlineReached-partialEvidence','baselineRowSha256':sha(baseline_row)}
(transfer/'source-postflight.json').write_bytes((json.dumps(post,indent=2)+'\n').encode())
files={}
for prefix,directory in [('raw',raw),('baseline',baseline)]:
 for path in directory.rglob('*'):
  if path.is_file() and not path.is_symlink():files[prefix+'/'+path.relative_to(directory).as_posix()]=path
for name in [*seal['files'],'seal.json']:files['program/'+name]=program/name
files['metadata/baseline-row.json']=baseline_row
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
