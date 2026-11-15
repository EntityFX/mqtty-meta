from copy import deepcopy
WINDOWS={'warmupSeconds':.25,'measurementSeconds':2,'drainTimeoutSeconds':10}
def make_pair(source):
 base=deepcopy(source);base.update(WINDOWS);changed=deepcopy(base);changed['queueCapacity']=4096;validate_pair(base,changed);return base,changed
def validate_pair(base,changed):
 if base.get('queueCapacity')!=1024 or changed.get('queueCapacity')!=4096:raise ValueError('Expected1024/4096')
 if set(base)!=set(changed) or {k for k in base if base[k]!=changed[k]}!={'queueCapacity'}:raise ValueError('Only queueCapacity may differ')
 if any(base.get(k)!=v for k,v in WINDOWS.items()):raise ValueError('Common short windows differ')
def model_progress(results):
 return {'status':'running','phase':'models','finishedModels':len(results),'completedModels':sum(r.get('technicalError') is None and r.get('returnCode')==0 for r in results),'technicalErrors':sum(r.get('technicalError') is not None or r.get('returnCode')!=0 for r in results),'verifiedModels':0,'total':2}
