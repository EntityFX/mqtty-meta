"""Synthetic MQTT journals for testing orchestration, never research observations."""
from pathlib import Path

from network_study_inputs import sha, write


def program(root, seeds=(42,)):
    path = root / "program-source"; (path / "inputs").mkdir(parents=True)
    write(path / "inputs/mosquitto-profile.json", {})
    jobs = []
    for kind, values in (("pilot", seeds), ("matrix", (42,)), ("replay", (42,))):
        for i, seed in enumerate(values):
            identity = f"{kind}.{i}"; config = "configs/" + identity + ".json"
            (path / "configs").mkdir(exist_ok=True)
            write(path / config, {"mode": "single", "topology": "line", "brokers": 1, "publishers": 1,
                  "receiverSites": [0], "payloadBytes": 16, "qos": 0, "seed": seed,
                  "measurementSeconds": .001, "offeredRps": 1000, "ideal": True, "brokerProfilePath": None})
            job = {"id": identity, "kind": kind, "config": config, "sha256": sha(path / config)}
            if kind == "replay":
                job["replayOf"] = "matrix.0"
            jobs.append(job)
    write(path / "plan.json", {"schemaVersion": 1, "pilotJobs": len(seeds), "matrixJobs": 1, "replayJobs": 1,
          "optionalDiagnosticJobs": 0, "profileSha256": sha(path / "inputs/mosquitto-profile.json"), "jobs": jobs})
    return path


def runtime(root):
    path = root / "runtime-source"; path.mkdir()
    (path / "EntityFX.MqttY.Federation.App.dll").write_text(FAKE_CLI, encoding="utf-8")
    return path


FAKE_CLI = '''import base64,hashlib,json,pathlib,struct,sys
config=pathlib.Path(sys.argv[sys.argv.index('--config')+1]);o=json.loads(config.read_text())
if o['seed']==99 and pathlib.Path.cwd().name=='attempt-001':
 print('Synthetic technical failure',flush=True);sys.exit(2)
out=pathlib.Path(sys.argv[sys.argv.index('--output')+1]);out.mkdir()
(out/'inputs').mkdir();(out/'inputs/config.json').write_bytes(config.read_bytes())
p=struct.pack('<qq',1,10);h=hashlib.sha256(p).hexdigest();received=o['seed']!=100
hist={'15':1} if received else {}
metrics=dict(attempted=1,admitted=1,completed=1,publishFailed=0,rateRejected=0,latencyHistogramTicks={})
s=dict(status='completed',error=None,apiAttempts=1,sendRejected=0,scheduledPublishes=1,backpressureUnsent=0,
 sourceCompleted=1,sourceCompletedBeforeDrain=0,sourceCompletedDuringDrain=1,sourceCompletedRps=1000,
 forwardAttempts=0,forwardAccepted=0,forwardRejected=0,forwardTopicPayloadBytes=0,
 expectedDeliveries=1,uniqueDeliveries=int(received),uniqueBeforeDrain=0,uniqueDuringDrain=int(received),
 duplicateDeliveries=0,fullyDeliveredPublications=int(received),receiverHistogramTicks=hist,
 publishHistogramTicks={},tickMilliseconds=.1,deliveryLoss=0 if received else 1,uniqueGoodput=1000 if received else 0,
 queueDrops=0,hopLimitDrops=0,suppressedDuplicates=0,unknownDeliveries=0,unexpectedDeliveries=0,
 phases=[dict(name='measure',startTick=10,endTick=20),dict(name='drain',startTick=20,endTick=30)],
 sources=[dict(publisher=0,site=0,broker=0,metrics=metrics)],
 receivers=[dict(site=0,uniqueDeliveries=int(received),duplicates=0,latencyHistogramTicks=hist)])
s.update({f'publishP{q}Ms':None for q in [50,95,99]})
s.update({f'receiverP{q}Ms':1.5 if received else None for q in [50,95,99]})
pub=[dict(id=1,origin=0,sequence=1,tick=10,measurement=True,payload=base64.b64encode(p).decode())]
receipts=[dict(receiver=0,publicationId=1,topic='federation/delivered/0',tick=25,payloadSha256=h)] if received else []
tx=[dict(publicationId=1,measurement=True,payloadSha256=h,payloadBytes=16,topic='federation/delivered/0',
 topicPayloadBytes=len('federation/delivered/0')+16,role='source',accepted=True)]
for name,value in [('summary.json',s),('run-config.json',o),('publications.json',pub),('delivery-receipts.json',receipts),
 ('transmissions.json',tx),('histograms.json',dict(publish={},receiver=hist))]:
 (out/name).write_text(json.dumps(value),encoding='utf-8')
nodes=[('n','Network','site0'),('p','Client','publisher0000'),('r','Client','receiver0000'),('b','Server','broker0000')]
xml='<graphml xmlns="http://graphml.graphdrawing.org/xmlns"><graph>'
for i,t,label in nodes: xml+=f'<node id="{i}"><data key="type">{t}</data><data key="label">{label}</data></node>'
for i in ['p','r','b']:xml+=f'<edge source="n" target="{i}"/>'
(out/'topology.graphml').write_text(xml+'</graph></graphml>',encoding='utf-8')
(out/'report.md').write_text('# Synthetic runner fixture; not an experiment\\n',encoding='utf-8')
seals={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()}
(out/'sha256.json').write_text(json.dumps(seals),encoding='utf-8')
'''
