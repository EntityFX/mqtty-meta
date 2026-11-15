"""Local acceptance of all available raw; no simulation or alteration of old gates."""
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
import sys

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
sys.path.insert(0,str(REPO/'tools/dissertation'))
from network_study_verification import verify_loaded_run
from network_review import check_graph
from run_acceleration_pilot import read, digest, verify_files, SCIENTIFIC
from run_matched_main_campaign import review


def canonical_records(rows):
    # Compare full records with multiplicities, including ticks, topic and payload hash.
    return Counter(json.dumps(r,sort_keys=True,separators=(',',':')) for r in rows)


def main():
    transfer=read(HERE/'transfer.json');raw=Path(transfer['localRaw']);program=Path(transfer['localProgram'])
    assert digest(transfer['localArchive'])==transfer['archiveSha256']
    assert digest(HERE/'source-file-index.json')==transfer['sourceIndexSha256']
    collection=raw.parent;index=read(HERE/'source-file-index.json')['files']
    actual={p.relative_to(collection).as_posix():p for p in collection.rglob('*') if p.is_file()}
    assert set(actual)==set(index)|{'metadata/index.json'}
    for name,row in index.items():
        assert actual[name].stat().st_size==row['bytes'] and digest(actual[name])==row['sha256'],name
    seal=read(program/'seal.json');verify_files(program,seal['files'])
    original_admission=read(HERE/'admission.json')
    assert digest(program/'seal.json')==original_admission['inputSealSha256']
    assert seal==original_admission['seal']
    metadata=read(program/'program.json')
    source_manifest=REPO/'reports/audits/dissertation/network-main-final-v1/source-metadata/execution-manifest.json'
    source_post=read(collection/'metadata/source-postflight.json')
    assert digest(source_manifest)==source_post['sourceManifestSha256']
    checked=[]
    for label in ('core-original','core-serial','core-parallel'):
        directory=raw/label/'job-1/output'
        summary=verify_loaded_run(directory)
        check_graph(directory/'topology.graphml',read(directory/'run-config.json'))
        assert summary['status']=='completed'
        receipt=read(raw/label/'verification.json')
        hashes={name:digest(directory/name) for name in SCIENTIFIC}
        assert hashes==receipt['scientificSha256']
        assert summary==receipt['summary']
        checked.append(dict(name=label,hashes=hashes,summary={k:summary[k] for k in
            ('apiAttempts','sourceCompleted','expectedDeliveries','uniqueDeliveries','deliveryLoss','receiverP99Ms')},
            process=read(raw/label/'measurement.json')))
    assert checked[0]['hashes']==checked[1]['hashes']
    changed=[name for name in SCIENTIFIC if checked[1]['hashes'][name]!=checked[2]['hashes'][name]]
    assert changed==['delivery-receipts.json']
    a,b=[read(raw/label/'job-1/output/delivery-receipts.json') for label in ('core-serial','core-parallel')]
    assert a!=b and canonical_records(a)==canonical_records(b)
    # Reordering is restricted to equal tick; even global tick order is unchanged.
    assert [r['tick'] for r in a]==[r['tick'] for r in b]
    core=dict(localRawPassed=3,originalAndSerialByteEqual=True,parallelDifferentFiles=changed,
              fullReceiptMultisetEqual=True,receiptTickSequenceEqual=True,
              classification='receiptOrderWithinSameTickOnly',originalStrictGatePassed=False,
              scientificEquivalenceContractChanged=False,stableSpeedupEstablished=False,runs=checked)
    queue=read(raw/'queue-1024/measurement.json')
    assert queue['error']=='childTimeout' and queue['jobs'][0]['returnCode']==-9
    assert not (raw/'queue-4096').exists()
    queue_receipt=dict(classification='baselineWallTimeout',attempts=1,referenceIncomplete=True,
        secondVariantStarted=False,process=queue,scientificPairAccepted=False,
        missingCpuAccountingMustNotBeInterpretedAsZero=True,
        reasonOfWallExhaustion='Not established; no final phase snapshot or summary')
    temporal=read(raw/'temporal/comparison.json');success={}
    for directory in sorted((raw/'temporal').iterdir()):
        if not directory.is_dir():continue
        record=read(directory/'review.json')
        relative=Path(record['raw']).relative_to(Path(metadata['networkSource']).parent/'linux-selected-20261030')
        local=raw/relative
        current=review(local,record['job']['kind'])
        assert current==record['review'],directory
        success[directory.name]=dict(job=record['job'],review=current)
    assert len(success)==24 and temporal['completed']==24 and not temporal['failures']
    rebuilt=[];per_broker=[]
    fields=[('completedCohortPerSubmissionSecond',True,.15),('p99PublishMs',True,.20),
            ('publishFailureRate',False,.05),('deliveryLossRate',False,.05)]
    for broker in ('Aedes','Mosquitto','ActiveMQ','EMQX'):
        native=[success[f'native.{broker}.r{r}']['review']['metrics'] for r in (1,2,3)]
        native_mean={key:sum(row[key] for row in native)/3 for key,_,_ in fields}
        old=metadata['previousControlMetrics'][broker]
        old_mean={key:sum(row[key] for row in old)/3 for key,_,_ in fields}
        counts=0;model_means={key:0 for key,_,_ in fields};max_errors={key:0 for key,_,_ in fields}
        for repeat in (1,2,3):
            model=success[f'model.{broker}.r{repeat}']['review']['metrics']
            original=next(row for row in temporal['comparisons'] if row['broker']==broker and row['repeat']==repeat)
            errors={};passed=True
            for key,relative,limit in fields:
                reference=native_mean[key];value=model[key]
                error=abs(value-reference)/reference if relative and reference else abs(value-reference) if not relative else None
                assert error==original['metrics'][key]['error']
                passed&=error is not None and error<=limit
                errors[key]=error;model_means[key]+=value/3
                max_errors[key]=max(max_errors[key],error or 0)
            assert passed==original['allCriteriaPassed'];counts+=passed
            rebuilt.append(dict(broker=broker,repeat=repeat,passed=passed,errors=errors))
        per_broker.append(dict(broker=broker,passed=counts,total=3,oldNative=old_mean,newNative=native_mean,
                               modelMean=model_means,maxErrors=max_errors))
    assert sum(row['passed'] for row in rebuilt)==temporal['scientificallyPassed']==10
    aa=collection/'aa';aa_program=collection/'aa-program'
    aa_seal=read(aa_program/'seal.json');verify_files(aa_program,aa_seal['files'])
    verify_files(aa,read(aa/'preflight.json')['pinned'])
    expected=read(aa/'result.json')['scientificSha256'];aa_runs=[]
    for batch in ('aa-1','aa-2'):
        for job in (1,2):
            directory=aa/batch/f'job-{job}/output';s=verify_loaded_run(directory)
            assert s['status']=='completed'
            assert {name:digest(directory/name) for name in SCIENTIFIC}==expected
            aa_runs.append(dict(batch=batch,job=job,rawPassed=True))
    protected=read(REPO/'reports/audits/dissertation/chapter-drafts-v2/source-before.json')['protectedFiles']
    for name,sha in protected.items():assert digest(REPO/name)==sha,name
    result=dict(status='acceptedWithRecordedFailures',filesVerified=len(index),
                sealedProgramFilesVerified=len(seal['files']),core=core,queue=queue_receipt,
                temporal=dict(rawAccepted=24,modelComparisonsPassed=10,totalModelComparisons=12,
                              perBroker=per_broker,comparisons=rebuilt,profilesUnchanged=True),
                aa=dict(rawAccepted=4,relativeSpread=read(aa/'result.json')['aaRelativeSpread']),
                rawModelRunsRecounted=19,nativeRunsRecounted=12,newSimulationsExecuted=0,
                scientificThresholdsChanged=False,protectedLaTexFilesVerified=len(protected))
    (HERE/'acceptance.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
    with (HERE/'temporal-comparison.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(['broker','metric','oldNativeMean','newNativeMean','modelMean','maxModelError','passedSeeds','totalSeeds'])
        for row in per_broker:
            for metric,_,_ in fields:
                writer.writerow([row['broker'],metric,row['oldNative'][metric],row['newNative'][metric],
                    row['modelMean'][metric],row['maxErrors'][metric],row['passed'],row['total']])
    print(json.dumps({'status':result['status'],'filesVerified':len(index),'core':core['classification'],
                      'queue':queue_receipt['classification'],'temporalPassed':10,'temporalTotal':12,
                      'aaRawAccepted':4,'perBroker':per_broker},ensure_ascii=False))


if __name__=='__main__':main()
