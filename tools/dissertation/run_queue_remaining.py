"""One missing4096 model against a previously accepted, sealed1024 reference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode=True
IDENTITY='queue-main-remaining-20261030-v1'
BASELINE_SHA='eb9b1aa1f93d618f42d123ba05cf5ace6a41832c359d4525b36f645d48611bd0'
BASELINE_ID='matrix.star.federation.q2.r5000.w10.s42'


def admit(base,candidate,known_wall,budget):
    if base.get('queueCapacity')!=1024 or candidate.get('queueCapacity')!=4096 or set(base)!=set(candidate) or {k for k in base if base[k]!=candidate[k]}!={'queueCapacity'}:
        raise ValueError('Only queueCapacity1024/4096 may differ')
    if not 0<known_wall<budget<=1800:
        raise ValueError('Budget must exceed observed reference cost and remain<=1800s')


def require_success(row):
    if row.get('returnCode')!=0 or row.get('technicalError') is not None:
        raise ValueError('Cannot reuse an incomplete/failed baseline')


def run(inputs,root):
    inputs,root=inputs.resolve(),root.resolve()
    # Only the already sealed original helper set is imported.
    sys.path.insert(0,str(inputs))
    from run_acceleration_pilot import digest,read,verify_files,SCIENTIFIC
    seal=read(inputs/'seal.json')
    if seal['programIdentity']!=IDENTITY:raise ValueError('Wrong program')
    verify_files(inputs,seal['files'])
    source=Path(read(inputs/'program.json')['source'])
    sys.path.insert(0,str(source/'tools/dissertation'))
    from network_study_inputs import StorageGuard,verify_snapshot,lock
    from network_study_verification import verify_loaded_run
    from network_review import check_graph
    from network_study_process import execute
    guard=StorageGuard(source.parent.parent,root,10*1024**3)
    group_name=next(line.split(':',2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    group=Path('/sys/fs/cgroup'+group_name)
    props=dict(line.split('=',1) for line in subprocess.check_output(['systemctl','--user','show',group.name,
        '-p','RuntimeMaxUSec','-p','TimeoutStopUSec'],text=True).splitlines())
    quota,period=(group/'cpu.max').read_text().split()
    if props!={'RuntimeMaxUSec':'30min 55s','TimeoutStopUSec':'5s'} or set(os.sched_getaffinity(0))!={2,3}:
        raise ValueError('Require declared1855+5s service and CPU2,3')
    if (group/'memory.max').read_text().strip()!=str(6*1024**3) or quota=='max' or int(quota)!=2*int(period) or (group/'memory.swap.max').read_text().strip()!='0':
        raise ValueError('Require6GiB/200%/swap0')
    with lock(guard.mount/'.mqtty-network-study.lock'):
        root.mkdir(parents=True,exist_ok=False)
        started,epoch=time.monotonic(),time.time()
        try:
            guard.save(root/'progress.json',dict(programIdentity=IDENTITY,phase='verifyPreviousBaseline',totalNewJobs=1,
                                               completedNewJobs=0,deadlineEpoch=epoch+1860))
            manifest=verify_snapshot(source)
            row_path=source/'rows'/f'{BASELINE_ID}.attempt-001.json'
            if digest(row_path)!=BASELINE_SHA:raise ValueError('Previously accepted index row changed')
            row=read(row_path);require_success(row)
            baseline=source/row['attempt']/'output'
            checked=verify_loaded_run(baseline)
            if checked!=row['summary'] or checked['status']!='completed':raise ValueError('Baseline recount failed')
            for name,expected in row['scientificSha256'].items():
                if digest(baseline/name)!=expected:raise ValueError('Baseline scientific SHA changed')
            base=read(baseline/'inputs/config.json');candidate=read(inputs/'config.json')
            admit(base,candidate,row['processWallSeconds'],1800)
            guard.save(root/'baseline-reference.json',dict(id=BASELINE_ID,rowSha256=BASELINE_SHA,
                rawArithmeticPassed=True,scientificSha256=row['scientificSha256'],
                knownProcessWallSeconds=row['processWallSeconds'],config=base,
                referenceSource='Previously locally accepted4412-file network archive; no baseline recomputation'))
            attempt=root/'attempt-001';attempt.mkdir()
            argv=[manifest['dotnet'],str(source/'runtime/EntityFX.MqttY.Federation.App.dll'),
                  'run','--config',str(inputs/'config.json'),'--output','output']
            guard.save(attempt/'command.json',dict(argv=argv,sealSha256=digest(inputs/'seal.json')))
            guard.save(root/'progress.json',dict(programIdentity=IDENTITY,phase='model4096',totalNewJobs=1,
                completedNewJobs=0,baselineReused=True,baselineRecountPassed=True,deadlineEpoch=epoch+1860))
            result=execute(argv,attempt,guard,max(1,started+1800-time.monotonic()),2*1024**3,1024**3,
                lambda value:guard.save(attempt/'process-state.json',value))
            guard.save(attempt/'execution.json',result)
            if result['technicalError'] or result['returnCode']!=0:raise RuntimeError('new4096:'+str(result))
            output=attempt/'output';summary=verify_loaded_run(output)
            check_graph(output/'topology.graphml',read(output/'run-config.json'))
            if summary['status']!='completed' or digest(output/'inputs/config.json')!=digest(inputs/'config.json'):
                raise ValueError('Incomplete candidate or changed config')
            verify_snapshot(source);verify_files(inputs,seal['files'])
            fields=('apiAttempts','sourceCompleted','expectedDeliveries','uniqueDeliveries','deliveryLoss','queueDrops','receiverP99Ms')
            final=dict(programIdentity=IDENTITY,phase='completed',totalNewJobs=1,completedNewJobs=1,baselineReused=True,
                metrics1024={k:checked[k] for k in fields},metrics4096={k:summary[k] for k in fields},
                scientificSha256={name:digest(output/name) for name in SCIENTIFIC},
                sourceInputsUnchanged=True,comparisonIsSpeedBenchmark=False,retryPerformed=False,
                elapsedSeconds=time.monotonic()-started,baselineRowSha256=BASELINE_SHA)
            guard.save(root/'result.json',final);guard.save(root/'progress.json',final)
        except BaseException as error:
            failure=dict(programIdentity=IDENTITY,phase='failed',error=repr(error),traceback=traceback.format_exc(),
                retryPerformed=False,elapsedSeconds=time.monotonic()-started)
            guard.save(root/'failure.json',failure);guard.save(root/'progress.json',failure);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.inputs,args.output)
