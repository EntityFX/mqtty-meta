"""Draw the recorded native and model phases; never start an experiment."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    sources = {
        'native': ROOT/'reports/data/inputs/Aedes-calibration.json',
        'model': ROOT/'reports/data/model-evidence.json',
        'timing': ROOT/'reports/audits/dissertation/sensitivity-v1/plan.json',
    }
    raw = {key:p.read_bytes() for key,p in sources.items()}
    native = json.loads(raw['native'])
    model = next(r for r in json.loads(raw['model'])['Aedes'] if r['stage']=='holdout')['options']
    tick = json.loads(raw['timing'])['baselineTickUs'] / 1_000_000
    phases = {
        'Натурный запуск': [native['warmupSeconds'],native['measurementSeconds'],native['drainTimeoutSeconds']],
        'Запуск MqttY': [model['warmupTicks']*tick,model['measurementTicks']*tick,model['drainTimeoutTicks']*tick],
    }
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams.update({'svg.hashsalt':'article1-plain-v3','svg.fonttype':'none','font.family':'DejaVu Sans'})
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(11,4.7))
    labels=['Прогрев','Основная нагрузка','Ожидание завершения']
    colors=['#88b5dc','#4a9b78','#e6bd69']
    for ax,(name,durations) in zip(axes,phases.items()):
        start=0
        for duration,label,color in zip(durations,labels,colors):
            ax.barh(0,duration,left=start,height=.55,color=color,edgecolor='white')
            suffix=' — максимум' if label==labels[2] else (' — задано' if name=='Натурный запуск' and label==labels[1] else '')
            ax.text(start+duration/2,0,f'{label}\n{duration:g} с'+suffix,ha='center',va='center',fontsize=9)
            start+=duration
        ax.set(xlim=(0,start),ylim=(-.6,.6),yticks=[],title=name,
               xlabel='Реальные секунды' if name=='Натурный запуск' else 'Модельные секунды')
        ax.grid(axis='x',alpha=.2)
    fig.suptitle('После подготовки и отдельного пробного запуска',fontsize=12)
    fig.tight_layout(rect=(0,.045,1,.94))
    fig.text(.5,.015,'Оси имеют разный масштаб. Ожидание может завершиться раньше; это не график ускорения.',ha='center',fontsize=9)
    out=ROOT/'reports/audits/dissertation/article-1-plain-v3';out.mkdir(parents=True,exist_ok=True)
    fig.savefig(out/'timeline.svg',metadata={'Date':None})
    fig.savefig(out/'timeline.png',dpi=140)
    plt.close(fig)
    svg=out/'timeline.svg'
    svg.write_bytes(('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n').encode('utf-8'))
    proof={'sources':{sources[k].relative_to(ROOT).as_posix():hashlib.sha256(v).hexdigest() for k,v in raw.items()},
           'phasesSeconds':phases,'modelTickSeconds':tick,
           'generatorSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [svg,out/'timeline.png']}}
    (out/'timeline-manifest.json').write_bytes((json.dumps(proof,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    print(json.dumps(phases,ensure_ascii=False))


if __name__=='__main__':
    main()
