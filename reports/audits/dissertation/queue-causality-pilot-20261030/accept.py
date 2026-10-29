"""Local raw acceptance and separate ID/receipt arithmetic; never execute a model."""
import base64
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'tools/dissertation'))
from network_study_verification import verify_loaded_run
from network_review import check_graph

def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def close(a,b):return math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12)

def main():
    transfer=read(HERE/'transfer.json');raw=Path(transfer['localRaw']);program=Path(transfer['localProgram'])
    archive=Path(transfer['localArchive'])
    assert sha(archive)==transfer['archiveSha256']
    assert sha(HERE/'source-file-index.json')==transfer['sourceIndexSha256']
    index=read(HERE/'source-file-index.json')['files'];collection=raw.parent
    actual={p.relative_to(collection).as_posix():p for p in collection.rglob('*') if p.is_file()}
    assert set(actual)==set(index)|{'metadata/index.json'}
    for name,row in index.items():assert actual[name].stat().st_size==row['bytes'] and sha(actual[name])==row['sha256'],name
    post=read(collection/'metadata/source-postflight.json');pre=read(raw/'preflight.json')
    manifest=ROOT/'reports/audits/dissertation/network-main-final-v1/source-metadata/execution-manifest.json'
    assert sha(manifest)==pre['sourceManifestSha256']==post['sourceManifestSha256']
    original=read(manifest)
    assert len(original['files'])==post['sourceFilesVerified']==281
    assert sha(program/'program-seal.json')==post['programSealSha256']==pre['programSealSha256']
    seal=read(program/'program-seal.json')
    for name,digest in seal['files'].items():assert sha(program/name)==digest,name
    for name,digest in pre['pinnedFiles'].items():
        assert sha(raw/name)==digest,name
        if name.startswith('runtime/'):assert digest==original['files'][name],name
    a,b=[read(raw/f'configs/q{q}.json') for q in (1,1024)]
    assert set(a)==set(b) and {k for k in a if a[k]!=b[k]}=={'queueCapacity'}
    assert (a['queueCapacity'],b['queueCapacity'])==(1,1024)
    assert (a['warmupSeconds'],a['measurementSeconds'],a['drainTimeoutSeconds'])==(.25,2,10)
    metrics=('apiAttempts','sourceCompleted','expectedDeliveries','uniqueDeliveries','uniqueDuringDrain',
             'fullyDeliveredPublications','deliveryLoss','receiverP99Ms','publishP99Ms','queueDrops',
             'duplicateDeliveries','forwardAttempts','suppressedDuplicates','hopLimitDrops','backpressureUnsent')
    results=[];cohorts=[];starts=[];recounts=[]
    for q in (1,1024):
        attempt=raw/f'q{q}/attempt-001';directory=attempt/'output';execution=read(attempt/'result.json')
        assert execution['returnCode']==0 and execution['technicalError'] is None
        summary=verify_loaded_run(directory)
        assert summary==execution['summary'] and summary['status']=='completed'
        config=read(raw/f'configs/q{q}.json');executed=read(directory/'run-config.json')
        assert all(executed.get(k)==v for k,v in config.items())
        assert sha(directory/'inputs/config.json')==sha(raw/f'configs/q{q}.json')==sha(program/f'configs/q{q}.json')
        check_graph(directory/'topology.graphml',executed)
        for name,digest in execution['scientificSha256'].items():assert sha(directory/name)==digest
        publications=read(directory/'publications.json');all_ids={p['id']:p for p in publications}
        assert len(all_ids)==len(publications)
        cohort={p['id']:p for p in publications if p['measurement']}
        phases=read(directory/'phases.json');start=next(p['startTick'] for p in phases if p['name']=='measure')
        assert all(p['status']=='completed' for p in phases)
        receiver_sites=set(config['receiverSites']);first={};duplicates=0
        for receipt in read(directory/'delivery-receipts.json'):
            pub=all_ids[receipt['publicationId']]
            payload=base64.b64decode(pub['payload'],validate=True)
            assert len(payload)==config['payloadBytes']
            assert hashlib.sha256(payload).hexdigest()==receipt['payloadSha256']
            if pub['id'] not in cohort:continue
            assert receipt['receiver'] in receiver_sites and receipt['tick']>=pub['tick']
            pair=(pub['id'],receipt['receiver'])
            if pair in first:duplicates+=1
            first[pair]=min(first.get(pair,receipt['tick']),receipt['tick'])
        expected=len(cohort)*len(receiver_sites)
        latencies=sorted(stamp-cohort[pair[0]]['tick'] for pair,stamp in first.items())
        p99=latencies[math.ceil(.99*len(latencies))-1]*summary['tickMilliseconds']
        deliveries_by_id=Counter(pair[0] for pair in first)
        independent={'apiAttempts':len(cohort),'expectedDeliveries':expected,'uniqueDeliveries':len(first),
                     'deliveryLoss':1-len(first)/expected,'receiverP99Ms':p99,'duplicateDeliveries':duplicates,
                     'fullyDeliveredPublications':sum(n==len(receiver_sites) for n in deliveries_by_id.values())}
        for key,value in independent.items():assert close(value,summary[key]),(q,key,value,summary[key])
        assert expected==8000 and len(cohort)==2000
        results.append({'queueCapacity':q,'metrics':{k:summary[k] for k in metrics},
                        'processWallSeconds':execution['processWallSeconds'],'peakRssBytes':execution['peakRssBytes'],
                        'phaseTicks':{p['name']:p['endTick']-p['startTick'] for p in phases}})
        cohorts.append(cohort);starts.append(start);recounts.append(independent)
    x,y=cohorts
    identities=lambda c:{i:(p['origin'],p['sequence'],p['publisher']) for i,p in c.items()}
    identity_equal=identities(x)==identities(y)
    relative_ticks_equal=all(x[i]['tick']-starts[0]==y[i]['tick']-starts[1] for i in x)
    payload_equal=all(x[i]['payload']==y[i]['payload'] for i in x)
    timestamp_only=True
    for i in x:
        px,py=base64.b64decode(x[i]['payload']),base64.b64decode(y[i]['payload'])
        timestamp_only &= px[:8]==py[:8] and int.from_bytes(px[8:],'little')==x[i]['tick'] and int.from_bytes(py[8:],'little')==y[i]['tick']
    assert identity_equal and relative_ticks_equal and timestamp_only
    assert starts==[2715,6302] and not payload_equal
    final=read(raw/'result.json')
    assert final['status']=='completed' and final['classification']=='queueCapacityContrast'
    assert results[0]['metrics']['queueDrops']>0 and results[1]['metrics']['queueDrops']==0
    assert final['sourceSnapshotUnchanged'] and final['pinnedInputsUnchanged'] and not final['retryPerformed']
    protected=read(ROOT/'reports/audits/dissertation/chapter-drafts-v2/source-before.json')['protectedFiles']
    for name,digest in protected.items():assert sha(ROOT/name)==digest
    receipt={'status':'accepted','rawRunsPassed':2,'filesVerified':len(index),'pinnedFilesVerified':len(pre['pinnedFiles']),
             'sealedProgramFilesVerified':len(seal['files']),'sourceSnapshotFilesVerified':281,
             'classification':final['classification'],'sharedWallSeconds':final['sharedWallSeconds'],
             'singleChangedConfigFactor':'queueCapacity','results':results,'separateReceiptRecounts':recounts,
             'sourceIdentityEqual':identity_equal,'sourceScheduleRelativeToMeasurementEqual':relative_ticks_equal,
             'sourcePayloadBytesEqual':payload_equal,'payloadDifferenceIsAbsoluteTickFieldOnly':timestamp_only,
             'measurementStartTicks':starts,'measurementStartShiftTicks':starts[1]-starts[0],
             'supportsQueueCapacityEffectInDeclaredEpisode':True,'mainSeriesLossCauseEstablished':False,
             'scope':'One seed; queue capacity changes the whole episode including warmup drain. Separate local verifier execution plus separate receipt/ID arithmetic; not a human replication.',
             'archiveSha256':transfer['archiveSha256'],'newExperimentsDuringAcceptance':0,'humanReviewPassed':False,
             'protectedLaTexFilesVerified':len(protected),
             'localVerifierSha256':{n:sha(ROOT/n) for n in ('tools/dissertation/network_study_verification.py','tools/dissertation/network_review.py')},
             'acceptanceProcedureSha256':sha(Path(__file__))}
    (HERE/'acceptance.json').write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('results','separateReceiptRecounts','localVerifierSha256')}))

if __name__=='__main__':main()
