"""Build compact Web references from accepted native controls, without rerunning experiments."""
import argparse,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_matched_main_results import calculate,assert_close
from independent_twin_recount import calculate as independent,read_events,connection_and_subscription

def sha(p):return hashlib.file_digest(p.open('rb'),'sha256').hexdigest()

def build(root,output):
    root=Path(root).resolve(strict=True);output=Path(output);output.mkdir(parents=True)
    index_path=root.parent/'article1-matched-main-v1-raw-001.json';index=json.loads(index_path.read_text(encoding='utf-8-sig'))
    published=json.loads(Path('reports/audits/dissertation/article-1-main-execution-v1/raw-transfer.json').read_text())
    assert sha(index_path)==published['indexSha256']
    inputs={}
    def read(rel):
        p=root/rel;h=sha(p);assert h==index['files'][rel]['sha256'],rel;inputs[rel]=h;return json.loads(p.read_text(encoding='utf-8-sig'))
    program=read('program.json');freeze=read('profile-freeze.json')
    assert freeze['beforeControl'] and freeze['programSha256']==inputs['program.json']
    audit=Path('reports/audits/dissertation/article-1-matched-main-final-v1/verification.json');accepted=json.loads(audit.read_text());assert accepted['jobs']==40 and accepted['status']=='verified'
    output_files={}
    for broker in ('Aedes','Mosquitto','ActiveMQ','EMQX'):
        profile=read(f'attempt-001/profiles/{broker}.profile.json');phash=inputs[f'attempt-001/profiles/{broker}.profile.json'];assert phash==freeze['profiles'][broker]['sha256']
        calibration=profile['calibration']['provenance']['observations']['identity']['campaignId'];runs=[]
        for repeat in (1,2,3):
            prefix=f'attempt-001/jobs/control.{broker}.r{repeat}/';config=read(prefix+'config.json');trial=read(prefix+'raw/trial.json');phases=read(prefix+'raw/phases.json');review=read(prefix+'review.json')
            for name in ('publishes.ndjson','deliveries.ndjson','connections.ndjson','subscriptions.ndjson'):
                rel=prefix+'raw/'+name;p=root/rel;assert sha(p)==index['files'][rel]['sha256'];inputs[rel]=sha(p)
            directory=root/prefix/'raw';pub=list(read_events(directory/'publishes.ndjson'));deliveries=list(read_events(directory/'deliveries.ndjson'));metrics=calculate(pub,deliveries,phases);second=independent(pub,deliveries,phases)
            for key,value in metrics.items():assert_close(value,second[key],broker+' independent '+key);assert_close(value,review['metrics'][key],broker+' accepted '+key)
            connection_and_subscription(directory,phases,'native')
            assert config['campaignId']!=calibration and config['qos']==1 and config['publishers']==16 and config['payloadBytes']==16
            assert metrics['pending']==0 and metrics['completed']>0
            assert datetime.fromisoformat(trial['measurementStartedUtc'].replace('Z','+00:00')).timestamp()>freeze['frozenEpoch']
            runs.append(dict(id=f'control.{broker}.r{repeat}',stage='control',measurementStartedUtc=trial['measurementStartedUtc'],
                publicationsSha256=inputs[prefix+'raw/publishes.ndjson'],deliveriesSha256=inputs[prefix+'raw/deliveries.ndjson'],phasesSha256=inputs[prefix+'raw/phases.json'],
                **{key:metrics[key] for key in ('attempted','completed','failed','completedIdsDelivered','pending','measurementSeconds','p99PublishMs')}))
        reference=dict(schemaVersion=1,artifactType='mqtt-native-control-v1',name=f'Статья 1: {broker}, отдельный натурный контроль',broker=broker,profileSha256=phash,
            calibrationCampaignId=calibration,controlCampaignId=config['campaignId'],profileFrozenUtc=datetime.fromtimestamp(freeze['frozenEpoch'],timezone.utc).isoformat(),
            programSha256=inputs['program.json'],auditSha256=sha(audit),workload=dict(payloadBytes=16,qos=1,publishers=16,subscribers=1,warmupSeconds=1,measurementSeconds=4,drainSeconds=5,loadPolicy='closedLoopPerPublisher'),runs=runs)
        path=output/f'{broker}.native-control.json';path.write_bytes((json.dumps(reference,ensure_ascii=False,indent=2)+'\n').encode());output_files[path.name]=sha(path)
    receipt=dict(status='verified',nativeRunsRecounted=12,profiles=4,sourceIndexSha256=published['indexSha256'],inputsSha256=inputs,files=output_files,newNativeRuns=0,scope='Recounted accepted controls after profile freeze; not proof of blind independence of all model development')
    (output/'verification.json').write_bytes((json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode());print(json.dumps({'nativeRunsRecounted':12,'profiles':4,'output':str(output)},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__);parser.add_argument('--root',required=True);parser.add_argument('--output',required=True);args=parser.parse_args();build(args.root,args.output)
