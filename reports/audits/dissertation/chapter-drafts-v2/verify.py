"""Recount saved chapter tables; never execute or modify an experiment."""
from pathlib import Path
import csv,hashlib,json,re,subprocess

ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
BASE='f4bbb1f'
checks=[]
sources={}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def source(rel):
    p=ROOT/rel;sources[rel]=sha(p);return p
def data(rel):return json.loads(source(rel).read_text(encoding='utf-8'))
def rows(rel):return list(csv.DictReader(source(rel).open(encoding='utf-8-sig')))
def check(ok,label):
    if not ok:raise AssertionError(label)
    checks.append(label)
def num(v):return float(v.replace(' ','').replace(',','.'))
drafts=ROOT/'docs/dissertation/drafts'
c2=(drafts/'chapter-2-measurement.md').read_text(encoding='utf-8')
c3=(drafts/'chapter-3-relay.md').read_text(encoding='utf-8')
c4=(drafts/'chapter-4-validation.md').read_text(encoding='utf-8')
native={(r['broker'],r['metric']):r for r in rows('reports/audits/dissertation/article-1-matched-main-final-v1/native-model.csv')}
seen2=seen4=0
for text,chapter in [(c2,2),(c4,4)]:
    section=text.split('## 2.4.',1)[1].split('## 2.5.',1)[0] if chapter==2 else text.split('## 4.5.',1)[1].split('## 4.6.',1)[0]
    for line in section.splitlines():
        cells=[v.strip() for v in line.strip('|').split('|')]
        if not line.startswith('| ') or cells[0] not in ('Aedes','Mosquitto','ActiveMQ Classic','EMQX'):continue
        # Ignore version and calibration-floor tables.
        if len(cells)!=(5 if chapter==2 else 7):continue
        broker=cells[0].replace(' Classic','')
        metrics=[('rps','nativeMean',1,.0051),('p99','nativeMean',2,.000051),('loss','nativeMean',3,.00000051),('failure','nativeMean',4,.00000051)] if chapter==2 else [('rps','nativeMean',1,.0051),('rps','modelMean',2,.0051),('p99','nativeMean',3,.000051),('p99','modelMean',4,.000051),('loss','nativeMean',5,.00000051),('loss','modelMean',6,.00000051)]
        # Version table has text in the RPS column; only the numeric result table applies.
        try:num(cells[1])
        except ValueError:continue
        for metric,field,index,tol in metrics:
            value=num(cells[index]);value=value/100 if metric in ('loss','failure') else value
            check(abs(value-float(native[(broker,metric)][field]))<=tol,f'chapter{chapter} {broker} {metric} {field}')
        if chapter==2:seen2+=1
        else:seen4+=1
check((seen2,seen4)==(4,4),'both native/model tables contain exactly four brokers')
stats=data('reports/audits/dissertation/network-main-final-v1/raw-recount/condition-stats.json')
groups={(r['topology'],r['qos'],r['offeredRps'],r['mode']):r for r in stats}
names={'Линия':'line','Кольцо':'ring','Звезда с транзитом':'star'}
seen=0
for line in c3.splitlines():
    cells=[v.strip() for v in line.strip('|').split('|')]
    if len(cells)!=7 or cells[0] not in names:continue
    topology=names[cells[0]];qos=int(cells[1]);rate=int(cells[2])
    for mode,index in [('single',3),('relay',4),('federation',5)]:
        g=groups[(topology,qos,rate,mode)]
        check(g['matchedSeeds']==[42,43,44],f'{topology}/{qos}/{rate}/{mode} seeds')
        check(abs(num(cells[index])-(1-g['metrics']['deliveryLoss']['mean'])*100)<=.00051,f'{topology}/{qos}/{rate}/{mode} completeness')
    check(abs(num(cells[6])-groups[(topology,qos,rate,'federation')]['metrics']['receiverP99Ms']['mean'])<=.051,f'{topology}/{qos}/{rate} receiver p99')
    seen+=1
check(seen==18,'full 18-condition network table')
na=0
for g in stats:
    for name,v in g['metrics'].items():
        if v['n']==0:
            check(g['qos']==0 and name=='publishP99Ms' and v['mean'] is None,'unavailable publication p99 retained as unavailable');na+=1
        else:check(v['n']==3 and v['min']==v['max'],'observed ideal-seed equality for '+name)
check(na==18,'18 unavailable p99 groups, not observations of zero spread')
transit=rows('reports/audits/dissertation/network-main-final-v1/transit.csv')
for mode in ('single','relay','federation'):
    chosen=[r for r in transit if r['id'].startswith('matrix.star.'+mode+'.q2.r5000.')]
    check(len(chosen)==3,mode+' transit three runs')
    line=next(l for l in c3.splitlines() if l.startswith('| '+mode+' |'))
    cells=[v.strip() for v in line.strip('|').split('|')]
    for i,key in enumerate(('inboundBytes','transferredPackets','refused'),1):
        check(num(cells[i])==sum(float(r[key]) for r in chosen)/3,mode+' transit '+key)
floor=data('reports/audits/dissertation/manuscript-offline-review-v1/floor-analysis.json')
floor_table=source('reports/audits/dissertation/manuscript-offline-review-v1/Table.md').read_text(encoding='utf-8').strip()
check(floor_table in c4,'weighted floor table copied without numeric change')
for row in floor['rows']:check(row['zeroResidual']==row['belowFloor']+row['atFloor'],row['broker']+' clipping boundary distinguished')
phases=rows('reports/audits/dissertation/article-1-matched-main-final-v1/phase-ticks.csv')
durations={}
for r in phases:durations[r['job']]=durations.get(r['job'],0)+int(r['ticks'])
check(len(durations)==16 and min(durations.values())==1000014 and max(durations.values())==1000018,'16 actual execution durations including repeats')
accepted=data('reports/audits/dissertation/network-main-final-v1/acceptance.json')
check((accepted['matrixRawPassed'],accepted['replaysRawPassed'],accepted['warningMatrixRuns'],accepted['warningReplayRuns'],accepted['replayScientificFileComparisons'])==(162,6,27,2,36),'network coverage/warnings/replay scope')
protected=data('reports/audits/dissertation/chapter-drafts-v2/source-before.json')['protectedFiles']
for rel,h in protected.items():
    check(sha(ROOT/rel)==h,'LaTeX unchanged since intake '+rel)
    tracked=subprocess.check_output(['git','show','HEAD:'+rel.split('/',1)[1]],cwd=ROOT/'diss')
    check(tracked.replace(b'\r\n',b'\n')==(ROOT/rel).read_bytes().replace(b'\r\n',b'\n'),'LaTeX tracked content matches after checkout line-ending normalization '+rel)
outputs=list(drafts.glob('*.md'))+[ROOT/'docs/dissertation/chapter-refresh-plan.md',ROOT/'docs/dissertation/claim-evidence-map.md',ROOT/'reports/analysis/Dissertation-evidence-integration.md']
claim_text=(ROOT/'docs/dissertation/claim-evidence-map.md').read_text(encoding='utf-8')
check('162/162 raw прошли' in claim_text and '27 основных' in claim_text,'claims map updated to accepted complete network')
check('106/162 raw прошли' not in claim_text and 'Нет звезды' not in claim_text,'obsolete coverage limitations removed from current claims map')
tex1=(ROOT/'diss/Dissertation/chapter1.tex').read_text(encoding='utf-8-sig')
tex4=(ROOT/'diss/Dissertation/chapter4.tex').read_text(encoding='utf-8-sig')
check('sec:ch1/sec3' in tex1 and 'sec:ch1/sec5' in tex1,'integration map uses actual chapter 1 labels')
check(tex4.count('label{sec:ch4/sec4}')==2,'existing duplicate chapter 4 label recorded without modifying LaTeX')
for p in outputs:
    text=p.read_text(encoding='utf-8')
    check('\ufffd' not in text,p.name+' no replacement characters')
    for href in re.findall(r'\]\(([^)]+)\)',text):
        if '://' in href or href.startswith('#'):continue
        target=(p.parent/href.split('#')[0]).resolve()
        check(target.exists() or target==OUT/'verification.json',p.name+' local link '+href)
for rel in list(sources):
    if rel.startswith('reports/audits/dissertation/chapter-drafts-v2/'):continue
    check(subprocess.check_output(['git','show',BASE+':'+rel],cwd=ROOT)==(ROOT/rel).read_bytes(),'accepted source unchanged '+rel)
calc=data('reports/audits/dissertation/manuscript-offline-review-v1/progress-recalculation.json')
check(calc['creditedPersonDays']==25 and calc['baselinePersonDays']==47,'no extra credit for draft-only preparation')
result=dict(status='verifiedDrafts',checksPassed=len(checks),checks=checks,sourceSha256=sources,outputSha256={p.relative_to(ROOT).as_posix():sha(p) for p in outputs},protectedLaTexFiles=6,newExperiments=0,linuxPolled=False,originalLaTexModified=False,steps30_31_32_34Credited=False,globalPercent=53.2,localPercent=80,humanReviewCompleted=False,pdfAssessed=False,submissionReady=False,reviewScope='Bounded self-review of Markdown drafts, tables and evidence; no full-PDF or human-scientific review')
(OUT/'verification.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
print('PASS:',len(checks),'saved-data checks; no new simulation or LaTeX edit')
