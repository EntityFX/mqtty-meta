"""One bounded API/direct parity check using final, unchanged article-1 profiles."""
from pathlib import Path
import hashlib,json,shutil,subprocess,time,urllib.request,urllib.error,traceback

root=Path('/run/media/entityfx/hd250/mqtty-evidence/web-final-profile-check-v1-20261029')
root.mkdir()
start=time.monotonic()
main=Path('/run/media/entityfx/hd250/mqtty-evidence/article1-matched-main-v1')
host=Path('/run/media/entityfx/hd250/mqtty-evidence/web-linux-v1/host')
manifest=json.loads((main/'runtime-manifest.json').read_text())
def sha(p):return hashlib.file_digest(Path(p).open('rb'),'sha256').hexdigest()
def save(name,value):(root/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def api(path,body=None):
    raw=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request('http://127.0.0.1:5088'+path,data=raw,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
try:
    assert api('/healthz')['status']=='ok'
    jobs=api('/api/simulations')
    assert not any(j['state'] in ('Queued','Running','Paused','Cancelling') for j in jobs)
    for rel,h in manifest['files'].items():assert sha(main/rel)==h,rel
    profiles={}
    for broker in ('Aedes','Mosquitto','ActiveMQ','EMQX'):
        source=main/'attempt-001/profiles'/f'{broker}.profile.json'
        cfg=json.loads((main/f'attempt-001/jobs/model.{broker}.r1/config.json').read_text())
        digest=sha(source);assert digest==cfg['profileSha256']
        relative=f'profiles/article1-final-{broker}-{digest}.json'
        target=host/'data'/relative
        assert not target.exists(),'Do not overwrite an existing profile import'
        shutil.copyfile(source,target);assert sha(target)==digest
        preset='article1-final-'+broker.lower()+'-'+digest[:8]
        assert not any(p['id']==preset for p in api('/api/presets/brokers'))
        api('/api/presets/brokers',dict(id=preset,name=f'Статья 1 — {broker}, итоговый профиль',brokerType=broker,profilePath=relative,description='Фиксированный профиль серии 40/40; QoS 1, 16 отправителей, 16 байт.'))
        request=dict(name='Final profile API check '+broker,brokerPresetId=preset,publishers=16,qos=1,seed=42,warmupSeconds=.01,measurementSeconds=.1,drainSeconds=.1,tickSeconds=.00001,timeLimitSeconds=120,validateOnly=True)
        validated=api('/api/twin-validations',request)
        assert validated['valid'] and validated['profileSha256']==digest
        assert [validated[k] for k in ('warmupTicks','measurementTicks','drainTicks')]==[1000,10000,10000]
        try:api('/api/twin-validations',{**request,'publishers':1})
        except urllib.error.HTTPError as e:
            error=json.loads(e.read());assert e.code==400
        else:raise AssertionError('Missing point accepted')
        profiles[broker]=dict(sha256=digest,presetId=preset,relativePath=relative,validation=validated,missingPointRejected=True,request=request)
        save('profiles.json',profiles)
    launch=api('/api/twin-validations',{**profiles['Aedes']['request'],'validateOnly':False})
    save('launch.json',launch);identifier=launch['id']
    while True:
        status=api('/api/simulations/'+identifier);save('job-status.json',status)
        if status['state'] in ('Completed','Failed','Cancelled','TimedOut','Interrupted'):break
        if time.monotonic()-start>300:raise TimeoutError('Bounded API job wait exceeded')
        time.sleep(.5)
    assert status['state']=='Completed',status
    files=api('/api/simulations/'+identifier+'/results');save('http-files.json',files)
    web=root/'web';web.mkdir()
    for name,url in files.items():
        assert Path(name).name==name
        with urllib.request.urlopen('http://127.0.0.1:5088'+url,timeout=10) as r:(web/name).write_bytes(r.read())
    assert sha(web/'profile-snapshot.json')==profiles['Aedes']['sha256']
    frozen=main/'attempt-001/jobs/model.Aedes.r1/config.json'
    config=json.loads(frozen.read_text());config.update(id='web-parity-Aedes',campaignId='web-final-profile-check-v1',warmupSeconds=.01,measurementSeconds=.1,drainSeconds=.1)
    config_path=root/'direct-config.json';save('direct-config.json',config)
    command=['/bin/dotnet',str(main/'runtime/TwinValidationProbe.dll'),'model',str(config_path),str(root/'direct')]
    save('direct-command.json',command)
    process=subprocess.run(command,capture_output=True,text=True,timeout=120)
    save('direct-process.json',dict(returnCode=process.returncode,stdout=process.stdout,stderr=process.stderr));assert process.returncode==0
    summaries=list((root/'direct').rglob('summary.json'));assert len(summaries)==1
    direct=summaries[0].parent
    names=('summary.json','latency-histogram.json','publishes.ndjson','deliveries.ndjson','subscriptions.ndjson','phases.json')
    comparisons={name:dict(webSha256=sha(web/name),directSha256=sha(direct/name),byteEqual=(web/name).read_bytes()==(direct/name).read_bytes()) for name in names}
    save('comparisons.json',comparisons)
    assert all(row['byteEqual'] for row in comparisons.values()),'Scientific file mismatch; preserve evidence'
    wc=json.loads((web/'counters.json').read_text());dc=json.loads((direct/'counters.json').read_text())
    fields=('brokers','receivers','receiverScope');assert all(wc[k]==dc[k] for k in fields)
    for rel,h in manifest['files'].items():assert sha(main/rel)==h,rel
    for broker,p in profiles.items():assert sha(main/'attempt-001/profiles'/f'{broker}.profile.json')==p['sha256']
    result=dict(status='verified',jobId=identifier,totalTicks=status.get('totalTicks'),totalSteps=status.get('step'),profilesValidated=4,missingPointsRejected=4,byteEqualScientificFiles=6,counterFieldsEqual=list(fields),runtimeFilesRechecked=len(manifest['files']),elapsedSeconds=time.monotonic()-start,directDirectory=str(direct),historicalInputsChanged=False,newNativeMeasurements=0,fullBrowserFlowVerified=False,nativeReferenceScope='No new independent native reference attached; API reports calibration only',newCalibrationAccuracyClaim=False)
    save('result.json',result);print(json.dumps(result),flush=True)
except Exception as e:
    save('failure.json',dict(error=type(e).__name__+': '+str(e),traceback=traceback.format_exc(),elapsedSeconds=time.monotonic()-start));raise
