import hashlib,json,subprocess,time
from pathlib import Path
root=Path('/run/media/entityfx/hd250/mqtty-evidence/network-full-3h-v1')
external=Path('/run/media/entityfx/hd250/mqtty-evidence/network-remaining-controller-v1')
unit='mqtty-network-remaining-v1.service'
fields=subprocess.check_output(['systemctl','--user','show',unit,'-p','MainPID,ActiveState,SubState,Result,ExecMainStatus,MemoryMax,MemorySwapMax,RuntimeMaxUSec,ActiveEnterTimestamp'],text=True)
service=dict(line.split('=',1) for line in fields.splitlines() if '=' in line)
read=lambda p:json.loads(p.read_text())
policy=read(external/'session-decision.json')
budget=read(root/'additional-sessions/remaining-v1/wall-budget.json')
progress=read(root/'progress.json')
result=dict(checkedEpoch=time.time(),service=service,budget=budget,progress=progress,
 oldBudgetSha256=hashlib.sha256((root/'wall-budget.json').read_bytes()).hexdigest(),
 originalBudgetUnchanged=hashlib.sha256((root/'wall-budget.json').read_bytes()).hexdigest()==policy['originalBudgetSha256'],
 stage='preflightRevalidation' if progress.get('status')=='budgetExhausted' else progress.get('status'),
 stderr=(external/'service.stderr.log').read_text()[-4000:] if (external/'service.stderr.log').exists() else '')
(external/'start-status.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
assert service['ActiveState']=='active' and int(service['MainPID'])>0
assert result['originalBudgetUnchanged']
