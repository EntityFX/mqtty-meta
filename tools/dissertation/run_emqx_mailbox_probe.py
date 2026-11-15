"""Two separate short diagnostic arms; never edits the final experiment inputs."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_twin_cohort import events, file_digest
from audit_twin_connections import subscriber_coverage
from run_fresh_twin_pilot import verify_events, write_new
from run_fresh_twin_series import available_port, broker_start, execute, terminate


def run(base, output):
    base, output = base.resolve(), output.resolve()
    if not Path('/run/media/entityfx/hd250').is_mount() or output.parent != base:
        raise ValueError('Expected evidence mount and separate diagnostic child directory')
    if (output/'progress.json').exists():
        raise ValueError('Diagnostic has already been started')
    inputs = base/'inputs'
    manifest = json.loads((inputs/'input-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if file_digest(inputs/name) != digest:
            raise ValueError('Frozen input changed: '+name)
    program = json.loads((base/'launcher-v2/program.json').read_text())
    metadata = json.loads(Path(program['standMetadata']['EMQX']).read_text())['brokers'][0]
    original = json.loads((base/'series-attempt-001/runs/EMQX.q1.p128.r1/attempt-002/config.json').read_text())
    results=[]
    started=time.monotonic()

    def progress(status, arm=None, error=None):
        target=output/'progress.tmp'
        target.write_text(json.dumps(dict(status=status, completed=len(results), total=2,
            arm=arm,error=error,elapsedSeconds=time.monotonic()-started),indent=2))
        target.replace(output/'progress.json')

    try:
        for maximum in (1000,5000):
            progress('running',maximum)
            case=output/('mailbox-'+str(maximum));case.mkdir(exist_ok=False)
            config=dict(original,endpoint='mqtt://127.0.0.1:33085')
            write_new(case/'config.json',config)
            arm_meta=deepcopy(metadata)
            arm_meta['environment']['EMQX_FORCE_SHUTDOWN__MAX_MAILBOX_SIZE']=str(maximum)
            available_port(33085)
            broker=log=None
            try:
                broker,log=broker_start(arm_meta,case,33085,90)
                home=case/'broker-home'
                env=dict(os.environ,**arm_meta['environment'])
                env['EMQX_NODE__NAME']='article1_'+str(os.getpid())+'@127.0.0.1'
                effective=subprocess.run([str(home/'bin/emqx'),'ctl','conf','show','force_shutdown'],
                    cwd=home,env=env,capture_output=True,text=True,timeout=20)
                write_new(case/'effective-config.json',dict(exitCode=effective.returncode,stdout=effective.stdout,stderr=effective.stderr))
                if effective.returncode or not re.search(r'max_mailbox_size\s*(?:=>|=|:)\s*'+str(maximum)+r'\b',effective.stdout):
                    raise ValueError('Effective mailbox setting was not confirmed; load not started')
                process=execute(['dotnet',str(inputs/'runtime/TwinValidationProbe.dll'),'native',str(case/'config.json'),str(case/'native')],case/'native.log','1,2',30)
                metrics=verify_events(case/'native')
                phases=json.loads((case/'native/phases.json').read_text())
                connections=subscriber_coverage(list(events(case/'native/connections.ndjson')),
                    phases['measurementStartSeconds'],phases['measurementEndSeconds'])
            finally:
                if broker is not None:
                    terminate(broker)
                    log.close()
            shutdowns=[line for line in (case/'broker.log').read_text(errors='replace').splitlines()
                if 'check_oom_shutdown' in line]
            result=dict(maxMailboxSize=maximum,metrics=metrics,subscriber=connections,
                brokerShutdowns=shutdowns,process=process,scope='Diagnostic; excluded from final accuracy matrix')
            write_new(case/'result.json',result)
            results.append(result)
        for name,digest in manifest['files'].items():
            if file_digest(inputs/name)!=digest:
                raise ValueError('Frozen input changed during diagnostic: '+name)
        write_new(output/'summary.json',dict(status='completed',arms=results,
            frozenInputsUnchanged=True,scientificCriteriaChanged=False,scope='Two one-factor diagnostic arms; not validation of profile accuracy'))
        progress('completed')
    except Exception as error:
        write_new(output/'failure.json',dict(error=repr(error),completedArms=len(results)))
        progress('failed',error=repr(error))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    run(args.base,args.output)
