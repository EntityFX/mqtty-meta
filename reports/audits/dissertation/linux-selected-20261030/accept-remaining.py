"""Recount partial4096 and completed1024, without weakening completion gates."""
import csv
import base64
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent/'remaining-result'
REPO=HERE.parents[4]
sys.path.insert(0,str(REPO/'tools/dissertation'))
from run_acceleration_pilot import read,digest,verify_files
from network_study_verification import verify_loaded_run
from network_review import check_graph
from queue_window_analysis import delivery_window
from run_queue_remaining import admit,BASELINE_SHA


def main():
    transfer=read(HERE/'transfer.json');raw=Path(transfer['localRaw']);program=Path(transfer['localProgram']);collection=raw.parent
    assert digest(transfer['localArchive'])==transfer['archiveSha256']
    assert digest(HERE/'source-file-index.json')==transfer['sourceIndexSha256']
    index=read(HERE/'source-file-index.json')['files']
    actual={p.relative_to(collection).as_posix():p for p in collection.rglob('*') if p.is_file()}
    assert set(actual)==set(index)|{'metadata/index.json'}
    for name,row in index.items():assert actual[name].stat().st_size==row['bytes'] and digest(actual[name])==row['sha256'],name
    seal=read(program/'seal.json');verify_files(program,seal['files'])
    assert digest(program/'seal.json')==read(HERE.parent/'remaining-admission.json')['sealSha256']
    baseline=collection/'baseline';candidate=raw/'attempt-001/output'
    row=read(collection/'metadata/baseline-row.json')
    assert digest(collection/'metadata/baseline-row.json')==BASELINE_SHA
    admit(read(baseline/'inputs/config.json'),read(candidate/'inputs/config.json'),row['processWallSeconds'],1800)
    for name,sha in row['scientificSha256'].items():assert digest(baseline/name)==sha,name
    execution=read(raw/'attempt-001/execution.json')
    assert execution['returnCode']==2 and execution['technicalError'] is None and execution['processWallSeconds']<1800
    windows=[];identities=[];schedules=[]
    for capacity,directory in ((1024,baseline),(4096,candidate)):
        summary=verify_loaded_run(directory);config=read(directory/'run-config.json')
        check_graph(directory/'topology.graphml',config)
        start=next(p['startTick'] for p in summary['phases'] if p['name']=='measure')
        drain=next(p for p in summary['phases'] if p['name']=='drain')
        if capacity==4096:
            assert summary['status']=='failed' and 'drain timed out' in summary['error']
            assert drain['status']=='failed' and drain['endTick']-drain['startTick']==300000
        else:
            assert summary==row['summary'] and summary['status']=='completed'
        pubs=read(directory/'publications.json');receipts=read(directory/'delivery-receipts.json')
        receivers=config['receiverSites'] or list(range(config['brokers']))
        # Common40-second observation since measurement start. A completed reference is idle afterwards.
        cutoff=start+400000
        assert drain['endTick']<=cutoff
        counted=delivery_window(pubs,receipts,receivers,start,cutoff,.0001)
        assert counted['uniquePairs']==summary['uniqueDeliveries']
        assert counted['expectedPairs']==summary['expectedDeliveries']==200000
        assert counted['sourcePublications']==summary['apiAttempts']==summary['sourceCompleted']==50000
        assert math.isclose(counted['p99ReceivedMs'],summary['receiverP99Ms'],rel_tol=1e-12)
        assert math.isclose(counted['notObservedFraction'],summary['deliveryLoss'],abs_tol=1e-12)
        forwards=read(directory/'transmissions.json')
        tail=[t for t in forwards if t['measurement'] and t['role']=='forward' and t['tick']>cutoff-10000]
        window=dict(queueCapacity=capacity,status=summary['status'],observationSeconds=40,
            measurementStartTick=start,actualObservationEndSeconds=(drain['endTick']-start)*.0001,
            metrics={k:summary[k] for k in ('uniqueDeliveries','expectedDeliveries','queueDrops','fullyDeliveredPublications','receiverP99Ms')},
            independentCount={k:v for k,v in counted.items() if k!='curve'},
            forwardingEventsInLastModelSecond=len(tail),curve=counted['curve'])
        windows.append(window)
        identities.append({p['id']:(p['origin'],p['sequence'],p['publisher']) for p in pubs if p['measurement']})
        schedules.append({p['id']:p['tick']-start for p in pubs if p['measurement']})
    source_identity_equal=identities[0]==identities[1]
    relative_schedule_equal=schedules[0]==schedules[1]
    assert source_identity_equal and relative_schedule_equal
    x={p['id']:p for p in read(baseline/'publications.json') if p['measurement']}
    y={p['id']:p for p in read(candidate/'publications.json') if p['measurement']}
    payload_equal=all(x[i]['payload']==y[i]['payload'] for i in x)
    timestamp_only=True
    for identity in x:
        a,b=base64.b64decode(x[identity]['payload']),base64.b64decode(y[identity]['payload'])
        timestamp_only &= a[:8]==b[:8] and int.from_bytes(a[8:],'little')==x[identity]['tick'] and int.from_bytes(b[8:],'little')==y[identity]['tick']
    assert not payload_equal and timestamp_only
    assert windows[1]['independentCount']['uniqueInLastModelSecond']>0
    protected=read(REPO/'reports/audits/dissertation/chapter-drafts-v2/source-before.json')['protectedFiles']
    for name,sha in protected.items():assert digest(REPO/name)==sha,name
    receipt=dict(status='partialEvidenceAccepted',classification='finiteWindowQueueCapacityContrast',
        completedPairAccepted=False,partialRunPromotedToCompleted=False,filesVerified=len(index),
        sourceIdentityEqual=source_identity_equal,relativeEmissionScheduleEqual=relative_schedule_equal,
        sourcePayloadBytesEqual=payload_equal,payloadDifferenceIsAbsoluteTickFieldOnly=timestamp_only,
        windows=windows,termination='modelDrainDeadline30Seconds',wallDeadlineReached=False,
        newModelRunsExecutedDuringAcceptance=0,protectedLaTexFilesVerified=len(protected))
    (HERE/'acceptance.json').write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
    with (HERE/'delivery-curve.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['queueCapacity','secondsFromMeasurementStart','uniquePairs','expectedPairs','fractionDelivered'])
        for window in windows:
            for point in window['curve']:
                writer.writerow([window['queueCapacity'],point['seconds'],point['uniquePairs'],point['expectedPairs'],point['uniquePairs']/point['expectedPairs']])
    print(json.dumps({k:v for k,v in receipt.items() if k!='windows'}))
    print(json.dumps([{k:v for k,v in window.items() if k!='curve'} for window in windows]))


if __name__=='__main__':main()
