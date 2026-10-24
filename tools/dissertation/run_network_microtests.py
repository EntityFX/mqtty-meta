"""Four bounded mechanism checks; not a scientific accuracy campaign."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parent))
from network_study_inputs import StorageGuard, lock, read, sha
from network_study_process import execute


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--root',type=Path,required=True)
    a=p.parse_args(); ref=a.reference.resolve(); root=a.root.resolve()
    manifest=read(ref/'staging-manifest.json')
    guard=StorageGuard(manifest['mount'],root,10*1024**3)
    dll=ref/'runtime/EntityFX.MqttY.Federation.App.dll'
    for relative,expected in manifest['files'].items():
        if relative.startswith('runtime/') and sha(ref/relative)!=expected:
            raise ValueError('Pinned runtime changed: '+relative)
    if sha(Path(manifest['dotnet']))!=manifest['dotnetSha256']:
        raise ValueError('Dotnet host changed')
    module=Path(manifest['originalRoot'])/'tools/dissertation/run_network_study.py'
    sys.path.insert(0,str(module.parent))
    spec=importlib.util.spec_from_file_location('micro_verifier',module)
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    cases=[('q0',0,1000,4,1024,.02),('q1',1,1000,4,1024,.02),
           ('q2',2,1000,4,1024,.02),('queue-overflow-q1',1,5000,64,1,.05)]
    rows=[];started=time.monotonic()
    with lock(guard.mount/'.mqtty-network-study.lock'):
        root.mkdir(exist_ok=False)
        guard.save(root/'inputs.json',{'reference':str(ref),'runtimeSha256':sha(dll),
            'helperSha256':sha(Path(__file__)),'verifierSha256':sha(module),
            'totalProcessBudgetSeconds':60,'perProcessBudgetSeconds':15,
            'scope':'mechanism smoke; no accuracy claim or matrix admission'})
        for name,qos,rate,publishers,capacity,window in cases:
            remaining=60-(time.monotonic()-started)
            if remaining<=0: break
            folder=root/name;folder.mkdir()
            config=read(ref/'inputs/config.json')
            config.update(qos=qos,offeredRps=rate,publishers=publishers,queueCapacity=capacity,
                          measurementSeconds=window,warmupSeconds=0,drainTimeoutSeconds=1)
            guard.save(folder/'config.json',config)
            command=[manifest['dotnet'],str(dll),'run','--config',str(folder/'config.json'),
                     '--output','output','--diagnostics']
            guard.save(folder/'command.json',{'argv':command,'configSha256':sha(folder/'config.json')})
            row={'case':name,**execute(command,folder,guard,min(15,remaining),1024**3,64*1024**2,
                                     lambda state:None,sample_seconds=.05)}
            try:
                if row['technicalError'] or row['returnCode']!=0:
                    raise ValueError('CLI did not complete: '+str(row))
                s,warnings=verifier.verify_output(folder/'output',folder/'config.json',Path(manifest['originalRoot']))
                if s['status']!='completed' or s['duplicateDeliveries']!=0:
                    raise ValueError('Unexpected completion or duplicates')
                if name.startswith('queue-overflow'):
                    if s['queueDrops']<=0: raise ValueError('Overflow not exercised')
                elif s['uniqueDeliveries']!=s['expectedDeliveries'] or warnings:
                    raise ValueError('Ideal low-load delivery incomplete')
                d=read(folder/'output/drain-diagnostics.json')['snapshots'][-1]
                if not d['isIdle']: raise ValueError('Pending model state after completion')
                row.update(passed=True,uniqueDeliveries=s['uniqueDeliveries'],expectedDeliveries=s['expectedDeliveries'],
                           queueDrops=s['queueDrops'],warnings=warnings,finalIdle=True,sealsVerified=True)
            except Exception as error:
                row.update(passed=False,reason=str(error))
            rows.append(row);guard.save(folder/'result.json',row)
            guard.save(root/'results.json',{'rows':rows,'wallSeconds':time.monotonic()-started,'matrixAdmitted':False})
            if not row['passed']: break
    result={'passed':sum(r['passed'] for r in rows),'total':4,'wallSeconds':time.monotonic()-started,'rows':rows,'matrixAdmitted':False}
    guard.save(root/'results.json',result)
    print(json.dumps(result))
    return 0 if result['passed']==4 else 1
if __name__=='__main__': raise SystemExit(main())
