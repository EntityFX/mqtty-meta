"""Render final Russian reports and an explicit EST adaptation from frozen evidence."""
import argparse
from contextlib import nullcontext
import base64
import csv
import hashlib
import html
import itertools
import json
import math
from pathlib import Path
import re
import statistics as st
import zipfile
import numpy as np

BROKERS=('Aedes','Mosquitto','ActiveMQ','EMQX')
KEYS=list(itertools.product((16,256),(0,1,2),(1,16,64,128)))
STAGES={'calibration':'Натурная калибровка','fidelity':'Fidelity','validation':'Независимые замеры',
        'holdout':'Контрольные симуляции','relay':'MqttRelay'}
CRITERIA=('rps','latencyP99Ms','publishFailureRate','deliveryLossRate')


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p,text):
    p.parent.mkdir(parents=True,exist_ok=True)
    if p.suffix=='.md':
        text=text.replace('EST*','EST\\*')
        text=re.sub(r'\]\(data/[^)]+\.zip\)','](data/external-archives.json)',text)
    p.write_text(text,encoding='utf-8',newline='\n')


def dump(p,value):
    write(p,json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
        ['| '+' | '.join(str(c) for c in row)+' |' for row in rows])


def num(x):
    return 'н/п' if x is None else f'{x:.4g}'


def csvwrite(path,rows):
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def pointkey(p):
    return p.get('messageBytes',p.get('bytes')),p['qos'],p['publishers']


def verify(out,archive_root=None):
    for name,expected in read(out/'data/final-evidence-sha256.json')['files'].items():
        if sha(out/'data'/name)!=expected:
            raise ValueError('Evidence changed: '+name)
    state=read(out/'data/state.json')
    if state['status']!='completed':
        raise ValueError('Incomplete campaign')
    for b in BROKERS:
        if sha(out/'data'/b/'profile/broker-calibration.v3.json')!=state['profiles'][b]:
            raise ValueError('Frozen profile mismatch')
    if archive_root is not None:
        for name,entry in read(out/'data/external-archives.json')['archives'].items():
            if sha(archive_root/name)!=entry['sha256']:
                raise ValueError('External archive changed: '+name)


def aggregate(out):
    raw=read(out/'data/native-evidence.json')['runs']
    points={}
    for b in BROKERS:
        for stage in ('calibration','validation'):
            observations=read(out/'data'/b/stage/'observations.json')
            if observations['runCount']!=72 or len(observations['points'])!=24:
                raise ValueError('Incomplete matrix')
            for p in observations['points']:
                key=pointkey(p)
                selected=sorted([r for r in raw if r['broker']==b and r['stage']==stage and pointkey(r['key'])==key],
                                key=lambda r:r['key']['repeat'])
                if [r['key']['repeat'] for r in selected]!=[1,2,3]:
                    raise ValueError('Repeats differ')
                for metric in ('attemptedRps','completedRps','publishFailureRate','deliveryLossRate'):
                    xs=[r['observation']['measurement'][metric] for r in selected]
                    if not math.isclose(st.mean(xs),p[metric]['mean'],rel_tol=1e-10,abs_tol=1e-10):
                        raise ValueError('Native mean differs')
                    ci=4.30265273*st.stdev(xs)/math.sqrt(3)
                    if not math.isclose(ci,p[metric]['ci95HalfWidth'],rel_tol=1e-7,abs_tol=1e-9):
                        raise ValueError('Student t CI differs')
                q=dict(p,runs=selected)
                q['cpuPercent']=st.mean(r['resources']['cpuPercent'] for r in selected)
                q['captureCpuPercent']=st.mean(r['resources']['captureCpuMeanPercent'] for r in selected)
                q['rssMiB']=max(r['resources']['rssMaxBytes'] for r in selected)/2**20
                q['receivedRps']=st.mean(r['observation']['measurement']['uniqueDeliveries']/r['observation']['measurement']['actualMeasurementSeconds'] for r in selected)
                q['attemptDeliverySuccess']=st.mean(r['observation']['measurement']['uniqueDeliveries']/r['observation']['measurement']['attemptedPublishes'] for r in selected)
                points[b,stage,key]=q
    return points


def score(values,qos,weights=(.25,.25,.25,.25)):
    """Rows=brokers, columns=completed RPS, CPU fraction, p99, received/attempted.

    QoS0 removes the inapplicable latency term and renormalizes the other weights.
    Only floating point boundary roundoff is clipped; invalid measurements fail.
    """
    v=np.array(values,dtype=float,copy=True)
    if not np.isfinite(v[:,[0,1,3]]).all() or np.any(v[:,0]<0) or np.any((v[:,[1,3]] < -1e-12)|(v[:,[1,3]] > 1+1e-12)):
        raise ValueError('Invalid score inputs')
    v[:,[1,3]]=np.clip(v[:,[1,3]],0,1)
    rmax=float(max(v[:,0]))
    r=np.zeros(len(v)) if rmax==0 else v[:,0]/rmax
    if qos==0:
        components=np.column_stack([r,1-v[:,1],v[:,3]])
        w=np.asarray([weights[0],weights[1],weights[3]],dtype=float)
    else:
        if not np.isfinite(v[:,2]).all() or np.any(v[:,2]<0):
            raise ValueError('Missing QoS1/2 latency')
        lmax=float(max(v[:,2])); latency=np.ones(len(v)) if lmax==0 else 1-v[:,2]/lmax
        components=np.column_stack([r,1-v[:,1],latency,v[:,3]])
        w=np.asarray(weights,dtype=float)
    if np.any(w<0) or w.sum()<=0:
        raise ValueError('Invalid weights')
    return components@(w/w.sum())


def score_grid(points,stage='validation',weights=(.25,.25,.25,.25),cpu='cpuPercent',success='attemptDeliverySuccess'):
    columns=[]
    for key in KEYS:
        values=[]
        for b in BROKERS:
            p=points[b,stage,key]
            values.append([p['completedRps']['mean'],p[cpu]/100,
                           (p['observedLatencyQuantiles'] or {}).get('p99Ms',float('nan')),
                           p[success] if success!='completedDeliverySuccess' else 1-p['deliveryLossRate']['mean']])
        columns.append(score(values,key[1],weights))
    return np.array(columns).T


def verify_models(out,points,models,archive_root=None):
    expected_keys={f'm{size}.q{qos}.p{n}.s{seed}' for size,qos,n in KEYS for seed in (42,43,44)}
    frozen=read(out/'data/state.json')['profiles']
    for b,rows in models.items():
        stages=('relay',) if b=='MqttRelay-Mixed' else ('fidelity','holdout','relay')
        context=zipfile.ZipFile(archive_root/f'{b}-model-evidence.zip') if archive_root is not None else nullcontext(None)
        with context as archive:
            for stage in stages:
                runs=[r for r in rows if r['stage']==stage]
                if len(runs)!=72 or {r['key'] for r in runs}!=expected_keys:
                    raise ValueError('Wrong model matrix')
                for r in runs:
                    prefix=f'{stage}/{r["key"]}/'
                    expected=frozen if b=='MqttRelay-Mixed' else {b:frozen[b]}
                    summary=r['summary']
                    if summary['status']!='completed': raise ValueError('Incomplete model')
                    if sorted(r['profileHashes'])!=sorted(expected.values()): raise ValueError('Profile hashes differ')
                    if archive is not None:
                        for name,digest in r['filesSha256'].items():
                            if hashlib.sha256(archive.read(prefix+name)).hexdigest()!=digest:
                                raise ValueError('Model bundle hash mismatch')
                        if hashlib.sha256(archive.read(prefix+'config.json')).hexdigest()!=r['configSha256']:
                            raise ValueError('Config hash mismatch')
                        manifest=json.loads(archive.read(prefix+'experiment-manifest.json'))
                        present={Path(p['path']).parent.name:p['sha256'].lower() for p in manifest['provenance']['brokerProfiles']}
                        if present!=expected or len(present)!=len(manifest['provenance']['brokerProfiles']):
                            raise ValueError('Wrong or duplicate profile set')
                        if json.loads(archive.read(prefix+'summary.json'))!=summary: raise ValueError('Summary mismatch')
                        counters=json.loads(archive.read(prefix+'counters.json'))
                        if counters['receivers']!=r['receivers']: raise ValueError('Receivers mismatch')
                        for count in ('attempted','admitted','completed','expectedDeliveries','delivered','rateRejected','publishFailed','deliveryDropped'):
                            total=sum(q[count] for br in counters['brokers'].values() for q in br['byQos'].values())
                            if total!=summary['simulated'][count]: raise ValueError('Broker counts differ')
                    key=pointkey(r)
                    if stage=='relay':
                        if any(c['status']!='notApplicable' for c in summary['acceptanceCriteria']):
                            raise ValueError('Relay cannot claim native precision')
                        offered=4*(min(points[x,'calibration',key]['attemptedRps']['mean'] for x in BROKERS)
                                   if b=='MqttRelay-Mixed' else points[b,'calibration',key]['attemptedRps']['mean'])
                    else:
                        p=points[b,'calibration' if stage=='fidelity' else 'validation',key]
                        offered=p['attemptedRps']['mean']
                        reference={'rps':p['completedRps']['mean'],'publishFailureRate':p['publishFailureRate']['mean'],
                                   'deliveryLossRate':p['deliveryLossRate']['mean'],
                                   'latencyP99Ms':(p['observedLatencyQuantiles'] or {}).get('p99Ms')}
                        if {c['name'] for c in summary['acceptanceCriteria']}!=set(CRITERIA):
                            raise ValueError('Missing criteria')
                        for c in summary['acceptanceCriteria']:
                            target=reference[c['name']]
                            if target is None:
                                if c['status']!='notApplicable': raise ValueError('QoS0 latency')
                                continue
                            if not math.isclose(c['reference'],target,rel_tol=1e-10,abs_tol=1e-10):
                                raise ValueError('Wrong reference observations')
                            metric='completedRps' if c['name']=='rps' else c['name']
                            actual=summary['simulated'][metric]
                            delta=abs(actual-target)
                            if c['name'] in ('rps','latencyP99Ms'):
                                delta=delta/abs(target) if target else (0 if actual==0 else float('inf'))
                            limit={'rps':.15,'latencyP99Ms':.2,'publishFailureRate':.05,'deliveryLossRate':.05}[c['name']]
                            if c['limit']!=limit or not math.isclose(delta,c['error'],rel_tol=1e-9,abs_tol=1e-10):
                                raise ValueError('Acceptance calculation differs')
                            if c['status']!=('passed' if delta<=limit else 'failed'):
                                raise ValueError('Acceptance status differs')
                    if not math.isclose(r['offeredRps'],offered,rel_tol=1e-10):
                        raise ValueError('Offered load does not match protocol')


def bootstrap(points,count=2000):
    rng=np.random.default_rng(20261019)
    # Resample complete repeats (all correlated metrics together) within each fixed point and broker.
    data=np.empty((4,24,3,4))
    for bi,b in enumerate(BROKERS):
        for ki,key in enumerate(KEYS):
            for ri,r in enumerate(points[b,'validation',key]['runs']):
                m=r['observation']['measurement']
                data[bi,ki,ri]=[m['completedRps'],r['resources']['cpuPercent']/100,
                    (m['publishLatencyMs'] or {}).get('p99Ms',float('nan')),
                    m['uniqueDeliveries']/m['attemptedPublishes']]
    totals=[]
    for _ in range(count):
        choice=rng.integers(0,3,size=(4,24,3))
        means=np.take_along_axis(data,choice[:,:,:,None],axis=2).mean(axis=2)
        totals.append(np.mean([score(means[:,i],key[1]) for i,key in enumerate(KEYS)],axis=0))
    vals=np.asarray(totals)
    return {b:dict(lower=float(np.quantile(vals[:,i],.025)),upper=float(np.quantile(vals[:,i],.975)),
                   firstPlaceFraction=float(np.mean(np.argmax(vals,axis=1)==i))) for i,b in enumerate(BROKERS)}


def figures(out,points,models,scores):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    assets=out/'assets'; assets.mkdir(exist_ok=True)
    for b in BROKERS:
        for stage in ('calibration','validation'):
            fig,axes=plt.subplots(2,2,figsize=(11,7))
            for col,size in enumerate((16,256)):
                for qos in range(3):
                    ps=[points[b,stage,(size,qos,n)] for n in (1,16,64,128)]
                    axes[0,col].errorbar([1,16,64,128],[p['completedRps']['mean'] for p in ps],
                        yerr=[p['completedRps']['ci95HalfWidth'] for p in ps],marker='o',capsize=3,label=f'QoS {qos}')
                    axes[1,col].plot([1,16,64,128],[100*p['deliveryLossRate']['mean'] for p in ps],marker='o',label=f'QoS {qos}')
                axes[0,col].set_title(f'{size} байт'); axes[0,col].set_ylabel('Завершений/с; CI95 среднего')
                axes[1,col].set_ylabel('Недоставка, %')
                for ax in axes[:,col]: ax.set_xlabel('Отправители'); ax.grid(alpha=.3); ax.legend()
            fig.suptitle(f'{b}: {STAGES[stage]}'); fig.tight_layout(); fig.savefig(assets/f'{b}-{stage}.png',dpi=130); plt.close(fig)
        fig,axes=plt.subplots(2,2,figsize=(12,7))
        for col,stage in enumerate(('fidelity','holdout')):
            rows=[r for r in models[b] if r['stage']==stage]
            matrix=[]
            for key in KEYS:
                selected=[r for r in rows if pointkey(r)==key]
                matrix.append([sum(any(c['name']==metric and c['status']=='failed' for c in r['summary']['acceptanceCriteria']) for r in selected) for metric in CRITERIA])
            heat=np.array(matrix).T.astype(float); heat[1,[i for i,k in enumerate(KEYS) if k[1]==0]]=np.nan
            im=axes[0,col].imshow(heat,vmin=0,vmax=3,aspect='auto',cmap='YlOrRd')
            for metric in range(4):
                for index in range(24):
                    value=heat[metric,index]
                    if np.isfinite(value) and value>0:
                        axes[0,col].text(index,metric,str(int(value)),ha='center',va='center',fontsize=8,
                                         color='white' if value==3 else 'black')
            axes[0,col].set_yticks(range(4),['RPS','p99','failure','loss']); axes[0,col].set_title(f'{STAGES[stage]}: нарушений из 3')
            axes[0,col].set_xticks(range(24),[f'{a}/{q}/{n}' for a,q,n in KEYS],rotation=90,fontsize=7)
            native='calibration' if stage=='fidelity' else 'validation'
            means=[st.mean(r['summary']['simulated']['completedRps'] for r in rows if pointkey(r)==key) for key in KEYS]
            axes[1,col].plot(range(24),[points[b,native,k]['completedRps']['mean'] for k in KEYS],marker='o',label='Измерение')
            axes[1,col].plot(range(24),means,marker='x',label='MqttY, среднее 3 seeds')
            axes[1,col].set_xticks(range(24),[f'{a}/{q}/{n}' for a,q,n in KEYS],rotation=90,fontsize=7)
            axes[1,col].set_ylabel('Завершений/с'); axes[1,col].grid(alpha=.3); axes[1,col].legend()
        fig.suptitle(f'{b}: точность модели; белая ячейка p99 = неприменимо'); fig.tight_layout()
        fig.savefig(assets/f'{b}-fidelity.png',dpi=130); plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,4.5))
    for i,b in enumerate(BROKERS):
        ax.plot(range(24),scores[i],marker='o',label=b)
    ax.set_xticks(range(24),[f'{a}/{q}/{n}' for a,q,n in KEYS],rotation=90)
    ax.set_ylim(0,1); ax.set_ylabel('EST*'); ax.set_xlabel('Байт / QoS / отправители'); ax.legend(); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(assets/'Broker-EST.png',dpi=140); plt.close(fig)
    # Logical forwarding, deliberately distinct from the complete physical graph in GraphML.
    fig,ax=plt.subplots(figsize=(9,5)); ax.axis('off')
    positions=[(-1,1),(1,1),(-1,-1),(1,-1)]
    for i,(x,y) in enumerate(positions):
        ax.text(x,y,f'Брокер {i+1}\nиздатели + Relay\nудалённый получатель',ha='center',va='center',bbox=dict(boxstyle='round',facecolor='#e2eef7'))
        ax.annotate('',xy=(x*.7,y*.65),xytext=(x*.15,y*.15),arrowprops=dict(arrowstyle='<->',color='#40596b'))
    ax.text(0,0,'Общая сеть\nветвь: networkLength=2',ha='center',va='center',bbox=dict(boxstyle='round',facecolor='#e4f2df'))
    ax.set_xlim(-1.7,1.7); ax.set_ylim(-1.6,1.6); ax.set_title('Логическая схема: исходные темы пересылаются остальным брокерам')
    fig.tight_layout(); fig.savefig(assets/'Relay-topology.png',dpi=140); plt.close(fig)
    for b in (*BROKERS,'MqttRelay-Mixed'):
        fig,axes=plt.subplots(2,1,figsize=(11,6),sharex=True)
        selected=[r for r in models[b] if r['stage']=='relay']
        offered=[]; completed=[]; receipts=[]
        for key in KEYS:
            group=[r for r in selected if pointkey(r)==key]
            offered.append(group[0]['offeredRps'])
            completed.append(st.mean(r['summary']['simulated']['completedRps'] for r in group))
            receipts.append(st.mean(sum(r['receivers'].values()) for r in group))
        axes[0].plot(range(24),offered,label='Заданная внешняя нагрузка /с',marker='o')
        axes[0].plot(range(24),completed,label='Брокерные завершения /с, с пересылками',marker='x')
        axes[0].set_ylabel('Операций/с'); axes[0].legend()
        axes[1].plot(range(24),receipts,marker='o',color='#15756e')
        axes[1].set_ylabel('Получений receivers, включая drain')
        axes[1].set_xticks(range(24),[f'{a}/{q}/{n}' for a,q,n in KEYS],rotation=90,fontsize=8)
        for ax in axes: ax.grid(alpha=.3)
        fig.suptitle(f'{b}: модель сети; различающиеся счётчики не означают сквозную долю доставки')
        fig.tight_layout(); fig.savefig(assets/f'{b}-relay.png',dpi=130); plt.close(fig)


def relay_section(out,broker,rows):
    rows=[r for r in rows if r['stage']=='relay']
    table_rows=[]; export=[]
    for key in KEYS:
        group=[r for r in rows if pointkey(r)==key]
        if len(group)!=3: raise ValueError('Missing relay seeds')
        offered=group[0]['offeredRps']
        if any(r['offeredRps']!=offered for r in group): raise ValueError('Offered rates differ by seed')
        v={k:st.mean(r['summary']['simulated'][k] for r in group) for k in
           ('completedRps','publishFailureRate','deliveryLossRate','expectedDeliveries','delivered','rateRejected')}
        v['latencyP99Ms']=None if key[1]==0 else st.mean(r['summary']['simulated']['latencyP99Ms'] for r in group)
        receiver_total=st.mean(sum(r['receivers'].values()) for r in group)
        table_rows.append([*key,num(offered),num(offered/4),num(v['completedRps']),num(100*v['publishFailureRate']),
                           num(100*v['deliveryLossRate']),num(v['latencyP99Ms']),num(v['rateRejected']),num(v['expectedDeliveries']),num(v['delivered']),num(receiver_total)])
        export.append(dict(messageBytes=key[0],qos=key[1],publishers=key[2],offeredRps=offered,
                           offeredPerBrokerRps=offered/4,**v,receiverReceipts=receiver_total))
    csvwrite(out/f'{broker}-relay.csv',export)
    return ['## Модель сети MqttRelay',
        'Завершены 72 запуска: четыре двойника, networkLength=2, 24 точки × три seed. '
        'Все показатели ниже — средние по трём seed. Общий брокерный RPS включает внутренние пересылки. '
        'Счётчик receivers означает фактические получения модельными удалёнными приложениями, а не настоящими сетевыми процессами. '
        'Он охватывает время после сброса измерений до конца drain; сообщения прогрева в пути могут пересечь границу. '
        'Без сквозных ID его нельзя делить на внешнюю нагрузку и называть уникальной сквозной доставкой.',
        '![Логическая схема Relay](assets/Relay-topology.png)',
        ('Порядок двойников: Aedes, Mosquitto, ActiveMQ, EMQX. Суммарная внешняя нагрузка равна '
         '4 × минимуму attemptedRps четырёх соответствующих калибровочных точек. На каждый узел приходится четверть.'
         if broker=='MqttRelay-Mixed' else
         f'Все четыре двойника — {broker}. Суммарная внешняя нагрузка равна 4 × attemptedRps калибровочной точки; на каждый узел приходится четверть.'),
        'Модель транспорта: 125 000 000 байт/с (1 Гбит/с), transferTicks=2, очередь 100 000, tick=0,1 мс. '
        'Пропускная способность и очередь — допущения; loopback этой кампании не измеряет реальную Ethernet-сеть. '
        'Внутренние Relay-публикации добавляют нагрузку и могут получать rateRejected.',
        table(['Байт','QoS','N','Внешних/с','На узел/с','Брокерных завершений/с','Failure, %','Loss, %','p99, мс','RateRejected','Ожидалось доставок','Доставлено брокерами','Получений receivers'],table_rows),
        f'![Показатели Relay](assets/{broker}-relay.png)',
        f'[CSV]({broker}-relay.csv) · [Все настройки, counters, manifests и точные GraphML/PlantUML](data/{broker}-model-evidence.zip). '
        'Все критерии натурной точности Relay имеют статус `notApplicable`. Они не считаются пройденными.']


def broker_report(out,b,points,models,snapshot):
    raw=[r for r in read(out/'data/native-evidence.json')['runs'] if r['broker']==b]
    versions=raw[0]['versions']
    coverage=[]
    for stage,title in STAGES.items():
        if stage in ('calibration','validation'):
            attempts=snapshot['brokers'][b]['native'][stage]['attempts']
            failed=sum(a['status']=='failed' for a in attempts)
            interrupted=sum(a['status']=='interrupted' for a in attempts)
            error=f'{failed}; прервано {interrupted}'
            warning='неприменимо'
        else:
            selected=[r for r in models[b] if r['stage']==stage]
            error='0 незавершённых в итоговом индексе'
            warning='натурного эталона нет' if stage=='relay' else str(sum(any(c['status']=='failed' for c in r['summary']['acceptanceCriteria']) for r in selected))
        coverage.append([title,'✅ 72/72','❌ '+error,'⚠ '+warning])
    text=[f'# {b}: итоговый отчёт',
        'Полный цикл: **144 натурных измерения и 216 симуляций**. Финальная серия завершена 19 октября 2026 года в 16:44 МСК. '
        'Полное покрытие не означает прохождения всех проверок точности. '
        f'[Сравнение и EST](Broker-comparison.md) · [Краткий выбор](Best-broker.md) · [HTML]({b}.html).',
        '## Установка и воспроизведение',
        f'Брокер: **{versions["brokerVersion"]}**; исполняющая среда: `{versions["executableVersion"]}`. '
        'Linux, Intel Xeon E5-2680 v3, 12 физических ядер / 24 логических CPU; контроллер и брокер на одной машине, loopback. '
        'Брокер закреплён за CPU 0, контроллер — за 1–11 и 13–23. Это не эксклюзивное физическое ядро; SMT, память и ОС общие. '
        'Натурные измерения и симуляции не пересекались. Guard контроллера — 99%, это не предел CPU брокера.',
        'Сетка: 16/256 байт × QoS 0/1/2 × 1/16/64/128 отправителей × три повтора. '
        'Натурные warmup/measurement/cooldown/drain: 5/20/1/до 10 с. Модель: 1/2/до 5 с, seeds 42/43/44. '
        'BrokerFidelity: closedLoopPerPublisher, empiricalServiceBudget, tick=10 мкс. Relay: openLoopRate, fixed, tick=100 мкс. '
        'Запуски симуляций выполнялись до четырёх одновременно, обновление внутри каждой модели последовательное.',
        'Измерительный CLI: `3a689466009a0d5410f5dc88dc1282d72c27f7dd`; MqttY: '
        '`115885e012053d0112413e07743b061369ebea93`; калибратор: `f61daa47ec581ff88f20ee70882722085d1a0a10`. '
        'Изменения скриптов телеметрии и происхождение заимствованных этапов записаны в '
        '[series](data/series.json), [native-origins](data/native-origins.json), [repair-transition](data/repair-transition.json). '
        'Идентификаторы исходных серий сохранены; они не заменены идентификатором итоговой сборки.',
        f'[Настройки стенда](data/inputs/{b}-stand.json) · [Калибровочная программа](data/inputs/{b}-calibration.json) · '
        f'[Контрольная программа](data/inputs/{b}-validation.json) · [Окружение](data/environment.json).',
        f'Профиль: [broker-calibration.v3.json](data/{b}/profile/broker-calibration.v3.json), SHA-256 '
        f'`{sha(out/"data"/b/"profile/broker-calibration.v3.json")}`. '
        'Хэш проверен во всех manifests Fidelity, holdout и Relay; независимые измерения не изменяли профиль.',
        '## Покрытие',table(['Этап','Готово','Технические попытки','Нарушения точности'],coverage),
        'Технические ошибки относятся к попыткам, а предупреждения — к завершённым сценариям. '
        'Итоговый индекс симуляций не является полным журналом прежних перезапусков. Pilots и диагностика исключены из покрытия.']
    if b=='Mosquitto': text+=['Контрольная натурная серия выполнена в двух сеансах: 50 сохранённых успешных запусков и ещё 22 после паузы и перезагрузки. Это ограничивает однородность условий.']
    for stage in ('calibration','validation'):
        rows=[]; exports=[]; resource_rows=[]
        for key in KEYS:
            p=points[b,stage,key]; lat=p['observedLatencyQuantiles'] or {}
            rows.append([*key,num(p['attemptedRps']['mean']),f'{num(p["completedRps"]["mean"])} ± {num(p["completedRps"]["ci95HalfWidth"])}',
                         num(100*p['publishFailureRate']['mean']),num(100*p['deliveryLossRate']['mean']),
                         *[num(lat.get(k)) for k in ('p50Ms','p95Ms','p99Ms')],num(p['rttBaselineMs'])])
            resource_rows.append([*key,num(p['cpuPercent']),num(p['rssMiB']),num(p['receivedRps']),num(100*p['attemptDeliverySuccess'])])
            exports.append(dict(messageBytes=key[0],qos=key[1],publishers=key[2],
                **{m:p[m]['mean'] for m in ('attemptedRps','completedRps','publishFailureRate','deliveryLossRate')},
                completedRpsCi95HalfWidth=p['completedRps']['ci95HalfWidth'],**{k:lat.get(k) for k in ('p50Ms','p95Ms','p99Ms')},
                rttBaselineMs=p['rttBaselineMs'],cpuPercent=p['cpuPercent'],rssMiB=p['rssMiB'],
                receivedRps=p['receivedRps'],attemptDeliverySuccess=p['attemptDeliverySuccess']))
        csvwrite(out/f'{b}-{stage}.csv',exports)
        text += [f'## {STAGES[stage]}',
            'Средние по трём повторам. ± — полуширина CI95 Стьюдента: 4,30265273 × s / √3. '
            'Полные интервалы RPS, failure/loss и задержек сохранены в JSON. p50/p95/p99 — среднее перцентилей повторов, '
            'а не перцентиль объединённых сообщений. Для QoS 0 ACK latency неприменима. RTT измерен отдельно.',
            table(['Байт','QoS','N','Попыток/с','Завершений/с ± CI95','Failure, %','Loss, %','p50, мс','p95, мс','p99, мс','RTT, мс'],rows),
            f'![{b}: {STAGES[stage]}](assets/{b}-{stage}.png)',
            'CPU: взвешенное по длительности среднее внутренних интервалов нагрузки; интервалы у границ исключены из-за округления UTC до секунды. '
            'В таблице среднее CPU трёх повторов; RSS — максимум наблюдённых отсчётов в этих интервалах. '
            'CPU нормирован на одно закреплённое логическое CPU. Эти показатели относятся к настоящему процессу, модель их не воспроизводит. '
            'Receipts/s = uniqueDeliveries / actualMeasurementSeconds, включая получения при drain в числителе. '
            'Доставлено от попыток = uniqueDeliveries / attemptedPublishes; это другая доля, чем loss среди завершённых публикаций.',
            table(['Байт','QoS','N','CPU, %','RSS, МиБ','Receipts/s','Доставлено от попыток, %'],resource_rows),
            f'[CSV]({b}-{stage}.csv) · [Агрегат](data/{b}/{stage}/observations.json) · [Восстановление и повторная агрегация](data/{b}/{stage}/archive-verification.json).']
    counts=[]; failures=[]; validity=[]
    for stage in ('fidelity','holdout'):
        selected=[r for r in models[b] if r['stage']==stage]
        for metric in CRITERIA:
            cs=[c for r in selected for c in r['summary']['acceptanceCriteria'] if c['name']==metric]
            counts.append([STAGES[stage],metric,*[sum(c['status']==s for c in cs) for s in ('passed','failed','notApplicable')]])
        for r in selected:
            for c in r['summary']['acceptanceCriteria']:
                if c['status']=='failed': failures.append(dict(stage=stage,key=r['key'],**c))
    for key in KEYS:
        passes=[]
        for stage in ('fidelity','holdout'):
            selected=[r for r in models[b] if r['stage']==stage and pointkey(r)==key]
            passes.append(sum(all(c['status']!='failed' for c in r['summary']['acceptanceCriteria']) for r in selected))
        validity.append([*key,*[f'{n}/3' for n in passes]])
    if failures: csvwrite(out/f'{b}-fidelity-failures.csv',failures)
    text += ['## Точность двойника MqttY',
        'Пороги сохранены: RPS ≤15%, p99 ≤20%, абсолютная разница failure/loss ≤0,05 (5 процентных пунктов). '
        'Fidelity сравнивает модель с данными настройки; holdout — с новыми измерениями при неизменном профиле. '
        'Успешность процесса и выполнение критериев — разные результаты. Числа нарушений по критериям могут пересекаться.',
        table(['Этап','Критерий','passed','failed','notApplicable'],counts),
        f'![Карта ошибок и сравнение RPS](assets/{b}-fidelity.png)',
        '### Область подтверждённого соответствия в этой сетке',
        'Число seed, прошедших все применимые критерии. Только 3/3 в контроле означает прохождение всех трёх контрольных симуляций данной точки; '
        'это не доказывает точность на других нагрузках или оборудовании.',
        table(['Байт','QoS','N','Fidelity','Контроль'],validity),
        f'[Все нарушения, точные числа и пределы]({b}-fidelity-failures.csv) · [Настройки и исходные модельные файлы](data/{b}-model-evidence.zip).']
    text+=relay_section(out,b,models[b])
    text+=['## Вывод и ограничения',
        {'Aedes':'Отказов PublishAsync в натурных агрегатах нет; при этом при большой нагрузке возможна недоставка. Стабильный RPS сам по себе не подтверждает стабильность получения подписчиком.',
         'Mosquitto':'Высокий RPS сопровождается большой недоставкой в части точек. Предыдущая диагностика наблюдала сбросы очереди. Причину каждой потери этой кампании по агрегату установить нельзя. [Диагностика](history/mosquitto-fidelity-diagnosis.md).',
         'ActiveMQ':'Контрольные расхождения особенно велики. Состояние соединений и быстрая повторная ошибка локального клиента искажают смысл attemptedRps как сетевой нагрузки. Изменение натурной недоставки объясняет значительную часть расхождений фиксированного профиля. [Подробная диагностика и аудит ID](ActiveMQ-diagnosis.md).',
         'EMQX':'Все калибровочные проверки прошли, но независимый контроль содержит нарушения. Воспроизведение данных настройки не заменяет независимую проверку. Две неуспешные попытки старта были повторены; они не означают два отказа обработки сообщений.'}[b],
        'Ни один двойник не прошёл все 72 независимых сценария. Короткое модельное окно, изменчивость натурного стенда, '
        'разные знаменатели времени и способ объединения распределений ограничивают интерпретацию. '
        'Benchmark делит на фактическое окно завершения нагрузки; модель — на заданные 2 с, включая завершения при drain. '
        'Недоставка означает отсутствие ID к концу наблюдения, а не доказанную безвозвратную потерю. '
        'Показатели CPU/RSS и точность настоящей Relay-сети этой моделью не подтверждены.',
        '## Проверяемость и команды',
        '[Индекс исходных файлов и их SHA-256](data/snapshot.json) · [Архивы raw](data/archive-registry.json) · '
        '[Результаты и ресурсы всех 576 натурных запусков](data/native-evidence.json) · [Извлечённые sealed members](data/native-members.zip) · '
        '[Повторение выбранных моделей](data/replay-results.json). '
        'При финальной публикации сверены seals прочитанных членов ZIP, исходные результаты и средние/CI95. '
        'Полные ZIP заново не хэшировались и журналы всех сообщений не пересчитывались: сохранены квитанции прежнего восстановления. '
        'Полные raw остаются на hd250; Git содержит компактные доказательства и пути к архивам.',
        '```bash\npython tools/campaign/render-final-reports.py --output reports\n```',
        'Для повтора модели распакуйте model-evidence.zip, возьмите config.json нужной точки, замените только пути к профилям/observations/репозиториям '
        'и вызовите `dotnet EntityFX.MqttY.MqttRelay.App.dll experiment --config <config.json> --output <новый-каталог>`. '
        'Нужна зафиксированная версия DLL, а не произвольная свежая сборка. Для новой полной натурной серии: [руководство](../docs/mqtt-benchmark/runbook.md).']
    write(out/f'{b}.md','\n\n'.join(text)+'\n')


def comparison(out,points,models):
    scores=score_grid(points); means=scores.mean(axis=1); order=np.argsort(-means); winner=BROKERS[order[0]]
    boot=bootstrap(points)
    preset={'Равные веса':(.25,.25,.25,.25),'Приоритет RPS':(.55,.15,.15,.15),
            'Приоритет CPU':(.15,.55,.15,.15),'Приоритет задержки':(.15,.15,.55,.15),'Приоритет доставки':(.15,.15,.15,.55)}
    variants={name:score_grid(points,weights=w).mean(axis=1).tolist() for name,w in preset.items()}
    variants['CPU за весь захват (диагностика)']=score_grid(points,cpu='captureCpuPercent').mean(axis=1).tolist()
    variants['Доставка только от completed (диагностика)']=score_grid(points,success='completedDeliverySuccess').mean(axis=1).tolist()
    variants['Калибровочная серия (диагностика)']=score_grid(points,stage='calibration').mean(axis=1).tolist()
    variants['Только QoS 1/2, исходные четыре члена']=scores[:,[i for i,k in enumerate(KEYS) if k[1]>0]].mean(axis=1).tolist()
    variants['Только QoS 0, три члена']=scores[:,[i for i,k in enumerate(KEYS) if k[1]==0]].mean(axis=1).tolist()
    variants['Только N=1']=scores[:,[i for i,k in enumerate(KEYS) if k[2]==1]].mean(axis=1).tolist()
    clean=[i for i,k in enumerate(KEYS) if all(points[b,'validation',k]['publishFailureRate']['mean']==0 for b in BROKERS)]
    variants['Общие точки без ошибок публикации']=scores[:,clean].mean(axis=1).tolist()
    weight_grid=[tuple(x/10 for x in weights) for weights in itertools.product(range(1,8),repeat=4) if sum(weights)==10]
    wins=np.zeros(4)
    for w in weight_grid:
        m=score_grid(points,weights=w).mean(axis=1); best=np.isclose(m,m.max(),rtol=0,atol=1e-12); wins+=best/best.sum()
    result=dict(method='EST* adaptation: independent measurements, equal 24-point weighting; QoS0 excludes latency and renormalizes',
                weights=dict(R=.25,C=.25,L=.25,F=.25),brokers=list(BROKERS),keys=KEYS,scoreByPoint=scores.tolist(),
                means=dict(zip(BROKERS,map(float,means))),winner=winner,bootstrap=boot,bootstrapSeed=20261019,bootstrapSamples=2000,
                sensitivity=variants,commonNoPublishFailurePointCount=len(clean),weightGrid=list(weight_grid),
                weightGridFirstPlaceFraction=dict(zip(BROKERS,(wins/len(weight_grid)).tolist())))
    dump(out/'data/integral-assessment.json',result)
    exports=[]
    for ki,key in enumerate(KEYS):
        for bi,b in enumerate(BROKERS):
            p=points[b,'validation',key]
            exports.append(dict(broker=b,messageBytes=key[0],qos=key[1],publishers=key[2],score=float(scores[bi,ki]),
                completedRps=p['completedRps']['mean'],cpuPercent=p['cpuPercent'],
                p99Ms=(p['observedLatencyQuantiles'] or {}).get('p99Ms'),attemptDeliverySuccess=p['attemptDeliverySuccess']))
    csvwrite(out/'Broker-EST.csv',exports)
    overall=[]
    for bi in order:
        b=BROKERS[bi]; ps=[points[b,'validation',k] for k in KEYS]
        overall.append([b,f'{means[bi]:.4f}',f'{boot[b]["lower"]:.4f}–{boot[b]["upper"]:.4f}',
            num(st.mean(p['completedRps']['mean'] for p in ps)),num(st.mean(p['receivedRps'] for p in ps)),
            num(st.mean(p['cpuPercent'] for p in ps)),num(100*st.mean(p['attemptDeliverySuccess'] for p in ps)),
            num(st.mean(p['observedLatencyQuantiles']['p99Ms'] for p in ps if p['qos']>0)),num(max(p['rssMiB'] for p in ps))])
    exact=read(out/'data/article-sources.json')[0]['tables']
    old_rows=[]
    for size,tab in zip((16,256),exact[1:3]):
        for row in tab[2:]:
            n=int(row[0])
            if n not in (1,16,64,128): continue
            for qos in range(3):
                old={b:float(v.replace(',','.')) for b,v in zip(('Mosquitto','ActiveMQ','Aedes','EMQX'),row[1+4*qos:5+4*qos])}
                ki=KEYS.index((size,qos,n)); oldbest=max(old,key=old.get); newbest=BROKERS[int(np.argmax(scores[:,ki]))]
                old_rows.append([size,qos,n,oldbest,f'{old[oldbest]:.3f}',newbest,f'{max(scores[:,ki]):.3f}'])
    text=['# Сравнение брокеров и интегральная оценка',
        f'**По заранее выбранным равным весам и равному весу 24 нагрузочных точек лидер этой кампании — {winner}: '
        f'{means[order[0]]:.4f} из 1.** Это условный выбор для исследованной конфигурации, а не универсальный победитель. '
        'Данные независимых замеров используются для рейтинга; точность модели оценивается отдельно и в EST не входит.',
        '## Данные и сопоставимость',
        'Все четыре брокера завершили по 144 натурных измерения и 216 симуляций; смешанная сеть — ещё 72. Всего 1512. '
        'Для рейтинга берутся 72 независимых измерения каждого брокера: 24 одинаковые комбинации нагрузки по три повтора. '
        'Брокер работает на одном закреплённом логическом CPU. Общий хост, разные даты/сеансы, перезапуски и короткие окна ограничивают обобщение. '
        'Нагрузки closed-loop не означают одинаковый offeredRps: он зависит от времени ответа и ошибок клиента. '
        'Известная проблема повторных локальных ошибок после отключения ActiveMQ влияет на attempted и долю доставок от попыток; '
        'это рейтинг системы «брокер + клиент + настройки стенда», а не изолированной реализации брокера.',
        '## Формула EST и явно обозначенная адаптация',
        'Источник — [глава 2, §2.4 диссертации](../diss/book/02-chapter2.md) и авторская статья '
        '«Выбор брокера сообщений для Интернета вещей на основе нагрузочного тестирования». Формула:',
        '`EST = K_R × R/max(R_B) + K_C × (1−C) + K_L × (1−L/max(L_B)) + K_F × (1−F)`',
        'K_R=K_C=K_L=K_F=0,25. Максимумы считаются по четырём брокерам **в одной и той же нагрузочной точке**, '
        'после усреднения трёх повторов. R — completedRps; C — доля CPU процесса на одном закреплённом CPU; '
        'L — среднее p99 завершения публикации (QoS 1/2); 1−F — среднее uniqueDeliveries/attemptedPublishes. '
        'Последний знаменатель соответствует сравнению отправленных и полученных в статье и включает влияние локальных неудачных попыток. '
        'Он отличается от deliveryLossRate программы, которая проверяет доставку среди завершённых публикаций.',
        'Для QoS 0 нет MQTT ACK, поэтому L не подменяется нулём: остаются R, C и F с весами 1/3. '
        'Среднее по всем 24 точкам называем **EST***. Для QoS 1/2 отдельно приводится исходная четырёхкомпонентная оценка. '
        'Все точки имеют одинаковый вес; в среднем по смешанной QoS-сетке эффективный вклад коэффициентов R/C/F равен 5/18, L — 1/6: '
        'равные веса четырёх членов действуют только там, где все четыре применимы. Повторное нормирование готового рейтинга по лучшему не выполняется. '
        '0 и 1 — границы выбранного критерия, а не вероятность исправной работы. '
        'Если R всех брокеров равен нулю, вклад R равен нулю; если все применимые L равны нулю, вклад задержки равен единице. '
        'Пропуски обязательных значений запрещены. Изменение множества сравниваемых брокеров может изменить нормировку и рейтинг.',
        'CPU вычислен по внутренним интервалам измерения, с исключением граничных секунд. В статье CPU относился к хосту; '
        'здесь процессу выделено одно логическое CPU, поэтому это методическая адаптация, а не буквальное повторение старого опыта. '
        'Низкая загрузка CPU при простое сама по себе не доказывает эффективность: остальные члены EST могут лишь частично компенсировать её вклад.',
        '## Результаты независимой серии',
        'В таблице описательные средние по фиксированным точкам. p99 усреднён по 16 точкам QoS 1/2, RSS — максимальный отсчёт. '
        'Средний receipts/s не является скоростью уникальных сообщений в одном общем опыте.',
        table(['Брокер','EST*','Bootstrap 95%','Completed/s','Receipts/s','CPU, %','Доставлено от попыток, %','p99, мс','RSS max, МиБ'],overall),
        'Bootstrap: 2000 выборок с seed 20261019; внутри каждой пары «брокер/точка» с возвращением выбираются три целых повтора, '
        'все связанные метрики сохраняются вместе; нормировка между брокерами пересчитывается. Точки нагрузки не ресэмплируются. '
        'При n=3 интервалы условны и не описывают дрейф ОС/дней; это анализ устойчивости имеющихся повторов, не доказательство превосходства генеральной совокупности.',
        '![Оценки по точкам](assets/Broker-EST.png)',
        table(['Байт','QoS','N',*BROKERS,'Лидер'],[[*k,*[f'{scores[i,j]:.4f}' for i in range(4)],BROKERS[int(np.argmax(scores[:,j]))]] for j,k in enumerate(KEYS)]),
        '[Все компоненты и оценки CSV](Broker-EST.csv) · [Веса, bootstrap, чувствительность JSON](data/integral-assessment.json).',
        '## Чувствительность выбора',
        table(['Вариант',*BROKERS,'Лидер'],[[name,*[f'{x:.4f}' for x in values],BROKERS[int(np.argmax(values))]] for name,values in variants.items()]),
        f'Общих точек без ошибок публикации у всех четырёх брокеров: {len(clean)}. Их поднабор выбран по результатам, поэтому является диагностикой. '
        'В вариантах «приоритет» выбранный вес 0,55, остальные по 0,15; для QoS 0 неприменимый член снова исключается и веса нормируются.',
        f'Перебраны {len(weight_grid)} комбинации положительных весов с шагом 0,1 и суммой 1. '
        'Доля первого места в этой сетке предпочтений (не статистическая вероятность): '+
        '; '.join(f'{b} {wins[i]/len(weight_grid):.1%}' for i,b in enumerate(BROKERS))+'. '
        'Веса после получения рейтинга не подбирались для сохранения победителя.',
        '## Сопоставление с диссертацией и статьями',
        'Прочитаны три предоставленных DOCX из авторского каталога. [Имена и SHA-256, численные таблицы первой статьи](data/article-sources.json). '
        'Полные рукописи не включены в Git. Библиография: [author.bib](../diss/biblio/author.bib).',
        table(['Работа','Что проверяла','Что даёт текущая серия','Что нельзя заключить'],[
            ['Выбор брокера… (2025), DOI 10.18127/j19997493-202503-04','RPS, CPU, p99, доля недоставок; EST','Те же группы показателей, явные определения и EST* по новым данным','Численные рейтинги старого и нового стенда не являются повторными измерениями одного режима'],
            ['Симуляция распределённых сетей… (2025), DOI 10.1109/RusAutoCon65989.2025.11177411','Архитектура MqttRelay, виртуальные узлы и передача телеметрии','360 сетевых моделей с эмпирическими профилями и счётчиками receivers','Нет натурного эталона распределённой сети'],
            ['Ускорение симуляции MqttY (2026), DOI 10.33693/2313-223X-2026-13-1-212-221','Ticks, real/virtual time, steps, errors, память; сравнение режимов и реализаций','Проверка детерминизма фиксированного runtime и научных итогов','Ускорение 2,5–3 и 12,5 раз из статьи этой кампанией повторно не измерялось']]),
        'В первой статье для малого числа клиентов рекомендован Mosquitto, для остальных случаев особенно QoS 2 — ActiveMQ. '
        'В главе 2 есть более подробное разделение по QoS и размеру. Ниже заново прочитаны численные EST-таблицы статьи, '
        'а не перенесены её словесные рекомендации. Совпадающие входы ограничены N=1/16/64/128 и 16/256 байт. '
        'В старой работе были три Windows-узла, свободное использование CPU брокерами, NBomber и иной учёт. '
        'В новой — один Linux-хост, одно логическое CPU, ID-учёт, другой клиент и ограниченное время наблюдения. '
        'Отличие лидера не опровергает старую статью; знак изменения не показывает прогресс версии брокера.',
        table(['Байт','QoS','N','Лидер статьи','EST статьи','Лидер сейчас','EST* сейчас'],old_rows),
        'Страница [статьи о выборе брокера](https://radiotec.ru/ru/journal/Dynamics_of_Difficult_Systems%E2%80%93XXI_century/number/2025-3/article/25179) '
        'подтверждает состав показателей; [статья об ускорении MqttY](https://journals.eco-vector.com/2313-223X/article/view/707588/ru_RU) '
        'описывает другую задачу — производительность самого симулятора. Здесь выводы основаны также на предоставленных полных рукописях.',
        '## Практический выбор',
        f'Для воспроизведения именно этой программы выбираем **{winner}** по EST*. Для новой системы сначала выбирают её нагрузочные точки и веса, '
        'проверяют приемлемую долю доставки и ограничения памяти, затем пересчитывают оценку. '
        'Даже высокий EST не разрешает игнорировать недоставку. Публикационная ошибка и недоставка не равны падению процесса брокера. '
        'Перед рекомендацией для эксплуатации нужно исправить отключения генератора и выполнить новую сопоставимую серию, особенно для ActiveMQ. '
        '[Доказательства и границы модели](Model-validation.md) · [Диагностика ActiveMQ](ActiveMQ-diagnosis.md).']
    write(out/'Broker-comparison.md','\n\n'.join(text)+'\n')
    write(out/'Best-broker.md',f'# Какой брокер лучший\n\nВ этой кампании по **EST*** с равными весами RPS, CPU, p99 и доставки лидер — **{winner}: {means[order[0]]:.3f} из 1**.\n\n'+
        table(['Место','Брокер','Оценка 0–1'],[[j+1,BROKERS[i],f'{means[i]:.3f}'] for j,i in enumerate(order)])+
        '\n\nОценка рассчитана по 72 независимым измерениям каждого брокера; 24 нагрузочные точки имеют равный вес. '
        'Для QoS 0 неприменимая задержка исключена, оставшиеся веса нормированы. Это адаптация EST к этой серии, а не универсальный рейтинг. '
        'На результат влияют клиент, одно закреплённое CPU, очередь и изменчивость стенда; особенно существенны локальные ошибки клиента ActiveMQ. '
        f'При приоритете доставки (вес 0,55; остальные по 0,15) лидер — {BROKERS[int(np.argmax(variants["Приоритет доставки"]))]}: {max(variants["Приоритет доставки"]):.3f}. '
        f'У Mosquitto средняя доля полученных от попыток — {100*st.mean(points["Mosquitto","validation",k]["attemptDeliverySuccess"] for k in KEYS):.2f}%, '
        f'у Aedes — {100*st.mean(points["Aedes","validation",k]["attemptDeliverySuccess"] for k in KEYS):.2f}%; средние относятся к фиксированной сетке, '
        'а не к эксплуатационному SLA. Небольшой перевес общего EST* не делает Mosquitto лучшим для задачи надёжной доставки. '
        'Ни один двойник не прошёл все независимые проверки.\n\n'
        '[Формула, компоненты, интервалы и чувствительность](Broker-comparison.md) · [Точность моделей](Model-validation.md).\n')
    return scores,result


def model_report(out,models):
    rows=[]; coverage=[]
    for b in BROKERS:
        counts=[]
        for stage in ('fidelity','holdout'):
            runs=[r for r in models[b] if r['stage']==stage]
            passed=sum(all(c['status']!='failed' for c in r['summary']['acceptanceCriteria']) for r in runs)
            allpoints=sum(all(all(c['status']!='failed' for c in r['summary']['acceptanceCriteria']) for r in runs if pointkey(r)==k) for k in KEYS)
            counts.extend([f'{passed}/72',f'{allpoints}/24'])
        rows.append([b,*counts])
    repeat=read(out/'data/replay-results.json')
    reruns=[]
    for r in repeat['rows']:
        eq=r['equal']
        reruns.append([r['broker'],r['stage'],r['key'],eq['summary.json'],eq['scientificCounters'],eq['latency-histogram.json'],eq['phases'],num(r['wallSeconds'])])
    text=['# Проверка MqttY и MqttRelay: доказательства и границы',
        '**Доказана работоспособность модели и повторяемость выбранных запусков. Полное соответствие настоящим брокерам не подтверждено.** '
        'Ни один двойник не прошёл все независимые точки. Натурная точность Relay-сети не проверялась.',
        '## 1. Привязка двойника к измерениям',
        'У каждого брокера 72 измерения для калибровки и 24 профильные точки. Все 936 manifests моделей проверены против четырёх '
        'замороженных SHA-256 профилей из финального state. В mixed-сети сохранены четыре отдельные provenance; '
        'campaign identities не объединяются. Версия модели, длительности и режимы одинаковы в Fidelity и holdout; '
        'offeredRps берётся из соответствующей калибровочной или независимой точки. '
        '[Снимок](data/snapshot.json), [сводка моделей с хэшами](data/model-evidence.json), полные manifests — в пяти model-evidence.zip.',
        '## 2. Количественная проверка точности',
        'В таблице сначала число запусков, прошедших все применимые критерии, затем число точек, в которых прошли все три seed. '
        'QoS 0 p99 неприменим и не засчитывается отдельным успехом.',
        table(['Брокер','Fidelity: запусков','Fidelity: точек','Независимый контроль: запусков','Контроль: точек'],rows),
        'Критерии: относительное отклонение RPS ≤15%, p99 ≤20%, абсолютное отличие failure/loss ≤0,05. '
        'Fidelity — воспроизведение данных настройки, а не независимое доказательство. '
        'Контроль использует отдельные измерения. Каждый broker-report содержит карту всех 24 точек и CSV нарушений. '
        'Три seed одной модели не заменяют три независимых натурных стенда. Результаты seed коррелированы общим профилем.',
        '## 3. Повторяемость при тех же входах',
        'После окончания серии повторены четыре контрольные точки BrokerFidelity, однородная Aedes Relay и смешанная Relay. '
        'Выбор заранее фиксирован в скрипте: 16 байт, QoS 1, N=1, seed=42; это малая нагрузка, а не вся сетка. '
        'Использованы прежние config и DLL. Сравниваются полные summary, научные broker/receiver-счётчики, histogram и фазы. '
        'Профили проверены до и после. Реальное время вычислений и runId не являются научными итогами и могут различаться.',
        table(['Брокер/сеть','Этап','Точка','Summary совпал','Научные counters','Histogram','Фазы','Реальное время, с'],reruns),
        '[Протокол повторений](data/replay-results.json) · [Пути архивов на диске результатов и SHA-256](data/external-archives.json). '
        'ZIP не включены в Git. Команда проверки: '
        '`python tools/campaign/replay-final-models.py --controller <controller> --output <новый-каталог> --dotnet <dotnet>`. '
        'Эти шесть повторений не включены в 1512 запусков и не являются новой слепой валидацией. '
        'Совпадение итогов подтверждает повторяемость данной реализации на выбранных входах; оно не доказывает правильность физической модели.',
        '## 4. Сетевые модели',
        'Завершены 360 запусков: по 72 однородных сети каждого брокера и 72 смешанных. '
        'В model-evidence.zip сохранены config, topology.graphml, topology.puml, manifest, summary, counters, histogram. '
        'Модельные receivers получают пересланные сообщения; число получений приведено для каждой точки в отчётах. '
        'Все 72 смешанных запуска используют по одному неизменному профилю Aedes, Mosquitto, ActiveMQ и EMQX.',
        '![Логическая схема](assets/Relay-topology.png)',
        'Receiver-счётчики включают завершения при drain и возможные сообщения прогрева в пути. Они не заменяют уникальный учёт сквозных ID. '
        'Брокерные counters включают внутренние операции Relay, поэтому суммарный RPS не является внешним goodput. '
        'Модельные отказ/потеря и задержки сети зависят также от принятой пропускной способности, очереди и fixed transport. '
        'Допуски одиночного брокера в Relay имеют `notApplicable`. Сравнение сети с одиночным профилем диагностическое.',
        '## 5. Что потребуется для более сильного доказательства',
        'Для точности одиночного двойника: исправить генератор после отключения, раздельно учитывать локальные вызовы и сетевые публикации, '
        'моделировать соединения и очереди, затем собрать новую калибровку и новый, ранее не использованный контроль. '
        'Проверить чувствительность к длине окна и шагу модели без изменения порогов по результатам проверки. '
        'Для Relay: развернуть настоящие четыре брокера и ретрансляторы с той же топологией, измерить сквозную доставку по ID, задержки и нагрузку сети, '
        'провести независимое сопоставление. CPU/RSS брокеров текущая модель не предсказывает. '
        'Измерения одиночных брокеров и детерминированность вычисления не заменяют этот сетевой эксперимент.',
        '[Сравнение брокеров](Broker-comparison.md) · [Смешанная сеть](MqttRelay-Mixed.md).']
    write(out/'Model-validation.md','\n\n'.join(text)+'\n')


def html_all(out):
    import markdown
    names=(*BROKERS,'MqttRelay-Mixed','Broker-comparison','Best-broker','Model-validation')
    for name in names:
        body=markdown.markdown((out/f'{name}.md').read_text(encoding='utf-8'),extensions=['tables','fenced_code'])
        def embed(match):
            p=out/match.group(1)
            if not p.is_file(): raise ValueError('Missing figure: '+str(p))
            return 'src="data:image/png;base64,'+base64.b64encode(p.read_bytes()).decode()+'"'
        body=re.sub(r'src="(assets/[^"]+)"',embed,body)
        write(out/f'{name}.html','<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>{html.escape(name)}</title><style>body{{font:16px/1.55 system-ui;max-width:1400px;margin:2em auto;padding:0 1em}}'
            'table{border-collapse:collapse;display:block;overflow:auto}td,th{border:1px solid #bbb;padding:.4em}th{background:#e8eef4}'
            'img{max-width:100%}code{overflow-wrap:anywhere}a{color:#17548a}</style><body>'+body+'</body></html>')


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--archive-root',type=Path,help='Optionally recheck external ZIP hashes and every original model member; not required to recalculate from published JSON')
    args=parser.parse_args(); out=args.output
    verify(out,args.archive_root); points=aggregate(out); models=read(out/'data/model-evidence.json'); snapshot=read(out/'data/snapshot.json')
    verify_models(out,points,models,args.archive_root)
    for b in (*BROKERS,'MqttRelay-Mixed'):
        required=('relay',) if b=='MqttRelay-Mixed' else ('fidelity','holdout','relay')
        for stage in required:
            runs=[r for r in models[b] if r['stage']==stage]
            if len(runs)!=72 or len({r['key'] for r in runs})!=72:
                raise ValueError('Incomplete model matrix')
    scores,result=comparison(out,points,models)
    figures(out,points,models,scores)
    for b in BROKERS: broker_report(out,b,points,models,snapshot)
    write(out/'MqttRelay-Mixed.md','\n\n'.join(['# Смешанная сеть MqttRelay: итоговый отчёт',
        'Завершены **72/72** запуска. Использованы четыре замороженных профиля. '
        'Это модельный эксперимент; натурного эталона сети в данной кампании нет.']+
        relay_section(out,'MqttRelay-Mixed',models['MqttRelay-Mixed'])+
        ['[Проверка модели и повторяемости](Model-validation.md) · [Версии и программа](data/series.json) · '
         '[Хэши профилей](data/state.json) · [Сводка всех моделей](data/model-evidence.json).'])+'\n')
    model_report(out,models); html_all(out)
    dump(out/'publication-sha256.json',dict(note='Published files; this manifest excludes itself. Original raw archives are separate.',
        files={p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.suffix.lower()!='.zip' and p!=out/'publication-sha256.json'}))
    print('Verification:', 'external archives and compact JSON' if args.archive_root else 'compact JSON; external archives not read')
    print('REPORTS',result['means'],'winner',result['winner'])
    return out


if __name__=='__main__':
    report_root = main()

# Canonical meta-repository documentation layout.
if __name__ == '__main__':
    import sys as _layout_sys
    _layout_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "documentation"))
    from layout import organize_reports as _organize_reports
    _organize_reports(report_root)
