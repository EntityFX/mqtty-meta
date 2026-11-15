"""Independent arithmetic audit and Russian assessment of saved campaign evidence.

Uses standard-library scalar arithmetic, not the report publisher's scoring code.
No new load is generated. External ZIP files are read only with --archive-root.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
import zipfile

BROKERS=('Aedes','Mosquitto','ActiveMQ','EMQX')
KEYS=list(itertools.product((16,256),(0,1,2),(1,16,64,128)))
CRITERIA=('rps','latencyP99Ms','publishFailureRate','deliveryLossRate')


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def close(actual,expected,label):
    if not math.isclose(actual,expected,rel_tol=1e-9,abs_tol=1e-10):
        raise ValueError(f'{label}: {actual} != {expected}')


def key(p):
    return p.get('messageBytes',p.get('bytes')),p['qos'],p['publishers']


def audit_native(runs,observations):
    expected={(b,s,k,repeat) for b in BROKERS for s in ('calibration','validation') for k in KEYS for repeat in (1,2,3)}
    identities=[(r['broker'],r['stage'],key(r['key']),r['key']['repeat']) for r in runs]
    if len(identities)!=len(expected) or set(identities)!=expected:
        raise ValueError('Wrong native matrix or duplicate repeat')
    metrics={}
    for r in runs:
        m=r['observation']['measurement']; label=r['key']['key']
        a,c,f=m['attemptedPublishes'],m['completedPublishes'],m['failedPublishes']
        if a!=c+f or a<=0 or not 0<=m['completedIdsDelivered']<=c or not 0<=m['uniqueDeliveries']<=a:
            raise ValueError('Invalid native counter identities: '+label)
        if m['uniqueDeliveries']!=m['completedIdsDelivered']+m['deliveredAfterFailedPublish']:
            raise ValueError('Delivered ID partitions differ: '+label)
        if sum(m['errorReasons'].values())!=f:
            raise ValueError('Failure reasons do not match total')
        duration=m['actualMeasurementSeconds']
        if duration<=0: raise ValueError('Invalid duration')
        derived=dict(attemptedRps=a/duration,completedRps=c/duration,
                     publishFailureRate=f/a,deliveryLossRate=0 if c==0 else 1-m['completedIdsDelivered']/c)
        for name,value in derived.items(): close(m[name],value,label+'/'+name)
        latency=m['publishLatencyMs']
        if r['key']['qos']==0:
            if latency is not None or m['latencyStatus']!='notApplicable': raise ValueError('QoS0 ACK latency')
        elif not 0<=latency['minMs']<=latency['p50Ms']<=latency['p95Ms']<=latency['p99Ms']<=latency['maxMs']:
            raise ValueError('Invalid latency quantiles')
        metrics[r['broker'],r['stage'],key(r['key']),r['key']['repeat']]=derived
    for b in BROKERS:
        for stage in ('calibration','validation'):
            obs=observations[b,stage]
            if obs['runCount']!=72 or len(obs['points'])!=24 or {key(p) for p in obs['points']}!=set(KEYS):
                raise ValueError('Invalid native aggregate')
            for p in obs['points']:
                for name in ('attemptedRps','completedRps','publishFailureRate','deliveryLossRate'):
                    values=[metrics[b,stage,key(p),repeat][name] for repeat in (1,2,3)]
                    close(p[name]['mean'],statistics.mean(values),'Aggregate mean')
                    close(p[name]['standardDeviation'],statistics.stdev(values),'Aggregate SD')
                    close(p[name]['ci95HalfWidth'],4.30265273*statistics.stdev(values)/math.sqrt(3),'Student-t CI')
                selected=[r for r in runs if r['broker']==b and r['stage']==stage and key(r['key'])==key(p)]
                close(p['rttBaselineMs'],statistics.mean(r['observation']['rttBaselineMs'] for r in selected),'RTT mean')
                if p['qos']>0:
                    for quantile in ('p50Ms','p95Ms','p99Ms'):
                        close(p['observedLatencyQuantiles'][quantile],statistics.mean(r['observation']['measurement']['publishLatencyMs'][quantile] for r in selected),'Mean repeat quantile')
    return dict(runs=len(runs),runRateChecks=4*len(runs),aggregateMetricChecks=4*4*2*24,
                qos12QuantileMeanChecks=3*4*2*16,primaryRawQuantilesRecomputed=False,
                actualMeasurementSeconds=dict(min=min(r['observation']['measurement']['actualMeasurementSeconds'] for r in runs),
                                             max=max(r['observation']['measurement']['actualMeasurementSeconds'] for r in runs)))


def audit_members(root,runs):
    """Check saved native members and independently recompute interior CPU/RSS."""
    with zipfile.ZipFile(root/'native-members.zip') as archive:
        for r in runs:
            prefix=f'{r["broker"]}/{r["stage"]}/{r["key"]["key"]}/'
            seals=json.loads(archive.read(prefix+'sha256.json'))
            payload={}
            for name,digest in r['memberSha256'].items():
                data=archive.read(prefix+name)
                if hashlib.sha256(data).hexdigest()!=digest or digest!=seals[name].lower():
                    raise ValueError('Native member seal mismatch')
                payload[name]=data
            if json.loads(payload['result.json'])['observation']!=r['observation']:
                raise ValueError('Selected observation differs from sealed result')
            if not json.loads(payload['load-generator-summary.json'])['assessment']['success']:
                raise ValueError('Guard failed')
            samples=[json.loads(line) for line in payload['stand/telemetry.ndjson'].splitlines()]
            parse=lambda value:datetime.fromisoformat(value.replace('Z','+00:00'))
            started=parse(r['observation']['measurementStartedUtc']); ended=parse(r['observation']['measurementEndedUtc'])
            inside=[]
            for before,after in zip(samples,samples[1:]):
                if parse(before['timestamp'])>=started and parse(after['timestamp'])+timedelta(seconds=1)<=ended and after.get('processCpuPercent') is not None:
                    if before['unitId']!=after['unitId']: raise ValueError('Telemetry process changed')
                    inside.append((after['monotonicSeconds']-before['monotonicSeconds'],after))
            seconds=sum(d for d,_ in inside)
            if seconds<15: raise ValueError('Insufficient measurement telemetry')
            close(r['resources']['cpuPercent'],sum(d*x['processCpuPercent'] for d,x in inside)/seconds,'Interior CPU')
            close(r['resources']['rssMaxBytes'],max(x['processRssBytes'] for _,x in inside),'Interior RSS')
    return dict(nativeMemberSets=len(runs),interiorCpuRssChecks=2*len(runs),fullOriginalRawAudit=False)


def evaluate_est(values,qos):
    """Direct scalar implementation of equal-weight EST, with explicit QoS0 adaptation."""
    rmax=max(v[0] for v in values)
    lmax=None if qos==0 else max(v[2] for v in values)
    result=[]
    for r,c,l,success in values:
        if not 0<=c<=1+1e-12 or not 0<=success<=1: raise ValueError('Invalid EST fractions')
        terms=[r/rmax if rmax else 0,1-min(c,1),success]
        if qos>0:
            if l is None or l<0: raise ValueError('Missing applicable latency')
            terms.append(1-l/lmax if lmax else 1)
        result.append(sum(terms)/len(terms))
    return result


def audit_replays(root,rows):
    with zipfile.ZipFile(root/'replay-evidence.zip') as repeat_archive:
        for r in rows:
            name=f'{r["broker"]}-{r["stage"]}/{Path(r["repeatDirectory"]).name}/'
            original_name=f'{r["stage"]}/{r["key"]}/'
            with zipfile.ZipFile(root/f'{r["broker"]}-model-evidence.zip') as original:
                if hashlib.sha256(original.read(original_name+'config.json')).hexdigest()!=r['configSha256']:
                    raise ValueError('Replay input config mismatch')
                for file in ('summary.json','latency-histogram.json'):
                    if json.loads(original.read(original_name+file))!=json.loads(repeat_archive.read(name+file)):
                        raise ValueError('Saved replay outputs differ')
                before=json.loads(original.read(original_name+'counters.json'))
                after=json.loads(repeat_archive.read(name+'counters.json'))
                if any(before[k]!=after[k] for k in ('brokers','receivers','receiverScope')):
                    raise ValueError('Saved replay scientific counters differ')
                before=json.loads(original.read(original_name+'experiment-manifest.json'))
                after=json.loads(repeat_archive.read(name+'experiment-manifest.json'))
                if before['phases']!=after['phases'] or before['provenance']['brokerProfiles']!=after['provenance']['brokerProfiles']:
                    raise ValueError('Saved replay phases/profiles differ')
    return len(rows)


def assess(out,archive_root):
    data=out/'data'
    # The frozen input manifest excludes newly generated assessment outputs.
    sources=read(data/'final-evidence-sha256.json')['files']
    for name,digest in sources.items():
        if sha(data/name)!=digest: raise ValueError('Input hash mismatch: '+name)
    runs=read(data/'native-evidence.json')['runs']
    observations={(b,s):read(data/b/s/'observations.json') for b in BROKERS for s in ('calibration','validation')}
    points={(b,s,key(p)):p for (b,s),o in observations.items() for p in o['points']}
    result=dict(method='Separate standard-library arithmetic audit; no publisher scoring functions imported',verifierSha256=sha(Path(__file__)),
                native=audit_native(runs,observations),externalArchivesChecked=False)
    if archive_root is not None:
        for name,entry in read(data/'external-archives.json')['archives'].items():
            if sha(archive_root/name)!=entry['sha256']: raise ValueError('External archive hash mismatch')
        result['externalArchivesChecked']=True
        result['nativeMembers']=audit_members(archive_root,runs)
    models=read(data/'model-evidence.json'); counts={}; recalculated_criteria=0
    for b in BROKERS:
        counts[b]={}
        for stage in ('fidelity','holdout'):
            selected=[r for r in models[b] if r['stage']==stage]
            expected={f'm{a}.q{q}.p{n}.s{s}' for a,q,n in KEYS for s in (42,43,44)}
            if len(selected)!=72 or {r['key'] for r in selected}!=expected: raise ValueError('Model coverage differs')
            summary=dict(runs=72,passedAll=0,passedAllPoints=0,criteria={name:dict(passed=0,failed=0,notApplicable=0) for name in CRITERIA})
            for r in selected:
                p=points[b,'calibration' if stage=='fidelity' else 'validation',key(r)]
                targets={'rps':p['completedRps']['mean'],'publishFailureRate':p['publishFailureRate']['mean'],
                         'deliveryLossRate':p['deliveryLossRate']['mean'],'latencyP99Ms':(p['observedLatencyQuantiles'] or {}).get('p99Ms')}
                cs=r['summary']['acceptanceCriteria']
                if len(cs)!=4 or {c['name'] for c in cs}!=set(CRITERIA): raise ValueError('Missing criteria')
                for c in cs:
                    name=c['name']; target=targets[name]
                    if target is None:
                        if c['status']!='notApplicable': raise ValueError('Inapplicable latency counted')
                    else:
                        actual=r['summary']['simulated']['completedRps' if name=='rps' else name]
                        limit={'rps':.15,'latencyP99Ms':.2,'publishFailureRate':.05,'deliveryLossRate':.05}[name]
                        delta=abs(actual-target)/(abs(target) if name in ('rps','latencyP99Ms') else 1)
                        close(c['reference'],target,'Model reference');close(c['simulated'],actual,'Model value');close(c['error'],delta,'Model deviation')
                        if c['limit']!=limit or c['status']!=('passed' if delta<=limit else 'failed'): raise ValueError('Wrong acceptance decision')
                        recalculated_criteria+=1
                    summary['criteria'][name][c['status']]+=1
                summary['passedAll']+=all(c['status']!='failed' for c in cs)
            summary['passedAllPoints']=sum(all(all(c['status']!='failed' for c in r['summary']['acceptanceCriteria']) for r in selected if key(r)==k) for k in KEYS)
            counts[b][stage]=summary
    result['modelAccuracy']=counts;result['recomputedApplicableCriteria']=recalculated_criteria
    relay_count=0
    for b in (*BROKERS,'MqttRelay-Mixed'):
        selected=[r for r in models[b] if r['stage']=='relay']
        if len(selected)!=72 or {r['key'] for r in selected}!=expected: raise ValueError('Wrong Relay coverage')
        for r in selected:
            if r['summary']['status']!='completed' or any(c['status']!='notApplicable' for c in r['summary']['acceptanceCriteria']):
                raise ValueError('Wrong Relay completion/acceptance interpretation')
            if len(r['receivers'])!=4 or any(n<0 for n in r['receivers'].values()): raise ValueError('Invalid model receivers')
        relay_count+=len(selected)
    result['relay']=dict(completed=relay_count,nativeNetworkAccuracyValidated=False)
    result['nativeBetweenSeries']={}
    for b in BROKERS:
        drift=Counter()
        for k in KEYS:
            a,z=points[b,'calibration',k],points[b,'validation',k]
            for metric,limit in (('completedRps',.15),('publishFailureRate',.05),('deliveryLossRate',.05)):
                delta=abs(a[metric]['mean']-z[metric]['mean'])/(abs(a[metric]['mean']) if metric=='completedRps' else 1)
                drift[metric]+=delta>limit
            if k[1]>0:
                x,y=a['observedLatencyQuantiles']['p99Ms'],z['observedLatencyQuantiles']['p99Ms']
                drift['p99Ms']+=abs(x-y)/x>.2
        result['nativeBetweenSeries'][b]=dict(drift)
    integral=read(data/'integral-assessment.json'); scores={b:[] for b in BROKERS}
    for ki,k in enumerate(KEYS):
        values=[]
        for b in BROKERS:
            p=points[b,'validation',k]
            selected=[r for r in runs if r['broker']==b and r['stage']=='validation' and key(r['key'])==k]
            values.append([p['completedRps']['mean'],statistics.mean(r['resources']['cpuPercent']/100 for r in selected),
                (p['observedLatencyQuantiles'] or {}).get('p99Ms'),statistics.mean(r['observation']['measurement']['uniqueDeliveries']/r['observation']['measurement']['attemptedPublishes'] for r in selected)])
        for bi,value in enumerate(evaluate_est(values,k[1])):
            close(value,integral['scoreByPoint'][bi][ki],'Independent EST point')
            scores[BROKERS[bi]].append(value)
    result['est']={}
    for b,values in scores.items():
        mean=statistics.mean(values);close(mean,integral['means'][b],'Independent overall EST*')
        result['est'][b]=dict(strictQos12=statistics.mean(v for v,k in zip(values,KEYS) if k[1]>0),
            adaptedAllQos=mean,adaptedBootstrap95=integral['bootstrap'][b])
    replay=read(data/'replay-results.json')
    selected_replays={(b,'holdout') for b in BROKERS}|{('Aedes','relay'),('MqttRelay-Mixed','relay')}
    if len(replay['rows'])!=6 or {(r['broker'],r['stage']) for r in replay['rows']}!=selected_replays or any(r['key']!='m16.q1.p1.s42' for r in replay['rows']):
        raise ValueError('Selected replay coverage differs')
    for r in replay['rows']:
        if not r['profilesUnchanged'] or not all(r['equal'][n] for n in ('summary.json','scientificCounters','latency-histogram.json','phases','profiles')):
            raise ValueError('Selected replay mismatch')
    result['replays']=dict(count=len(replay['rows']),scientificOutputsEqual=True,scope='Selected same-seed same-runtime points; not independent-machine replication')
    if archive_root is not None:
        result['replays']['savedOriginalAndRepeatFilesCompared']=audit_replays(archive_root,replay['rows'])
    diagnosis=read(data/'ActiveMQ-diagnosis-20261019/diagnosis.json')
    audited=[r for r in diagnosis['native'] if r.get('fullArchiveVerified')]
    result['priorRawAudit']=dict(broker='ActiveMQ',archives=len(audited),primaryQuantilesRecomputed=False)
    result['sourceSha256']={str(p.relative_to(out)).replace('\\','/'):sha(p) for p in
        [data/'native-evidence.json',data/'model-evidence.json',data/'integral-assessment.json',data/'replay-results.json',
         data/'ActiveMQ-diagnosis-20261019/diagnosis.json',data/'publication-validation.json']+
        [data/b/s/'observations.json' for b in BROKERS for s in ('calibration','validation')]}
    return result


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(str,row))+' |' for row in rows])


def document(out,result):
    accuracy=result['modelAccuracy']; est=result['est']
    criterion_rows=[]
    for b in BROKERS:
        held=accuracy[b]['holdout']
        criterion_rows.append([b,*[f'{held["criteria"][c]["passed"]}/{held["criteria"][c]["passed"]+held["criteria"][c]["failed"]}' for c in CRITERIA],
                               f'{held["passedAll"]}/72',f'{held["passedAllPoints"]}/24'])
    text=['# Достоверность, проверяемость и воспроизводимость результатов',
        'Оценка от 19 октября 2026 года. Базовая публикация — Git-коммит `0b0c6dd`; серия `overnight-20261018-instrumentation-v2`. '
        'Проверяются четыре разных утверждения: '
        'правильность вычисления метрик, соответствие двойников натурным измерениям, повторяемость моделей и обоснованность рейтинга EST. '
        '**Арифметика сохранённых данных повторно проверена. Точность двойников подтверждена лишь на части сетки; натурная точность Relay не установлена. '
        'EST корректно вычисляется как выбранный критерий, но не является мерой достоверности измерений.**',
        '## 1. Оценка доказательств',
        table(['Утверждение','Результат','Основание и граница'],[
            ['RPS, failure/loss правильно получены из счётчиков','Подтверждено для 576 сохранённых результатов','Новый отдельный пересчёт формул и средних; это не проверка сетевого пакета для каждого ID'],
            ['Средние, SD и CI95 для RPS и долей ошибок посчитаны правильно','Подтверждено','Три повтора; условия применимости статистической модели не следуют из правильности арифметики'],
            ['Первичные p50/p95/p99 всех сообщений верны','Полная проверка не выполнена','Проверены порядок перцентилей и среднее перцентилей повторов; raw-задержки всех сообщений заново не сортировались'],
            ['Двойники воспроизводят весь независимый контроль','Не подтверждено','Во всех четырёх двойниках есть нарушенные критерии'],
            ['Выбранные модели MqttY и Relay повторяются','Подтверждено для шести запусков','Те же seed, config и runtime; не вся сетка и не новая независимая машина'],
            ['Модель Relay соответствует настоящей распределённой сети','Не проверено','Нет натурного сетевого эталона и уникальной сквозной проверки ID'],
            ['EST с весами по 0,25 вычислен по формуле','Подтверждено для QoS 1/2','Для QoS 0 отсутствует L, поэтому общий EST* является отдельной адаптацией'],
            ['Высокий EST доказывает достоверность или надёжность','Такой вывод не следует','EST агрегирует выбранные свойства с заданными предпочтениями, а не измеряет истинность данных']]),
        '## 2. Верификация RPS и остальных метрик',
        'Новый проверяющий скрипт использует стандартную библиотеку Python и отдельную скалярную реализацию формулы. '
        'Он не вызывает функцию оценки из генератора основных отчётов. '
        '[Результаты пересчёта и SHA-256 входов](data/evidence-assessment.json) · [Код проверки](../tools/campaign/assess-evidence.py).',
        'Обозначения одного натурного запуска: A — попытки публикации, P — успешно завершённые вызовы, E — ошибки вызовов, '
        'D — уникальные получения, D_P — полученные ID среди успешно завершённых публикаций, T — фактическое время измерения. '
        'Буква C ниже в формуле EST обозначает загрузку CPU.',
        table(['Показатель','Независимо проверенная формула','Что именно измеряет'],[
            ['Attempted RPS','A / T','Частоту вызовов, включая быстрые локальные ошибки; не гарантированно число сообщений в сети'],
            ['Completed RPS','P / T','Завершения PublishAsync; для QoS 1/2 это публикация с подтверждением, а не получение подписчиком'],
            ['Publish failure','E / A','Долю неуспешных вызовов публикации'],
            ['Delivery loss','1 − D_P / P','Отсутствие получения к концу наблюдения среди завершённых публикаций'],
            ['Успешность доставки для EST','D / A','Получения относительно всех попыток; включает влияние неуспешных локальных вызовов'],
            ['p50/p95/p99','Среднее соответствующих перцентилей трёх повторов','Задержку завершения публикации QoS 1/2; не сквозную задержку доставки'],
            ['RTT','Среднее трёх отдельных измерений baseline','Опорное время обмена, не p99 рабочей нагрузки'],
            ['CPU','Среднее процесса по длительности внутренних интервалов нагрузки','Долю одного закреплённого логического CPU'],
            ['RSS','Максимум наблюдённых внутренних отсчётов','Выборочную, а не непрерывно измеренную пиковую память процесса']]),
        f'Повторно проверены {result["native"]["runRateChecks"]} значений RPS/failure/loss у 576 запусков, '
        f'{result["native"]["aggregateMetricChecks"]} групп «среднее / SD / CI» и '
        f'{result["native"]["qos12QuantileMeanChecks"]} средних перцентилей. '
        'Проверены A=P+E, разбиение полученных ID на исходы публикации, сумма причин ошибок и полное покрытие повторов.',
        f'При чтении внешних архивов: **{result.get("nativeMembers",{}).get("nativeMemberSets",0)}** комплектов файлов с внутренними контрольными суммами '
        f'и **{result.get("nativeMembers",{}).get("interiorCpuRssChecks",0)}** повторных вычислений CPU/RSS. '
        'Seals сверены для извлечённых результатов, телеметрии и guard. Это не новый пересчёт всех первичных журналов сообщений. '
        f'Прежний подробный аудит охватил {result["priorRawAudit"]["archives"]} raw-архива ActiveMQ и не пересчитывал p99. '
        '[Границы этого аудита](ActiveMQ-diagnosis.md).',
        '### Время и единицы',
        f'Фактическое натурное окно T находится в диапазоне **{result["native"]["actualMeasurementSeconds"]["min"]:.6f}–'
        f'{result["native"]["actualMeasurementSeconds"]["max"]:.6f} с**, хотя плановая нагрузка — 20 с. '
        'Последний начатый вызов может завершиться позже границы. Деление на 20 вместо сохранённого T изменило бы RPS. '
        'В симуляторе знаменатель фиксирован: 2 с; завершающиеся при drain операции учитываются по правилам модели. '
        'Знаменатели необходимо сохранять явно при сравнении.',
        'CPU-сэмплы имеют UTC с точностью до секунды, поэтому в расчёт включены только интервалы, гарантированно лежащие внутри нагрузки; '
        'вес — их монотонная длительность. Граничные интервалы исключены. Guard 99% относится к контролю нагруженности стенда '
        'и не гарантирует отсутствие влияния общего хоста. Привязка к логическому CPU не означает выделенное физическое ядро.',
        '### Статистическая неопределённость',
        'Для среднего трёх повторов: CI95 = mean ± 4,30265273 × s / √3, две степени свободы. '
        'Проверена арифметика этой формулы. Независимость повторов, стационарность стенда и подходящая форма распределения при n=3 не доказаны. '
        'CI не является интервалом задержек 95% сообщений. Среднее p99 повторов не равно p99 объединённых сообщений. '
        'Натурный и модельный способы построения распределений также различаются.',
        '## 3. Точность двойников брокеров',
        'Эталон — сохранённые натурные observations этого стенда, а не недоступная «абсолютно истинная производительность» брокера. '
        'Сначала профиль построен по 72 калибровочным запускам. Затем получены 72 новых успешных натурных измерения '
        'и 72 контрольные симуляции при неизменном профиле. Известные контрольные результаты нельзя использовать для настройки '
        'и повторно называть их слепым независимым контролем.',
        'Критерии не изменены: RPS — относительная ошибка ≤15%; p99 — ≤20%; failure и loss — абсолютная разница ≤0,05 '
        '(5 процентных пунктов). В таблице прошедшие проверки / применимые проверки; p99 имеет 48 применимых сценариев, QoS 0 исключён.',
        table(['Двойник','RPS','p99','Failure','Loss','Все критерии: запусков','Все три seed: точек'],criterion_rows),
        'Число прошедших независимых запусков: Aedes 58/72, Mosquitto 58/72, ActiveMQ 29/72, EMQX 58/72. '
        'Процент прохождения — описательное покрытие заданной сетки, а не вероятность будущей точности. '
        'Три seed коррелированы общим профилем и натурным эталоном; применять к ним обычный биномиальный интервал без обоснования нельзя.',
        f'Отдельно пересчитаны **{result["recomputedApplicableCriteria"]}** применимых решений Fidelity/holdout. '
        'Полные карты точек: [Aedes](Aedes.md), [Mosquitto](Mosquitto.md), [ActiveMQ](ActiveMQ.md), [EMQX](EMQX.md). '
        'Имеются подтверждённые области соответствия, но утверждение «все четыре двойника точны во всём диапазоне» в рамках принятых критериев не подтверждено.',
        '### Изменчивость натурного эталона',
        'Ниже число соответствующих точек, в которых среднее новой натурной серии отличается от среднего калибровочной '
        'больше тех же численных порогов. Это диагностическое сравнение двух настоящих серий, а не статистический тест значимости.',
        table(['Брокер','RPS >15% /24','p99 >20% /16','Failure >0,05 /24','Loss >0,05 /24'],
            [[b,*[result['nativeBetweenSeries'][b].get(m,0) for m in ('completedRps','p99Ms','publishFailureRate','deliveryLossRate')]] for b in BROKERS]),
        'Часть расхождений двойника отражает нестабильность самого опыта. Для ActiveMQ дополнительно установлены повторные локальные '
        'ошибки после отключения, увеличивающие attempted без пропорционального сетевого трафика. Эти эффекты ограничивают выводы '
        'о внутренней реализации брокера и о переносимости профиля на иное оборудование.',
        '## 4. Повторяемость MqttY и модели MqttRelay',
        'Для однозначности: **повторяемость** — тот же seed, config, профиль и версия на том же стенде; '
        '**воспроизводимость независимым исполнителем** — повторение по сохранённым материалам в отдельной установке. '
        'Вторая проверка для всей программы этой кампанией не выполнена.',
        table(['Объект','Выполненная проверка','Допустимый вывод'],[
            ['BrokerFidelity MqttY','По одной контрольной точке каждого брокера, повтор с seed=42','4/4 совпадения summary, научных counters, histogram, фаз и профилей'],
            ['Однородная MqttRelay','Одна точка Aedes, тот же seed/config','1/1 совпадение тех же научных итогов'],
            ['Смешанная MqttRelay','Одна точка четырёх разных профилей','1/1 совпадение тех же научных итогов'],
            ['Вся сетка и другие машины','Полное независимое повторение отсутствует','Обобщение детерминизма на все входы не доказано']]),
        'Во всех шести повторах использованы 16 байт, QoS 1, один отправитель на брокер, seed 42. '
        'Сравнение одинаковых seed проверяет повторяемость; три различных seed сами по себе её не проверяют. '
        'Полный counters.json отличается из-за строки с реальным временем вычисления. Научные broker/receiver-счётчики совпали. '
        '[Протокол](data/replay-results.json) · [Точный runtime и процедура](../tools/campaign/replay-final-models.py).',
        f'При этой оценке повторно сравнены сохранённые оригиналы и результаты **{result["replays"].get("savedOriginalAndRepeatFilesCompared",0)}** '
        'повторов из внешних архивов: config SHA-256, summary, histogram, научные counters, фазы и profile provenance. '
        'Новая нагрузка не генерировалась. Зафиксированный MqttY CLI имеет SHA-256 '
        '`60c5a79fca5291b061af345f3fb98e758c62ae551763693d5f39f74d40e9cebd`; '
        'Git-версия исходников сама по себе не заменяет идентификацию запущенных DLL.',
        '360 сетевых запусков демонстрируют выполнение схемы Relay с четырьмя двойниками, подключение, пересылку и получения в модельных receivers. '
        'Их счётчики охватывают drain и могут включать сообщения прогрева в пути. Брокерный RPS включает внутренние пересылки. '
        'Из них нельзя вывести долю уникальной сквозной доставки настоящей сети. Натурного Relay-эталона нет; '
        'все критерии его натурной точности — notApplicable. Подтверждены работа вычислительной модели и выбранные повторения, '
        'а не её полная физическая адекватность.',
        '## 5. Проверка EST при K_R=K_C=K_L=K_F=0,25',
        '`EST = 0,25 × R/max(R_B) + 0,25 × (1−C) + 0,25 × (1−L/max(L_B)) + 0,25 × (1−F)`',
        'Для одной точки R — средний completedRps; C — средняя доля CPU процесса; L — среднее p99; '
        '1−F — среднее D/A. Максимумы берутся по четырём брокерам в той же комбинации байт/QoS/отправителей. '
        'Члены рассчитываются после усреднения трёх повторов, затем оценки усредняются по точкам. '
        'Нормировка, набор брокеров и порядок усреднения являются частью метода.',
        '**Строго исходная четырёхкомпонентная формула применима к 16 точкам QoS 1/2.** '
        'Для восьми точек QoS 0 нет L подтверждения; подстановка нуля необоснованно улучшила бы оценку. '
        'В прежнем общем EST* член L исключён и три оставшихся веса равны 1/3. Это другая, явно обозначенная оценка.',
        table(['Брокер','EST: QoS 1/2, четыре веса 0,25','EST*: все QoS, адаптация','Bootstrap 95% для EST*'],
            [[b,f'{est[b]["strictQos12"]:.4f}',f'{est[b]["adaptedAllQos"]:.4f}',
              f'{est[b]["adaptedBootstrap95"]["lower"]:.4f}–{est[b]["adaptedBootstrap95"]["upper"]:.4f}'] for b in BROKERS]),
        'Новый независимый скалярный пересчёт совпал со всеми **96** опубликованными оценками «брокер/точка» и четырьмя общими средними. '
        'Лидер обоих вариантов — Mosquitto. При повышенном весе доставки 0,55 лидирует Aedes, поэтому выбор зависит от цели. '
        'Подробная чувствительность и исходные компоненты: [сравнение](Broker-comparison.md), [CSV](Broker-EST.csv).',
        '### Что доказывает формула',
        'Если R и L неотрицательны, знаменатели положительны, C и F лежат в [0;1], каждый нормированный член лежит в [0;1]. '
        'При неотрицательных весах с суммой 1 их взвешенная сумма также лежит в [0;1]. '
        'Это математическое свойство шкалы. Оно не доказывает правильность входных измерений, достаточность повторов или объективность весов. '
        'Совместная независимость R/C/L/F для вычисления суммы не требуется; считать эти свойства статистически независимыми оснований нет.',
        'Максимум L делает вклад задержки худшего брокера нулевым; наличие выброса меняет относительные оценки остальных. '
        'Изменение набора сравниваемых брокеров меняет максимумы и может изменить рейтинг. '
        'Низкая загрузка CPU вследствие простоя сама по себе не равна эффективности. '
        'Время и стоимость плохой доставки могут быть недооценены суммой: высокая скорость способна компенсировать loss. '
        'Для выбора под SLA сначала необходим отдельный допустимый предел недоставки, а затем рейтинг допустимых вариантов.',
        '### Доверие к рейтингу',
        'Опубликованный bootstrap использует 2000 выборок с seed 20261019: целые повторы выбираются с возвращением внутри каждой '
        'фиксированной пары «брокер/точка», связанные метрики сохраняются вместе и максимумы пересчитываются. '
        'Он отражает только разброс трёх имеющихся повторов при данной сетке. Междневной дрейф, ошибки генератора, '
        'смена hardware и систематические ошибки в него не включены. '
        'Частоту первого места в bootstrap нельзя объявлять вероятностью того, что брокер лучший вообще. '
        'Для строгой четырёхкомпонентной оценки отдельный bootstrap-интервал здесь не рассчитывался; интервалы в таблице относятся только к EST*.',
        'EST=0,562 не означает ни 56,2% достоверности, ни 56,2% успешных доставок. Это положение в выбранной системе нормирования. '
        'Условный рейтинг арифметически проверяем; универсальное превосходство Mosquitto данными этой серии не доказано.',
        '## 6. Как проверить другим сотрудником',
        'Для проверки расчёта по опубликованным JSON достаточно Python 3.11+ и репозитория с подмодулями:',
        '```bash\npython tools/campaign/assess-evidence.py --reports reports\n```',
        'Для проверки извлечённых sealed members, CPU/RSS и SHA-256 внешних архивов добавьте их каталог:',
        '```bash\npython tools/campaign/assess-evidence.py --reports reports --archive-root <каталог-архивов>\n```',
        '[Реестр архивов на диске результатов](data/external-archives.json). ZIP намеренно исключены из Git. '
        'Режим без --archive-root подтверждает арифметику JSON, а не повторное чтение первичных архивов. '
        'Полный пересчёт ID всех сообщений требует оригинальных raw ZIP из [реестра кампании](data/archive-registry.json), '
        'а не только компактных native-members.zip.',
        'Для повторения модели используйте сохранённый config из внешнего модельного архива, тот же runtime, SHA-256 профиля и seed. '
        'Меняются только пути к источникам. Сравнивайте summary, histogram, научные counters и фазы, исключая служебные runId и время вычисления. '
        'Для выбранной шестёрки готов [скрипт](../tools/campaign/replay-final-models.py); для новой установки — '
        '[руководство полной серии](../docs/mqtt-benchmark/runbook.md). Для независимой проверки после исправления модели нужен новый натурный контроль.',
        '## 7. Что необходимо для более сильного заключения',
        '1. Пересчитать первичные ID и перцентили по всем raw-журналам; проверить MQTT-трафик отдельным приёмником или захватом пакетов в диагностических опытах.\n'
        '2. Исправить повторные вызовы отключённого клиента, разделить локальные ошибки и фактические сетевые публикации; собрать новую калибровку и новый контроль.\n'
        '3. Повторить граничные нагрузки с более длинным окном и другим шагом модели; заранее зафиксировать критерии и проверить новые данные без подбора профиля по контролю.\n'
        '4. Повторить выбранные и граничные точки независимым сотрудником на другой машине с фиксацией binary/config/profile SHA-256.\n'
        '5. Измерить настоящую Relay-сеть той же топологии с уникальными сквозными ID и сопоставить её с моделью.\n'
        '6. До нового опыта определить рабочую нагрузку, допустимую недоставку и веса EST; проверить устойчивость ранга на независимых днях, а не только в bootstrap текущих трёх повторов.',
        'До этих проверок обоснованное заключение таково: сохранённые расчёты проверяемы, выбранные модельные запуски повторяемы, '
        'точность двойников имеет измеренные ограничения, а EST даёт условный выбор по явно заданным предпочтениям.']
    return '\n\n'.join(text)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports',type=Path,required=True)
    parser.add_argument('--archive-root',type=Path)
    parser.add_argument('--html',action='store_true',help='Also render HTML; requires Markdown from report-requirements.txt')
    args=parser.parse_args(); result=assess(args.reports,args.archive_root)
    (args.reports/'data/evidence-assessment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    text=document(args.reports,result).replace('EST*','EST\\*')
    (args.reports/'Evidence-assessment.md').write_text(text,encoding='utf-8')
    if args.html:
        import markdown
        body=markdown.markdown(text,extensions=['tables','fenced_code'])
        (args.reports/'Evidence-assessment.html').write_text(
            '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Достоверность, проверяемость и воспроизводимость результатов</title>'
            '<style>body{font:16px/1.6 system-ui;max-width:1200px;margin:2em auto;padding:0 1em}'
            'table{border-collapse:collapse;display:block;overflow:auto}td,th{border:1px solid #bbb;padding:.4em}'
            'th{background:#e8eef4}code{overflow-wrap:anywhere}a{color:#17548a}</style><body>'+body+'</body></html>',encoding='utf-8')
    print(json.dumps(dict(native=result['native'],est=result['est'],externalArchivesChecked=result['externalArchivesChecked']),ensure_ascii=False))
    return args.reports


if __name__=='__main__': report_root = main()

# Canonical meta-repository documentation layout.
if __name__ == '__main__':
    import sys as _layout_sys
    _layout_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "documentation"))
    from layout import organize_reports as _organize_reports
    _organize_reports(report_root)
