"""Explicit extra wall budget; never reset the original campaign deadline."""
import hashlib
import json
from pathlib import Path
import re
import time

from network_study_inputs import contained, read, sha


def admit(root, guard, path, manifest_sha, controller_sha, workers, *, now=None):
    raw=Path(path).read_bytes();policy=json.loads(raw)
    if (policy.get('executionManifestSha256')!=manifest_sha or
            policy.get('controllerSha256')!=controller_sha or policy.get('workers')!=workers or
            policy.get('sessionHelperSha256')!=sha(Path(__file__)) or
            not policy.get('reason','').strip()):
        raise ValueError('Additional session must bind execution, controller, workers and reason')
    seconds=policy.get('wallLimitSeconds')
    if type(seconds) is not int or not 0<seconds<=21600:
        raise ValueError('Additional session is limited to six hours')
    name=policy.get('sessionId','')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',name):
        raise ValueError('Invalid additional session ID')
    if sha(root/'wall-budget.json')!=policy.get('originalBudgetSha256'):
        raise ValueError('Original budget differs')
    if not policy.get('originalReceipts') or not policy.get('allowedJobIds'):
        raise ValueError('Original evidence and explicit remaining keys required')
    for relative,digest in policy['originalReceipts'].items():
        if sha(contained(root,relative))!=digest:raise ValueError('Original receipt differs')
    folder=root/'additional-sessions'/name
    folder.mkdir(parents=True,exist_ok=True)
    budget=folder/'wall-budget.json'
    digest=hashlib.sha256(raw).hexdigest()
    now=time.time() if now is None else now
    if budget.exists():
        saved=read(budget)
        if saved.get('decisionSha256')!=digest:raise ValueError('Additional session decision changed')
    else:
        saved=dict(decisionSha256=digest,startedEpoch=now,deadlineEpoch=now+seconds,
                   wallLimitSeconds=seconds,originalBudgetSha256=policy['originalBudgetSha256'])
        guard.save(folder/'decision.json',policy)
        if (root/'progress.json').exists():guard.save(folder/'initial-progress.json',read(root/'progress.json'))
        guard.save(budget,saved)
    if now>=saved['deadlineEpoch']:raise RuntimeError('Additional session budget exhausted; cannot reset')
    return saved['deadlineEpoch'],policy


def check_pending(policy,jobs,rows,resume):
    if not resume:raise ValueError('Additional session requires explicit resume')
    allowed=set(policy['allowedJobIds']);retries=policy.get('retryReasons',{})
    for job in jobs:
        row=rows.get(job['id'])
        if row and not row['technicalError']:continue
        if job['id'] not in allowed:raise ValueError('Pending job outside additional session')
        if row and not retries.get(job['id'],'').strip():
            raise ValueError('Prior technical failure needs an inspected retry reason')
