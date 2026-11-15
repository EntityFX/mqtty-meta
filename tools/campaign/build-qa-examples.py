"""Build the Q&A figure and portable configs from the published Aedes evidence.

Reads saved results only; no broker or simulator is launched. Archived config
bytes and the archive itself are verified before adapting repository/file paths.
"""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import statistics
import zipfile


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8',newline='\n')


def build(root,archive):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    matplotlib.rcParams['svg.hashsalt']='mqtty-qa-aedes-holdout'
    evidence=root/'reports/data'
    assets=root/'docs/overview/assets'
    examples=root/'docs/mqtty/examples/qa'
    assets.mkdir(parents=True,exist_ok=True)
    examples.mkdir(parents=True,exist_ok=True)
    registry=read(evidence/'external-archives.json')['archives']['Aedes-model-evidence.zip']
    if digest(archive.read_bytes())!=registry['sha256']:
        raise ValueError('Archived model evidence hash differs')
    native=read(evidence/'native-evidence.json')['runs']
    model=read(evidence/'model-evidence.json')['Aedes']
    observations=read(evidence/'Aedes/validation/observations.json')
    metadata={'broker':'Aedes','payloadBytes':256,'qos':1,'stage':'holdout',
        'purpose':'Illustrate measurement vs model; this subset is not an overall broker ranking',
        'sourceSha256':{},'configs':{}}
    for path in (evidence/'native-evidence.json',evidence/'model-evidence.json',
                 evidence/'Aedes/validation/observations.json',evidence/'inputs/Aedes-calibration.json'):
        metadata['sourceSha256'][path.relative_to(root).as_posix()]=digest(path.read_bytes())
    source=evidence/'inputs/Aedes-calibration.json'
    (examples/'Aedes-calibration.json').write_bytes(source.read_bytes())
    metadata['configs']['Aedes-calibration.json']={'source':source.relative_to(root).as_posix(),
                                                 'sourceSha256':digest(source.read_bytes()),'changedFields':[]}
    with zipfile.ZipFile(archive) as bundle:
        for stage in ('holdout','relay'):
            key='m256.q1.p64.s42'
            item=next(r for r in model if r['stage']==stage and r['key']==key)
            member=f'{stage}/{key}/config.json'
            raw=bundle.read(member)
            if digest(raw)!=item['configSha256']:
                raise ValueError('Archived config hash differs')
            original=json.loads(raw)
            config=copy.deepcopy(original)
            paths={'mqttYRepository':'mqtty','mqttBenchmarkRepository':'MqttBenchmark',
                   'brokerProfilePath':'reports/data/Aedes/profile/broker-calibration.v3.json'}
            if stage=='holdout':
                paths['referenceObservationsPath']='reports/data/Aedes/validation/observations.json'
            config.update(paths)
            if any(config[k]!=original[k] for k in original if k not in paths):
                raise ValueError('A non-path experiment input changed')
            filename=f'Aedes-{stage}-m256.q1.p64.s42.json'
            dump(examples/filename,config)
            metadata['configs'][filename]={'archive':'Aedes-model-evidence.zip','archiveSha256':registry['sha256'],
                'member':member,'sourceSha256':digest(raw),'adaptedSha256':digest((examples/filename).read_bytes()),
                'changedFields':paths}
    rows=[]
    for n in (1,16,64,128):
        real=sorted([r for r in native if r['broker']=='Aedes' and r['stage']=='validation'
                     and (r['key']['messageBytes'],r['key']['qos'],r['key']['publishers'])==(256,1,n)],
                    key=lambda r:r['key']['repeat'])
        simulated=sorted([r for r in model if r['stage']=='holdout'
                          and (r['bytes'],r['qos'],r['publishers'])==(256,1,n)],key=lambda r:r['seed'])
        if len(real)!=3 or len(simulated)!=3: raise ValueError('Incomplete chart point')
        for r in real:
            m=r['observation']['measurement']
            rows.append(dict(source='native-validation',publishers=n,repeat=r['key']['repeat'],seed='',
                             completedRps=m['completedRps'],latencyP99Ms=m['publishLatencyMs']['p99Ms'],
                             deliveryLossRate=m['deliveryLossRate'],publishFailureRate=m['publishFailureRate']))
        for r in simulated:
            m=r['summary']['simulated']
            rows.append(dict(source='mqtty-holdout',publishers=n,repeat='',seed=r['seed'],
                             **{k:m[k] for k in ('completedRps','latencyP99Ms','deliveryLossRate','publishFailureRate')}))
        point=next(p for p in observations['points'] if (p['messageBytes'],p['qos'],p['publishers'])==(256,1,n))
        if abs(statistics.mean(r['observation']['measurement']['completedRps'] for r in real)-point['completedRps']['mean'])>1e-6:
            raise ValueError('Chart mean differs from observations')
    csv_path=assets/'qa-aedes-holdout.csv'
    with csv_path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    fig,axes=plt.subplots(1,3,figsize=(15,4.8))
    for ax,metric,title,unit,scale in zip(axes,('completedRps','latencyP99Ms','deliveryLossRate'),
        ('Скорость публикаций','Завершение публикации','Недоставка подписчику'),
        ('Завершений/с','p99, мс','Недоставка, %'),(1,1,100)):
        for source,color,marker,shift,label in (
            ('native-validation','#1766a4','o',-.10,'Натурный контроль'),
            ('mqtty-holdout','#cf681a','s',.10,'MqttY: контрольные симуляции')):
            groups=[[r[metric]*scale for r in rows if r['source']==source and r['publishers']==n] for n in (1,16,64,128)]
            means=[statistics.mean(xs) for xs in groups]
            ax.plot([i+shift for i in range(4)],means,color=color,marker=marker,label=label)
            for i,xs in enumerate(groups):
                ax.scatter([i+shift+j*.025-.025 for j in range(3)],xs,color=color,alpha=.5,s=23,zorder=3)
            if metric=='completedRps' and source=='native-validation':
                ci=[4.30265273*statistics.stdev(xs)/(3**.5) for xs in groups]
                ax.errorbar([i+shift for i in range(4)],means,yerr=ci,fmt='none',capsize=4,color=color,alpha=.65)
        ax.set_title(title);ax.set_xlabel('Отправители');ax.set_ylabel(unit)
        ax.set_xticks(range(4),(1,16,64,128));ax.grid(alpha=.25);ax.set_ylim(bottom=0)
    axes[2].set_ylim(-3,103)
    axes[0].legend(fontsize=8,loc='upper left')
    fig.suptitle('Aedes · 256 байт · QoS 1 · независимый контроль',fontsize=14)
    fig.text(.5,.02,'Крупные маркеры — средние; малые — 3 натурных повтора или seed 42/43/44. '
             'Усы — CI95 среднего только для натурного RPS.\n'
             'Натурная нагрузка 20 с, модельная 2 с. CPU/RSS модель не предсказывает.',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.11,1,.94))
    for extension in ('png','svg'):
        fig.savefig(assets/f'qa-aedes-holdout.{extension}',dpi=160,metadata={'Date':None} if extension=='svg' else None)
    plt.close(fig)
    metadata['chartCsvSha256']=digest(csv_path.read_bytes())
    metadata['profileSha256']=digest((evidence/'Aedes/profile/broker-calibration.v3.json').read_bytes())
    metadata['chartRows']=len(rows)
    dump(assets/'qa-aedes-holdout-sources.json',metadata)
    print('Built 3 actual-data configs and a 24-observation chart; no load executed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--archive',type=Path,required=True,help='Read-only Aedes-model-evidence.zip from evidence storage')
    args=parser.parse_args();build(args.root,args.archive)
