"""Repeat selected completed simulations with their unchanged configs and frozen CLI."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(controller, output, dotnet):
    plan, state = read(controller/'series.json'), read(controller/'state.json')
    if state['status'] != 'completed':
        raise ValueError('Run only after campaign completion')
    runtime = Path(plan['simulatorCli'])
    if sha(runtime) != '60c5a79fca5291b061af345f3fb98e758c62ae551763693d5f39f74d40e9cebd':
        raise ValueError('Frozen runtime mismatch')
    output.mkdir(parents=True, exist_ok=False)
    rows=[]
    result=dict(excludedFromCampaignCoverage=True, purpose='Same-input repeatability; not additional blind validation',
                runtimeFiles={p.name:sha(p) for p in sorted(runtime.parent.glob('*.dll'))}, rows=rows)
    selected=[(b,'holdout') for b in ('Aedes','Mosquitto','ActiveMQ','EMQX')]+[('Aedes','relay'),('MqttRelay-Mixed','relay')]
    for broker,stage in selected:
        base=controller/'mixed' if broker=='MqttRelay-Mixed' else controller/'brokers'/broker
        folder=base/'simulations'/f'{stage}-2s-overnight-v1'
        key='m16.q1.p1.s42'
        entry=next(r for r in read(folder/'index.json')['runs'] if r['key']==key)
        original=Path(entry['directory'])
        config=folder/(key+'.json')
        cfg=read(config)
        profiles=[Path(p) for p in cfg.get('brokerProfilePaths',[])]+([Path(cfg['brokerProfilePath'])] if 'brokerProfilePath' in cfg else [])
        before={str(p):sha(p) for p in profiles}
        for p in profiles:
            if before[str(p)]!=state['profiles'][p.parent.name]:
                raise ValueError('Profile mismatch')
        destination=output/f'{broker}-{stage}'
        print('REPLAY',broker,stage,flush=True)
        started=time.monotonic()
        with (output/f'{broker}-{stage}.log').open('w') as log:
            proc=subprocess.run([dotnet,str(runtime),'experiment','--config',str(config),'--output',str(destination)],
                                stdout=log,stderr=subprocess.STDOUT,timeout=1200)
        if proc.returncode not in (0,3):
            raise ValueError('Replay execution failed')
        repeat=next(p for p in destination.iterdir() if p.is_dir())
        comparisons={name: read(original/name)==read(repeat/name)
                     for name in ('summary.json','counters.json','latency-histogram.json')}
        counter_pair=[read(p/'counters.json') for p in (original,repeat)]
        comparisons['scientificCounters']=all(counter_pair[0][k]==counter_pair[1][k]
                                              for k in ('brokers','receivers','receiverScope'))
        manifests=[read(p/'experiment-manifest.json') for p in (original,repeat)]
        comparisons['phases']=manifests[0]['phases']==manifests[1]['phases']
        comparisons['profiles']=manifests[0]['provenance']['brokerProfiles']==manifests[1]['provenance']['brokerProfiles']
        unchanged=all(sha(Path(p))==h for p,h in before.items())
        rows.append(dict(broker=broker,stage=stage,key=key,wallSeconds=time.monotonic()-started,
                         originalDirectory=str(original),repeatDirectory=str(repeat),
                         configSha256=sha(config),profileSha256=before,profilesUnchanged=unchanged,
                         equal=comparisons,returnCode=proc.returncode))
        (output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('DONE',broker,comparisons,flush=True)
        if not unchanged:
            raise ValueError('Profile modified')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dotnet',default=shutil.which('dotnet'))
    args=parser.parse_args()
    run(args.controller,args.output,args.dotnet)
