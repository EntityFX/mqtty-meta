import hashlib,json,time
from pathlib import Path
import sys
root=Path('/run/media/entityfx/hd250/mqtty-evidence/network-full-3h-v1')
external=Path('/run/media/entityfx/hd250/mqtty-evidence/network-remaining-controller-v1')
sys.path.insert(0,str(external/'tools/dissertation'))
from network_study_inputs import verify_snapshot,program_check
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
manifest=verify_snapshot(root)
identity=sha(root/'execution-manifest.json')
assert identity=='24ae3654a610545f2a13ebc796eeddcaa898d9bde2103ee722592e211b8bffa7'
plan=program_check(root/'program')
latest={}
receipts={}
for path in sorted((root/'rows').glob('*.json')):
 row=read(path);receipts[path.relative_to(root).as_posix()]=sha(path)
 if row['id'] not in latest or row['attemptNumber']>latest[row['id']]['attemptNumber']:latest[row['id']]=row
matrix=[j for j in plan['jobs'] if j['kind']=='matrix']
ready=[j for j in matrix if j['id'] in latest and latest[j['id']]['technicalError'] is None]
missing=[j['id'] for j in matrix if j not in ready]
failed=[r for r in latest.values() if r['technicalError']]
assert len(ready)==106 and len(missing)==56 and len(failed)==2
assert all(r['technicalError']=='RuntimeError: executionBudgetExhausted' and r['returnCode']==-9 for r in failed)
replays=[j['id'] for j in plan['jobs'] if j['kind']=='replay']
assert len(replays)==6 and not any(k in latest for k in replays)
controller=external/'tools/dissertation/run_network_parallel.py'
helper=external/'tools/dissertation/network_additional_session.py'
policy=dict(sessionId='remaining-v1',executionManifestSha256=identity,
 controllerSha256=sha(controller),sessionHelperSha256=sha(helper),workers=2,wallLimitSeconds=21600,
 originalBudgetSha256=sha(root/'wall-budget.json'),originalReceipts=receipts,
 allowedJobIds=missing+replays,
 retryReasons={r['id']:'Inspected prior exit -9 RuntimeError: executionBudgetExhausted; explicit additional six-hour session, unchanged runtime and inputs; original attempt retained' for r in failed},
 reason='User asked to run remaining work in parallel on Linux; explicit additional six-hour session, not reset of old three-hour deadline',
 initialVerifiedMatrix=106,remainingMatrix=56,remainingReplays=6,createdEpoch=time.time(),scientificInputsChanged=False)
resource=dict(executionManifestSha256=identity,controllerSha256=sha(controller),workers=2,
 jobRssLimitBytes=4*1024**3,reason='Retain inspected 4GiB worker cap and two workers within 10GiB cgroup; explicit additional session; scientific inputs unchanged')
for name,data in [('session-decision.json',policy),('resource-decision.json',resource)]:
 p=external/name
 with p.open('x') as stream:json.dump(data,stream,indent=2);stream.write('\n')
print(json.dumps({'status':'additionalSessionPreparedNotStarted','executionManifestSha256':identity,
 'remainingMatrix':56,'remainingReplay':6,'controllerSha256':sha(controller),'helperSha256':sha(helper),
 'sessionDecisionSha256':sha(external/'session-decision.json'),'sourceSnapshotVerified':True}))
