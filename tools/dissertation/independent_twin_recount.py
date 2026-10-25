"""Second implementation of bounded single-subscriber arithmetic; standard library only."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path


def checked(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('Expected a finite nonnegative observation')
    return value


def calculate(publications,deliveries,phases):
    start,end,horizon=[checked(phases[k]) for k in ('measurementStartSeconds','measurementEndSeconds','drainEndSeconds')]
    if not start<end<=horizon or phases.get('timeOrigin')!='sharedMonotonic' or phases.get('completed') is not True:
        raise ValueError('Incomplete or unsupported observation window')
    qos=phases['qos']
    if isinstance(qos,bool) or qos not in (0,1,2):raise ValueError('Invalid QoS')
    records={}
    for row in publications:
        identifier=row['id'];kind=row['event'];checked(row['elapsedSeconds'])
        if not isinstance(identifier,str) or not identifier or kind not in ('attempt','completed','failed'):
            raise ValueError('Invalid publication event')
        entry=records.setdefault(identifier,{})
        if kind=='attempt':
            if 'attempt' in entry:raise ValueError('Repeated attempt ID')
            entry['attempt']=row
        else:
            if 'outcome' in entry:raise ValueError('Repeated outcome ID')
            if kind=='completed' and qos:checked(row['latencyMs'])
            entry['outcome']=row
    measured={}
    for identifier,entry in records.items():
        attempt=entry.get('attempt');outcome=entry.get('outcome')
        if attempt is None or (outcome and outcome['elapsedSeconds']<attempt['elapsedSeconds']):
            raise ValueError('Outcome without preceding attempt')
        if start<=attempt['elapsedSeconds']<end:measured[identifier]=entry
    done=set();failed=set();latencies=[];done_in_window=0
    for identifier,entry in measured.items():
        outcome=entry.get('outcome')
        if outcome is None or outcome['elapsedSeconds']>horizon:continue
        if outcome['event']=='completed':
            done.add(identifier)
            done_in_window+=outcome['elapsedSeconds']<end
            if qos:latencies.append(outcome['latencyMs'])
        else:failed.add(identifier)
    first={};counts=Counter();outside=0
    for row in deliveries:
        stamp=checked(row['elapsedSeconds']);identifier=row['id']
        if identifier in records and stamp<records[identifier]['attempt']['elapsedSeconds']:
            raise ValueError('Receipt before attempt')
        if identifier not in measured or stamp<start or stamp>horizon:
            outside+=1;continue
        if stamp<measured[identifier]['attempt']['elapsedSeconds']:raise ValueError('Receipt before attempt')
        counts[identifier]+=1
        first[identifier]=min(first.get(identifier,stamp),stamp)
    latencies.sort();attempted=len(measured);completed=len(done);pending=attempted-completed-len(failed)
    received_done=sum(identifier in first for identifier in done);seconds=end-start
    return dict(attempted=attempted,completed=completed,failed=len(failed),pending=pending,measurementSeconds=seconds,
        attemptedRps=attempted/seconds,completedInWindow=done_in_window,completedRpsInWindow=done_in_window/seconds,
        completedDuringDrain=completed-done_in_window,completedCohortPerSubmissionSecond=completed/seconds,
        uniqueDeliveries=len(first),completedIdsDelivered=received_done,deliveredAfterFailedPublish=sum(i in first for i in failed),
        firstDeliveriesDuringDrain=sum(t>=end for t in first.values()),duplicates=sum(n-1 for n in counts.values()),
        outsideCohortDeliveryEvents=outside,publishFailureRate=len(failed)/attempted if attempted else None,
        deliveryLossRate=(completed-received_done)/completed if completed else None,
        p99PublishMs=latencies[(99*len(latencies)+99)//100-1] if latencies else None,
        latencyStatus='notApplicable' if not qos else 'observed' if latencies else 'unobserved',outcomesSettled=pending==0)


def read_events(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        for line in stream:
            if line.strip():yield json.loads(line)


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def connection_and_subscription(directory,phases,scope):
    start=phases['measurementStartSeconds'];end=phases['drainEndSeconds']
    rows=[r for r in read_events(directory/'connections.ndjson') if r.get('role')=='subscriber' or r.get('client')=='subscriber']
    ids={r.get('clientId') for r in rows}
    if len(ids)!=1 or not all(ids):raise ValueError('Ambiguous or missing subscriber identity')
    rows.sort(key=lambda r:r['elapsedSeconds'])
    before=[r for r in rows if r['elapsedSeconds']<=start]
    connected=bool(before) and before[-1]['state']=='connected' and not any(
        r['state']!='connected' for r in rows if start<r['elapsedSeconds']<end)
    if not connected:raise ValueError('Subscriber interrupted or unobserved')
    subs=[r for r in read_events(directory/'subscriptions.ndjson') if r.get('state','subscribed')=='subscribed']
    if len(subs)!=2 or any(r.get('clientId') not in ids or r.get('requestedQos')!=phases['qos'] or
        r.get('grantedQos')!=phases['qos'] or not 0<=r['elapsedSeconds']<=start for r in subs):
        raise ValueError('Missing or incorrect subscription evidence')
    topics={r['topic'] for r in subs}
    if scope=='model':
        if topics!={'telemetry/+','local/telemetry/+'} or phases.get('connectionObservation')!='everyModelTick':
            raise ValueError('Unexpected model filters or observation policy')
    else:
        main=[t for t in topics if not t.endswith('/warmup')]
        if len(main)!=1 or topics!={main[0],main[0]+'/warmup'}:raise ValueError('Warmup topic mismatch')
    return dict(continuous=True,subscriberIdentities=1,confirmedSubscriptions=2)


def audit(root,index_path,output):
    root=Path(root).resolve(strict=True);output=Path(output);output.mkdir(exist_ok=False,parents=True)
    index=json.loads(Path(index_path).read_text(encoding='utf-8-sig'))
    for relative,expected in index['files'].items():
        path=(root/relative).resolve()
        if root not in path.parents or path.stat().st_size!=expected['bytes'] or sha(path)!=expected['sha256']:
            raise ValueError('Raw export SHA mismatch: '+relative)
    summary=json.loads((root/'meta/summary.json').read_text(encoding='utf-8-sig'))
    rows=[];metrics_verified=0
    for case in summary['cases']:
        paired={}
        for scope in ('native','model'):
            directory=root/'cases'/case['key']/scope
            phases=json.loads((directory/'phases.json').read_text(encoding='utf-8-sig'))
            result=calculate(read_events(directory/'publishes.ndjson'),read_events(directory/'deliveries.ndjson'),phases)
            for key,value in result.items():
                expected=case[scope]['metrics'][key]
                # Arithmetic order may change the last floating bit; counts remain exact.
                equal=(value==expected) or (isinstance(value,float) and isinstance(expected,(float,int)) and
                    math.isclose(value,expected,rel_tol=1e-12,abs_tol=1e-12))
                if not equal:raise ValueError(f'{case["key"]}/{scope}/{key}: {value} != {expected}')
                metrics_verified+=1
            checks=connection_and_subscription(directory,phases,scope)
            paired[scope]=dict(metrics=result,connection=checks)
        a=paired['native']['metrics'];b=paired['model']['metrics']
        errors=dict(rps=abs(b['completedCohortPerSubmissionSecond']-a['completedCohortPerSubmissionSecond'])/a['completedCohortPerSubmissionSecond'],
                    p99=abs(b['p99PublishMs']-a['p99PublishMs'])/a['p99PublishMs'],
                    failure=abs(b['publishFailureRate']-a['publishFailureRate']),loss=abs(b['deliveryLossRate']-a['deliveryLossRate']))
        rows.append(dict(key=case['key'],broker=case['broker'],windowSeconds=case['window'],**paired,errors=errors,
                         oldProfileAllCriteriaPassed=all(errors[k]<=limit for k,limit in [('rps',.15),('p99',.20),('failure',.05),('loss',.05)])))
    receipt=dict(status='verified',pairs=len(rows),rawFilesVerified=len(index['files']),metricValuesCompared=metrics_verified,
        calculatorSha256=sha(__file__),indexSha256=sha(index_path),rows=rows,
        scope='Independent arithmetic implementation; raw connection evidence; not a new calibration or blind validation')
    (output/'verification.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (output/'comparison.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(['case','nativeRps','modelRps','nativeP99Ms','modelP99Ms','nativeLoss','modelLoss','rpsRelativeError','p99RelativeError','lossAbsoluteError','allOldProfileCriteriaPassed'])
        for row in rows:
            a=row['native']['metrics'];b=row['model']['metrics'];e=row['errors']
            writer.writerow([row['key'],a['completedCohortPerSubmissionSecond'],b['completedCohortPerSubmissionSecond'],a['p99PublishMs'],b['p99PublishMs'],a['deliveryLossRate'],b['deliveryLossRate'],e['rps'],e['p99'],e['loss'],row['oldProfileAllCriteriaPassed']])
    print(json.dumps({k:v for k,v in receipt.items() if k!='rows'},ensure_ascii=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-root',required=True)
    parser.add_argument('--index',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args();audit(args.archive_root,args.index,args.output)
