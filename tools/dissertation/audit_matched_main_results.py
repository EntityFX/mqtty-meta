"""Recount a transferred frozen 40-run series; never runs experiments or alters inputs."""
import argparse
import base64
from collections import Counter
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import tarfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
from independent_twin_recount import connection_and_subscription, read_events, sha

BROKERS=('Aedes','Mosquitto','ActiveMQ','EMQX')
FIELDS={'rps':'completedCohortPerSubmissionSecond','p99':'p99PublishMs',
        'failure':'publishFailureRate','loss':'deliveryLossRate'}
LIMITS=dict(rps=.15,p99=.20,failure=.05,loss=.05)
QUANTILES=dict(minMs=0,p50Ms=.5,p75Ms=.75,p95Ms=.95,p99Ms=.99,maxMs=1)


def calculate(publications,deliveries,phases):
    """Separate cohort recount: join event tables by ID, retaining measured attempts only."""
    begin,stop,horizon=(phases[k] for k in ('measurementStartSeconds','measurementEndSeconds','drainEndSeconds'))
    if not 0<=begin<stop<=horizon or phases['timeOrigin']!='sharedMonotonic' or phases['completed'] is not True:raise ValueError('Invalid phases')
    attempts={};outcomes={}
    for row in publications:
        if not math.isfinite(row['elapsedSeconds']) or row['elapsedSeconds']<0:raise ValueError('Invalid event time')
        table=attempts if row['event']=='attempt' else outcomes
        if row['event'] not in ('attempt','completed','failed') or row['id'] in table:raise ValueError('Repeated/invalid event')
        table[row['id']]=row
    for identifier,row in outcomes.items():
        if identifier not in attempts or row['elapsedSeconds']<attempts[identifier]['elapsedSeconds']:raise ValueError('Orphan/time-reversed outcome')
    selected={identifier for identifier,row in attempts.items() if begin<=row['elapsedSeconds']<stop}
    finished={identifier for identifier,row in outcomes.items() if identifier in selected and row['elapsedSeconds']<=horizon and row['event']=='completed'}
    failed={identifier for identifier,row in outcomes.items() if identifier in selected and row['elapsedSeconds']<=horizon and row['event']=='failed'}
    observed={};outside=0
    for row in deliveries:
        identifier=row['id'];stamp=row['elapsedSeconds']
        if not math.isfinite(stamp) or stamp<0:raise ValueError('Invalid receipt time')
        if identifier in attempts and stamp<attempts[identifier]['elapsedSeconds']:raise ValueError('Receipt before attempt')
        if identifier not in selected or not begin<=stamp<=horizon:outside+=1;continue
        observed.setdefault(identifier,[]).append(stamp)
    n=len(selected);done=len(finished);duration=stop-begin
    window_done=sum(outcomes[identifier]['elapsedSeconds']<stop for identifier in finished)
    pending=n-done-len(failed);delivered=len(finished.intersection(observed))
    latency=sorted(outcomes[identifier]['latencyMs'] for identifier in finished) if phases['qos'] else []
    return dict(attempted=n,completed=done,failed=len(failed),pending=pending,measurementSeconds=duration,
        attemptedRps=n/duration,completedInWindow=window_done,completedRpsInWindow=window_done/duration,
        completedDuringDrain=done-window_done,completedCohortPerSubmissionSecond=done/duration,
        uniqueDeliveries=len(observed),completedIdsDelivered=delivered,deliveredAfterFailedPublish=len(failed.intersection(observed)),
        firstDeliveriesDuringDrain=sum(min(stamps)>=stop for stamps in observed.values()),
        duplicates=sum(len(stamps)-1 for stamps in observed.values()),outsideCohortDeliveryEvents=outside,
        publishFailureRate=len(failed)/n if n else None,deliveryLossRate=(done-delivered)/done if done else None,
        p99PublishMs=latency[math.ceil(.99*len(latency))-1] if latency else None,
        latencyStatus='notApplicable' if phases['qos']==0 else 'observed' if latency else 'unobserved',outcomesSettled=pending==0)


def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def assert_close(actual,expected,label):
    if isinstance(actual,bool) or isinstance(expected,bool):
        equal=type(actual)==type(expected) and actual==expected
    elif isinstance(actual,int) and isinstance(expected,int):
        equal=actual==expected
    elif isinstance(actual,(float,int)) and isinstance(expected,(float,int)):
        equal=math.isfinite(actual) and math.isfinite(expected) and math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-12)
    else:equal=actual==expected
    if not equal:raise ValueError(f'{label}: {actual!r} != {expected!r}')


def summarize(values):
    if len(values)!=3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in values):
        raise ValueError('Exactly three finite run-level observations required')
    average=statistics.mean(values);deviation=statistics.stdev(values);half=4.30265272975*deviation/math.sqrt(3)
    return dict(mean=average,min=min(values),max=max(values),standardDeviation=deviation,
                ci95HalfWidth=half,ci95Lower=average-half,ci95Upper=average+half,count=3)


def compare(native,model):
    errors={key:abs(model[key]-native[key])/native[key] if key in ('rps','p99') and native[key]>0
            else abs(model[key]-native[key]) if key in ('failure','loss') else None for key in FIELDS}
    return dict(errors=errors,allCriteriaPassed=all(value is not None and value<=LIMITS[key] for key,value in errors.items()))


def histogram(rows,phases):
    cohort={r['id'] for r in rows if r['event']=='attempt' and phases['measurementStartSeconds']<=r['elapsedSeconds']<phases['measurementEndSeconds']}
    bins=Counter(round(r['latencyMs']*1000) for r in rows if r['event']=='completed' and r['id'] in cohort and r['elapsedSeconds']<=phases['drainEndSeconds'])
    if not bins:raise ValueError('No measured latency observations')
    return [list(pair) for pair in sorted(bins.items())]


def quantiles(bins):
    samples=sum(count for _,count in bins);out={}
    for name,probability in QUANTILES.items():
        rank=max(1,math.ceil(samples*probability));accumulated=0
        for micros,count in bins:
            accumulated+=count
            if accumulated>=rank:out[name]=micros/1000;break
    return out


def verify_seal(root,files,allow_omitted_trials=False):
    count=0
    for relative,expected in files.items():
        path=(root/relative).resolve()
        if root.resolve() not in path.parents:raise ValueError('Path escapes raw root')
        if allow_omitted_trials and Path(relative).parts[0]=='trial':continue
        if not path.is_file() or sha(path)!=expected:raise ValueError('SHA mismatch: '+str(path))
        count+=1
    return count


def replay_hashes(root):
    result={name:sha(root/name) for name in ('summary.json','latency-histogram.json','publishes.ndjson','deliveries.ndjson','subscriptions.ndjson','phases.json')}
    counters=load(root/'counters.json');scientific={key:counters[key] for key in ('brokers','receivers','receiverScope')}
    result['counters-scientific.json']=hashlib.sha256(json.dumps(scientific,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result


def verify_profile(root,broker,jobs,recounts,freeze):
    directory=root/'attempt-001/profiles';profile_path=directory/f'{broker}.profile.json'
    profile=load(profile_path);observations=load(directory/f'{broker}.observations.json');hist_path=directory/f'{broker}.histograms.json';hist=load(hist_path)
    assert_close(sha(profile_path),freeze['profiles'][broker]['sha256'],broker+' frozen profile')
    if profile.get('schemaVersion')!=4 or profile['calibration']['provenance'].get('coverageProtocol')!='single-point-v1':
        raise ValueError('Not a strict single-point empirical profile')
    if base64.b64decode(profile['histogramsBase64'],validate=True)!=hist_path.read_bytes():raise ValueError('Embedded histogram mismatch')
    for provenance in (profile['provenance'],profile['calibration']['provenance']):
        assert_close(provenance['observationsSha256'],sha(directory/f'{broker}.observations.json'),broker+' observations SHA')
    assert_close(profile['provenance']['latencyHistogramsSha256'],sha(hist_path),broker+' histogram SHA')
    assert_close(hist['observationsSha256'],sha(directory/f'{broker}.observations.json'),broker+' linked histogram')
    if observations['runCount']!=3 or len(observations['points'])!=1 or len(hist['points'])!=1 or len(profile['calibration']['points'])!=1:
        raise ValueError('Incomplete or unexpected point coverage')
    point=observations['points'][0];target=profile['calibration']['points'][0];hpoint=hist['points'][0]
    for value in (point,target,hpoint):
        if (value['broker'],value['messageBytes'],value['qos'],value['publishers'])!=(broker,16,1,16):raise ValueError('Wrong exact point')
    if sorted(r['repeat'] for r in hpoint['repeats'])!=[1,2,3]:raise ValueError('Missing histogram repeat')
    merged=Counter();qs=[];archive_files=0
    for repeat in (1,2,3):
        key=f'calibration.{broker}.r{repeat}';raw=jobs[key]/'raw';rec=recounts[key]
        rows=list(read_events(raw/'publishes.ndjson'));phases=load(raw/'phases.json');bins=histogram(rows,phases)
        given=next(r for r in hpoint['repeats'] if r['repeat']==repeat)
        assert_close(given['bins'],bins,key+' bins');assert_close(given['completedPublishes'],rec['completed'],key+' histogram sample count')
        assert_close(given['actualMeasurementSeconds'],rec['measurementSeconds'],key+' histogram duration')
        archive=directory/f'{broker}.r{repeat}.native.tar.gz'
        assert_close(sha(archive),given['archiveSha256'],key+' archive SHA')
        assert_close(observations['provenance']['inputSha256']['archives/'+archive.name],sha(archive),key+' observation archive SHA')
        with tarfile.open(archive,'r:gz') as package:
            members=[m for m in package.getmembers() if m.isfile()]
            names={m.name for m in members};actual_names={str(p.relative_to(raw)).replace('\\','/') for p in raw.rglob('*') if p.is_file()}
            if names!=actual_names:raise ValueError('Calibration archive file set mismatch')
            for member in members:
                stream=package.extractfile(member);digest=hashlib.file_digest(stream,'sha256').hexdigest()
                assert_close(digest,sha(raw/member.name),key+' archive '+member.name);archive_files+=1
        for micros,count in bins:merged[micros]+=count
        qs.append(quantiles(bins))
    for out,key in [('attemptedRps','attemptedRps'),('completedRps',FIELDS['rps']),('publishFailureRate',FIELDS['failure']),('deliveryLossRate',FIELDS['loss'])]:
        stats=summarize([recounts[f'calibration.{broker}.r{r}'][key] for r in (1,2,3)])
        for name,value in stats.items():assert_close(point[out][name],value,broker+'/'+out+'/'+name)
    mapped={'attemptedRps':'attemptedRps','targetCompletedRps':'completedRps','targetPublishFailureRate':'publishFailureRate','conditionalDeliveryLossRate':'deliveryLossRate'}
    for name,source in mapped.items():
        assert_close(target[name],point[source]['mean'],broker+'/'+name)
    rejection=target['rateRejectionRate'];failure=point['publishFailureRate']['mean']
    assert_close(target['capacityRps'],point['attemptedRps']['mean']*(1-rejection),broker+'/capacity')
    assert_close(target['conditionalPublishFailureRate'],(failure-rejection)/(1-rejection),broker+'/conditionalFailure')
    for name in QUANTILES:
        expected=statistics.mean(q[name] for q in qs)
        assert_close(point['observedLatencyQuantiles'][name],expected,broker+'/'+name)
        assert_close(target['observedLatencyQuantiles'][name],expected,broker+'/target/'+name)
    return dict(profileSha256=sha(profile_path),observationsSha256=sha(directory/f'{broker}.observations.json'),
                histogramSha256=sha(hist_path),realArchiveFilesCompared=archive_files,
                mergedLatencySamples=sum(merged.values()),mergedBins=len(merged),repeatCount=3,exactPoint=[16,1,16])


def audit(root,output):
    root=Path(root).resolve(strict=True);output=Path(output)
    if output.exists():raise ValueError('Use a new output directory')
    program=load(root/'program.json');freeze=load(root/'profile-freeze.json');summary=load(root/'attempt-001/summary.json')
    program_sha=sha(root/'program.json');assert_close(freeze['programSha256'],program_sha,'frozen program')
    if not freeze['beforeControl'] or not program['admission']['admitted'] or summary['completed']!=40 or summary['status']!='completed':raise ValueError('Campaign incomplete or not admitted')
    expected={r['key']:r for r in program['jobs']}
    exact={f'{stage}.{broker}.r{repeat}' for stage in ('calibration','control','model','replay') for broker in BROKERS for repeat in ((1,) if stage=='replay' else (1,2,3))}
    if set(expected)!=exact or len(program['jobs'])!=40:raise ValueError('Program is not the exact predefined 40-run series')
    jobs={};recounts={};counts=0;metrics_compared=0;phase_rows=[];control_times=[]
    for success in sorted(root.glob('attempt-*/jobs/*/success.json')):
        receipt=load(success);key=receipt['job']['key'];directory=success.parent
        if key in jobs or receipt['job']!=expected[key]:raise ValueError('Duplicate or unplanned job')
        assert_close(receipt['programSha256'],program_sha,key+' program')
        counts+=verify_seal(directory,receipt['files'],True)
        raw=directory/'raw';phases=load(raw/'phases.json');scope='native' if receipt['job']['stage'] in ('calibration','control') else 'model'
        publications=list(read_events(raw/'publishes.ndjson'));value=calculate(publications,read_events(raw/'deliveries.ndjson'),phases)
        for source in (receipt['review']['metrics'],load(directory/'review.json')['metrics']):
            for name,actual in value.items():assert_close(actual,source[name],key+'/'+name);metrics_compared+=1
        connection_and_subscription(raw,phases,scope)
        if not value['attempted'] or not value['completed'] or not value['outcomesSettled']:raise ValueError('Unsettled or empty cohort')
        config=load(directory/'config.json')
        for name,expected_value in dict(qos=1,publishers=16,payloadBytes=16,warmupSeconds=1,measurementSeconds=4,drainSeconds=5,tickSeconds=.00001,observeFullDrainWindow=True,writeMessageEventLogs=True).items():assert_close(config[name],expected_value,key+'/config/'+name)
        if scope=='model':
            assert_close(config['profileSha256'],freeze['profiles'][receipt['job']['broker']]['sha256'],key+' profile')
            manifest=load(raw/'experiment-manifest.json')
            tick_seconds=config['tickSeconds']
            for phase in manifest['phases']:
                phase_rows.append(dict(job=key,phase=phase['name'],startTick=phase['startTick'],endTick=phase['endTick'],ticks=phase['endTick']-phase['startTick'],seconds=(phase['endTick']-phase['startTick'])*tick_seconds,status=phase['status']))
            by_name={p['phase']:p for p in phase_rows if p['job']==key}
            for name,ticks in [('warmup',100000),('measure',400000),('drain',500000)]:assert_close(by_name[name]['ticks'],ticks,key+' phase '+name)
        else:
            trial=load(raw/'trial.json')
            if receipt['job']['stage']=='control':
                start_epoch=datetime.fromisoformat(trial['measurementStartedUtc'].replace('Z','+00:00')).timestamp()
                if start_epoch<=freeze['frozenEpoch']:raise ValueError('Control measurement preceded profile freeze')
                control_times.append(dict(job=key,measurementStartedUtc=trial['measurementStartedUtc'],secondsAfterFreeze=start_epoch-freeze['frozenEpoch']))
        jobs[key]=directory;recounts[key]=value
    if set(jobs)!=set(expected) or len(jobs)!=40:raise ValueError('Missing planned scientific job')
    profile_checks={broker:verify_profile(root,broker,jobs,recounts,freeze) for broker in BROKERS}
    profile_seals=verify_seal(root,freeze['files']);replays={}
    for broker in BROKERS:
        original=replay_hashes(jobs[f'model.{broker}.r1']/'raw');replay=replay_hashes(jobs[f'replay.{broker}.r1']/'raw')
        if original!=replay:raise ValueError('Scientific replay differs: '+broker)
        saved=load(jobs[f'replay.{broker}.r1']/'replay-verification.json')
        for name,digest in original.items():assert_close(saved[name],dict(original=digest,replay=digest),broker+' replay '+name)
        replays[broker]=original
    model_rows=[];condition_rows=[]
    for broker in BROKERS:
        stats={stage:{label:summarize([recounts[f'{stage}.{broker}.r{r}'][field] for r in (1,2,3)]) for label,field in FIELDS.items()} for stage in ('calibration','control','model')}
        native={key:value['mean'] for key,value in stats['control'].items()};baseline={key:value['mean'] for key,value in stats['calibration'].items()}
        for repeat in (1,2,3):
            model={label:recounts[f'model.{broker}.r{repeat}'][field] for label,field in FIELDS.items()};comparison=compare(native,model)
            recorded=next(r for r in summary['modelComparisons'] if r['broker']==broker and r['repeat']==repeat)
            for label in FIELDS:
                for suffix,actual in [('Native',native[label]),('Model',model[label]),('CalibrationMean',baseline[label]),('Error',comparison['errors'][label]),('BaselineError',compare(native,baseline)['errors'][label])]:assert_close(actual,recorded[label+suffix],broker+'/'+str(repeat)+'/'+label+suffix)
            assert_close(comparison['allCriteriaPassed'],recorded['allCriteriaPassed'],broker+' passed')
            model_rows.append(dict(broker=broker,repeat=repeat,seed=41+repeat,native=native,model=model,**comparison))
        condition_rows.append(dict(broker=broker,statistics=stats,baselineComparison=compare(native,baseline),
            meanModelComparison=compare(native,{key:value['mean'] for key,value in stats['model'].items()}),
            allThreeSeedsPassed=all(r['allCriteriaPassed'] for r in model_rows if r['broker']==broker)))
    receipt=dict(status='verified',jobs=40,rawSets=40,metricValuesCompared=metrics_compared,jobFilesShaVerified=counts,profileFilesShaVerified=profile_seals,
        profiles=profile_checks,replays=replays,modelComparisons=model_rows,conditions=condition_rows,
        allCriteriaPassedRuns=sum(r['allCriteriaPassed'] for r in model_rows),allThreeSeedsPassedConditions=sum(r['allThreeSeedsPassed'] for r in condition_rows),
        profileFreezeBeforeAllControlMeasurements=control_times,
        programSha256=program_sha,calculatorSha256=sha(__file__),arithmeticTolerance=dict(relative=1e-12,absolute=1e-12),
        scope='Independent local arithmetic implementation and integrity audit; not independent human review or cross-machine validation')
    output.mkdir(parents=True)
    (output/'verification.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for filename,rows in [('phase-ticks.csv',phase_rows),('native-model.csv',[dict(broker=c['broker'],metric=m,nativeMean=c['statistics']['control'][m]['mean'],nativeMin=c['statistics']['control'][m]['min'],nativeMax=c['statistics']['control'][m]['max'],modelMean=c['statistics']['model'][m]['mean'],modelMin=c['statistics']['model'][m]['min'],modelMax=c['statistics']['model'][m]['max'],errorOfMean=c['meanModelComparison']['errors'][m],calibrationMean=c['statistics']['calibration'][m]['mean'],calibrationMeanError=c['baselineComparison']['errors'][m],allThreeSeedsPassed=c['allThreeSeedsPassed']) for c in condition_rows for m in FIELDS])]:
        with (output/filename).open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('profiles','replays','modelComparisons','conditions')},ensure_ascii=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();audit(args.root,args.output)
