"""Detached campaign worker: execute frozen schedule, verify offline, plot and archive.

Launch with a hidden window and redirected logs on Windows. Does not contact remote hosts.
"""
import argparse
import csv
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time

import benchmark as b


def plot(root):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import statistics
    data=b.read(root/'analysis.json')
    fig,axes=plt.subplots(1,2,figsize=(10.5,5),sharey=True,dpi=180)
    for count,color,marker in ((4,'#24539D','o'),(16,'#BF5E1E','s'),(64,'#22745B','^')):
        rows=sorted((r for r in data['rows'] if r['backends']==count),key=lambda r:r['rpsPerBackend'])
        for ax,field,title in zip(axes,('refreshSpeedup','processSpeedup'),('Только Refresh измерительной фазы','Весь процесс: JIT, построение, экспорт')):
            points=[r[field] if r['validated'] else float('nan') for r in rows]
            ax.plot(range(3),points,marker=marker,color=color,label='%d backend'%count)
            for x,r in enumerate(rows):
                if r['validated']:
                    key='measureRefreshSeconds' if field=='refreshSpeedup' else 'wallSeconds'
                    pairs=[s/p for s,p in zip(r['serial'][key],r['parallel'][key])]
                    # Arrays retain the same repeat order in both modes.
                    ax.scatter([x+(n-3)*.014 for n in range(7)],pairs,s=12,color=color,alpha=.3)
            ax.set_title(title,fontsize=11)
            ax.set_xticks(range(3),['250','1000','4000'])
            ax.set_xlabel('Публикаций/с на backend')
            ax.axhline(1,color='#555',ls='--',lw=1)
            ax.set_ylim(bottom=0);ax.grid(axis='y',alpha=.25)
            ax.spines[['top','right']].set_visible(False)
    axes[0].set_ylabel('Время serial / время parallel, ×')
    axes[1].legend(frameon=False)
    fig.suptitle('Load-balancer: размер графа и нагрузка, локальная i7',fontsize=13)
    fig.text(.5,.025,'Линии — отношение медиан; бледные точки — 7 пар. Выше 1 — parallel быстрее. Стратегия 0.',ha='center',fontsize=9)
    fig.subplots_adjust(left=.075,right=.98,top=.83,bottom=.18,wspace=.14)
    fig.savefig(root/'load-scaling.png',facecolor='white');plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--plot-packages',type=Path)
    parser.add_argument('--deliver',type=Path,help='Durable local destination for completed archive and derived files')
    args=parser.parse_args();root=args.root.resolve()
    status=dict(status='running',pid=os.getpid(),startedEpoch=time.time(),
                protocolSha256=b.digest(root/'protocol.json'),stage='main',mainWallLimitSeconds=7200)
    b.save(root/'campaign-status.json',status)
    scripts=Path(__file__).resolve().parent
    try:
        for stage,argv in [('main',[str(scripts/'load_benchmark.py'),'--mode','main','--root',str(root)]),
                           ('analysis',[str(scripts/'load_benchmark.py'),'--mode','analyze','--root',str(root)]),
                           ('offline-verification',[str(scripts/'verify_load_benchmark.py'),str(root),'--output',str(root/'verification.json')])]:
            status['stage']=stage;b.save(root/'campaign-status.json',status)
            process=subprocess.Popen([sys.executable]+argv,start_new_session=os.name!='nt')
            try:
                code=process.wait(timeout=7200 if stage=='main' else 600)
            except subprocess.TimeoutExpired:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                else:os.killpg(process.pid,signal.SIGKILL)
                process.wait()
                raise TimeoutError(stage+' exceeded worker wall-clock limit')
            if code:raise subprocess.CalledProcessError(code,process.args)
        analysis=b.read(root/'analysis.json')
        import statistics
        with (root/'summary.csv').open('w',encoding='utf-8',newline='') as stream:
            writer=csv.writer(stream)
            writer.writerow(['backends','rpsPerBackend','offeredRps','validated','serialRefreshSeconds','parallelRefreshSeconds','refreshSpeedup','serialProcessSeconds','parallelProcessSeconds','processSpeedup'])
            for r in analysis['rows']:
                med=lambda mode,key:statistics.median(r[mode][key]) if r[mode][key] else None
                writer.writerow([r['backends'],r['rpsPerBackend'],r['offeredRps'],r['validated'],med('serial','measureRefreshSeconds'),med('parallel','measureRefreshSeconds'),r['refreshSpeedup'],med('serial','wallSeconds'),med('parallel','wallSeconds'),r['processSpeedup']])
        status['stage']='plot-and-archive';b.save(root/'campaign-status.json',status)
        if args.plot_packages:sys.path.insert(0,str(args.plot_packages.resolve()))
        plot(root)
        names=['protocol.json','admission.json','attempts.json','analysis.json','verification.json','environment.json','summary.csv','load-scaling.png','configs','attempts','controller','source.tar','package/app','package/probe']
        files=[p for name in names for p in ([root/name] if (root/name).is_file() else (root/name).rglob('*')) if p.is_file() and '__pycache__' not in p.parts and 'obj' not in p.parts and 'bin' not in p.parts]
        b.save(root/'export-sha256.json',{p.relative_to(root).as_posix():b.digest(p) for p in files})
        archive=root/'load-benchmark-results.tar.gz'
        with tarfile.open(archive,'w:gz') as output:
            for p in files:output.add(p,arcname=p.relative_to(root).as_posix(),recursive=False)
            output.add(root/'export-sha256.json',arcname='export-sha256.json')
        b.save(root/'collection-receipt.json',dict(archive=archive.name,archiveSha256=b.digest(archive),archiveBytes=archive.stat().st_size,
                verificationStatus=b.read(root/'verification.json')['status'],scientificallyValidCells=sum(r['validated'] for r in analysis['rows']),
                chartVisualQA='Pending human/agent collection; chart generated but not yet visually inspected',
                manuscriptsEdited=False,remoteRunsStarted=0))
        if args.deliver:
            delivery=args.deliver.resolve();delivery.mkdir(parents=True,exist_ok=True)
            for name in ('load-benchmark-results.tar.gz','collection-receipt.json','protocol.json','admission.json','analysis.json','verification.json','summary.csv','load-scaling.png'):
                with (root/name).open('rb') as source,(delivery/name).open('xb') as target:shutil.copyfileobj(source,target)
                if b.digest(delivery/name)!=b.digest(root/name):raise ValueError('Delivery checksum: '+name)
            status['delivery']=str(delivery)
        status['status']='completed' if b.read(root/'verification.json')['status']=='PASS' else 'completed-with-diagnostic'
        status['stage']='finished'
    except Exception as error:
        status.update(status='failed',error=str(error))
        raise
    finally:
        status['finishedEpoch']=time.time();b.save(root/'campaign-status.json',status)


if __name__=='__main__':main()
