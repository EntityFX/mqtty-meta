"""Retrospective condition-level analysis requested by the external critique."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('rps', 'latencyP99Ms', 'publishFailureRate', 'deliveryLossRate')
BROKERS = ('Aedes','Mosquitto','ActiveMQ','EMQX')


def encode_csv(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, list(rows[0]), lineterminator='\n')
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def condition_rows(models):
    groups = defaultdict(list)
    for broker, runs in models.items():
        for row in runs:
            if row['stage'] in ('fidelity', 'holdout'):
                groups[(broker,row['stage'],row['bytes'],row['qos'],row['publishers'])].append(row)
    expected = set(itertools.product(BROKERS, ('fidelity','holdout'), (16,256), range(3), (1,16,64,128)))
    if set(groups) != expected:
        raise ValueError('Missing experimental conditions')
    result = []
    for key, runs in sorted(groups.items()):
        if sorted(r['seed'] for r in runs) != [42,43,44]:
            raise ValueError('Missing or duplicate seed')
        broker, stage, size, qos, publishers = key
        row = dict(broker=broker,stage=stage,payloadBytes=size,qos=qos,publishers=publishers,
                   passedSeeds=sum(not any(c['status']=='failed' for c in r['summary']['acceptanceCriteria']) for r in runs))
        for name in NAMES:
            cells = [next(c for c in r['summary']['acceptanceCriteria'] if c['name']==name) for r in runs]
            errors = [c['error'] for c in cells if c['error'] is not None]
            row[name+'MaxError'] = max(errors) if errors else None
            row[name+'MinError'] = min(errors) if errors else None
            row[name+'ApplicableSeeds'] = len(errors)
            actual = [c['simulated'] for c in cells if c['simulated'] is not None]
            row[name+'SimulatedMin'] = min(actual) if actual else None
            row[name+'SimulatedMax'] = max(actual) if actual else None
            row[name+'SimulatedMean'] = statistics.mean(actual) if actual else None
            references = [c['reference'] for c in cells]
            if any(value != references[0] for value in references):
                raise ValueError('Reference changes between seeds of one condition')
            row[name+'NativeReference'] = references[0]
        row['p99PassedButLossFailedSeeds'] = sum(
            any(c['name']=='latencyP99Ms' and c['status']=='passed' for c in r['summary']['acceptanceCriteria']) and
            any(c['name']=='deliveryLossRate' and c['status']=='failed' for c in r['summary']['acceptanceCriteria']) for r in runs)
        result.append(row)
    return result


def threshold_pass(row, multiplier):
    limits = dict(rps=.15,latencyP99Ms=.20,publishFailureRate=.05,deliveryLossRate=.05)
    return all(row[name+'MaxError'] is None or row[name+'MaxError'] <= limits[name]*multiplier for name in NAMES)


def quantile(values, p):
    values = sorted(values)
    return values[max(0,math.ceil(len(values)*p)-1)] if values else None


def build(output):
    if output.exists(): raise ValueError('Use a new output directory')
    paths = ['reports/data/model-evidence.json','reports/data/native-evidence.json',
             'reports/audits/dissertation/accuracy-analysis-v1/analysis.json','reports/data/environment.json']
    raw = {p:(ROOT/p).read_bytes() for p in paths}
    models,native,accuracy,environment = [json.loads(raw[p]) for p in paths]
    models={b:models[b] for b in BROKERS}
    conditions = condition_rows(models)
    counts = []
    for broker,stage in itertools.product(models, ('fidelity','holdout')):
        group=[r for r in conditions if r['broker']==broker and r['stage']==stage]
        counts.append(dict(broker=broker,stage=stage,conditions=len(group),allSeedsPassed=sum(r['passedSeeds']==3 for r in group),
                           someSeedsPassed=sum(0<r['passedSeeds']<3 for r in group),noSeedPassed=sum(r['passedSeeds']==0 for r in group),
                           passedSeedRuns=sum(r['passedSeeds'] for r in group),totalSeedRuns=3*len(group)))
    control=[r for r in conditions if r['stage']=='holdout']
    thresholds=[dict(multiplier=m,rpsLimit=.15*m,p99Limit=.20*m,absoluteRateLimit=.05*m,
                     allSeedsConditionsPassed=sum(threshold_pass(r,m) for r in control),conditions=96)
                for m in (.5,.75,1.,1.25,1.5,2.,3.)]
    distributions=[]
    for broker in models:
        for name in NAMES:
            values=[r[name+'MaxError'] for r in control if r['broker']==broker and r[name+'MaxError'] is not None]
            distributions.append(dict(broker=broker,metric=name,nConditions=len(values),
                                      median=quantile(values,.5),p90=quantile(values,.9),maximum=max(values)))
    # Exhaustive empirical resampling of three native repetitions: 3^3=27.
    # These discrete bounds are diagnostic and do not claim calibrated 95% coverage.
    native_groups=defaultdict(list)
    for run in native['runs']:
        if run['stage']=='validation':
            k=run['key'];native_groups[(run['broker'],k['messageBytes'],k['qos'],k['publishers'])].append(run)
    native_bounds=[]
    for key,runs in sorted(native_groups.items()):
        if sorted(r['key']['repeat'] for r in runs)!=[1,2,3]:raise ValueError('Missing native repeat')
        values=[r['observation']['measurement']['deliveryLossRate'] for r in runs]
        boot=[statistics.mean(sample) for sample in itertools.product(values,repeat=3)]
        native_bounds.append(dict(broker=key[0],payloadBytes=key[1],qos=key[2],publishers=key[3],n=3,
                                 meanLoss=statistics.mean(values),minLoss=min(values),maxLoss=max(values),
                                 empiricalBootstrapP025=quantile(boot,.025),empiricalBootstrapP975=quantile(boot,.975),resamples=27))
    comparison=[]
    for row in control:
        for metric in NAMES:
            reference=row[metric+'NativeReference'];mean=row[metric+'SimulatedMean']
            error=None if reference is None or mean is None else (
                None if reference==0 and metric in ('rps','latencyP99Ms') else
                abs(mean-reference)/reference if metric in ('rps','latencyP99Ms') else abs(mean-reference))
            comparison.append(dict(broker=row['broker'],payloadBytes=row['payloadBytes'],qos=row['qos'],publishers=row['publishers'],
                                   metric=metric,nativeReference=reference,modelMean=mean,
                                   modelSeedMin=row[metric+'SimulatedMin'],modelSeedMax=row[metric+'SimulatedMax'],
                                   meanError=error,maxSeedError=row[metric+'MaxError'],
                                   applicableSeeds=row[metric+'ApplicableSeeds']))
    boundary=[]
    for broker in models:
        rows=[r for r in accuracy['rawWindows'] if r['broker']==broker and r['stage']=='validation']
        chosen=max(rows,key=lambda r:r['fractionPossiblyAfter'])
        boundary.append(dict(broker=broker,runs=len(rows),uncertainReceipts=sum(r['boundaryUncertain'] for r in rows),
                             totalReceipts=sum(r['uniqueDeliveries'] for r in rows),
                             example=chosen['key'],exampleReceipts=chosen['uniqueDeliveries'],
                             exampleDefinitelyAfter=chosen['definitelyAfterMeasurement'],exampleUncertain=chosen['boundaryUncertain'],
                             exampleLateLower=chosen['fractionDefinitelyAfter'],exampleLateUpper=chosen['fractionPossiblyAfter']))
    versions={broker:[json.loads(v) for v in sorted({json.dumps(r['versions'],sort_keys=True) for r in native['runs'] if r['broker']==broker})] for broker in models}
    duplicates={broker:sum(r['observation']['measurement']['duplicateDeliveries'] for r in native['runs'] if r['broker']==broker and r['stage']=='validation') for broker in models}
    output.mkdir(parents=True)
    for name,rows in [('conditions',conditions),('calibration-control',counts),('thresholds',thresholds),
                      ('error-distributions',distributions),('native-loss-resampling',native_bounds),('boundaries',boundary)]:
        (output/(name+'.csv')).write_bytes(encode_csv(rows))
    (output/'native-vs-model.csv').write_bytes(encode_csv(comparison))
    detail='# Все 96 исторических контрольных условий\n\nЧисла ошибок — максимум по трём seed; неприменимые показатели обозначены н/п. Это описательная карта, не независимая валидация.\n\n| Брокер | Байт | QoS | Publishers | Прошли seed /3 | RPS, отн. | p99, отн. | Failure, абс. | Loss, абс. |\n| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n'
    for r in control:
        errors=['н/п' if r[n+'MaxError'] is None else f"{r[n+'MaxError']:.6f}" for n in NAMES]
        detail+=f"| {r['broker']} | {r['payloadBytes']} | {r['qos']} | {r['publishers']} | {r['passedSeeds']}/3 | "+' | '.join(errors)+' |\n'
    (output/'Conditions.md').write_bytes(detail.encode('utf-8'))
    result=dict(conditionCounts=counts,thresholds=thresholds,errorDistributions=distributions,boundaries=boundary,
                jointP99PassLossFailSeedRuns=sum(r['p99PassedButLossFailedSeeds'] for r in control),
                duplicateReceiptsInNativeControl=duplicates,versions=versions,
                scope='Retrospective descriptive grid; seed runs clustered within condition; no prospective validation')
    (output/'analysis.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams.update({'svg.hashsalt':'article1-revision','svg.fonttype':'none'})
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,4))
    for name,label in [('rps','RPS'),('latencyP99Ms','Publication p99')]:
        vals=sorted(r[name+'MaxError'] for r in control if r[name+'MaxError'] is not None)
        axes[0].step(vals,[(i+1)/len(vals) for i in range(len(vals))],where='post',label=label)
    axes[0].set(xlabel='Maximum relative error across three seeds',ylabel='Fraction of conditions',xscale='symlog')
    axes[0].legend();axes[0].grid(alpha=.3)
    axes[1].plot([r['multiplier'] for r in thresholds],[r['allSeedsConditionsPassed']/96 for r in thresholds],marker='o')
    axes[1].set(xlabel='Common multiplier of fixed tolerances',ylabel='Conditions passing all seeds',ylim=(0,1))
    axes[1].axvline(1,color='gray',linestyle='--');axes[1].grid(alpha=.3)
    fig.tight_layout();fig.savefig(output/'error-and-threshold.svg',metadata={'Date':None});plt.close(fig)
    fig,ax=plt.subplots(figsize=(11,3.3));ax.set(xlim=(0,11),ylim=(0,3));ax.axis('off')
    from matplotlib.patches import FancyBboxPatch
    boxes=[(.2,1.6,'Real broker'),(2.5,1.6,'MqttBenchmark\nclosed-loop'),(4.8,1.6,'Profile\ncalibration'),(7.1,1.6,'MqttY\nconditional replay'),(7.1,.2,'Independent\nraw/metric checks')]
    for x,y,label in boxes:
        ax.add_patch(FancyBboxPatch((x,y),2,1,boxstyle='round,pad=0.05',facecolor='#edf3f8',edgecolor='#34516d'))
        ax.text(x+1,y+.5,label,ha='center',va='center',fontsize=10)
    for start,end in [((2.2,2.1),(2.5,2.1)),((4.5,2.1),(4.8,2.1)),((6.8,2.1),(7.1,2.1)),((8.1,1.6),(8.1,1.2)),((3.5,1.6),(7.1,.7))]:
        ax.annotate('',xy=end,xytext=start,arrowprops={'arrowstyle':'->','color':'#34516d'})
    fig.tight_layout();fig.savefig(output/'workflow.svg',metadata={'Date':None});plt.close(fig)
    manifest={'sources':{p:hashlib.sha256(v).hexdigest() for p,v in raw.items()},
              'generatorSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir())}}
    (output/'manifest.json').write_bytes((json.dumps(manifest,indent=2)+'\n').encode())
    print(json.dumps({k:result[k] for k in ['conditionCounts','thresholds','jointP99PassLossFailSeedRuns','duplicateReceiptsInNativeControl']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    build(parser.parse_args().output)
