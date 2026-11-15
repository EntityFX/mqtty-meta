"""Read-only local acceptance of the already finished short queue pair."""
from pathlib import Path
import sys,json,hashlib

ROOT=Path(__file__).resolve().parents[4]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'tools/dissertation'))
from network_study_verification import verify_loaded_run
from network_review import check_graph

packet=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
transfer=read(packet/'transfer.json');raw=Path(transfer['localRaw'])
index=read(packet/'source-file-index.json')
assert sha(packet/'source-file-index.json')==transfer['sourceIndexSha256']
assert sha(Path(transfer['fullCollectionSnapshot']))==transfer['collectedMetadataSha256']
actual={p.relative_to(raw).as_posix():p for p in raw.rglob('*') if p.is_file()}
assert set(actual)==set(index['files'])
for name,p in actual.items():assert p.stat().st_size==index['files'][name]['bytes'] and sha(p)==index['files'][name]['sha256'],name
launch=read(packet/'launch.json');pre=read(raw/'preflight.json');assert pre==launch['preflight']
for name,h in pre['pinnedFiles'].items():assert sha(raw/name)==h,name
post=read(packet/'source-postflight.json');assert post['sourceFilesVerified']==281 and post['manifestSha256']==pre['sourceManifestSha256']
a,b=(read(raw/f'configs/q{q}.json') for q in (1024,4096))
assert set(a)==set(b) and [k for k in a if a[k]!=b[k]]==['queueCapacity']
assert (a['queueCapacity'],b['queueCapacity'])==(1024,4096)
assert (a['warmupSeconds'],a['measurementSeconds'],a['drainTimeoutSeconds'])==(.25,2,10)
results=[]
metrics=('apiAttempts','sourceCompleted','expectedDeliveries','uniqueDeliveries','uniqueDuringDrain','fullyDeliveredPublications','deliveryLoss','receiverP99Ms','publishP99Ms','queueDrops','duplicateDeliveries','forwardAttempts','suppressedDuplicates')
for q in (1024,4096):
    attempt=raw/f'q{q}/attempt-001';output=attempt/'output';result=read(attempt/'result.json')
    assert result['returnCode']==0 and result['technicalError'] is None
    summary=verify_loaded_run(output);assert result['summary']==summary
    executed=read(output/'run-config.json');check_graph(output/'topology.graphml',executed)
    configured=read(raw/f'configs/q{q}.json');assert all(executed.get(k)==v for k,v in configured.items())
    results.append(dict(queueCapacity=q,metrics={k:summary[k] for k in metrics},processWallSeconds=result['processWallSeconds'],peakRssBytes=result['peakRssBytes']))
names=('summary.json','histograms.json','publications.json','delivery-receipts.json','transmissions.json','network-evidence.json')
equal={n:(raw/'q1024/attempt-001/output'/n).read_bytes()==(raw/'q4096/attempt-001/output'/n).read_bytes() for n in names}
assert all(equal.values())
assert results[0]['metrics']['queueDrops']==0
final=read(raw/'result.json');assert final['diagnosticClassification']=='completedWithoutBaselineOverflow'
receipt=dict(status='accepted',classification=final['diagnosticClassification'],rawRunsPassed=2,filesVerified=len(actual),pinnedFilesVerified=len(pre['pinnedFiles']),sourceSnapshotFilesVerified=281,sourceManifestSha256=post['manifestSha256'],singleChangedFactor='queueCapacity',sharedWallSeconds=final['sharedWallSeconds'],results=results,byteEqualFiles=equal,archiveSha256=transfer['archiveSha256'],scientificallyInformativeForOverload=False,causalQueueClaimEstablished=False,automaticRetryPerformed=False,newExperimentsDuringAcceptance=0,fullSeriesRuntimeChanged=False)
(packet/'acceptance.json').write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
print('PASS: 77 files and two raw runs accepted; zero baseline overflow makes contrast uninformative')
