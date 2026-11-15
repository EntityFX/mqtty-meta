"""One fixed queue-capacity contrast; execution record, not a general campaign runner."""
from pathlib import Path
import sys,json,time,hashlib,shutil,concurrent.futures,traceback
sys.dont_write_bytecode=True
source=Path('/run/media/entityfx/hd250/mqtty-evidence/network-full-3h-v1')
root=Path(__file__).resolve().parent
sys.path.insert(0,str(source/'tools/dissertation'))
sys.path.insert(0,str(root))
from short_gate import make_pair,validate_pair,model_progress
from network_study_inputs import StorageGuard,verify_snapshot,sha,read
from network_study_process import execute
from network_study_verification import verify_loaded_run
from network_review import check_graph
if (root/'run-identity.json').exists():raise RuntimeError('Existing diagnostic cannot reset its deadline')
started=time.time();deadline=started+600
(root/'run-identity.json').write_text(json.dumps({'startedEpoch':started,'deadlineEpoch':deadline,'hardWallSeconds':600,'modelTimeoutSeconds':240,'workers':2,'rssPerModelBytes':2*1024**3,'serviceMemoryMaxBytes':6*1024**3,'launcherSha256':sha(Path(__file__)),'gateSha256':sha(root/'short_gate.py'),'protocolSha256':sha(root/'protocol.md')},indent=2))
guard=StorageGuard(source.parent.parent,root,10*1024**3)
manifest=verify_snapshot(source)
base_path=source/'program/configs/matrix/matrix.star.federation.q2.r1000.w10.s42.json'
base,changed=make_pair(read(base_path));validate_pair(base,changed)
assert (base['mode'],base['topology'],base['qos'],base['offeredRps'],base['seed'])==('federation','star',2,1000,42)
assert (base['warmupSeconds'],base['measurementSeconds'],base['drainTimeoutSeconds'],base['tickPeriod'])==(.25,2,10,'00:00:00.0001000')
assert base['ideal'] and base['brokerProfilePath'] is None
(root/'runtime').mkdir()
for p in (source/'runtime').iterdir():
 if p.is_file():shutil.copy2(p,root/'runtime'/p.name)
(root/'configs').mkdir();(root/'configs/q1024.json').write_text(json.dumps(base,indent=2)+'\n');(root/'configs/q4096.json').write_text(json.dumps(changed,indent=2)+'\n')
pinned={p.relative_to(root).as_posix():sha(p) for folder in ('runtime','configs') for p in (root/folder).iterdir() if p.is_file()}
pinned.update({name:sha(root/name) for name in ('run_short.py','short_gate.py','test_short_gate.py','protocol.md','run-identity.json')})

guard.save(root/'preflight.json',{'status':'passed','sourceManifestSha256':sha(source/'execution-manifest.json'),'sourceBaseConfigSha256':sha(base_path),'dotnetSha256':sha(manifest['dotnet']),'pinnedFiles':pinned,'sourceSnapshotFilesVerified':len(manifest['files']),'singleDifference':['queueCapacity'],'commonOverrides':{'warmupSeconds':.25,'measurementSeconds':2,'drainTimeoutSeconds':10},'historicalRunIsNotControl':True})
guard.save(root/'progress.json',{**model_progress([]),'startedEpoch':started,'deadlineEpoch':deadline,'phaseVisibility':'Model phases/ticks available only after completion'})
def run_case(capacity):
 attempt=root/f'q{capacity}'/'attempt-001';attempt.mkdir(parents=True)
 command=[manifest['dotnet'],str(root/'runtime/EntityFX.MqttY.Federation.App.dll'),'run','--config',str(root/f'configs/q{capacity}.json'),'--output','output']
 guard.save(attempt/'command.json',{'argv':command,'queueCapacity':capacity})
 result=execute(command,attempt,guard,240,2*1024**3,1024**3,lambda state:guard.save(attempt/'process-state.json',state),cancel_requested=lambda:time.time()>=deadline-120)
 result.update(queueCapacity=capacity,attempt=attempt.relative_to(root).as_posix())
 guard.save(attempt/'result.json',result)
 return result
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 futures=[pool.submit(run_case,capacity) for capacity in (1024,4096)]
 for future in concurrent.futures.as_completed(futures):
  results.append(future.result())
  guard.save(root/'progress.json',{**model_progress(results),'startedEpoch':started,'deadlineEpoch':deadline})
results.sort(key=lambda r:r['queueCapacity'])
guard.save(root/'progress.json',{'status':'running','phase':'raw-verification','results':results,'total':2})
for result in results:
 if result['technicalError'] or result['returnCode']!=0:continue
 attempt=root/result['attempt'];output=attempt/'output'
 try:
  if time.time()>=deadline-10:raise TimeoutError('Shared diagnostic deadline exhausted before verification')
  summary=verify_loaded_run(output);check_graph(output/'topology.graphml',read(output/'run-config.json'))
  executed=read(output/'run-config.json');config=read(root/f"configs/q{result['queueCapacity']}.json")
  assert all(executed.get(k)==v for k,v in config.items())
  assert sha(output/'inputs/config.json')==sha(root/f"configs/q{result['queueCapacity']}.json")
  result.update(rawArithmeticPassed=True,summary=summary,scientificSha256={name:sha(output/name) for name in ('summary.json','histograms.json','publications.json','delivery-receipts.json','transmissions.json','network-evidence.json')})
  result['historicalBaselineCompared']=False  # Common windows differ from the main series.
 except Exception as error:result.update(technicalError=f'{type(error).__name__}: {error}',traceback=traceback.format_exc())
 guard.save(attempt/'result.json',result)
verify_snapshot(source)
assert all(sha(root/name)==h for name,h in pinned.items())
classification='incomplete'
if all(r.get('rawArithmeticPassed') for r in results):
 classification='completedWithoutBaselineOverflow' if results[0]['summary']['queueDrops']==0 else 'singleShortEpisodeContrast'
final={'diagnosticClassification':classification,'status':'completed' if all(r.get('rawArithmeticPassed') for r in results) else 'technicalFailure','results':results,'sharedWallSeconds':time.time()-started,'deadlineEpoch':deadline,'sourceSnapshotUnchanged':True,'pinnedExperimentalInputsUnchanged':True,'hypothesisScope':'Short episode only; common windows .25/2/10, only queue capacity differs, no extrapolation to the ten-second series'}
guard.save(root/'result.json',final);guard.save(root/'progress.json',{k:v for k,v in final.items() if k!='results'});print(json.dumps({'status':final['status'],'sharedWallSeconds':final['sharedWallSeconds']}),flush=True)
