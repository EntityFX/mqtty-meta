"""Bounded independent network jobs; phase gates and scientific checks stay intact."""
import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_network_study as runner
from network_study_inputs import StorageGuard, lock, read, sha, verify_snapshot, program_check
from network_study_process import tree_bytes


def memory_available():
    fields=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    return int(fields['MemAvailable'].split()[0])*1024


def deadline(root, guard, seconds):
    if not 0<seconds<=10800: raise ValueError('Global budget must be at most three hours')
    path=root/'wall-budget.json'
    if path.exists():
        saved=read(path)
        if saved['wallLimitSeconds']!=seconds: raise ValueError('Cannot reset or change saved global budget')
    else:
        saved={'wallLimitSeconds':seconds,'startedEpoch':time.time(),'deadlineEpoch':time.time()+seconds}
        guard.save(path,saved)
    remaining=saved['deadlineEpoch']-time.time()
    if remaining<=0: raise RuntimeError('executionBudgetExhausted; saved deadline cannot be reset')
    return time.monotonic()+remaining


def preserved_pilot_decision(root, manifest, manifest_sha, plan, registry_path, decision_bytes):
    path=Path(registry_path).resolve()
    relative=path.relative_to(root).as_posix()
    registry_bytes=path.read_bytes();digest=hashlib.sha256(registry_bytes).hexdigest()
    if manifest['files'].get(relative)!=digest:raise ValueError('Pilot registry is not pinned in this execution')
    registry=json.loads(registry_bytes);decision=json.loads(decision_bytes or b'{}')
    if decision.get('decision')!='accept' or decision.get('executionManifestSha256')!=manifest_sha or decision.get('pilotRegistrySha256')!=digest:
        raise ValueError('Explicit technical decision must bind program and pilot registry')
    if not decision.get('reviewer') or not decision.get('reason'):raise ValueError('Review and rationale required')
    expected={j['id']:j for j in plan['jobs'] if j['kind']=='pilot'}
    points=registry.get('points',[])
    if len(points)!=len(expected) or {p['id'] for p in points}!=set(expected) or registry.get('discrepancies'):
        raise ValueError('Preserved pilot coverage differs from program')
    mount=Path(manifest['mountRoot']).resolve()
    for point in points:
        output=Path(point['originalOutputAbsolute']).resolve();config=Path(point['originalConfigAbsolute']).resolve()
        if not output.is_relative_to(mount) or not config.is_relative_to(mount):raise ValueError('Pilot escaped evidence mount')
        if sha(config)!=point['originalConfigSha256'] or sha(output/'summary.json')!=point['originalSummarySha256'] or sha(output/'sha256.json')!=point['outputSealSha256']:
            raise ValueError('Preserved pilot identity changed')
        original=read(config);candidate=read(root/'program'/expected[point['id']]['config'])
        if dict(original,drainTimeoutSeconds=30)!=candidate:raise ValueError('Pilot inputs differ beyond declared drain ceiling')
        summary,warnings=runner.verify_output(output,config,root)
        if summary['status']!='completed':raise ValueError('Incomplete preserved pilot')
        for phase in summary['phases']:
            if phase['name'] in ('warmupDrain','drain') and (phase['endTick']-phase['startTick'])*summary['tickMilliseconds']/1000>original['drainTimeoutSeconds']+1e-8:
                raise ValueError('Pilot phases exceeded original observation limit')
        if sorted(warnings)!=sorted(point['warnings']):raise ValueError('Pilot warnings changed')
    return decision


def run(root, phase, *, workers=4, wall_seconds=10800, cleanup_seconds=120, decision_path=None, resume=False, pilot_registry=None):
    if not 1<=workers<=4 or not 0<=cleanup_seconds<wall_seconds: raise ValueError('Invalid worker or cleanup budget')
    root=Path(root).resolve();manifest=verify_snapshot(root);manifest_sha=sha(root/'execution-manifest.json')
    for path in (Path(__file__),Path(runner.__file__)):
        key='tools/dissertation/'+path.name
        if sha(path)!=manifest['files'].get(key): raise ValueError('Execute the pinned controller snapshot')
    memory_limit=10*1024**3
    if workers*manifest['jobRssLimitBytes']+2*1024**3>memory_limit:
        raise ValueError('Workers exceed aggregate memory admission budget')
    if memory_available()<workers*manifest['jobRssLimitBytes']+2*1024**3:
        raise RuntimeError('Insufficient available RAM for requested workers')
    guard=StorageGuard(manifest['mountRoot'],root,manifest['reserveBytes'])
    if guard.device!=manifest['mountDevice']: raise ValueError('Evidence mount identity changed')
    plan=program_check(root/'program');indexed={j['id']:j for j in plan['jobs']}
    with lock(root/'run.lock'),lock(guard.mount/'.mqtty-network-study.lock'):
        stop_at=deadline(root,guard,wall_seconds)-cleanup_seconds
        runner.isolation(manifest['afterUnits'])
        rows=runner.load_rows(root,manifest_sha,indexed,guard)
        if any(r['technicalError'] for r in rows.values()): raise ValueError('Inspect failed attempts before any retry')
        if phase in ('matrix','diagnostic'):
            decision_bytes=Path(decision_path).read_bytes() if decision_path else None
            if pilot_registry is not None:
                decision=preserved_pilot_decision(root,manifest,manifest_sha,plan,pilot_registry,decision_bytes)
            else:
                decision=runner.pilot_decision(root,decision_path,manifest_sha,plan,rows)
                if json.loads(decision_bytes)!=decision:raise ValueError('Decision changed during validation')
            folder=root/'decisions';folder.mkdir(exist_ok=True)
            digest=hashlib.sha256(decision_bytes).hexdigest()
            target=folder/(digest+'.json')
            if not target.exists():
                guard.identity()
                with target.open('xb') as stream:stream.write(decision_bytes)
            elif sha(target)!=digest:raise ValueError('Saved decision differs')
        jobs=[j for j in plan['jobs'] if j['kind']==phase]
        if not jobs:raise ValueError('Phase has no jobs')
        if phase=='replay' and any(j['replayOf'] not in rows for j in jobs):raise ValueError('Replay originals incomplete')
        if any(j['id'] in rows for j in jobs) and not resume:raise ValueError('Partial phase requires explicit resume')
        if (root/f'results-{phase}.json').exists():
            if not resume:raise ValueError('Phase exists; use explicit resume')
            runner.check_phase_result(root,phase,plan,rows,manifest_sha)
            return {'status':'completed','verified':len(jobs),'total':len(jobs),'reverifiedOnly':True}
        context=runner.progress_context(root,plan);active={};mutex=threading.RLock()
        verification_mutex=threading.Lock();abort=threading.Event()
        queue=iter(j for j in jobs if j['id'] not in rows)
        (root/'rows').mkdir(exist_ok=True)
        reason=None
        def expired():return time.monotonic()>=stop_at
        def cancelled():return ('controllerAbort: '+str(reason)) if abort.is_set() else expired()
        def publish(status='running'):
            runner.publish(root,phase,rows,guard,status,manifest_sha,context,
                {'workers':workers,'jobs':dict(active),'remainingSeconds':max(0,stop_at-time.monotonic())},reason)
        def work(job):
            def on_state(state):
                with mutex:active[job['id']]=state;publish()
            return runner.attempt(root,job,manifest,manifest_sha,guard,on_state,cancel_requested=cancelled,verification_lock=verification_mutex)
        pending={}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            with mutex:publish()
            while True:
                while len(pending)<workers and reason is None and not expired():
                    job=next(queue,None)
                    if job is None:break
                    try:
                        guard.check();runner.isolation(manifest['afterUnits'])
                        verify_snapshot(root)
                    except BaseException as error:
                        reason='admissionFailure: '+repr(error);abort.set();break
                    pending[pool.submit(work,job)]=job
                if not pending:break
                done,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                try:
                    guard.check()
                    if tree_bytes(root)>manifest['diskBudgetBytes']:raise RuntimeError('aggregateStudyDiskBudget')
                except BaseException as error:
                    reason='resourceFailure: '+repr(error);abort.set()
                if expired():reason='executionBudgetExhausted'
                for future in done:
                    job=pending.pop(future)
                    try:row=future.result()
                    except BaseException as error:
                        with mutex:
                            active.pop(job['id'],None)
                            reason='workerException: '+repr(error);abort.set()
                            # command.json and partial output remain available for explicit recovery.
                            guard.save(root/f"controller-error-{job['id']}.json",{
                                'controllerException':reason,'jobId':job['id'],
                                'requiresExplicitInterruptionRecovery':True})
                        continue
                    with mutex:
                        rows[job['id']]=row;active.pop(job['id'],None)
                        context['attempts'][phase]['total']+=1
                        context['attempts'][phase]['failed']+=bool(row['technicalError'])
                        if row['technicalError']:reason=reason or row['technicalError']
                        publish()
        with mutex:
            if reason or any(j['id'] not in rows for j in jobs):
                status='budgetExhausted' if expired() or reason=='executionBudgetExhausted' else 'failed'
                guard.save(root/f'partial-{phase}.json',{'status':status,'reason':reason,
                    'unfinished':[j['id'] for j in jobs if j['id'] not in rows],
                    'rows':[rows[j['id']] for j in jobs if j['id'] in rows]})
                publish(status)
                return {'status':status,'verified':sum(j['id'] in rows and not rows[j['id']]['technicalError'] for j in jobs),'total':len(jobs)}
            verify_snapshot(root)
            runner.phase_results(root,phase,jobs,rows,guard,manifest_sha,context);publish('completed')
            return {'status':'completed','verified':len(jobs),'total':len(jobs)}


def check_service(unit):
    values=dict(line.split('=',1) for line in subprocess.check_output(
        ['systemctl','--user','show',unit,'-p','RuntimeMaxUSec,MemoryMax,MemorySwapMax,ControlGroup,MainPID'],text=True).splitlines() if '=' in line)
    # Main is launched only inside a bounded unit; tests call run() with synthetic jobs.
    maximum=values.get('RuntimeMaxUSec','')
    if maximum not in ('3h','180min','10800000000','10800s'):
        raise ValueError('Unit must have RuntimeMaxSec=10800 as an outer hard limit')
    if int(values.get('MemoryMax','0'))!=10*1024**3 or values.get('MemorySwapMax')!='0':
        raise ValueError('Unit requires MemoryMax=10G and MemorySwapMax=0')
    groups=[line.split(':',2)[-1] for line in Path('/proc/self/cgroup').read_text().splitlines()]
    group=values.get('ControlGroup','')
    if not group or not any(g==group or g.startswith(group+'/') for g in groups):
        raise ValueError('Controller must actually belong to the bounded unit')


def main():
    p=argparse.ArgumentParser();p.add_argument('--study',type=Path,required=True)
    p.add_argument('--phase',choices=['pilot','matrix','replay','matrix-replay'],required=True)
    p.add_argument('--decision',type=Path);p.add_argument('--workers',type=int,default=4)
    p.add_argument('--pilot-registry',type=Path)
    p.add_argument('--resume',action='store_true');p.add_argument('--unit',required=True)
    a=p.parse_args();check_service(a.unit)
    phases=['matrix','replay'] if a.phase=='matrix-replay' else [a.phase]
    for phase in phases:
        result=run(a.study,phase,workers=a.workers,decision_path=a.decision,resume=a.resume,pilot_registry=a.pilot_registry)
        print(result,flush=True)
        if result['status']!='completed':return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
