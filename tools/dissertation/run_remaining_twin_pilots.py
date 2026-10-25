"""Bounded remaining broker pilots: native frozen probe, model through Linux Web."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import signal
import time
import urllib.request
import uuid

from audit_twin_cohort import events, file_digest, recount
from run_fresh_twin_pilot import write_new
from run_fresh_twin_series import available_port, broker_start, execute, terminate
from run_matched_twin_pilot import review_observations


def cases():
    return [dict(broker=broker,window=window,key=f'{broker}.w{window}')
            for broker in ('Aedes','ActiveMQ','EMQX') for window in (2,4)]


def web_request(broker,window,limit):
    preset={'Aedes':'aedes-default','ActiveMQ':'activemq-default','EMQX':'emqx-default'}[broker]
    if window not in (2,4) or not 1<=limit<=230:raise ValueError('Unplanned window or time limit')
    return dict(name=f'Remaining pilot {broker} w{window}',brokerPresetId=preset,publishers=16,qos=1,seed=42,
                warmupSeconds=1,measurementSeconds=window,drainSeconds=5,tickSeconds=.00001,
                timeLimitSeconds=limit,validateOnly=False)


def check_web_validation(value,sha,window):
    if value.get('valid') is not True or value.get('profileSha256')!=sha or any(
        value.get(key)!=expected for key,expected in [('warmupTicks',100000),
        ('measurementTicks',window*100000),('drainTicks',500000)]):
        raise ValueError('Web validation differs from pinned profile or phase plan')


def require_idle(jobs):
    live=[j for j in jobs if j['state'] in ('Queued','Running','Paused','Cancelling')]
    if live:raise RuntimeError('Web workload prevents isolated pilot: '+str([j['id'] for j in live]))


def recover_owned_job(jobs,name,prior_ids):
    found=[j['id'] for j in jobs if j['id'] not in prior_ids and j.get('name')==name and j.get('engine')=='twinValidation']
    if len(found)>1:raise RuntimeError('Ambiguous job receipt; do not cancel unrelated work')
    return found[0] if found else None


def verify_files(root,files):
    root=Path(root).resolve()
    for relative,sha in files.items():
        path=(root/relative).resolve()
        if root not in path.parents or not path.is_file() or file_digest(path)!=sha:
            raise ValueError('Pinned input mismatch: '+relative)


def require_case_admission(result):
    metrics=result['metrics']
    if not result['gate']['admitted'] or not metrics['attempted'] or not metrics['completed'] or not metrics['outcomesSettled']:
        raise RuntimeError('Pilot gate blocked: '+str(result['gate'].get('issues',[]))+
                           '; pending='+str(metrics.get('pending')))


def api(path,body=None):
    data=None if body is None else json.dumps(body).encode()
    request=urllib.request.Request('http://127.0.0.1:5088/api'+path,data=data,
        headers={'Content-Type':'application/json'},method='GET' if body is None else 'POST')
    with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)


def open_budget(root,program_sha,now):
    path=root/'budget.json'
    if path.exists():
        value=json.loads(path.read_text())
        if value['programSha256']!=program_sha:raise ValueError('Program changed after budget start')
    else:
        value=dict(programSha256=program_sha,startedEpoch=now,deadlineEpoch=now+900,budgetSeconds=900)
        write_new(path,value)
    if now>=value['deadlineEpoch']:raise TimeoutError('Original pilot budget exhausted; deadline is not reset')
    return value


def run(program_path,root,check_only=False):
    root=Path(root).resolve();program_path=Path(program_path).resolve()
    mount=Path('/run/media/entityfx/hd250')
    if not mount.is_mount() or root.parent!=mount/'mqtty-evidence' or root.name!='article1-matched-remaining-pilots-v1':
        raise ValueError('Require separate pilot directory on the mounted evidence disk')
    if shutil.disk_usage(mount).free<10*1024**3:raise RuntimeError('Evidence reserve below 10 GiB')
    program=json.loads(program_path.read_text());program_sha=file_digest(program_path)
    if program['cases']!=cases() or program['budgetSeconds']!=900:raise ValueError('Unplanned pilot matrix or budget')
    verify_files(root,program['launcherFiles'])
    frozen=Path(program['frozenRoot']);base=Path(program['base'])
    runtime_manifest=frozen/'runtime-manifest.json'
    if file_digest(runtime_manifest)!=program['runtimeManifestSha256']:raise ValueError('Runtime manifest changed')
    runtime=json.loads(runtime_manifest.read_text());verify_files(frozen,runtime['files'])
    for path,sha in program['pinnedFiles'].items():
        if file_digest(path)!=sha:raise ValueError('Source snapshot changed: '+path)
    release=Path(program['webReleaseManifest'])
    if file_digest(release)!=program['webReleaseManifestSha256']:raise ValueError('Web release manifest changed')
    web=json.loads(release.read_text())
    if web['mqttyCommit']!=program['webCommit']:raise ValueError('Wrong Web source identity')
    verify_files(release.parent/'payload',web['files'])
    jobs=api('/simulations');require_idle(jobs)
    # Validate every planned point before the first native measurement.
    for point in cases():
        item=program['brokers'][point['broker']]
        request=web_request(point['broker'],point['window'],230)
        check_web_validation(api('/twin-validations',{**request,'validateOnly':True}),item['profileSha256'],point['window'])
    if check_only:
        value=dict(passed=True,programSha256=program_sha,pointsValidated=6,webCommit=program['webCommit'],
                   frozenRuntimeFilesVerified=len(runtime['files']),mainCampaignStarted=False)
        write_new(root/'preflight-validation.json',value)
        print(json.dumps(value))
        return
    budget=open_budget(root,program_sha,time.time())
    output=root/'attempt-001';output.mkdir(exist_ok=False)
    results=[];active_web=None;active=None;request_name=None;prior_web_ids=set()

    def remaining():return budget['deadlineEpoch']-time.time()
    def progress(status,error=None):
        value=dict(status=status,active=active,completed=len(results),total=6,budget=budget,
            elapsedSeconds=time.time()-budget['startedEpoch'],error=error,webJobId=active_web,
            nativeCompleted=len(list(output.glob('*/native-review.json'))),
            modelCompleted=sum(json.loads(path.read_text()).get('state')=='Completed' for path in output.glob('*/web-status.json')),
            admittedPairsPercent=round(100*len(results)/6,1),
            mainCampaignAdmitted=False,scientificAccuracyClaim=False,
            scope='Instrumentation pilots; historical profiles; model via Web')
        temporary=output/'progress.tmp';temporary.write_text(json.dumps(value,indent=2))
        temporary.replace(output/'progress.json')
    def review(directory,scope,window):
        phases=json.loads((directory/'phases.json').read_text())
        gate=review_observations(phases,list(events(directory/'connections.ndjson')),
            list(events(directory/'subscriptions.ndjson')),scope=scope,window=window,drain=5)
        metrics=recount(events(directory/'publishes.ndjson'),events(directory/'deliveries.ndjson'),phases)
        return dict(gate=gate,metrics=metrics,phases=phases,rawSha256={name:file_digest(directory/name) for name in
            ('publishes.ndjson','deliveries.ndjson','connections.ndjson','subscriptions.ndjson','phases.json')})
    try:
        for point in cases():
            if remaining()<90:raise TimeoutError('Insufficient original budget for next pilot')
            broker_name,window=point['broker'],point['window']
            item=program['brokers'][broker_name];case=output/point['key'];case.mkdir()
            for path,sha in program['pinnedFiles'].items():
                if file_digest(path)!=sha:raise ValueError('Pinned input changed before case: '+path)
            before_jobs=api('/simulations');require_idle(before_jobs)
            before_ids={j['id'] for j in before_jobs}
            metadata=json.loads(Path(item['metadata']).read_text())['brokers'][0]
            port=33083;available_port(port)
            config=dict(id='remaining-'+point['key'],campaignId=root.name,broker=broker_name,qos=1,
                publishers=16,payloadBytes=16,seed=42,repeat=1,warmupSeconds=1,measurementSeconds=window,
                drainSeconds=5,tickSeconds=.00001,observeFullDrainWindow=True,endpoint=f'mqtt://127.0.0.1:{port}',
                profilePath=item['profilePath'],profileSha256=item['profileSha256'],
                runtimeIdentity='runtime-manifest-sha256:'+program['runtimeManifestSha256'])
            config_path=case/'config.json';write_new(config_path,config)
            active=point['key']+'/native';progress('running')
            native_broker=log=None
            try:
                native_broker,log=broker_start(metadata,case,port,min(70,remaining()-45))
                native_process=execute(['dotnet',str(frozen/'runtime/TwinValidationProbe.dll'),'native',str(config_path),str(case/'native')],
                    case/'native.log','2-3',min(35,remaining()-10))
            finally:
                if native_broker is not None:terminate(native_broker)
                if log is not None:log.close()
            after_jobs=api('/simulations');require_idle(after_jobs)
            if {j['id'] for j in after_jobs}-before_ids:raise RuntimeError('Other Web work overlapped native measurement')
            native=review(case/'native','native',window)
            write_new(case/'native-review.json',native)
            # Do not spend time on a model after a failed native instrumentation gate.
            require_case_admission(native)
            limit=int(min(230,remaining()-20))
            request=web_request(broker_name,window,limit)
            request['name']=f'{root.name}:attempt-001:{point["key"]}:{uuid.uuid4().hex[:12]}'
            write_new(case/'web-request.json',request)
            active=point['key']+'/model';progress('running')
            model_jobs=api('/simulations');require_idle(model_jobs)
            prior_web_ids={j['id'] for j in model_jobs};request_name=request['name']
            launched=api('/twin-validations',request);active_web=launched['id']
            write_new(case/'web-launch.json',launched);progress('running')
            wait_deadline=min(budget['deadlineEpoch']-10,time.time()+limit)
            while True:
                status=api('/simulations/'+active_web)
                write_path=case/'web-status.json';write_path.write_text(json.dumps(status,indent=2))
                progress('running')
                if status['state'] not in ('Queued','Running'):break
                if time.time()>=wait_deadline:raise TimeoutError('Bounded Web job wait exceeded')
                time.sleep(1)
            if status['state']!='Completed':raise RuntimeError('Web job failed: '+str(status.get('error') or status['state']))
            model_directory=Path(status['outputDir']).resolve()
            expected=Path(program['webDataRoot']).resolve()/'simulations'/active_web
            if model_directory!=expected or not model_directory.is_dir():raise ValueError('Web result directory differs from owned job')
            seals=json.loads((model_directory/'sha256.json').read_text());verify_files(model_directory,seals)
            stored=json.loads((model_directory/'twin-validation.json').read_text())
            if stored['profileSha256']!=item['profileSha256']:raise ValueError('Web executed another profile')
            model=review(model_directory,'model',window)
            row=dict(**point,native=native,model=model,nativeProcess=native_process,web=status,
                     modelSealedFilesVerified=len(seals),nativeAttempt=str(case/'native'))
            write_new(case/'review.json',row)
            require_case_admission(model)
            results.append(row);active_web=None;request_name=None;progress('running')
        verify_files(frozen,runtime['files'])
        for path,sha in program['pinnedFiles'].items():
            if file_digest(path)!=sha:raise ValueError('Source input changed during pilot: '+path)
        active=None
        write_new(output/'summary.json',dict(status='completed',cases=results,mainCampaignAdmitted=False,
            historicalInputsUnchanged=True,webCommit=program['webCommit'],elapsedSeconds=time.time()-budget['startedEpoch']))
        progress('completed')
    except BaseException as error:
        cleanup=None
        if active_web is None and request_name:
            try:
                active_web=recover_owned_job(api('/simulations'),request_name,prior_web_ids)
                if active_web:write_new(case/'web-recovered-receipt.json',dict(id=active_web,name=request_name,responseLost=True))
            except Exception as recovery:cleanup={'receiptRecoveryError':repr(recovery)}
        if active_web:
            try:cleanup=api('/simulations/'+active_web+'/stop',{})
            except Exception as stopping:cleanup={'error':repr(stopping)}
        progress('failed',repr(error))
        write_new(output/'failure.json',dict(error=repr(error),active=active,webJobId=active_web,
            completedCases=results,ownedJobStop=cleanup,deadlineReset=False))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--program',required=True,type=Path)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    def interrupted(signum,frame):raise InterruptedError('Pilot service stopped by signal '+str(signum))
    signal.signal(signal.SIGTERM,interrupted)
    try:
        run(args.program,args.root,args.check_only)
    except BaseException as error:
        root=args.root.resolve();mount=Path('/run/media/entityfx/hd250')
        if mount.is_mount() and root.parent==mount/'mqtty-evidence' and root.name=='article1-matched-remaining-pilots-v1' and root.is_dir():
            failure=root/'entry-failure.json'
            if not failure.exists():write_new(failure,dict(error=repr(error),program=str(args.program),mainCampaignStarted=False))
        raise
