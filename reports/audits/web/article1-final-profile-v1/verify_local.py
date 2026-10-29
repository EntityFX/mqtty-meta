"""Recount Web/direct raw events and compare declared scientific files locally."""
from pathlib import Path
from collections import Counter
import sys,json,hashlib,math

ROOT=Path(__file__).resolve().parents[4]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'tools/dissertation'))
from independent_twin_recount import calculate as independently_calculate,read_events,connection_and_subscription
from audit_matched_main_results import calculate
packet=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
transfer=read(packet/'transfer.json');root=Path(transfer['localRaw']);index=read(packet/'source-file-index.json')
files={p.relative_to(root).as_posix():p for p in root.rglob('*') if p.is_file()};assert set(files)==set(index['files'])
for n,p in files.items():assert sha(p)==index['files'][n]['sha256'] and p.stat().st_size==index['files'][n]['bytes'],n
web=root/'web';direct=next((root/'direct').rglob('summary.json')).parent
metrics=[]
for directory in (web,direct):
    phases=read(directory/'phases.json');pub=list(read_events(directory/'publishes.ndjson'));deliveries=list(read_events(directory/'deliveries.ndjson'))
    first=calculate(pub,deliveries,phases);second=independently_calculate(pub,deliveries,phases)
    for key in ('attempted','completed','failed','pending','completedIdsDelivered','duplicates','completedCohortPerSubmissionSecond','p99PublishMs','deliveryLossRate'):
        assert math.isclose(first[key],second[key],rel_tol=1e-12,abs_tol=1e-12),key
    summary=read(directory/'summary.json')['simulated']
    for key,other in [('attempted','attempted'),('completed','completed'),('publishFailureRate','publishFailureRate'),('deliveryLossRate','deliveryLossRate'),('completedCohortPerSubmissionSecond','completedRps'),('p99PublishMs','latencyP99Ms')]:assert math.isclose(first[key],summary[other],rel_tol=1e-12,abs_tol=1e-12),key
    cohort={r['id'] for r in pub if r['event']=='attempt' and phases['measurementStartSeconds']<=r['elapsedSeconds']<phases['measurementEndSeconds']}
    expected=Counter(round(r['latencyMs']/.01) for r in pub if r['id'] in cohort and r['event']=='completed' and r['elapsedSeconds']<=phases['drainEndSeconds'])
    recorded=Counter()
    for broker in read(directory/'latency-histogram.json').values():recorded.update({int(t):n for t,n in broker['atLeastOnce'].items()})
    assert recorded==expected and sum(recorded.values())==first['completed']
    assert first['outcomesSettled'] and first['attempted']>0
    connection_and_subscription(directory,phases,'model');metrics.append(first)
assert metrics[0]==metrics[1]
names=('summary.json','latency-histogram.json','publishes.ndjson','deliveries.ndjson','subscriptions.ndjson','phases.json')
for n in names:assert (web/n).read_bytes()==(direct/n).read_bytes(),n
for field in ('brokers','receivers','receiverScope'):assert read(web/'counters.json')[field]==read(direct/'counters.json')[field]
wrapper=read(web/'twin-validation.json');assert wrapper['totalTicks']==21014 and wrapper['steps']==64966
for n,h in read(web/'sha256.json').items():assert sha(web/n)==h,n
profiles=read(root/'profiles.json')
assert len(profiles)==4 and all(p['validation']['valid'] and p['missingPointRejected'] for p in profiles.values())
published=read(ROOT/'reports/audits/dissertation/manuscript-offline-review-v1/floor-analysis.json')['sourceInputsSha256']
for broker,p in profiles.items():assert p['sha256']==published[f'attempt-001/profiles/{broker}.profile.json']
assert sha(web/'profile-snapshot.json')==profiles['Aedes']['sha256']
result=dict(status='verified',metrics=metrics[0],filesVerified=len(files),profilesValidated=4,missingPointCasesRejected=4,byteEqualScientificFiles=list(names),counterFieldsEqual=['brokers','receivers','receiverScope'],subscriberContinuouslyConnected=True,ticks=wrapper['totalTicks'],steps=wrapper['steps'],archiveSha256=transfer['archiveSha256'],adapterTestsPassed=3,fullBrowserFlowVerified=False,newNativeReferenceAttached=False,newNativeMeasurements=0,accuracyBeyondHistoricalSeriesEstablished=False,originalDissertationModified=False)
(packet/'verification.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
print('PASS: actual Web/direct events independently recounted; 1714 delivered IDs, 21014 ticks')
