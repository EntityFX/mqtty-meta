"""Offline weighted-bin recount; never executes a model or changes its profile."""
from pathlib import Path
import json,hashlib,base64,csv,argparse,shutil

def count_floor(bins,floor_ticks,tick_us):
 if not isinstance(floor_ticks,int) or floor_ticks<=0 or not isinstance(tick_us,int) or tick_us<=0 or not bins:raise ValueError('Invalid floor/time bins')
 if any(not isinstance(v,int) or v<0 or not isinstance(n,int) or n<=0 for v,n in bins):raise ValueError('Invalid weighted bin')
 threshold=floor_ticks*tick_us;total=sum(n for v,n in bins);below=sum(n for v,n in bins if v<threshold);at=sum(n for v,n in bins if v==threshold)
 return dict(total=total,floorMicroseconds=threshold,belowFloor=below,atFloor=at,zeroResidual=below+at,belowFraction=below/total,zeroResidualFraction=(below+at)/total)

def main(root,out):
 root=Path(root);out=Path(out);out.mkdir(exist_ok=True,parents=True);(out/'inputs').mkdir(exist_ok=True)
 indexpath=root.parent/'article1-matched-main-v1-raw-001.json';index=json.loads(indexpath.read_text(encoding='utf-8'));published=json.loads(Path('reports/audits/dissertation/article-1-main-execution-v1/raw-transfer.json').read_text());assert hashlib.file_digest(indexpath.open('rb'),'sha256').hexdigest()==published['indexSha256']
 inputs={};results=[]
 def load(rel):
  p=root/rel;h=hashlib.file_digest(p.open('rb'),'sha256').hexdigest();assert h==index['files'][rel]['sha256'];inputs[rel]=h;return json.loads(p.read_text(encoding='utf-8-sig'))
 for broker in ('Aedes','Mosquitto','ActiveMQ','EMQX'):
  prefix='attempt-001/';h=load(prefix+f'profiles/{broker}.histograms.json');profile=load(prefix+f'profiles/{broker}.profile.json');assert json.loads(base64.b64decode(profile['histogramsBase64']).decode('utf-8-sig'))==h
  points=h['points'];assert len(points)==1;point=points[0];assert (point['messageBytes'],point['qos'],point['publishers'])==(16,1,16);assert len(point['repeats'])==3
  bins=[x for r in point['repeats'] for x in r['bins']];assert all(sum(n for v,n in r['bins'])==r['completedPublishes'] for r in point['repeats'])
  resolutions=[]
  for repeat in (1,2,3):
   rel=prefix+f'jobs/model.{broker}.r{repeat}/raw/transport-resolution.json';r=load(rel);config=load(prefix+f'jobs/model.{broker}.r{repeat}/config.json');assert config['tickSeconds']==.00001;assert config['profileSha256']==inputs[prefix+f'profiles/{broker}.profile.json'];resolutions.append(r)
  assert len({r['protocolFloorTicks'] for r in resolutions})==1
  result=count_floor(bins,resolutions[0]['protocolFloorTicks'],10);assert all(r['clippedEmpiricalSamples']==result['belowFloor'] for r in resolutions);assert min(v for v,n in bins)/1000==resolutions[0]['observedMinimumPublishLatencyMs']
  results.append({'broker':broker,**result,'modelResolutionsCompared':3,'sampleScope':'Pooled calibration histogram weights, not realized model draws'})
  shutil.copyfile(root/(prefix+f'profiles/{broker}.histograms.json'),out/'inputs'/f'{broker}.histograms.json');shutil.copyfile(root/(prefix+f'jobs/model.{broker}.r1/raw/transport-resolution.json'),out/'inputs'/f'{broker}.transport-resolution.json')
 result={'status':'verified','rows':results,'sourceInputsSha256':inputs,'sourceIndexSha256':published['indexSha256'],'modelRunsChecked':12,'profileAndHistogramAgree':True,'scope':'Integer microsecond bins; strict L<P*delta vs ceil(L/delta)<=P distinguished; not native continuous timestamps or realized samples','newExperiments':0,'scientificInputsChanged':False}
 (out/'floor-analysis.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
 with (out/'floor-analysis.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(results[0]));w.writeheader();w.writerows(results)
 print(json.dumps(results),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser(__doc__);p.add_argument('--root',required=True);p.add_argument('--output',required=True);a=p.parse_args();main(a.root,a.output)
