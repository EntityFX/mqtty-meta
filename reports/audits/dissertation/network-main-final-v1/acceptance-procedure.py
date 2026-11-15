from pathlib import Path
import json,hashlib,sys,csv,collections
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path('tools/dissertation').resolve()))
from network_study_verification import verify_loaded_run
from network_review import check_graph
sys.path.insert(0,str(Path(__file__).resolve().parent))
from registry_gates import validate_registry
root=Path('C:/temp/mqtty-evidence/network-final-20261028/raw-002'); out=Path('reports/audits/dissertation/network-main-final-v1'); previous=Path('reports/audits/dissertation/network-main-final-v1/raw-recount/verification.json')
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
recount=read(previous);assert recount['rawPassed']==162 and recount['rawFailed']==0 and not recount['missingResultKeys'];assert recount['completeModeTriplets']==54 and recount['conditionModeRows']==54
manifest=read(root/'execution-manifest.json');manifest_sha=sha(root/'execution-manifest.json');frozen=0
for rel,h in manifest['files'].items():assert sha(root/rel)==h,rel;frozen+=1
plan=read(root/'program/plan.json');planned={j['id']:j for j in plan['jobs']};matrix=read(root/'results-matrix.json');replays=read(root/'results-replay.json');validate_registry(plan,matrix,replays)
rows={r['id']:r for r in matrix['rows']};assert len(rows)==162;assert set(rows)=={j['id'] for j in plan['jobs'] if j['kind']=='matrix'}
assert len(replays['rows'])==6 and {r['id'] for r in replays['rows']}=={j['id'] for j in plan['jobs'] if j['kind']=='replay'}
checks=[]
for r in replays['rows']:
 job=planned[r['id']];original=rows[job['replayOf']];assert r['technicalError'] is None and r['returnCode']==0;assert r['executionManifestSha256']==manifest_sha;directory=root/r['attempt']/'output';prior=root/original['attempt']/'output'
 assert read(root/r['attempt']/'result.json')==r
 summary=verify_loaded_run(directory);assert summary==r['summary']==original['summary'];options=read(directory/'run-config.json');check_graph(directory/'topology.graphml',options);assert sha(directory/'inputs/config.json')==job['sha256'];assert sha(root/'program'/job['config'])==job['sha256'];assert r['scientificSha256']==original['scientificSha256'];assert len(r['scientificSha256'])==6
 for name,h in r['scientificSha256'].items():assert sha(directory/name)==sha(prior/name)==h
 checks.append({'id':r['id'],'replayOf':job['replayOf'],'rawArithmeticPassed':True,'scientificFilesByteEqual':list(r['scientificSha256']),'summaryEqual':True});print('Replay verified',r['id'],flush=True)
transit=[]
for row in matrix['rows']:
 if '.star.' not in row['id']:continue
 p=root/row['attempt']/'output/network-evidence.json';e=read(p);assert e['networkCount']==5 and e['brokerCount']==(1 if '.single.' in row['id'] else 4) and e['hasAttachedEndpoints'] is False;delta=e['measurementAndDrainCounters'];assert all(e['final']['counters'][k]-e['beforeMeasurement']['counters'][k]==v for k,v in delta.items());assert all(x['networks'][1]=='transit' and len(x['networks'])==3 for x in e['availablePaths'])
 transit.append({'id':row['id'],'inboundBytes':delta['Inbound'],'outboundBytes':delta['Outbound'],'transferredPackets':delta['TransferPackets'],'refused':delta['Refused'],'finalQueuedPackets':e['final']['queuedPackets'],'evidenceSha256':sha(p)})
with (out/'transit.csv').open('w',encoding='utf-8',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(transit[0]));w.writeheader();w.writerows(transit)
transfer=read(Path('C:/temp/mqtty-evidence/network-final-20261028/transfer-verification.json'))
result={'status':'fullMatrixAndReplaysAccepted','matrixRawPassed':162,'matrixRawFailed':0,'modeTriplets':54,'conditionModeRows':54,'replaysRawPassed':6,'replayScientificFileComparisons':36,'replayChecks':checks,'frozenManifestFilesVerified':frozen,'executionManifestSha256':manifest_sha,'transfer':transfer,'transitRawCases':len(transit),'newSimulationsExecuted':False,'historicalInputsChanged':False,'warningMatrixRuns':27,'warningReplayRuns':2,'historicalTechnicalAttempts':3,'scope':'Raw arithmetic and declared scientific byte equality; not physical accuracy, no claim of all-file equality or independent human review'}
(out/'acceptance.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf-8'));print('ACCEPTED',flush=True)
