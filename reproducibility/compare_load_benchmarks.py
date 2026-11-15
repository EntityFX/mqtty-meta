"""Recompute three-host workload comparison from retrieved, independently verified evidence."""
import argparse
import csv
import math
from pathlib import Path
import shutil
import statistics as st

import benchmark as b


def median(values):
    values=sorted(values)
    if len(values)%2!=1:raise ValueError('Odd number of repetitions required')
    return values[len(values)//2]


def build(base, output):
    output.mkdir(parents=True,exist_ok=True)
    hosts=('local-i7','windows-xeon','linux-e2k')
    result=dict(source='Raw attempts.json; seven counted pairs per cell; pilot/control excluded from timing estimates',
                measurement='Stopwatch sum strictly inside graph.Refresh during measure; whole-process perf_counter as secondary',
                speedupFormula='median(serial seconds) / median(parallel seconds)', rows=[],controls=[],hosts={})
    reference={};app=None;configs=None;cross_checked=0
    for host in hosts:
        parent=base/host;root=parent/'data';state=b.read(root/'attempts.json');protocol=b.read(root/'protocol.json')
        verified=b.read(parent/'verification-local.json')
        assert not verified['scienceMismatches'] and not verified['arithmeticFailures']
        assert not verified['controllerDisagreements']
        assert verified['status']==('DIAGNOSTIC' if host=='local-i7' else 'PASS')
        if app is None:app=protocol['appSha256'];configs=protocol['configSha256']
        assert protocol['appSha256']==app and all(configs[n]==h for n,h in protocol['configSha256'].items())
        selected=[r for r in state['rows'] if r['verified']]
        assert len(selected)==140
        for row in selected:
            reference.setdefault(row['cell'],row['fingerprint'])
            assert row['fingerprint']==reference[row['cell']],(host,row['name'])
            cross_checked+=1
        cells=sorted({r['cell'] for r in selected},key=lambda n:(int(n.split('-')[0][1:]),int(n.split('-')[1][1:])))
        assert len(cells)==8
        cached={r['name']:r for r in b.read(root/'analysis.json')['rows']}
        for cell in cells:
            meta=next(c for c in protocol['cells'] if c['name']==cell)
            entry=dict(host=host,cell=cell,backends=meta['backends'],rpsPerBackend=meta['rpsPerBackend'],offeredRps=meta['offeredRps'])
            for mode in ('serial','parallel'):
                rows=sorted([r for r in state['rows'] if r['phase']=='main' and r['cell']==cell and r['execution']==mode],key=lambda r:r['repeat'])
                assert [r['repeat'] for r in rows]==list(range(1,8)) and all(r['verified'] for r in rows)
                entry[mode]={}
                for key in ('measureRefreshSeconds','measurePhaseSeconds','wallSeconds','runnerSeconds','exportSeconds','fullRefreshSeconds'):
                    values=[r[key] for r in rows]
                    entry[mode][key]=dict(values=values,median=median(values),min=min(values),max=max(values))
                for name,values in [('cpuSeconds',[r['userSeconds']+r['systemSeconds'] for r in rows]),
                                    ('peakMemoryMiB',[r['peakMemoryBytes']/1024**2 for r in rows])]:
                    entry[mode][name]=dict(values=values,median=median(values),min=min(values),max=max(values))
            for name,key in [('refreshSpeedup','measureRefreshSeconds'),('processSpeedup','wallSeconds')]:
                entry[name]=entry['serial'][key]['median']/entry['parallel'][key]['median']
                entry[name+'Pairs']=[s/p for s,p in zip(entry['serial'][key]['values'],entry['parallel'][key]['values'])]
                entry[name+'PairsFaster']=sum(v>1 for v in entry[name+'Pairs'])
                assert math.isclose(entry[name],cached[cell][name],rel_tol=1e-12)
            entry['cpuRatioParallelToSerial']=entry['parallel']['cpuSeconds']['median']/entry['serial']['cpuSeconds']['median']
            entry['memoryRatioParallelToSerial']=entry['parallel']['peakMemoryMiB']['median']/entry['serial']['peakMemoryMiB']['median']
            result['rows'].append(entry)
        for mode in ('serial','parallel'):
            control=[r for r in state['rows'] if r['phase']=='control' and r['execution']==mode]
            assert len(control)==6 and all(r['verified'] for r in control)
            plain=[next(r['wallSeconds'] for r in control if r['repeat']==n and not r['timed']) for n in range(1,4)]
            timed=[next(r['wallSeconds'] for r in control if r['repeat']==n and r['timed']) for n in range(1,4)]
            result['controls'].append(dict(host=host,execution=mode,plainProcessSeconds=plain,timedProcessSeconds=timed,
                 ratioMediansTimedToPlain=median(timed)/median(plain),pairedTimedToPlain=[t/p for t,p in zip(timed,plain)]))
        result['hosts'][host]=dict(mqttyCommit=protocol['mqttyCommit'],protocolSha256=b.digest(root/'protocol.json'),
                retrieval=b.read(parent/'retrieval.json'),verificationStatus=verified['status'],attempts=verified['attemptsChecked'],
                sealedFiles=verified['sealedFilesChecked'],timerSidecars=verified['timerSidecarsChecked'],environment=b.read(root/'environment.json'))
        for name,source in [('attempts.json',root/'attempts.json'),('protocol.json',root/'protocol.json'),
                            ('verification.json',parent/'verification-local.json'),('retrieval.json',parent/'retrieval.json'),
                            ('environment.json',root/'environment.json')]:shutil.copyfile(source,output/(host+'-'+name))
    result['verification']=dict(totalAttempts=sum(h['attempts'] for h in result['hosts'].values()),
         successfulScienceComparedAcrossHosts=cross_checked,mainAttempts=336,controls=36,successfulPilots=48,
         excludedLocalPilots=2,sealedFiles=sum(h['sealedFiles'] for h in result['hosts'].values()),
         timerSidecars=sum(h['timerSidecars'] for h in result['hosts'].values()),sharedCells=8,identicalApplicationFiles=True)
    result['limits']=['Ideal simulated brokers, QoS 1 and round-robin only; not real-broker throughput.',
         'Payload 256 bytes; 16 publishers per backend; 2000 measured ticks, 0.2 simulated seconds. Startup/JIT may still occur within measured Refresh.',
         'Separate ordinary workstations; OS, runtime versions and CPU differ; no CPU-only causal attribution.',
         'Seven pairs per cell; all outliers retained. Pair ranges are observed variation, not confidence intervals; no significance or universal speedup claim.',
         'Per-process CPU and peak memory cover the full process, not just Refresh; Windows working set and Linux RSS differ.',
         'Local overloaded b64-r4000 excluded before remote dispatch; its two failed pilots are retained and not treated as performance successes.',
         'Three timer on/off pairs per mode test science and rough whole-process perturbation, not precise timer cost.',
         'Separate federation/16 series has varying delivery ticks; these load-balancer results do not establish correctness of every parallel topology.',
         'No Engine optimization performed and no new simulation executed for this report.']
    b.save(output/'comparison.json',result)
    columns=['host','cell','backends','rpsPerBackend','offeredRps','serialRefreshSeconds','parallelRefreshSeconds','refreshSpeedup',
             'refreshPairsFaster','serialProcessSeconds','parallelProcessSeconds','processSpeedup','serialCpuSeconds','parallelCpuSeconds','cpuRatioParallelToSerial',
             'serialPeakMemoryMiB','parallelPeakMemoryMiB']
    with (output/'comparison.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for r in result['rows']:
            line={k:r[k] for k in ('host','cell','backends','rpsPerBackend','offeredRps','refreshSpeedup','processSpeedup','cpuRatioParallelToSerial')}
            line['refreshPairsFaster']=r['refreshSpeedupPairsFaster']
            for mode in ('serial','parallel'):
                for label,key in [('RefreshSeconds','measureRefreshSeconds'),('ProcessSeconds','wallSeconds'),('CpuSeconds','cpuSeconds'),('PeakMemoryMiB','peakMemoryMiB')]:line[mode+label]=r[mode][key]['median']
            writer.writerow(line)
    return result


def plot(output,result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    cells=[r['cell'] for r in result['rows'] if r['host']=='local-i7']
    fig,axes=plt.subplots(1,2,figsize=(13.5,8.7),sharey=True,dpi=180)
    styles=[('local-i7','Windows / i7','#24539D',-.22,'o'),('windows-xeon','Windows / Xeon','#BF5E1E',0,'s'),('linux-e2k','Linux / Эльбрус','#22745B',.22,'^')]
    for ax,field,title in zip(axes,('refreshSpeedup','processSpeedup'),('Только Refresh измерительной фазы','Весь процесс, включая JIT и экспорт')):
        for host,label,color,offset,marker in styles:
            for y,cell in enumerate(cells):
                r=next(r for r in result['rows'] if r['host']==host and r['cell']==cell)
                pairs=r[field+'Pairs']
                ax.plot([min(pairs),max(pairs)],[y+offset]*2,color=color,lw=.8,alpha=.4)
                ax.scatter(pairs,[y+offset+(i-3)*.018 for i in range(7)],color=color,marker=marker,s=19,alpha=.6)
                ax.scatter([r[field]],[y+offset],marker='D',s=48,color=color,edgecolors='black',linewidths=.4,zorder=4)
        ax.axvline(1,color='#555',ls='--',lw=1)
        ax.set_title(title,fontsize=12,pad=15)
        ax.set_xlabel('Время serial / время parallel, ×',fontsize=10)
        ax.grid(axis='x',alpha=.2);ax.spines[['top','right']].set_visible(False)
    axes[0].set_yticks(range(8),[f"{c.split('-')[0][1:]} backend · {c.split('-')[1][1:]}/с" for c in cells])
    axes[0].set_ylim(7.55,-.55)
    # Common range includes every observed pair, without clipping outliers.
    values=[v for r in result['rows'] for f in ('refreshSpeedupPairs','processSpeedupPairs') for v in r[f]]
    from matplotlib.ticker import FixedLocator, FuncFormatter
    lo,hi=min(values),max(values)
    for ax in axes:
        ax.set_xscale('log');ax.set_xlim(lo*.85,hi*1.15)
        ax.xaxis.set_major_locator(FixedLocator([.2,.3,.5,.75,1,1.5,2,3,5,8]))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value,_:f'{value:g}'))
        ax.minorticks_off()
    fig.suptitle('MqttY: выигрыш параллельного исполнения зависит от графа и среды',fontsize=15,y=.97)
    handles=[Line2D([0],[0],marker=m,color=c,ls='',label=l,markersize=7) for _,l,c,_,m in styles]
    handles.append(Line2D([0],[0],marker='D',color='#555',ls='',label='Отношение медиан',markersize=7))
    fig.legend(handles=handles,loc='lower center',ncol=4,frameon=False,bbox_to_anchor=(.52,.055))
    fig.text(.5,.025,'По 7 пар; все выбросы сохранены. Ось X логарифмическая. Правее 1 — parallel быстрее. Стратегия 0; идеальная модель.',ha='center',fontsize=9)
    fig.subplots_adjust(left=.16,right=.985,top=.87,bottom=.14,wspace=.12)
    for suffix in ('png','svg'):fig.savefig(output/('performance-comparison.'+suffix),facecolor='white')
    plt.close(fig)
    # Single-panel figure keeps labels readable at manuscript page width.
    fig,ax=plt.subplots(figsize=(7.6,7.4),dpi=180)
    for host,label,color,offset,marker in styles:
        for y,cell in enumerate(cells):
            r=next(r for r in result['rows'] if r['host']==host and r['cell']==cell)
            pairs=r['refreshSpeedupPairs']
            ax.plot([min(pairs),max(pairs)],[y+offset]*2,color=color,lw=.8,alpha=.5)
            ax.scatter(pairs,[y+offset+(i-3)*.018 for i in range(7)],color=color,marker=marker,s=17,alpha=.65)
            ax.scatter([r['refreshSpeedup']],[y+offset],marker='D',s=40,color=color,edgecolors='black',linewidths=.4,zorder=4)
    ax.set_xscale('log');ax.set_xlim(lo*.85,hi*1.15)
    ax.xaxis.set_major_locator(FixedLocator([.2,.3,.5,.75,1,1.5,2,3,5,8]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value,_:f'{value:g}'));ax.minorticks_off()
    ax.set_yticks(range(8),[f"{c.split('-')[0][1:]} / {c.split('-')[1][1:]}" for c in cells])
    ax.set_ylim(7.55,-.55);ax.axvline(1,color='#555',ls='--',lw=1)
    ax.set_ylabel('Backend / публикаций·с⁻¹ на backend',fontsize=10)
    ax.set_xlabel('Время Refresh serial / parallel, × (логарифмическая ось)',fontsize=10)
    ax.set_title('Стоимость исполнения контрольной модели MqttY',fontsize=12,pad=15)
    ax.grid(axis='x',alpha=.2);ax.spines[['top','right']].set_visible(False)
    fig.legend(handles=handles[:3],loc='lower center',ncol=3,frameon=False,bbox_to_anchor=(.54,.052),fontsize=9)
    fig.text(.52,.025,'По 7 пар; ромб — отношение медиан; отрезок — min–max пар. Стратегия 0.',ha='center',fontsize=8.5)
    fig.subplots_adjust(left=.15,right=.98,top=.89,bottom=.17)
    for suffix in ('png','svg'):fig.savefig(output/('article-refresh-comparison.'+suffix),facecolor='white')
    plt.close(fig)
    for name in ('performance-comparison.svg','article-refresh-comparison.svg'):
        path=output/name
        path.write_text('\n'.join(line.rstrip() for line in path.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8',newline='\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=build(args.base.resolve(),args.output.resolve());plot(args.output,result)
    print(result['verification'])
