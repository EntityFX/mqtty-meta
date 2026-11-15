"""Build and verify the article handoff; no experiments or external publication."""
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[4]
AUDIT=Path(__file__).resolve().parent
OUT=Path('C:/temp/mqtty-evidence/article-portable-v4')
checks=[]
def check(value,label):
    if not value:raise AssertionError(label)
    checks.append(label)
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def read(path):return json.loads((ROOT/path).read_text(encoding='utf-8'))
def csv_rows(path):
    with (ROOT/path).open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))
def dec(value,n=3):return f'{value:.{n}f}'.replace('.',',')

articles=[ROOT/'docs/dissertation/articles'/name for name in ('article-1-twin-validation.md','article-2-relay-federation.md')]
twin=read('reports/audits/dissertation/article-1-matched-main-final-v1/verification.json')
network=read('reports/audits/dissertation/network-main-final-v1/acceptance.json')
check(twin['jobs']==40 and twin['allCriteriaPassedRuns']==12 and len(twin['replays'])==4,'40 main jobs; 12 passed models; four repeats')
check(network['matrixRawPassed']==162 and network['replaysRawPassed']==6 and network['matrixRawFailed']==0,'162 network cases and six repeats')
text1=articles[0].read_text(encoding='utf-8');text2=articles[1].read_text(encoding='utf-8')
for article,heading,count in [(articles[0],'## Литература',8),(articles[1],'## Источники и данные',6)]:
    tail=article.read_text(encoding='utf-8').split(heading)[-1]
    numbers=[int(n) for n in re.findall(r'^(\d+)\. ',tail,re.M)]
    check(numbers==list(range(1,count+1)),'bibliography numbering retained '+article.name)

for r in csv_rows('reports/audits/dissertation/article-1-matched-main-final-v1/native-model.csv'):
    metric=r['metric'];fraction=metric in ('failure','loss');scale=100 if fraction else 1
    label={'rps':'Завершений/с','p99':'p99, мс','failure':'Ошибки, %','loss':'Недоставка, %'}[metric]
    values=[dec(float(r[k])*scale,4) for k in ('nativeMean','calibrationMean','modelMean')]
    errors=[dec(float(r[k])*100)+(' п.п.' if fraction else '%') for k in ('calibrationMeanError','errorOfMean')]
    row='| '+' | '.join([r['broker'],label,*values,*errors])+' |'
    check(row in text1,'article1 numeric row '+r['broker']+'/'+metric)
for broker in ('Aedes','Mosquitto','ActiveMQ','EMQX'):
    rows=[r for r in twin['modelComparisons'] if r['broker']==broker]
    maxima=[dec(max(r['errors'][k] for r in rows)*100) for k in ('rps','p99','loss')]
    label='ActiveMQ Classic' if broker=='ActiveMQ' else broker
    check(f'| {label} | {maxima[0]}% | {maxima[1]}% | {maxima[2]} | 3 из 3 |' in text1,'article1 worst errors '+broker)
net=csv_rows('reports/audits/dissertation/network-main-final-v1/raw-recount/comparison.csv')
for topology,label in [('line','Линия'),('ring','Кольцо'),('star','Звезда с транзитом')]:
    for qos in range(3):
        for rate in (1000,5000):
            selected={r['mode']:r for r in net if r['topology']==topology and int(r['qos'])==qos and int(r['offeredRps'])==rate}
            check(len(selected)==3,'three matched modes')
            check(all(int(selected[m]['matchedSeedCount'])==3 for m in selected),'three seeds per mode')
            check(all(float(selected[m]['deliveryLoss'])==0 for m in ('single','relay')),'central and relay full delivery')
            row=f'| {label} | {qos} | {rate} | 3 | '+dec(100*(1-float(selected['federation']['deliveryLoss'])))+' | '+' | '.join(dec(float(selected[m]['receiverP99Ms'])) for m in ('single','relay','federation'))+' |'
            check(row in text2,'article2 main comparison '+topology+'/'+str(qos)+'/'+str(rate))
protected=read('reports/audits/dissertation/chapter-drafts-v2/source-before.json')['protectedFiles']
for name,digest in protected.items():check(sha(ROOT/name)==digest,'protected LaTeX '+name)
weights=read('reports/audits/dissertation/manuscript-offline-review-v1/progress-recalculation.json')['steps']
check(sum(r['personDays'] for r in weights)==47 and sum(r['personDays'] for r in weights if r['technicallyCredited'])==25,'unchanged 25/47 weights')

previous=read('reports/audits/dissertation/manuscript-offline-review-v1/portable-v3.json')
check(sha(previous['zip'])==previous['zipSha256'],'authentic portable v3 archive')
check((ROOT/'reports/audits/dissertation/articles-reading-20261030/independent-ai-review.md').is_file(),'separate bounded review is available')
OUT.mkdir();bundle=OUT/'bundle';bundle.mkdir()
with zipfile.ZipFile(previous['zip']) as archive:
    check(all(not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts for n in archive.namelist()),'archive names confined')
    archive.extractall(bundle)
old=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
for name,digest in old['files'].items():check(sha(bundle/name)==digest,'v3 member '+name)
def copy(path):
    path=Path(path);relative=path.relative_to(ROOT);target=bundle/relative
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(path.read_bytes())
for article in articles:
    copy(article)
    # Include immediate local dependencies. Additional historical links remain repository-based.
    for link in re.findall(r'\]\(([^)]+)\)',article.read_text(encoding='utf-8')):
        if '://' not in link and not link.startswith('#'):
            path=(article.parent/link.split('#')[0]).resolve()
            check(path.exists(),'article local link '+link)
            if path.is_file():copy(path)
extra=[ROOT/'docs/dissertation/reproduction-handoff.md',AUDIT/'Reader-guide.md',
       ROOT/'reports/audits/dissertation/article-1-matched-main-final-v1/verification.json',
       ROOT/'reports/audits/dissertation/network-main-final-v1/acceptance.json',
       ROOT/'reports/audits/dissertation/network-main-final-v1/transit.csv',
       ROOT/'reports/audits/dissertation/article-1-main-execution-v1/runtime-manifest.json',
       ROOT/'reports/analysis/Articles-review-handoff.md']
extra+=list((ROOT/'reports/audits/web/native-control-v1').rglob('*'))
extra+=list((ROOT/'reports/audits/dissertation/articles-reading-20261030').rglob('*.md'))
for path in extra:
    if path.is_file():copy(path)
(bundle/'README.txt').write_bytes((AUDIT/'Reader-guide.md').read_bytes())
files={p.relative_to(bundle).as_posix():sha(p) for p in sorted(bundle.rglob('*')) if p.is_file() and p.name!='manifest.json'}
# Keep nested experiment manifests in the seal as well.
for path in bundle.rglob('manifest.json'):
    if path.parent!=bundle:files[path.relative_to(bundle).as_posix()]=sha(path)
manifest={**old,'scope':'Updated manuscripts, compact final evidence, Web controls, three raw examples; no new experiments',
          'version':4,'sourcePortableV3Sha256':previous['zipSha256'],'files':dict(sorted(files.items()))}
(bundle/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode())
archive=OUT/'reproduction-examples.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for path in sorted(bundle.rglob('*')):
        if path.is_file():z.write(path,path.relative_to(bundle).as_posix())
fresh=OUT/'fresh';fresh.mkdir()
with zipfile.ZipFile(archive) as z:z.extractall(fresh)
result=subprocess.run([sys.executable,'-I',str(fresh/'verify_all.py')],cwd=OUT,capture_output=True,text=True,timeout=180)
check(result.returncode==0,'fresh extraction verify_all from outside checkout: '+result.stderr)
for article in articles:check((fresh/article.relative_to(ROOT)).read_bytes()==article.read_bytes(),'current manuscript exact bytes')
manifest_path=fresh/'manifest.json';original=manifest_path.read_bytes();corrupt=json.loads(original)
corrupt['files']['README.txt']='0'*64;manifest_path.write_text(json.dumps(corrupt),encoding='utf-8')
bad=subprocess.run([sys.executable,'-I',str(fresh/'verify_all.py')],cwd=OUT,capture_output=True,text=True,timeout=180)
manifest_path.write_bytes(original)
check(bad.returncode!=0 and 'README.txt' in bad.stderr,'tampered SHA rejected')
receipt={'status':'verified','zip':str(archive),'zipBytes':archive.stat().st_size,'zipSha256':sha(archive),
         'filesSealed':len(files),'freshExtractionPassed':True,'tamperedShaRejected':True,'currentManuscriptBytesMatch':True,
         'checksPassed':len(checks),'checks':checks,'stdout':result.stdout,'newExperiments':0,'linuxPolled':False,
         'humanReviewPassed':False,'globalPercent':53.2,'weightedStepsAdded':[],
         'manuscriptsSha256':{p.relative_to(ROOT).as_posix():sha(p) for p in articles},'procedureSha256':sha(__file__)}
(AUDIT/'verification.json').write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode())
print(json.dumps({k:v for k,v in receipt.items() if k not in ('checks','stdout','manuscriptsSha256')}))
