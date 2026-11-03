"""Remote workload entry point: pilot, cross-host science gate, bounded campaign.

Invoked by the host scheduler, never via a persistent SSH session.
"""
import argparse
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

import benchmark as b
import load_benchmark as load


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--main-wall-limit',type=int,required=True)
    parser.add_argument('--pilot-wall-limit',type=int,required=True)
    args=parser.parse_args();root=args.root.resolve();scripts=Path(__file__).resolve().parent
    state=dict(status='running',stage='preflight',pid=os.getpid(),startedEpoch=time.time(),
               protocolSha256=b.digest(root/'protocol.json'))
    b.save(root/'remote-job-status.json',state)
    def run(argv,limit):
        process=subprocess.Popen([sys.executable]+argv,start_new_session=os.name!='nt')
        try:code=process.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            else:os.killpg(process.pid,signal.SIGKILL)
            process.wait();raise TimeoutError('Remote stage exceeded wall-clock limit')
        if code:raise subprocess.CalledProcessError(code,process.args)
    try:
        protocol=b.read(root/'protocol.json');load.check_package(root,protocol)
        b.save(root/'environment.json',dict(os=platform.platform(),processor=platform.processor(),logicalCpus=os.cpu_count(),
               python=platform.python_version(),dotnetInfo=b.command(['dotnet','--info']),
               hardware=b.command(['lscpu']) if os.name!='nt' else b.command(['powershell','-NoProfile','-Command',
               'Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress']),
               runtimeEnvironment={k:v for k,v in os.environ.items() if k.startswith(('DOTNET_','COMPlus_'))},
               freeDiskBytes=__import__('shutil').disk_usage(root).free))
        if __import__('shutil').disk_usage(root).free<5*1024**3:raise ValueError('At least 5 GiB free required')
        if os.name!='nt' and not Path('/usr/bin/time').is_file():raise ValueError('GNU time required')
        state['stage']='pilot';b.save(root/'remote-job-status.json',state)
        run([str(scripts/'load_benchmark.py'),'--mode','pilot','--root',str(root)],args.pilot_wall_limit)
        admission=b.read(root/'admission.json');reference=b.read(root/'local-reference.json')
        pilot=b.read(root/'attempts.json')
        mismatches=[r['name'] for r in pilot['rows'] if not r['verified'] or r.get('fingerprint')!=reference['fingerprints'][r['cell']]]
        b.save(root/'cross-host-pilot-check.json',dict(status='PASS' if not mismatches and all(admission['cells'].values()) else 'FAIL',
               checkedAttempts=len(pilot['rows']),mismatches=mismatches,localProtocolSha256=reference['localProtocolSha256']))
        if mismatches or not all(admission['cells'].values()):
            raise ValueError('Remote pilot differs from local science or fails admission; main not started')
        run([str(scripts/'verify_load_benchmark.py'),str(root),'--pilot-only','--output',str(root/'pilot-verification.json')],600)
        state['stage']='campaign';b.save(root/'remote-job-status.json',state)
        run([str(scripts/'run_load_campaign.py'),'--root',str(root),'--skip-plot','--main-wall-limit',str(args.main_wall_limit)],
            args.main_wall_limit+1800)
        state['status']=b.read(root/'campaign-status.json')['status'];state['stage']='finished'
    except Exception as error:
        state.update(status='failed',error=str(error));raise
    finally:
        state['finishedEpoch']=time.time();b.save(root/'remote-job-status.json',state)


if __name__=='__main__':main()
