"""Frozen 40-run matched campaign. New native calibration/control; isolated model runtime."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import signal
import statistics
import tarfile
import time
import urllib.request

from audit_twin_cohort import events, file_digest, recount
from independent_twin_recount import calculate, connection_and_subscription
from run_fresh_twin_pilot import write_new
from run_fresh_twin_series import available_port, broker_start, execute, terminate
from run_remaining_twin_pilots import verify_files

BROKERS=('Aedes','Mosquitto','ActiveMQ','EMQX')
RAW=('publishes.ndjson','deliveries.ndjson','connections.ndjson','subscriptions.ndjson','phases.json')
QUANTILES=('minMs','p50Ms','p75Ms','p95Ms','p99Ms','maxMs')
PROBABILITIES=(0,.5,.75,.95,.99,1)


def planned_jobs():
    rows=[]
    for stage in ('calibration','control','model','replay'):
        for broker in BROKERS:
            for repeat in (range(1,4) if stage!='replay' else (1,)):
                rows.append(dict(stage=stage,broker=broker,repeat=repeat,seed=41+repeat,
                                 key=f'{stage}.{broker}.r{repeat}'))
    return rows


def statistics3(values):
    if len(values)!=3 or any(not isinstance(x,(float,int)) or not math.isfinite(x) or x<0 for x in values):
        raise ValueError('Exactly three real finite nonnegative observations required')
    mean=sum(values)/3;sd=statistics.stdev(values);half=4.30265272975*sd/math.sqrt(3)
    return dict(mean=mean,standardDeviation=sd,ci95HalfWidth=half,ci95Lower=mean-half,
                ci95Upper=mean+half,min=min(values),max=max(values),count=3)


def latency_bins(publications,phases):
    start,end,horizon=[phases[k] for k in ('measurementStartSeconds','measurementEndSeconds','drainEndSeconds')]
    cohort={r['id'] for r in publications if r['event']=='attempt' and start<=r['elapsedSeconds']<end}
    bins=Counter(int(round(r['latencyMs']*1000)) for r in publications if r['event']=='completed' and
                 r['id'] in cohort and r['elapsedSeconds']<=horizon)
    if not bins:raise ValueError('No measured completions for empirical distribution')
    return [[value,count] for value,count in sorted(bins.items())]


def bin_quantiles(bins):
    count=sum(n for _,n in bins);result={}
    for name,p in zip(QUANTILES,PROBABILITIES):
        rank=max(1,math.ceil(p*count));seen=0
        for micros,n in bins:
            seen+=n
            if seen>=rank:result[name]=micros/1000;break
    return result


def open_budget(root,program_sha,now):
    path=root/'budget.json'
    if path.exists():
        value=json.loads(path.read_text())
        if value['programSha256']!=program_sha:raise ValueError('Original program changed; deadline cannot reset')
    else:
        value=dict(programSha256=program_sha,startedEpoch=now,deadlineEpoch=now+21600,budgetSeconds=21600)
        write_new(path,value)
    if now>=value['deadlineEpoch']:raise TimeoutError('Original six-hour deadline exhausted')
    return value


def require_live_web_idle():
    # Observe, never restart/cancel jobs belonging to the live Web instance.
    with urllib.request.urlopen('http://127.0.0.1:5088/api/simulations',timeout=10) as response:
        jobs=json.load(response)
    live=[j['id'] for j in jobs if j['state'] in ('Queued','Running','Paused','Cancelling')]
    if live:raise RuntimeError('Live Web workload prevents isolated measurements: '+str(live))
    return {j['id'] for j in jobs}


def replay_comparison(original,replay):
    names=('summary.json','latency-histogram.json','publishes.ndjson','deliveries.ndjson','subscriptions.ndjson','phases.json')
    compared={name:dict(original=file_digest(original/name),replay=file_digest(replay/name)) for name in names}
    for name,root in [('original',original),('replay',replay)]:
        counters=json.loads((root/'counters.json').read_text())
        canonical={key:counters[key] for key in ('brokers','receivers','receiverScope')}
        compared.setdefault('counters-scientific.json',{})[name]=hashlib.sha256(
            json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return compared


def seal(directory):
    return {str(p.relative_to(directory)):file_digest(p) for p in sorted(directory.rglob('*'))
            if p.is_file() and 'broker-home' not in p.relative_to(directory).parts}


def completed_jobs(root,program_sha):
    result={}
    for receipt in sorted(root.glob('attempt-*/jobs/*/success.json')):
        row=json.loads(receipt.read_text())
        if row['programSha256']!=program_sha:raise ValueError('Success belongs to another frozen program')
        verify_files(receipt.parent,row['files'])
        if row['job']['key'] in result:raise ValueError('Duplicate successful scientific job; do not choose best result')
        result[row['job']['key']]=dict(row,directory=str(receipt.parent))
    return result


def review(directory,scope):
    phases=json.loads((directory/'phases.json').read_text())
    publications=list(events(directory/'publishes.ndjson'));deliveries=list(events(directory/'deliveries.ndjson'))
    value=calculate(publications,deliveries,phases);second=recount(iter(publications),iter(deliveries),phases)
    for key,expected in value.items():
        actual=second[key]
        if actual!=expected and not (isinstance(actual,(int,float)) and isinstance(expected,(int,float)) and
             math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-12)):
            raise ValueError('Independent recount differs: '+key)
    connection=connection_and_subscription(directory,phases,scope)
    if not value['attempted'] or not value['completed'] or not value['outcomesSettled']:
        raise ValueError('Missing/unfinished publication cohort')
    return dict(metrics=value,connection=connection,phases=phases,
                rawSha256={name:file_digest(directory/name) for name in RAW})


def profile_inputs(program,root,broker,completed,profile_dir):
    repeats=[];reviews=[];inputs={}
    for repeat in (1,2,3):
        row=completed[f'calibration.{broker}.r{repeat}'];directory=Path(row['directory'])
        raw=directory/'raw';reviewed=row['review'];reviews.append(reviewed['metrics'])
        archive=profile_dir/f'{broker}.r{repeat}.native.tar.gz'
        with tarfile.open(archive,'x:gz') as package:
            for file in sorted(raw.rglob('*')):
                if file.is_file():package.add(file,arcname=str(file.relative_to(raw)),recursive=False)
        bins=latency_bins(list(events(raw/'publishes.ndjson')),reviewed['phases'])
        trial=json.loads((raw/'trial.json').read_text());rtt=trial['rttBaselineMs']
        if not math.isfinite(rtt) or rtt<=0:raise ValueError('Calibration requires measured positive RTT')
        repeats.append(dict(repeat=repeat,archiveSha256=file_digest(archive),completedPublishes=reviewed['metrics']['completed'],
            actualMeasurementSeconds=reviewed['metrics']['measurementSeconds'],bins=bins,rtt=rtt))
        for relative,digest in row['files'].items():inputs[f'calibration/{broker}/r{repeat}/{relative}']=digest
        inputs[f'archives/{archive.name}']=file_digest(archive)
    fields={'attemptedRps':'attemptedRps','completedRps':'completedCohortPerSubmissionSecond',
            'publishFailureRate':'publishFailureRate','deliveryLossRate':'deliveryLossRate'}
    quantiles=[bin_quantiles(r['bins']) for r in repeats]
    point=dict(broker=broker,messageBytes=16,qos=1,publishers=16,repeats=3,
        **{name:statistics3([r[key] for r in reviews]) for name,key in fields.items()},
        observedLatencyQuantiles={name:sum(q[name] for q in quantiles)/3 for name in QUANTILES},
        observedLatencyStatistics={name:statistics3([q[name] for q in quantiles]) for name in QUANTILES},
        rttBaselineMs=sum(r['rtt'] for r in repeats)/3)
    identity=dict(campaignId=root.name+'-calibration',configSha256=file_digest(root/'program.json'),
        standSha256=program['standSha256'],deploymentMode='singleHostSequential',cpuMode='singleCorePinned',
        mqttBenchmarkCommitSha=program['mqttBenchmarkCommit'])
    observations=dict(schemaVersion=3,runCount=3,provenance=dict(identity=identity,inputSha256=inputs),points=[point])
    obs=profile_dir/(broker+'.observations.json');write_new(obs,observations)
    hist=dict(schemaVersion=1,artifactType='broker-publish-latency-histograms',observationsSha256=file_digest(obs),
        campaignId=identity['campaignId'],points=[dict(broker=broker,messageBytes=16,qos=1,publishers=16,
            repeats=[{k:v for k,v in r.items() if k!='rtt'} for r in repeats])])
    histogram=profile_dir/(broker+'.histograms.json');write_new(histogram,hist)
    return obs,histogram


def load_profiles(root,program_sha):
    manifest=root/'profile-freeze.json'
    if not manifest.exists():return None
    value=json.loads(manifest.read_text())
    if value['programSha256']!=program_sha:raise ValueError('Profile freeze belongs to changed program')
    verify_files(root,value['files'])
    return value


def compile_profiles(program,root,attempt,completed,remaining,program_sha):
    existing=load_profiles(root,program_sha)
    if existing:return existing
    if any(j['stage']!='calibration' for j in (r['job'] for r in completed.values())):
        raise ValueError('Control exists before immutable profile freeze')
    target=attempt/'profiles';target.mkdir();profiles={}
    for broker in BROKERS:
        observations,histograms=profile_inputs(program,root,broker,completed,target)
        profile=target/(broker+'.profile.json')
        execute(['dotnet',program['calibrationDll'],'calibrate','--observations',str(observations),
            '--latency-histograms',str(histograms),'--output',str(profile),'--broker',broker,
            '--coverage-protocol','single-point-v1','--mqtty-repository',program['mqttyRepository']],
            target/(broker+'.calibrate.log'),'2-3',min(90,remaining()-10))
        profiles[broker]=dict(path=str(profile),sha256=file_digest(profile))
    value=dict(programSha256=program_sha,profiles=profiles,files={str(p.relative_to(root)):file_digest(p)
        for p in target.rglob('*') if p.is_file()},frozenEpoch=time.time(),beforeControl=True)
    write_new(root/'profile-freeze.json',value)
    return value


def compare_results(root,completed):
    rows=[]
    for broker in BROKERS:
        calibration=[completed[f'calibration.{broker}.r{r}']['review']['metrics'] for r in (1,2,3)]
        control=[completed[f'control.{broker}.r{r}']['review']['metrics'] for r in (1,2,3)]
        for repeat in (1,2,3):
            model=completed[f'model.{broker}.r{repeat}']['review']['metrics']
            native={key:sum(r[key] for r in control)/3 for key in ('completedCohortPerSubmissionSecond','p99PublishMs','publishFailureRate','deliveryLossRate')}
            row=dict(broker=broker,repeat=repeat,seed=41+repeat,reference='mean of three new native control repeats')
            passed=True
            for label,key,relative,threshold in [('rps','completedCohortPerSubmissionSecond',True,.15),
                    ('p99','p99PublishMs',True,.20),('failure','publishFailureRate',False,.05),('loss','deliveryLossRate',False,.05)]:
                reference=native[key];actual=model[key];base=sum(r[key] for r in calibration)/3
                error=abs(actual-reference)/reference if relative and reference else abs(actual-reference) if not relative else None
                baseline_error=abs(base-reference)/reference if relative and reference else abs(base-reference) if not relative else None
                row.update({label+'Native':reference,label+'NativeMin':min(r[key] for r in control),
                            label+'NativeMax':max(r[key] for r in control),label+'Model':actual,label+'CalibrationMean':base,
                            label+'Error':error,label+'BaselineError':baseline_error})
                passed&=error is not None and error<=threshold
            row['allCriteriaPassed']=passed;rows.append(row)
    return rows


def run(program_path,root,attempt_number=1,check_only=False):
    root=Path(root).resolve();program_path=Path(program_path).resolve();mount=Path('/run/media/entityfx/hd250')
    if not mount.is_mount() or root.parent!=mount/'mqtty-evidence' or root.name!='article1-matched-main-v1':
        raise ValueError('Require new isolated main directory on mounted evidence disk')
    program=json.loads(program_path.read_text());program_sha=file_digest(program_path)
    if program_path!=root/'program.json':raise ValueError('Program must be inside owned main root')
    if program['jobs']!=planned_jobs() or program['budgetSeconds']!=21600 or not program['admission']['admitted']:
        raise ValueError('Unadmitted or unplanned matrix')
    if not 0<program['forecastSeconds']<21600:raise ValueError('Forecast exceeds total fixed budget')
    if program['phaseSeconds']!=dict(warmup=1,measurement=4,drain=5,tick=.00001):raise ValueError('Phases changed')
    verify_files(root,program['files'])
    if shutil.disk_usage(mount).free<10*1024**3:raise RuntimeError('Evidence free space reserve below 10 GiB')
    for path,digest in program['pinnedFiles'].items():
        if file_digest(path)!=digest:raise ValueError('Pinned external input changed: '+path)
    if not 1<=attempt_number<=999:raise ValueError('Invalid attempt number')
    completed=completed_jobs(root,program_sha);load_profiles(root,program_sha)
    if check_only:
        result=dict(passed=True,programSha256=program_sha,planned=40,alreadyComplete=len(completed),
                    estimatedWallSeconds=program['forecastSeconds'],budgetSeconds=21600,modelExecution=program['modelExecution'])
        write_new(root/f'preflight-{attempt_number:03d}.json',result);print(json.dumps(result));return
    budget=open_budget(root,program_sha,time.time());attempt=root/f'attempt-{attempt_number:03d}';attempt.mkdir(exist_ok=False)
    jobs_root=attempt/'jobs';jobs_root.mkdir();active=None
    def remaining():return budget['deadlineEpoch']-time.time()
    def progress(status,error=None):
        value=dict(status=status,active=active,completed=len(completed),total=40,percent=round(100*len(completed)/40,1),
            byStage={stage:sum(r['job']['stage']==stage for r in completed.values()) for stage in ('calibration','control','model','replay')},
            elapsedSeconds=time.time()-budget['startedEpoch'],budget=budget,error=error,attempt=attempt_number,
            modelExecution=program['modelExecution'],scientificAccuracySeparate=True)
        temp=root/'progress.tmp';temp.write_text(json.dumps(value,indent=2),encoding='utf-8');temp.replace(root/'progress.json')
    try:
        for job in planned_jobs():
            if file_digest(program_path)!=program_sha:raise ValueError('Frozen program changed during campaign')
            if job['stage']!='calibration':profiles=compile_profiles(program,root,attempt,completed,remaining,program_sha)
            if job['key'] in completed:continue
            if remaining()<120:raise TimeoutError('Six-hour campaign reserve exhausted; deadline unchanged')
            verify_files(root,program['files'])
            for pinned,digest in program['pinnedFiles'].items():
                if file_digest(pinned)!=digest:raise ValueError('Pinned input changed during campaign: '+pinned)
            active=job['key'];progress('running');directory=jobs_root/job['key'];directory.mkdir()
            native=job['stage'] in ('calibration','control');broker=job['broker']
            profile=None if native else profiles['profiles'][broker]
            config=dict(id=job['key'],campaignId=root.name+'-'+job['stage'],broker=broker,qos=1,publishers=16,
                payloadBytes=16,seed=job['seed'],repeat=job['repeat'],warmupSeconds=1,measurementSeconds=4,
                drainSeconds=5,tickSeconds=.00001,observeFullDrainWindow=True,writeMessageEventLogs=True,
                measureRttBaseline=native,endpoint='mqtt://127.0.0.1:33203',
                profilePath='' if native else profile['path'],profileSha256='' if native else profile['sha256'],
                runtimeIdentity='runtime-manifest-sha256:'+program['runtimeManifestSha256'])
            write_new(directory/'config.json',config)
            process=log=None
            before_jobs=require_live_web_idle()
            try:
                if native:
                    available_port(33203);metadata=json.loads(Path(program['brokers'][broker]['metadata']).read_text())['brokers'][0]
                    process,log=broker_start(metadata,directory,33203,min(70,remaining()-50))
                execution=execute(['dotnet',program['probeDll'],'native' if native else 'model',
                    str(directory/'config.json'),str(directory/'trial')],directory/'execution.log','2-3',min(300,remaining()-10))
            finally:
                if process is not None:terminate(process)
                if log is not None:log.close()
            after_jobs=require_live_web_idle()
            if after_jobs-before_jobs:raise RuntimeError('Another Web job overlapped isolated experiment')
            raw=directory/'trial'
            if not native:
                raw=Path(json.loads((raw/'trial.json').read_text())['runDirectory']).resolve()
                if directory.resolve() not in raw.parents:raise ValueError('Model raw outside owned attempt')
            # Stable per-job raw location; files are copied unchanged, originals remain preserved.
            shutil.copytree(raw,directory/'raw');raw=directory/'raw'
            reviewed=review(raw,'native' if native else 'model');write_new(directory/'review.json',reviewed)
            if native:
                rtt=json.loads((raw/'trial.json').read_text())['rttBaselineMs']
                if not math.isfinite(rtt) or rtt<=0:raise ValueError('Native measured RTT absent')
            if job['stage']=='replay':
                original=Path(completed[f'model.{broker}.r1']['directory'])/'raw'
                compared=replay_comparison(original,raw)
                if any(r['original']!=r['replay'] for r in compared.values()):
                    write_new(directory/'replay-mismatch.json',compared);raise ValueError('Exact replay mismatch')
                write_new(directory/'replay-verification.json',compared)
            receipt=dict(job=job,programSha256=program_sha,review=reviewed,execution=execution,files=seal(directory))
            write_new(directory/'success.json',receipt);completed[job['key']]=dict(receipt,directory=str(directory));progress('running')
        rows=compare_results(root,completed)
        with (attempt/'comparison.csv').open('x',newline='',encoding='utf-8') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        write_new(attempt/'summary.json',dict(status='completed',completed=40,modelComparisons=rows,
            modelAllCriteriaPassed=sum(r['allCriteriaPassed'] for r in rows),profiles=load_profiles(root,program_sha),
            historicalInputsUnchanged=True,modelExecution=program['modelExecution']))
        active=None;progress('completed')
    except BaseException as error:
        progress('failed',repr(error));write_new(attempt/'failure.json',dict(active=active,error=repr(error),
            completed=len(completed),deadlineReset=False,scientificThresholdsUnchanged=True));raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--program',required=True,type=Path)
    parser.add_argument('--root',required=True,type=Path);parser.add_argument('--attempt',type=int,default=1)
    parser.add_argument('--check-only',action='store_true');args=parser.parse_args()
    def interrupted(signum,frame):raise InterruptedError('Owned main campaign signal '+str(signum))
    signal.signal(signal.SIGTERM,interrupted)
    run(args.program,args.root,args.attempt,args.check_only)
