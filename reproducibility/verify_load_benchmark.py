"""Offline independent verification of load_benchmark.py evidence, including timer sidecars."""
import argparse
import math
from pathlib import Path

import benchmark as b
import verify_benchmark_results as a


def verify(root, pilot_only=False):
    protocol = b.read(root / 'protocol.json')
    state = b.read(root / 'attempts.json')
    gate = b.read(root / 'admission.json')
    sha = b.digest(root / 'protocol.json')
    a.require(state['protocolSha256'] == gate['protocolSha256'] == sha, 'Protocol identity')
    cases = {c['name']: c for c in protocol['cells']}
    actual = [(r['phase'],r['cell'],r['repeat'],r['execution'],r['timed']) for r in state['rows']]
    expected = [('pilot',c,1,m,True) for c in cases for m in ('serial','parallel')]
    if not pilot_only:
        expected += [('main',r['cell'],r['repeat'],r['execution'],True) for r in protocol['mainSchedule'] if gate['cells'][r['cell']]]
        expected += [('control','b4-r1000',n,m,t) for n in range(1,4) for m in ('serial','parallel')
                     for t in ((False,True) if n % 2 else (True,False))]
    a.require(actual == expected and len(actual)==len(set(actual)), 'Attempt coverage/order changed')
    result = dict(protocolSha256=sha, scope='pilots-only' if pilot_only else 'full-campaign', attemptsChecked=0, sealedFilesChecked=0, timerSidecarsChecked=0,
                  processFailures=[], arithmeticFailures=[], scienceMismatches=[], controllerDisagreements=[], rows=[])
    baseline = {}
    binaries = {n:h for n,h in protocol['appSha256'].items() if n.endswith('.dll')}
    for row in state['rows']:
        folder = root / 'attempts' / row['name']; raw = folder / 'raw'
        measurement = b.read(folder / 'measurement.json')
        a.require(all(row[k]==measurement[k] for k in measurement), 'Changed measurement: '+row['name'])
        if row['returnCode']!=0 or row['timedOut']:
            a.require(not row['verified'], 'Failed process marked verified')
            result['processFailures'].append(dict(attempt=row['name'],returnCode=row['returnCode'],timedOut=row['timedOut']))
            result['rows'].append(dict(attempt=row['name'],arithmeticPass=False,serialScienceMatch=False))
            result['attemptsChecked']+=1
            continue
        argv = b.read(folder / 'process.json')['argv']
        index = next(i for i,s in enumerate(argv) if s.replace('\\','/').endswith('/LoadProbe.dll'))
        a.require(argv[index+1:index+3]==[row['execution'],'timed' if row['timed'] else 'plain'], 'Wrong engine mode')
        specfile = root / 'configs' / (row['cell']+'.json')
        a.require(b.digest(specfile)==protocol['configSha256'][specfile.name], 'Changed config')
        spec = b.read(specfile)
        a.require(b.read(raw / 'inputs/config.json')==spec, 'Wrong actual config')
        seals, fingerprints = a.seal(raw)
        a.require(b.read(raw / 'manifest.json')['runtime']['binarySha256']==binaries, 'Wrong actual binaries')
        if row['phase']=='pilot' and row['execution']=='serial': baseline[row['cell']]=fingerprints
        same = fingerprints==baseline.get(row['cell'])
        if not same: result['scienceMismatches'].append(row['name'])
        arithmetic = True
        try:
            a.recount(raw,'load-balancer',spec)
        except ValueError as error:
            arithmetic = False
            result['arithmeticFailures'].append(dict(attempt=row['name'],error=str(error)))
        if arithmetic != row['verified']:
            result['controllerDisagreements'].append(row['name'])
        if row['verified']:
            a.require(fingerprints==row['fingerprint'], 'Controller fingerprint changed')
        if row['timed']:
            timer = b.read(folder / 'refresh-timing.json')
            summary = b.read(raw / 'summary.json')
            a.require(timer['stopwatchFrequency']>0, 'Invalid clock frequency')
            for phase in summary['phases']:
                name = phase['phase']; calls = timer['refreshCallsByPhase'].get(name,0)
                a.require(calls==phase['endTick']-phase['startTick'], 'Timer/virtual phase count differs')
                elapsed = timer['refreshWallSecondsByPhase'].get(name,0)
                a.require(0<=elapsed<=timer['phaseWallSeconds'][name], 'Refresh exceeds enclosing phase')
            a.require(math.isclose(sum(timer['refreshWallSecondsByPhase'].values()),timer['refreshWallSeconds'],rel_tol=1e-12), 'Refresh sum')
            a.require(timer['refreshWallSeconds']<=timer['runnerWallSeconds']<=row['wallSeconds'], 'Timer nesting')
            for key,path in [('measureRefreshSeconds',('refreshWallSecondsByPhase','measure')),
                             ('measurePhaseSeconds',('phaseWallSeconds','measure'))]:
                if key in row:a.require(row[key]==timer[path[0]][path[1]],'Changed derived timer')
            for key,source in [('runnerSeconds','runnerWallSeconds'),('exportSeconds','exportWallSeconds'),('fullRefreshSeconds','refreshWallSeconds')]:
                if key in row:a.require(row[key]==timer[source], 'Changed derived duration')
            result['timerSidecarsChecked']+=1
        result['attemptsChecked']+=1; result['sealedFilesChecked']+=seals
        result['rows'].append(dict(attempt=row['name'],arithmeticPass=arithmetic,serialScienceMatch=same))
    a.require(not result['controllerDisagreements'], 'Independent validation disagrees with controller')
    for name in cases:
        pilot = [r for r in result['rows'] if r['attempt'].startswith('pilot-'+name+'-')]
        admission = len(pilot)==2 and all(r['arithmeticPass'] and r['serialScienceMatch'] for r in pilot)
        a.require(admission==gate['cells'][name], 'Pilot admission differs')
    result['status'] = 'PASS' if not result['processFailures'] and not result['arithmeticFailures'] and not result['scienceMismatches'] else 'DIAGNOSTIC'
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--pilot-only',action='store_true')
    args=p.parse_args();result=verify(args.root.resolve(),pilot_only=args.pilot_only);b.save(args.output,result)
    print(result['status'],result['attemptsChecked'],'attempts',result['sealedFilesChecked'],'sealed files')
