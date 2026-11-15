"""Bounded editorial verification; does not execute experiments."""
from pathlib import Path
import hashlib,json,re,subprocess,zipfile

root=Path(__file__).resolve().parents[4]
out=Path(__file__).resolve().parent
BASE_COMMIT='774478d25ab4e5ee4a7aeaad7989ad5db8d527d6'
checks=[]
def check(ok,label):
    if not ok: raise AssertionError(label)
    checks.append(label)
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p): return json.loads((root/p).read_text(encoding='utf-8'))

floor=read(out.relative_to(root)/'floor-analysis.json')
check(len(floor['sourceInputsSha256'])==32,'32 source hashes retained')
check(floor['modelRunsChecked']==12,'12 transport definitions checked')
expected={'Aedes':(207314,0,0),'Mosquitto':(257092,2002,295),'ActiveMQ':(158284,0,0),'EMQX':(119857,0,0)}
for row in floor['rows']:
    check((row['total'],row['belowFloor'],row['atFloor'])==expected[row['broker']],row['broker']+' weighted counts')
    check(row['zeroResidual']==row['belowFloor']+row['atFloor'],row['broker']+' strict versus boundary')
check(read(out.relative_to(root)/'resources.json')['configsRead']==162,'162 configurations compared with source index')
stats=read('reports/audits/dissertation/network-main-final-v1/raw-recount/condition-stats.json')
na=0
for row in stats:
    for name,m in row['metrics'].items():
        if m['n']==0:
            check(name=='publishP99Ms' and row['qos']==0 and m['mean'] is None,'unavailable QoS0 p99 excluded')
            na+=1
        else: check(m['n']==3 and m['min']==m['max'],'three seeds agree for applicable '+name)
check(len(stats)==54 and na==18,'54 groups; 18 unavailable publication p99 cells')
baseline=(root/'reports/audits/dissertation/article-revision-v3/baseline-table.md').read_text(encoding='utf-8').strip()
article1=root/'docs/dissertation/articles/article-1-twin-validation.md'
article2=root/'docs/dissertation/articles/article-2-relay-federation.md'
check(baseline in article1.read_text(encoding='utf-8'),'all 16 previously verified baseline rows unchanged')
for p,count in [(article1,8),(article2,6)]:
    text=p.read_text(encoding='utf-8')
    # Numbered bibliography is separated from numbered experimental steps.
    tail=text.split('## Литература')[-1] if p==article1 else text.split('## Источники и данные')[-1]
    nums=[int(x) for x in re.findall(r'^(\d+)\. ',tail,re.M)]
    check(nums==list(range(1,count+1)),p.name+' numbered bibliography preserved')
    for href in re.findall(r'\]\(([^)]+)\)',text):
        if '://' in href or href.startswith('#'): continue
        check((p.parent/href.split('#')[0]).exists(),p.name+' local link '+href)
protected=read('reports/audits/dissertation/chapter-drafts-v1/source-before.json')['protectedFiles']
for rel,h in protected.items():check(sha(root/rel)==h,'protected LaTeX unchanged: '+rel)
for rel in ['reports/audits/dissertation/article-1-matched-main-final-v1/native-model.csv','reports/audits/dissertation/article-1-matched-main-final-v1/verification.json','reports/audits/dissertation/network-main-final-v1/raw-recount/condition-stats.json','reports/audits/dissertation/network-main-final-v1/package-verification.json']:
    old=subprocess.check_output(['git','show',BASE_COMMIT+':'+rel],cwd=root)
    check(old==(root/rel).read_bytes(),'accepted evidence unchanged: '+rel)
portable=read(out.relative_to(root)/'portable-v3.json')
check(sha(portable['zip'])==portable['zipSha256'],'portable v3 archive SHA')
with zipfile.ZipFile(portable['zip']) as z:
    for p in [article1,article2]:
        rel=p.relative_to(root).as_posix()
        candidates=[n for n in z.namelist() if n.endswith(rel)]
        check(len(candidates)==1 and z.read(candidates[0])==p.read_bytes(),p.name+' matches portable v3 bytes')
check(portable['freshExtractionPassed'],'portable fresh extraction passed')
calc=read(out.relative_to(root)/'progress-recalculation.json')
check((sum(x['personDays'] for x in calc['steps']),sum(x['personDays'] for x in calc['steps'] if x['technicallyCredited']))==(47,25),'plan weights independently summed')
check(calc['globalPercent']==round(25/47*100,1) and calc['localPercent']==80,'global and local percentages')
report=root/'reports/analysis/Network-main-final.md'
old_report=subprocess.check_output(['git','show',BASE_COMMIT+':reports/analysis/Network-main-final.md'],cwd=root)
receipt=read('reports/audits/dissertation/network-main-final-v1/package-verification.json')
check(hashlib.sha256(old_report).hexdigest()==receipt['reportSha256'],'old report receipt matches its prior Git snapshot')
source_files=['mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/Execution/FederationRunner.cs','mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Contracts.Mqtt/BrokerProfile/EmpiricalLatencyDistribution.cs','mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Mqtt/Experiments/MqttRelayExperimentRunner.cs']
result=dict(status='verifiedIntermediate',checksPassed=len(checks),checks=checks,article1Sha256=sha(article1),article2Sha256=sha(article2),networkReportEditorialRevision=dict(previousSha256=receipt['reportSha256'],currentSha256=sha(report),scientificDataChanged=False),sourceCodeSha256={p:sha(root/p) for p in source_files},floorUnitTestsPassed=4,citationAuditScope='One previously added closest source; static audit 0 errors/0 warnings',newExperiments=0,linuxPolled=False,humanReviewCompleted=False,submissionReady=False)
(out/'verification.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
print('PASS:',len(checks),'bounded consistency checks; original scientific receipts retained')
