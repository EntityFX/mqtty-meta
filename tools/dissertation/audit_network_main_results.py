"""Read-only raw recount of an incomplete campaign; never starts simulations."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import traceback

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
from network_review import check_comparison, check_graph
from network_study_inputs import contained
from network_study_verification import verify_loaded_run

MODES=('single','relay','federation')
METRICS=('sourceCompletedRps','uniqueGoodput','deliveryLoss','receiverP99Ms','publishP99Ms',
    'forwardAttempts','forwardTopicPayloadBytes','uniqueDuringDrain','expectedDeliveries',
    'uniqueDeliveries','fullyDeliveredPublications','sourceCompleted','sourceCompletedDuringDrain',
    'apiAttempts','queueDrops','hopLimitDrops','forwardRejected','backpressureUnsent',
    'duplicateDeliveries','suppressedDuplicates')


def key(options):return tuple(options[k] for k in ('topology','qos','offeredRps','seed'))


def matched_cases(cases):
    groups=defaultdict(dict)
    for row in cases:
        group=groups[key(row['options'])];mode=row['options']['mode']
        if mode not in MODES or mode in group:raise ValueError('Unexpected/duplicate mode in group')
        group[mode]=row
    selected=[];excluded=[]
    for group_key,group in sorted(groups.items()):
        if set(group)==set(MODES):
            rows=[group[m] for m in MODES]
            check_comparison([r['options'] for r in rows])
            selected.extend(rows)
        else:excluded.extend(r['id'] for r in group.values())
    return selected,sorted(excluded)


def comparison_rows(cases):
    selected,_=matched_cases(cases);groups=defaultdict(list)
    for row in selected:
        o=row['options'];groups[(o['topology'],o['qos'],o['offeredRps'],o['mode'])].append(row)
    result=[]
    for (topology,qos,rps,mode),rows in sorted(groups.items()):
        metrics={}
        for name in METRICS:
            values=[r['summary'][name] for r in rows if r['summary'][name] is not None]
            metrics[name]=dict(n=len(values),mean=statistics.mean(values) if values else None,
                min=min(values) if values else None,max=max(values) if values else None)
        result.append(dict(topology=topology,qos=qos,offeredRps=rps,mode=mode,
            matchedSeeds=sorted(r['options']['seed'] for r in rows),metrics=metrics))
    return result


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_bytes((json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8'))


def audit(root,output):
    root=Path(root).resolve(strict=True);output=Path(output).resolve()
    if output.exists() or output.is_relative_to(root):raise ValueError('Use a new output outside the source directory')
    plan=read(root/'program/plan.json');manifest_sha=sha(root/'execution-manifest.json')
    planned={j['id']:j for j in plan['jobs'] if j['kind']=='matrix'}
    latest={}
    for path in sorted((root/'rows').glob('*.json')):
        row=read(path)
        if row['kind']!='matrix':continue
        job=planned[row['id']]
        if row['executionManifestSha256']!=manifest_sha or row['configSha256']!=job['sha256']:raise ValueError('Result identity differs')
        if read(contained(root,row['attempt']+'/result.json'))!=row:raise ValueError('Attempt receipt differs')
        if row['id'] not in latest or row['attemptNumber']>latest[row['id']]['attemptNumber']:latest[row['id']]=row
    candidates=[r for _,r in sorted(latest.items()) if r['technicalError'] is None]
    output.mkdir(parents=True);(output/'cases').mkdir()
    records=[];failures=[];started=time.monotonic()
    for index,row in enumerate(candidates,1):
        before=time.monotonic();directory=contained(root,row['attempt']+'/output');job=planned[row['id']]
        receipt=dict(id=row['id'],attempt=row['attempt'],inputConfigSha256=job['sha256'])
        try:
            options=read(contained(root/'program',job['config']))
            if sha(contained(root/'program',job['config']))!=job['sha256']:raise ValueError('Frozen config differs')
            summary=verify_loaded_run(directory)
            if summary!=row['summary'] or row['returnCode']!=0:raise ValueError('Recounted summary differs from row')
            if sha(directory/'inputs/config.json')!=job['sha256']:raise ValueError('Executed input differs')
            for name,digest in row['scientificSha256'].items():
                if sha(contained(directory,name))!=digest:raise ValueError('Scientific receipt differs: '+name)
            resolved=read(directory/'run-config.json')
            if any(resolved.get(k)!=v for k,v in options.items() if k!='brokerProfilePath'):raise ValueError('Resolved options differ')
            check_graph(directory/'topology.graphml',resolved)
            records.append(dict(id=row['id'],options=resolved,summary=summary,warnings=row['warnings']))
            receipt.update(status='rawArithmeticPassed',summarySha256=sha(directory/'summary.json'),
                metrics={k:summary[k] for k in METRICS},originalWarnings=row['warnings'])
        except Exception as error:
            receipt.update(status='rawArithmeticFailed',error=f'{type(error).__name__}: {error}',traceback=traceback.format_exc())
            failures.append(receipt)
        receipt['verificationSeconds']=time.monotonic()-before
        write(output/'cases'/f"{row['id']}.json",receipt)
        progress=dict(status='running',processed=index,total=len(candidates),passed=len(records),failed=len(failures),
            elapsedSeconds=time.monotonic()-started,latest=row['id'])
        write(output/'progress.json',progress)
        if index%12==0 or index==len(candidates):print(json.dumps(progress),flush=True)
    selected,excluded=matched_cases(records);conditions=comparison_rows(records)
    write(output/'condition-stats.json',conditions)
    with (output/'comparison.csv').open('w',encoding='utf-8',newline='') as stream:
        columns=['topology','qos','offeredRps','mode','matchedSeeds','matchedSeedCount']+list(METRICS)+['receiverP99ApplicableRuns']
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for row in conditions:
            writer.writerow({**{k:row[k] for k in columns[:4]},'matchedSeeds':','.join(map(str,row['matchedSeeds'])),
                'matchedSeedCount':len(row['matchedSeeds']),**{m:row['metrics'][m]['mean'] for m in METRICS},
                'receiverP99ApplicableRuns':row['metrics']['receiverP99Ms']['n']})
    warning_labels=Counter(w for r in records for w in r['warnings'])
    source_files=[Path(__file__),Path(__file__).with_name('network_study_verification.py'),
        Path(__file__).with_name('network_review.py'),Path(__file__).resolve().parents[1]/'federation/study.py']
    result=dict(status='rawArithmeticVerifiedAvailableSubset' if not failures else 'rawArithmeticVerificationFailed',
        sourceRoot=str(root),executionManifestSha256=manifest_sha,
        availableCases=len(candidates),rawPassed=len(records),rawFailed=len(failures),
        plannedMatrix=len(planned),missingResultKeys=[k for k in planned if k not in latest or latest[k]['technicalError']],
        completeModeTriplets=len(selected)//3,plannedModeTriplets=len(planned)//3,
        excludedAvailableCaseIds=excluded,conditionModeRows=len(conditions),
        originalWarningCases=sum(bool(r['warnings']) for r in records),warningLabels=dict(warning_labels),
        replaysCompleted=read(root/'progress.json')['counts']['replay']['done'],
        replaysPlanned=plan['replayJobs'],mainCampaignComplete=False,
        failures=failures,elapsedSeconds=time.monotonic()-started,
        checkerSourceSha256={p.name:sha(p) for p in source_files},
        scientificInputsChanged=False,newSimulationsExecuted=False,
        scope='Existing independent checker rerun on available raw; matched means across common seeds, not validation of all planned scenarios or new exact simulation replays')
    write(output/'verification.json',result)
    write(output/'progress.json',{**progress,'status':result['status']})
    print(json.dumps({k:v for k,v in result.items() if k not in ('failures','missingResultKeys','checkerSourceSha256','excludedAvailableCaseIds')},ensure_ascii=False),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=audit(args.root,args.output)
    if result['rawFailed']:raise SystemExit(1)
