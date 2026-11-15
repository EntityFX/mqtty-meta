"""Publication figures and tables from the final 48+48 series only."""
import csv,json,hashlib
from collections import defaultdict
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'reports/audits/dissertation/article-1-fresh-validation-v1'
OUT=ROOT/'reports/audits/dissertation/article-1-final-v1'

def build():
    OUT.mkdir(exist_ok=False)
    conditions=list(csv.DictReader((SOURCE/'final-analysis/condition-metrics.csv').open(encoding='utf-8')))
    comparison=list(csv.DictReader((SOURCE/'final-native-vs-model.csv').open(encoding='utf-8')))
    phases=json.loads((SOURCE/'phase-evidence.json').read_text())
    replay=json.loads((SOURCE/'replay-summary.json').read_text())
    assert len(conditions)==64 and len(comparison)==192 and len(phases)==48
    assert replay['passed'] and len(replay['cases'])==4
    byrun=defaultdict(list)
    for row in comparison:byrun[(row['broker'],row['qos'],row['publishers'],row['seed'])].append(row['status'])
    groups=defaultdict(list)
    for key,values in byrun.items():groups[key[:3]].append(all(s=='passed' for s in values))
    full=sum(all(v) for v in groups.values());passed=sum(all(s=='passed' for s in v) for v in byrun.values())
    def number(v,digits=3):return f'{float(v):,.{digits}f}'.replace(',',' ').replace('.',',')
    lines=['| Брокер / QoS / отправители | RPS: реальный → MqttY | p99, мс: реальный → MqttY | Недоставка, %: реальный → MqttY |','| --- | ---: | ---: | ---: |']
    for key in sorted(groups):
        selected={r['metric']:r for r in conditions if (r['broker'],r['qos'],r['publishers'])==key}
        pairs=[]
        for metric,scale,digits in [('completedRpsInWindow',1,1),('p99PublishMs',1,3),('deliveryLossRate',100,2)]:
            row=selected[metric];pairs.append(number(float(row['nativeMean'])*scale,digits)+' → '+number(float(row['modelMean'])*scale,digits))
        lines.append('| '+' / '.join(key)+' | '+' | '.join(pairs)+' |')
    (OUT/'comparison-table.md').write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    fig,axes=plt.subplots(1,3,figsize=(13,4.3))
    colors={'Aedes':'#207c5a','Mosquitto':'#286cb8','ActiveMQ':'#c55036','EMQX':'#8451a1'}
    for ax,metric,title in zip(axes,['completedRpsInWindow','p99PublishMs','deliveryLossRate'],['Скорость публикаций, 1/с','Задержка p99, мс','Недоставка, %']):
        vals=[]
        for broker,color in colors.items():
            rows=[r for r in conditions if r['broker']==broker and r['metric']==metric];scale=100 if metric=='deliveryLossRate' else 1
            x=[float(r['nativeMean'])*scale for r in rows];y=[float(r['modelMean'])*scale for r in rows]
            vals+=x+y;ax.scatter(x,y,label=broker,color=color,s=45,alpha=.8)
        top=max(vals)*1.06 or 1;ax.plot([0,top],[0,top],color='#444',ls='--',lw=1,label='Совпадение')
        ax.set(xlim=(-top*.02,top),ylim=(-top*.02,top),xlabel='Реальный брокер',ylabel='MqttY',title=title);ax.grid(alpha=.2)
    axes[0].legend(fontsize=8);fig.suptitle('Финальная серия: средние значения по каждому из 16 режимов')
    fig.tight_layout();fig.savefig(OUT/'native-vs-model.svg');fig.savefig(OUT/'native-vs-model.png',dpi=150);plt.close(fig)
    chosen=next(r for r in phases if r['id']=='EMQX.q1.p16.r1')
    labels={'connect':'Подключение','subscribe':'Подписка','warmup':'Прогрев','measure':'Нагрузка','drain':'Ожидание'}
    stages=[p for p in chosen['manifest']['phases'] if p['name'] in labels]
    fig,axes=plt.subplots(2,1,figsize=(11,4.5),gridspec_kw={'height_ratios':[1,2]})
    palette=['#7866b4','#9679cb','#e5b64c','#377bbd','#38937b']
    for p,color in zip(stages,palette):
        axes[0].barh(0,(p['endTick']-p['startTick'])*1e-5,left=p['startTick']*1e-5,color=color,label=labels[p['name']])
    axes[0].set(xlabel='Модельное время, с',yticks=[]);axes[0].legend(ncol=5,loc='upper center',bbox_to_anchor=(.5,1.7),fontsize=9)
    axes[1].axis('off');table=[[labels[p['name']],f"{p['startTick']:,}–{p['endTick']:,}".replace(',',' '),f"{p['endTick']-p['startTick']:,}".replace(',',' '),f"{(p['endTick']-p['startTick'])*1e-5:.5f}".replace('.',',')] for p in stages]
    axes[1].table(cellText=table,colLabels=['Этап','Границы в тиках','Число тиков','Модельных секунд'],loc='center',cellLoc='center').scale(1,1.55)
    fig.suptitle('Пример: EMQX, QoS 1, 16 отправителей, seed 42. Один тик = 10 мкс',y=1.02)
    fig.tight_layout();fig.savefig(OUT/'stages.svg',bbox_inches='tight');fig.savefig(OUT/'stages.png',dpi=150,bbox_inches='tight');plt.close(fig)
    ranges={name:[min(p['endTick']-p['startTick'] for r in phases for p in r['manifest']['phases'] if p['name']==name),max(p['endTick']-p['startTick'] for r in phases for p in r['manifest']['phases'] if p['name']==name)] for name in labels}
    evidence=dict(fullConditions=full,totalConditions=len(groups),passedRuns=passed,totalRuns=len(byrun),passedMetricChecks=sum(r['status']=='passed' for r in comparison),totalMetricChecks=len(comparison),phaseTickRanges=ranges,replays=4,
        inputSha256={f:hashlib.sha256((SOURCE/f).read_bytes()).hexdigest() for f in ['final-summary.json','final-native-vs-model.csv','phase-evidence.json','replay-summary.json']})
    (OUT/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n');print(json.dumps(evidence,ensure_ascii=False))

if __name__=='__main__':build()
