"""Independently check saved comparison arithmetic; do not change scientific criteria."""
import argparse
import csv
import hashlib
import json
from collections import Counter,defaultdict
from pathlib import Path

METRICS={'completedRpsInWindow':(.15,True),'p99PublishMs':(.20,True),
         'publishFailureRate':(.05,False),'deliveryLossRate':(.05,False)}

def analyse(summary_path,comparison_path,output):
    output=Path(output);output.mkdir(exist_ok=False,parents=True)
    raw=Path(summary_path).read_bytes();summary=json.loads(raw)
    groups=defaultdict(list)
    for row in summary['rows']:groups[(row['broker'],row['qos'],row['publishers'])].append(row)
    expected={};details=[];broker_counts=defaultdict(Counter)
    for (broker,qos,n),rows in sorted(groups.items()):
        native=[r['native'] for r in rows if r.get('native')]
        allpassed=0
        for row in rows:
            passed=[]
            if not row.get('model'):continue
            for metric,(limit,relative) in METRICS.items():
                values=[v[metric] for v in native if v[metric] is not None]
                reference=sum(values)/3 if len(values)==3 else None
                model=row['model'][metric]
                error=None if reference is None or model is None or (relative and reference==0) else abs(model-reference)/(abs(reference) if relative else 1)
                status='notEvaluated' if error is None else 'passed' if error<=limit else 'failed'
                expected[(broker,str(qos),str(n),str(row['seed']),metric)]=(reference,model,error,limit,status,len(values))
                broker_counts[broker][status]+=1;passed.append(status=='passed')
            allpassed+=all(passed)
        for metric in METRICS:
            vals=[v[metric] for v in native if v[metric] is not None]
            models=[r['model'][metric] for r in rows if r.get('model') and r['model'][metric] is not None]
            details.append(dict(broker=broker,qos=qos,publishers=n,metric=metric,nativeMean=sum(vals)/len(vals) if vals else None,
                nativeMin=min(vals) if vals else None,nativeMax=max(vals) if vals else None,nativeRepeats=len(vals),
                modelMean=sum(models)/len(models) if models else None,modelMin=min(models) if models else None,modelMax=max(models) if models else None,allMetricsPassedSeeds=allpassed))
    actual=list(csv.DictReader(Path(comparison_path).open(newline='',encoding='utf-8-sig')))
    assert len(actual)==len(expected)
    seen=set()
    for row in actual:
        key=tuple(row[k] for k in ['broker','qos','publishers','seed','metric']);assert key not in seen;seen.add(key)
        reference,model,error,limit,status,count=expected[key]
        assert row['status']==status and int(row['nativeRepeats'])==count
        for field,value in [('nativeMean',reference),('model',model),('error',error),('limit',limit)]:
            assert (row[field]=='' and value is None) or (value is not None and abs(float(row[field])-value)<=1e-10*max(1,abs(value)))
    with (output/'condition-metrics.csv').open('x',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(details[0]));writer.writeheader();writer.writerows(details)
    receipt=dict(arithmeticVerified=True,sourceSummarySha256=hashlib.sha256(raw).hexdigest(),comparisonRows=len(actual),
        fullPairs=sum(bool(r.get('native') and r.get('model')) for r in summary['rows']),
        nativeErrors=sum('nativeError' in r for r in summary['rows']),countsByBroker={k:dict(v) for k,v in broker_counts.items()},
        rawEventsRecountedByThisTool=False,criteriaChanged=False)
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8',newline='\n')
    lines=['# Проверка арифметики новой серии статьи 1','',
        'Проверены сохранённые сводки и каждая строка сравнения отдельным пересчётом. Первичные события этим локальным инструментом заново не прочитаны; проверка событий выполняется контроллером на Linux.','',
        '| Брокер | Показатели в допуске | Вне допуска | Не оценены |','| --- | ---: | ---: | ---: |']
    for b,c in sorted(broker_counts.items()):lines.append(f"| {b} | {c['passed']} | {c['failed']} | {c['notEvaluated']} |")
    lines+=['','| Брокер / QoS / отправители | RPS: натура → модель | p99, мс: натура → модель | Недоставка: натура → модель |','| --- | ---: | ---: | ---: |']
    for key in sorted(groups):
        values={d['metric']:d for d in details if (d['broker'],d['qos'],d['publishers'])==key}
        def pair(metric):
            d=values[metric]
            return 'нет натурного результата' if d['nativeMean'] is None else f"{d['nativeMean']:.5g} → {d['modelMean']:.5g}"
        lines.append(f"| {key[0]} / {key[1]} / {key[2]} | {pair('completedRpsInWindow')} | {pair('p99PublishMs')} | {pair('deliveryLossRate')} |")
    lines+=['','Это средние трёх натурных повторов и трёх модельных seed; ошибки оцениваются по каждому seed. Доли неуспешных публикаций и диапазоны сохранены в CSV.',
        '', 'Расхождения нельзя объяснять только запуском EMQX: у остальных трёх брокеров сравнения уже выполнены. Старые профили получены в других окнах нагрузки, а новая серия использует явные ID, запись прогрева и подробные события. Такая запись может влиять на скорость генератора; её вклад здесь не измерен. Профиль не перестраивался. Изменения условий и реализации являются проверяемыми объяснениями, но вклад каждой причины пока не установлен.',
        '', 'Отдельно необходимо проверить число pending, завершения во время ожидания, отключения, исходную интенсивность и соответствие условий профиля. Малое окно не доказывает устойчивость p99. Ни допуски, ни неудобные результаты не исключались.']
    (output/'Review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',required=True);parser.add_argument('--comparison',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();print(json.dumps(analyse(args.summary,args.comparison,args.output)))
