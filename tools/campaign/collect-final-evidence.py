"""Collect a completed campaign and sealed, measurement-window resource evidence.

No raw archives are deleted. The destination must be new. ZIP member seals are
checked; full archive reaggregation receipts remain a separate kind of evidence.
"""
import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
import zipfile

SPEC = importlib.util.spec_from_file_location('publisher', Path(__file__).with_name('publish-broker-reports.py'))
pub = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pub)


def mapped(path, root):
    prefix = PurePosixPath('/run/media/entityfx/hd250/mqtty-evidence')
    suffix = PurePosixPath(path).relative_to(prefix)
    if '..' in suffix.parts:
        raise ValueError('Unsafe evidence path')
    return root.joinpath(*suffix.parts)


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def window_resources(samples, observation):
    """Use only intervals certainly inside the load window despite 1 s UTC truncation."""
    start = timestamp(observation['measurementStartedUtc'])
    end = timestamp(observation['measurementEndedUtc'])
    selected = []
    for previous, current in zip(samples, samples[1:]):
        if (timestamp(previous['timestamp']) >= start and
                timestamp(current['timestamp']) + timedelta(seconds=1) <= end and
                current.get('processCpuPercent') is not None):
            duration = current['monotonicSeconds'] - previous['monotonicSeconds']
            if duration <= 0 or previous['unitId'] != current['unitId']:
                raise ValueError('Invalid telemetry interval')
            selected.append((duration, current))
    total = sum(d for d, _ in selected)
    if total < 15:
        raise ValueError('Too little interior measurement telemetry')
    cpus = [x['processCpuPercent'] for x in samples if x.get('processCpuPercent') is not None]
    return dict(cpuPercent=sum(d*x['processCpuPercent'] for d,x in selected)/total,
                rssMaxBytes=max(x['processRssBytes'] for _,x in selected),
                coveredSeconds=total, intervals=len(selected),
                captureCpuMeanPercent=sum(cpus)/len(cpus),
                captureRssMaxBytes=max(x['processRssBytes'] for x in samples))


def zip_write(archive, name, raw):
    info = zipfile.ZipInfo(name, (2026, 10, 19, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, raw)


def collect(source, evidence, out):
    if pub.read(source/'state.json')['status'] != 'completed':
        raise ValueError('Campaign is not complete')
    out.mkdir(parents=True, exist_ok=False)
    pub.collect(source, evidence, out)
    snapshot = pub.read(out/'data/snapshot.json')
    resources, simulations = [], {}
    native_bundle = zipfile.ZipFile(out/'data/native-members.zip', 'w')
    for broker in pub.BROKERS:
        print('NATIVE', broker, flush=True)
        for stage in ('calibration', 'validation'):
            results = sorted((source.parent/'raw'/f'{broker}-{stage}'/'attempts').glob('*/attempt-*/result.json'))
            successful = []
            for path in results:
                result = pub.read(path)
                if result['status'] != 'success':
                    continue
                reference = pub.read(path.with_name('archive.json'))
                with zipfile.ZipFile(mapped(reference['archivePath'], evidence)) as archive:
                    seal_bytes = archive.read('sha256.json')
                    seals = json.loads(seal_bytes)
                    members, hashes = {}, {}
                    names = ('result.json', 'stand/telemetry.ndjson', 'stand/versions.json',
                             'stand/workload-end-fence.json', 'load-generator-summary.json')
                    for name in names:
                        raw = archive.read(name)
                        if pub.sha(raw) != seals[name].lower():
                            raise ValueError('Seal mismatch: '+name)
                        members[name] = raw
                        hashes[name] = pub.sha(raw)
                        zip_write(native_bundle, f'{broker}/{stage}/{result["key"]["key"]}/{name}', raw)
                    zip_write(native_bundle, f'{broker}/{stage}/{result["key"]["key"]}/sha256.json', seal_bytes)
                if json.loads(members['result.json']) != result:
                    raise ValueError('Archived result differs from selected result')
                samples = [json.loads(line) for line in members['stand/telemetry.ndjson'].splitlines()]
                guard = json.loads(members['load-generator-summary.json'])['assessment']
                if not guard['success']:
                    raise ValueError('Selected measurement failed guard')
                row = dict(broker=broker, stage=stage, key=result['key'], observation=result['observation'],
                           archive=reference, memberSha256=hashes, sealSha256=pub.sha(seal_bytes),
                           versions=json.loads(members['stand/versions.json']), guardPassed=True,
                           resources=window_resources(samples,result['observation']))
                resources.append(row)
                successful.append(result['key']['key'])
            if len(successful) != 72 or len(set(successful)) != 72:
                raise ValueError(f'Incomplete/duplicate native stage: {broker}/{stage}')
    native_bundle.close()
    for broker in (*pub.BROKERS, 'MqttRelay-Mixed'):
        print('MODEL', broker, flush=True)
        base = source/'mixed' if broker == 'MqttRelay-Mixed' else source/'brokers'/broker
        rows = []
        with zipfile.ZipFile(out/'data'/f'{broker}-model-evidence.zip', 'w') as bundle:
            stages = ('relay',) if broker == 'MqttRelay-Mixed' else ('fidelity','holdout','relay')
            for stage in stages:
                folder = base/'simulations'/f'{stage}-2s-overnight-v1'
                index = pub.read(folder/'index.json')
                if len(index['runs']) != 72:
                    raise ValueError('Incomplete model stage')
                for run in index['runs']:
                    directory = mapped(run['directory'], evidence)
                    files = {}
                    for path in sorted(directory.iterdir()):
                        if path.is_file():
                            raw = path.read_bytes()
                            files[path.name] = pub.sha(raw)
                            zip_write(bundle, f'{stage}/{run["key"]}/{path.name}', raw)
                    config_raw = (folder/(run['key']+'.json')).read_bytes()
                    zip_write(bundle, f'{stage}/{run["key"]}/config.json', config_raw)
                    manifest = pub.read(directory/'experiment-manifest.json')
                    summary = pub.read(directory/'summary.json')
                    if summary['status'] != 'completed':
                        raise ValueError('Unfinished model')
                    profiles = manifest['provenance']['brokerProfiles']
                    for profile in profiles:
                        name = PurePosixPath(profile['path']).parent.name
                        if profile['sha256'].lower() != pub.read(source/'state.json')['profiles'][name]:
                            raise ValueError('Profile changed during simulation')
                    counters = pub.read(directory/'counters.json')
                    rows.append(dict(**run,stage=stage,summary=summary,
                        receivers=counters.get('receivers',{}), options=manifest['options'],
                        assignedBrokerTypes=manifest.get('assignedBrokerTypes'),
                        profileHashes=[p['sha256'] for p in profiles], filesSha256=files,
                        configSha256=pub.sha(config_raw)))
                    if broker == 'MqttRelay-Mixed':
                        pub.dump(out/'data'/broker/stage/(run['key']+'.json'),summary)
        simulations[broker] = rows
    pub.dump(out/'data/native-evidence.json',dict(
        method='Sealed members read from original ZIP; whole ZIP hashes NOT recalculated in this extraction. Prior reaggregation receipts retained.',
        cpuMethod='Duration-weighted mean of intervals wholly inside measurement window, allowing 1 second UTC truncation; boundary intervals excluded.',
        runs=resources))
    pub.dump(out/'data/model-evidence.json', simulations)
    for relative in ('state.json','series.json'):
        if pub.sha((source/relative).read_bytes()) != snapshot['files'][relative]['sha256']:
            raise ValueError('Campaign changed during collection')
    pub.dump(out/'data/final-evidence-sha256.json', {'files': {
        p.relative_to(out/'data').as_posix(): pub.sha(p.read_bytes())
        for p in sorted((out/'data').rglob('*')) if p.is_file()}})
    print('COLLECTED',len(resources),'native,',sum(map(len,simulations.values())),'model',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source','evidence-root','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    collect(args.source,args.evidence_root,args.output)
