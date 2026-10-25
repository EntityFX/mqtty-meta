"""Collect actual phase ticks and replay four declared final-series points; bounded."""
import argparse,json,shutil,time
from pathlib import Path
from audit_twin_cohort import file_digest
from run_fresh_twin_series import execute,reverify_events
from run_fresh_twin_pilot import write_new

def main(base,output):
    base=Path(base);output=Path(output);output.mkdir(exist_ok=False)
    frozen=json.loads((base/'inputs/input-manifest.json').read_text())
    for relative,sha in frozen['files'].items():
        assert file_digest(base/'inputs'/relative)==sha,relative
    summary=json.loads((base/'series-attempt-001/analysis-176244/summary.json').read_text())
    cases=[];selected=[]
    for row in summary['rows']:
        parent=base/'series-attempt-001/runs'/row['id']
        case=next(p.parent for p in sorted(parent.glob('attempt-*/result.json'),reverse=True) if json.loads(p.read_text()).get('native'))
        trial=json.loads((case/'model/trial.json').read_text());model=Path(trial['runDirectory'])
        manifest=json.loads((model/'experiment-manifest.json').read_text())
        cases.append(dict(id=row['id'],manifest=manifest,modelProcess=row.get('modelProcess'),nativeProcess=row.get('nativeProcess'),modelPhases=json.loads((model/'phases.json').read_text())))
        if row['qos']==1 and row['publishers']==16 and row['seed']==42:selected.append((row,case,model))
    write_new(output/'phase-evidence.json',cases)
    write_new(output/'replay-selection.json',dict(keys=[r['id'] for r,_,_ in selected],scope='one QoS1 N16 seed42 point per broker; same frozen runtime, inputs and CPU1'))
    results=[]
    for row,case,model in selected:
        new=output/row['id'];new.mkdir();shutil.copyfile(case/'config.json',new/'config.json')
        process=execute(['dotnet',str(base/'inputs/runtime/TwinValidationProbe.dll'),'model',str(new/'config.json'),str(new/'model')],new/'model.log','1',120)
        replay=Path(json.loads((new/'model/trial.json').read_text())['runDirectory'])
        before=reverify_events(model);after=reverify_events(replay)
        same_metrics={k:v for k,v in before.items() if k!='rawSha256'}=={k:v for k,v in after.items() if k!='rawSha256'}
        hashes={name:dict(original=file_digest(model/name),replay=file_digest(replay/name)) for name in ['summary.json','latency-histogram.json','publishes.ndjson','deliveries.ndjson','phases.json']}
        result=dict(id=row['id'],sameCohortMetrics=same_metrics,hashes=hashes,allSelectedFilesIdentical=all(v['original']==v['replay'] for v in hashes.values()),process=process)
        results.append(result);write_new(new/'verification.json',result)
    write_new(output/'replay-summary.json',dict(cases=results,passed=all(r['sameCohortMetrics'] and r['allSelectedFilesIdentical'] for r in results),scope='Four chosen model points, not independent physical replication'))
    print(json.dumps(dict(cases=len(results),passed=all(r['sameCohortMetrics'] and r['allSelectedFilesIdentical'] for r in results))))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();main(args.base,args.output)
