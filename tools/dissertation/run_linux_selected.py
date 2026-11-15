"""Isolated ordered diagnostics for Linux work queue items2,4,5. No retries."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode=True
from run_acceleration_pilot import digest, read, save, verify_files, claim_output, measure_jobs, SCIENTIFIC, check_equivalence

BROKERS=('Aedes','Mosquitto','ActiveMQ','EMQX')
IDENTITY='linux-selected-2-4-5-20261030-v1'


def check_queue_pair(first, second):
    if first['queueCapacity']!=1024 or second['queueCapacity']!=4096 or set(first)!=set(second) or {k for k in first if first[k]!=second[k]}!={'queueCapacity'}:
        raise ValueError('Queue contrast may change only1024 to4096')


def temporal_jobs():
    return [dict(kind=kind,broker=broker,repeat=r,seed=41+r)
            for kind in ('native','model') for broker in BROKERS for r in (1,2,3)]


def require_three(rows):
    if len(rows)!=3 or {r['repeat'] for r in rows}!={1,2,3}:
        raise ValueError('Three complete repeats required; no model of partial control')


def stage_deadline(now, overall, budget):
    return min(overall,now+budget)


def verify_absolute(files):
    for name,expected in files.items():
        path=Path(name)
        if not path.is_absolute() or digest(path)!=expected:
            raise ValueError('Pinned absolute input changed: '+name)


def run(inputs, output):
    inputs,output=inputs.resolve(),output.resolve()
    program=read(inputs/'program.json');seal=read(inputs/'seal.json')
    if program['programIdentity']!=IDENTITY or seal['programIdentity']!=IDENTITY:
        raise ValueError('Wrong program')
    verify_files(inputs,seal['files'])
    source=Path(program['networkSource']);main=Path(program['twinSource'])
    sys.path.insert(0,str(source/'tools/dissertation'))
    from network_study_inputs import StorageGuard, verify_snapshot, lock
    from network_study_verification import verify_loaded_run
    from network_review import check_graph
    # Import pinned twin helpers from a separate, sealed copy of the historical source.
    sys.path.insert(0,str(inputs/'twin-tools'))
    from run_matched_main_campaign import review, require_live_web_idle, statistics3
    from run_fresh_twin_series import broker_start, terminate, execute, available_port
    from run_remaining_twin_pilots import verify_files as verify_twin
    old_program=read(main/'program.json')
    verify_twin(main,old_program['files'])
    verify_absolute(program['pinnedAbsoluteFiles'])
    verify_snapshot(source)
    guard=StorageGuard(source.parent.parent,output,10*1024**3)
    group_name=next(line.split(':',2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    group=Path('/sys/fs/cgroup'+group_name)
    properties=dict(line.split('=',1) for line in subprocess.check_output(['systemctl','--user','show',group.name,
        '-p','RuntimeMaxUSec','-p','TimeoutStopUSec'],text=True).splitlines())
    quota,period=(group/'cpu.max').read_text().split()
    if not group.name.endswith('.service') or properties!={'RuntimeMaxUSec':'49min 55s','TimeoutStopUSec':'5s'} or set(os.sched_getaffinity(0))!={0,2,3}:
        raise ValueError('Require declared bounded unit and CPU0,2,3')
    if (group/'memory.max').read_text().strip()!=str(6*1024**3) or quota=='max' or int(quota)!=3*int(period) or (group/'memory.swap.max').read_text().strip()!='0':
        raise ValueError('Require6GiB/300%/swap0')
    with lock(guard.mount/'.mqtty-network-study.lock'):
        claim_output(output)
        started,epoch=time.monotonic(),time.time();overall=started+2900
        outcomes=[];active=None
        def status(phase,extra=None):
            guard.save(output/'progress.json',dict(programIdentity=IDENTITY,phase=phase,active=active,
                finishedStages=len(outcomes),totalStages=3,deadlineEpoch=epoch+3000,
                elapsedSeconds=time.monotonic()-started,outcomes=outcomes,**(extra or {})))
        def checkpoint():
            guard.check()
            if time.monotonic()>=overall:raise TimeoutError('overallBudget')
            require_live_web_idle()
        def verify_network(raw):
            summary=verify_loaded_run(raw)
            check_graph(raw/'topology.graphml',read(raw/'run-config.json'))
            if summary['status']!='completed':raise ValueError('Incomplete model')
            hashes={name:digest(raw/name) for name in SCIENTIFIC}
            return dict(summary=summary,scientificSha256=hashes)
        def network_case(name,argv,deadline,timeout,config_path):
            checkpoint()
            measurement=measure_jobs([argv],output/name,1,deadline,timeout,check=guard.check)
            if measurement['error']:raise RuntimeError(name+':'+measurement['error'])
            result=verify_network(output/name/'job-1/output')
            raw=output/name/'job-1/output'
            config=read(raw/'run-config.json')
            if digest(raw/'inputs/config.json')!=digest(config_path) or any(config.get(k)!=v for k,v in read(config_path).items()):
                raise ValueError('Executed config differs from pinned input')
            guard.save(output/name/'verification.json',result)
            return dict(name=name,measurement=measurement,**result)
        def stage(number,action,budget):
            nonlocal active
            active=number;status('stage-'+number)
            deadline=stage_deadline(time.monotonic(),overall,budget)
            try:
                result=action(deadline)
                outcomes.append(dict(item=number,status='completed',result=result))
            except Exception as error:
                failure=dict(item=number,status='failed',error=repr(error),traceback=traceback.format_exc(),retry=False)
                outcomes.append(failure);guard.save(output/('failure-item-'+number+'.json'),failure)
            active=None;status('betweenStages')
        dotnet=program['dotnet']

        def core(deadline):
            # Same current runtime for original serial and both adapter variants.
            runtime=inputs/'graph-runtime'
            config=inputs/'core.json'
            results=[]
            for label,assembly,args in (
                ('core-original','EntityFX.MqttY.Federation.App.dll',['run','--config',str(config),'--output','output']),
                ('core-serial','GraphExecutionProbe.dll',['serial',str(config),'output']),
                ('core-parallel','GraphExecutionProbe.dll',['parallel',str(config),'output'])):
                argv=['taskset','-c','2,3',dotnet,str(runtime/assembly),*args]
                row=network_case(label,argv,deadline,120,config);results.append(row)
                if len(results)>1:check_equivalence(results[0]['scientificSha256'],row['scientificSha256'])
            return dict(equivalent=True,results=results,stableSpeedupClaimed=False)

        def queue(deadline):
            first,second=read(inputs/'queue1024.json'),read(inputs/'queue4096.json')
            check_queue_pair(first,second)
            results=[]
            for capacity in (1024,4096):
                argv=['taskset','-c','2,3',dotnet,str(source/'runtime/EntityFX.MqttY.Federation.App.dll'),
                      'run','--config',str(inputs/f'queue{capacity}.json'),'--output','output']
                row=network_case(f'queue-{capacity}',argv,deadline,240,inputs/f'queue{capacity}.json');results.append(row)
            a,b=[r['summary'] for r in results]
            cohorts=[];starts=[]
            for capacity,summary in zip((1024,4096),(a,b)):
                raw=output/f'queue-{capacity}/job-1/output'
                cohorts.append({p['id']:p for p in read(raw/'publications.json') if p['measurement']})
                starts.append(next(p['startTick'] for p in summary['phases'] if p['name']=='measure'))
            x,y=cohorts;shared=x.keys() & y.keys()
            cohort_check=dict(sourceIdsEqual=x.keys()==y.keys(),measurementStartTicks=starts,
                relativeScheduleEqual=x.keys()==y.keys() and all(x[i]['tick']-starts[0]==y[i]['tick']-starts[1] for i in shared),
                sourcePayloadBytesEqual=x.keys()==y.keys() and all(x[i]['payload']==y[i]['payload'] for i in shared))
            return dict(results=results,classification='queueCapacityContrast' if a['queueDrops']!=b['queueDrops'] or a['uniqueDeliveries']!=b['uniqueDeliveries'] else 'noObservedContrast',
                        sourceCohortCheck=cohort_check,affectedMainConditionOnly=True,claimAllMainLossExplained=False)

        def temporal(deadline):
            # Profile paths/SHA were sealed BEFORE this new native session.
            root=output/'temporal';root.mkdir();success={};failures=[]
            def publish():
                guard.save(root/'progress.json',dict(completed=len(success),failed=len(failures),total=24,
                    failures=failures,active=active_job))
            for job in temporal_jobs():
                active_job=f"{job['kind']}.{job['broker']}.r{job['repeat']}"
                publish();checkpoint()
                if deadline-time.monotonic()<90:raise TimeoutError('temporalBudgetReserve')
                broker=job['broker'];profile=program['profiles'][broker]
                directory=root/active_job;directory.mkdir()
                if job['kind']=='model':
                    native=[r for k,r in success.items() if k.startswith('native.'+broker+'.')]
                    try:require_three([r['job'] for r in native])
                    except ValueError:
                        failures.append(dict(job=job,error='incompleteNativeControl-modelNotAdmitted'));publish();continue
                config=dict(id=active_job,campaignId=output.name+'-temporal-'+job['kind'],broker=broker,
                    qos=1,publishers=16,payloadBytes=16,seed=job['seed'],repeat=job['repeat'],
                    warmupSeconds=1,measurementSeconds=4,drainSeconds=5,tickSeconds=.00001,
                    observeFullDrainWindow=True,writeMessageEventLogs=True,
                    measureRttBaseline=job['kind']=='native',endpoint='mqtt://127.0.0.1:33203',
                    profilePath='' if job['kind']=='native' else profile['path'],
                    profileSha256='' if job['kind']=='native' else profile['sha256'],
                    runtimeIdentity='runtime-manifest-sha256:'+old_program['runtimeManifestSha256'])
                save(directory/'config.json',config)
                process=log=None
                try:
                    verify_absolute(program['pinnedAbsoluteFiles'])
                    if job['kind']=='native':
                        available_port(33203)
                        metadata=read(old_program['brokers'][broker]['metadata'])['brokers'][0]
                        process,log=broker_start(metadata,directory,33203,min(70,deadline-time.monotonic()-20))
                    receipt=execute([dotnet,old_program['probeDll'],job['kind'],str(directory/'config.json'),str(directory/'trial')],
                                    directory/'execution.log','2-3',min(120,deadline-time.monotonic()-10))
                    raw=directory/'trial'
                    if job['kind']=='model':
                        raw=Path(read(raw/'trial.json')['runDirectory']).resolve()
                        if not raw.is_relative_to(directory.resolve()):raise ValueError('Model raw escaped attempt')
                    reviewed=review(raw,job['kind'])
                    if job['kind']=='native':
                        rtt=read(raw/'trial.json')['rttBaselineMs']
                        if not math.isfinite(rtt) or rtt<=0:raise ValueError('Missing measured RTT')
                    row=dict(job=job,raw=str(raw),review=reviewed,execution=receipt)
                    guard.save(directory/'review.json',row);success[active_job]=row
                except Exception as error:
                    failure=dict(job=job,error=repr(error),traceback=traceback.format_exc(),retry=False)
                    failures.append(failure);guard.save(directory/'failure.json',failure)
                finally:
                    if process is not None:terminate(process)
                    if log is not None:log.close()
                publish();require_live_web_idle()
            comparisons=[];native_drift=[]
            for broker in BROKERS:
                native=[r['review']['metrics'] for k,r in success.items() if k.startswith('native.'+broker+'.')]
                if len(native)!=3:continue
                previous=program['previousControlMetrics'][broker]
                drift=dict(broker=broker,oldNativeRepeats=previous,newNativeRepeats=native,metrics={})
                for metric in ('completedCohortPerSubmissionSecond','p99PublishMs','publishFailureRate','deliveryLossRate'):
                    old_mean=sum(r[metric] for r in previous)/3;new_mean=sum(r[metric] for r in native)/3
                    drift['metrics'][metric]=dict(oldMean=old_mean,newMean=new_mean,
                        signedDifference=new_mean-old_mean,relativeDifference=(new_mean-old_mean)/old_mean if old_mean else None)
                native_drift.append(drift)
                for repeat in (1,2,3):
                    key=f'model.{broker}.r{repeat}'
                    if key not in success:continue
                    model=success[key]['review']['metrics'];row=dict(broker=broker,repeat=repeat,metrics={})
                    for key,relative,limit in [('completedCohortPerSubmissionSecond',True,.15),('p99PublishMs',True,.20),('publishFailureRate',False,.05),('deliveryLossRate',False,.05)]:
                        values=[r[key] for r in native];reference=sum(values)/3;actual=model[key]
                        error=abs(actual-reference)/reference if relative and reference else abs(actual-reference) if not relative else None
                        row['metrics'][key]=dict(nativeMean=reference,nativeMin=min(values),nativeMax=max(values),model=actual,
                                                error=error,limit=limit,passed=error is not None and error<=limit)
                    row['allCriteriaPassed']=all(r['passed'] for r in row['metrics'].values());comparisons.append(row)
            result=dict(completed=len(success),total=24,failures=failures,comparisons=comparisons,
                        nativeSessionDrift=native_drift,
                        frozenProfiles=program['profiles'],recalibrationPerformed=False,
                        scientificallyPassed=sum(r['allCriteriaPassed'] for r in comparisons))
            guard.save(root/'comparison.json',result)
            if failures:raise RuntimeError('temporalTechnicalFailures='+str(len(failures)))
            return result

        try:
            status('preflight');require_live_web_idle()
            stage('2',core,420)
            stage('4',queue,600)
            stage('5',temporal,1800)
            verify_snapshot(source);verify_files(inputs,seal['files'])
            verify_absolute(program['pinnedAbsoluteFiles']);verify_twin(main,old_program['files'])
            passed=all(o['status']=='completed' for o in outcomes)
            status('completed' if passed else 'completedWithFailures',dict(sourceInputsUnchanged=True,retryPerformed=False))
            return passed
        except BaseException as error:
            guard.save(output/'failure.json',dict(error=repr(error),traceback=traceback.format_exc(),retry=False))
            status('failed');raise


if __name__=='__main__':
    def interrupted(signum,frame):raise InterruptedError('Owned selected service signal '+str(signum))
    signal.signal(signal.SIGTERM,interrupted)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();sys.exit(0 if run(args.inputs,args.output) else 2)
