import sys,json,hashlib,subprocess,time,csv,io
from pathlib import Path
sys.dont_write_bytecode=True
root=Path('/run/media/entityfx/hd250/mqtty-evidence/network-long-v1')
sys.path.insert(0,str(root/'tools/dissertation'))
import run_network_study as r
out=Path('/run/media/entityfx/hd250/mqtty-evidence/network-pilot-tail-audit-v1')
if out.exists(): raise RuntimeError('Audit output exists')
out.mkdir()
def save(name,obj): (out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
manifest=r.verify_snapshot(root)
plan=r.program_check(root/'program'); jobs={j['id']:j for j in plan['jobs']}
rows=[]; errors=[]; started=time.monotonic()
for receipt in sorted((root/'runs').glob('*/attempt-*/result.json')):
    row=r.read(receipt); job=jobs[row['id']]; folder=receipt.parent/'output'
    item={k:row.get(k) for k in ['id','returnCode','technicalError','processWallSeconds','peakRssBytes','configSha256','executionManifestSha256']}
    item['receiptSha256']=sha(receipt)
    if not row['technicalError']:
        try:
            s,w=r.verify_output(folder,root/'program'/job['config'],root)
            assert s==row['summary'] and s['status']=='completed' and row['returnCode']==0
            assert {n:sha(folder/n) for n in r.SCIENTIFIC_FILES if (folder/n).exists()}==row['scientificSha256']
            item['verified']=True; item['warnings']=w
        except Exception as exc:
            item['verified']=False; item['verificationError']=repr(exc); errors.append(item['id'])
    else:
        item['verified']=False
        s=r.read(folder/'summary.json') if (folder/'summary.json').exists() else {}
    for key in ['status','sourceCompleted','uniqueDeliveries','expectedDeliveries','sourceCompletedRps','deliveryLoss','receiverP99Ms','forwardAttempts','suppressedDuplicates','queueDrops']:
        item[key]=s.get(key)
    if row['technicalError']:
        files=[]
        for p in sorted(receipt.parent.rglob('*')):
            if p.is_file(): files.append({'path':str(p.relative_to(receipt.parent)),'size':p.stat().st_size,'sha256':sha(p)})
        item['files']=files
        item['processLog']=(receipt.parent/'process.log').read_text(errors='replace')
        for name in ['result.json','last-process-state.json','command.json','process.log']:
            (out/(row['id']+'.'+name)).write_bytes((receipt.parent/name).read_bytes())
    rows.append(item)
service=subprocess.run(['systemctl','--user','show','mqtty-network-pilot-tail-v1.service','-p','ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,MainPID,MemoryPeak,MemoryMax,MemorySwapMax,ControlGroup,OOMPolicy'],capture_output=True,text=True)
journal=subprocess.run(['journalctl','--user','-u','mqtty-network-pilot-tail-v1.service','-n','30','--no-pager'],capture_output=True,text=True)
kernel=subprocess.run(['journalctl','-k','--since','2026-10-24 04:25','--until','2026-10-24 04:36','--no-pager'],capture_output=True,text=True)
missing=[j['id'] for j in plan['jobs'] if j['kind']=='pilot' and j['id'] not in {x['id'] for x in rows}]
result={'executionManifestSha256':sha(root/'execution-manifest.json'),'frozenSnapshotVerified':True,'verificationWallSeconds':time.monotonic()-started,'success':sum(x.get('verified',False) for x in rows),'technicalFailures':sum(bool(x['technicalError']) for x in rows),'verificationErrors':errors,'unattempted':missing,'service':service.stdout,'serviceJournal':journal.stdout,'kernelJournal':kernel.stdout,'kernelJournalStderr':kernel.stderr,'resultsPilotExists':(root/'results-pilot.json').exists(),'matrixAdmitted':False,'rows':rows}
save('verification.json',result)
stream=io.StringIO(); keys=['id','verified','returnCode','processWallSeconds','peakRssBytes','status','sourceCompletedRps','uniqueDeliveries','expectedDeliveries','deliveryLoss','receiverP99Ms','forwardAttempts','suppressedDuplicates','queueDrops']; writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows({k:x.get(k) for k in keys} for x in rows)
(out/'pilots.csv').write_text(stream.getvalue())
print(json.dumps({'output':str(out),'success':result['success'],'failures':result['technicalFailures'],'unattempted':missing,'verificationErrors':errors,'seconds':result['verificationWallSeconds']}))
