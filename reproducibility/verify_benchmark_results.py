"""Independently verify a retrieved fixed benchmark and recount its evidence.

Consumes an extracted export (results/, package/, baseline-local.json) and the
original package manifest. No SSH, .NET, retries or benchmark execution.
"""
import argparse
import base64
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def member(root, name):
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()), 'Unsafe member: ' + name)
    require(path.is_file() and not path.is_symlink(), 'Missing or linked member: ' + name)
    return path


def seal(raw):
    registry = read(raw / 'sha256.json')
    actual = {p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file()
              and p.relative_to(raw).as_posix() not in ('sha256.json', '.experiment')}
    require(actual == set(registry), 'Incomplete raw seal: ' + str(raw))
    fingerprints = {}
    for name, digest in registry.items():
        path = member(raw, name)
        require(sha(path).lower() == digest.lower(), 'Raw SHA mismatch: ' + str(path))
        if path.parent == raw.resolve() and path.suffix == '.json' and path.name != 'manifest.json':
            encoded = json.dumps(read(path), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            fingerprints[name] = hashlib.sha256(encoded).hexdigest()
        elif path.parent == raw.resolve() and path.suffix == '.csv':
            fingerprints[name] = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    return len(registry), fingerprints


def p99(histogram, tick_ms):
    count = sum(histogram.values())
    require(count > 0, 'Empty applicable latency histogram')
    rank, cumulative = math.ceil(count * .99), 0
    for tick, amount in sorted(histogram.items()):
        cumulative += amount
        if cumulative >= rank:
            return tick * tick_ms
    raise ValueError('Invalid histogram')


def histogram(value):
    return {int(k): int(v) for k, v in value.items()}


def recount(raw, engine, config):
    s = read(raw / 'summary.json')
    h = read(raw / 'histograms.json')
    require(s['status'] == 'completed' and s['error'] is None, 'Model did not complete')
    planned = round(config['offeredRps'] * config['measurementSeconds'])
    require(s['scheduledPublishes'] == planned == s['apiAttempts'] + s['backpressureUnsent'], 'Workload arithmetic')
    require(s['measurementSeconds'] == config['measurementSeconds'], 'Changed measurement window')
    if engine != 'load-balancer':
        publications = read(raw / 'publications.json')
        by_id = {p['id']: p for p in publications}
        require(len(by_id) == len(publications), 'Duplicate publication ID')
        measured = {key: p for key, p in by_id.items() if p['measurement']}
        require(len(measured) == s['apiAttempts'], 'Measured publication count')
        payload_sha = {key: hashlib.sha256(base64.b64decode(p['payload'])).hexdigest() for key, p in by_id.items()}
        pairs, delays = set(), Counter()
        for receipt in read(raw / 'delivery-receipts.json'):
            publication = by_id[receipt['publicationId']]
            require(receipt['payloadSha256'] == payload_sha[publication['id']], 'Delivery payload changed')
            require(receipt['topic'] == 'federation/delivered/' + str(publication['origin']), 'Delivery topic changed')
            require(receipt['tick'] >= publication['tick'], 'Delivery precedes creation')
            if publication['measurement']:
                pair = (publication['id'], receipt['receiver'])
                require(pair not in pairs, 'Unexpected duplicate measured delivery')
                pairs.add(pair)
                delays[receipt['tick'] - publication['tick']] += 1
        require(len(pairs) == s['uniqueDeliveries'] == s['expectedDeliveries'] == planned * config['brokers'], 'Delivery-pair arithmetic')
        require(dict(delays) == histogram(h['receiver']), 'Primary-event latency histogram')
        require(s['uniqueGoodput'] == len(pairs) / config['measurementSeconds'], 'Goodput arithmetic')
        transmissions = read(raw / 'transmissions.json')
        for t in transmissions:
            require(t['payloadSha256'] == payload_sha[t['publicationId']], 'Transmission payload changed')
            require(t['measurement'] == by_id[t['publicationId']]['measurement'], 'Transmission cohort changed')
        forwards = [t for t in transmissions if t['measurement'] and t['role'] == 'forward']
        require(len(forwards) == s['forwardAttempts'], 'Forward-attempt arithmetic')
        require(sum(t['accepted'] for t in forwards) == s['forwardAccepted'], 'Forward-acceptance arithmetic')
        require(sum(t['topicPayloadBytes'] for t in forwards) == s['forwardTopicPayloadBytes'], 'Forward-byte arithmetic')
        publish = histogram(h['publish'])
        receiver = histogram(h['receiver'])
        require(sum(publish.values()) == s['sourceCompleted'], 'Publish histogram count')
        kind = 'primary-publications-deliveries-transmissions'
    else:
        d = s['delivery']
        require(d['measurementCreated'] == d['uniqueDelivered'] == planned, 'Balancing delivery count')
        require(s['graphErrors'] == 0, 'Graph errors')
        require(sum(b['uniqueDelivered'] for b in d['backends']) == planned, 'Backend delivery reconciliation')
        merged = Counter()
        for backend in d['backends']:
            merged.update(histogram(backend['latencyHistogramTicks']))
        require(dict(merged) == histogram(d['latencyHistogramTicks']), 'Backend latency reconciliation')
        with (raw / 'backend-metrics.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        require(sum(int(row['uniqueReceived']) for row in rows) == planned, 'Backend CSV delivery count')
        require(sum(int(row['completed']) for row in rows) == planned, 'Backend CSV completion count')
        with (raw / 'assignments.csv').open(encoding='utf-8-sig', newline='') as stream:
            assignments = list(csv.DictReader(stream))
        require(len(assignments) == config['totalPublishers'], 'Publisher assignment count')
        publish = histogram(h['publishTicks'])
        receiver = histogram(h['receiverTicks'])
        require(sum(publish.values()) == planned == sum(receiver.values()), 'Balancing histogram count')
        kind = 'backend-csv-and-histogram-reconciliation-no-per-message-raw-ledger'
    require(math.isclose(p99(publish, s['tickMilliseconds']), s['publishP99Ms'], rel_tol=1e-12), 'Publish p99 arithmetic')
    require(math.isclose(p99(receiver, s['tickMilliseconds']), s['receiverP99Ms'], rel_tol=1e-12), 'Receiver p99 arithmetic')
    return kind


def verify(export, expected_manifest, diagnostic=False):
    export = export.resolve()
    package_sha = sha(expected_manifest)
    manifest = read(expected_manifest)
    require(sha(export / 'package/manifest.json') == package_sha, 'Retrieved package identity changed')
    for name in ('adapter.json', 'cases.json'):
        require(sha(export / 'package' / name) == manifest['files'][name], 'Package input changed: ' + name)
    cases = read(export / 'package/cases.json')
    baseline = read(export / 'baseline-local.json')
    report = read(export / 'results/result.json')
    require(report['status'] == baseline['status'] == 'completed', 'Incomplete controller')
    require(report['packageSha256'] == baseline['packageSha256'] == package_sha, 'Controller package identity')
    expected = {(phase, repeat, engine, mode) for phase, repeats in [('pilot', [1]), ('main', range(1, 6))]
                for repeat in repeats for engine in cases for mode in ('serial', 'parallel')}
    actual = [(r['phase'], r['repeat'], r['case'], r['execution']) for r in report['rows']]
    require(len(actual) == len(set(actual)) and set(actual) == expected, 'Missing or repeated attempts')
    binaries = {Path(name).name: value for name, value in manifest['files'].items()
                if name.startswith('app/') and name.endswith('.dll')}
    result = {'packageSha256': package_sha, 'sourceCommits': manifest['sourceCommits'],
              'attemptsChecked': 0, 'sealedFilesChecked': 0, 'primaryEventRecounts': 0,
              'balancingReconciliations': 0, 'runtimeFrameworks': [], 'runtimeGitLabels': [], 'attempts': []}
    seen_runtime, git_labels = set(), set()
    first_serial = {}
    for row in report['rows']:
        phase, repeat, engine, mode = row['phase'], row['repeat'], row['case'], row['execution']
        name = '%s-r%d-%s-%s' % (phase, repeat, engine, mode)
        attempt = export / 'results' / name
        require(row['returnCode'] == 0 and not row['timedOut'], 'Failed process: ' + name)
        require(math.isfinite(row['wallSeconds']) and row['wallSeconds'] > 0, 'Invalid process time')
        measurement = read(attempt / 'measurement.json')
        for key in ('wallSeconds', 'returnCode', 'timedOut', 'userSeconds', 'systemSeconds', 'peakMemoryBytes'):
            require(row[key] == measurement[key], 'Changed process measurement: ' + name + ':' + key)
        argv = read(attempt / 'process.json')['argv']
        probe = [i for i, value in enumerate(argv) if value.replace('\\', '/').endswith('/BenchmarkProbe.dll')]
        require(len(probe) == 1 and argv[probe[0] + 1:probe[0] + 3] == [engine, mode], 'Wrong measured engine/mode')
        require(read(export / 'results' / (engine + '.json')) == cases[engine], 'Changed submitted config')
        require(read(attempt / 'raw/inputs/config.json') == cases[engine], 'Changed executed config')
        count, fingerprints = seal(attempt / 'raw')
        key = engine + ':' + mode
        baseline_match = fingerprints == baseline['fingerprints'][key]
        if mode == 'serial':
            first_serial.setdefault(engine, fingerprints)
        serial_match = fingerprints == first_serial[engine]
        repeat_match = report['fingerprints'][key] == fingerprints
        if not diagnostic:
            require(baseline_match, 'Independent baseline mismatch: ' + name)
            require(serial_match, 'Independent serial/parallel mismatch: ' + name)
            require(repeat_match, 'Controller fingerprint mismatch')
        require(row['matchesBaseline'] == baseline_match, 'Controller baseline flag changed')
        require(row['matchesSerialScience'] == (fingerprints == baseline['fingerprints'][engine + ':serial']), 'Controller serial flag changed')
        require(row['matchesLocalRepeat'] == repeat_match, 'Controller repeat flag changed')
        runtime = read(attempt / 'raw/manifest.json')['runtime']
        require(runtime.get('binaries', runtime.get('binarySha256')) == binaries, 'Executed binary hash mismatch')
        seen_runtime.add(runtime['framework']); git_labels.add(str(runtime.get('mqttYCommit')))
        kind = recount(attempt / 'raw', engine, cases[engine])
        result['primaryEventRecounts'] += kind.startswith('primary-')
        result['balancingReconciliations'] += kind.startswith('backend-')
        result['attemptsChecked'] += 1; result['sealedFilesChecked'] += count
        differing = [key for key in fingerprints.keys() | first_serial[engine].keys()
                     if fingerprints.get(key) != first_serial[engine].get(key)]
        result['attempts'].append({'attempt': name, 'sealedFiles': count,
                                   'scientificMatch': baseline_match and serial_match and repeat_match,
                                   'matchesBaseline': baseline_match, 'matchesSerial': serial_match,
                                   'matchesLocalRepeat': repeat_match, 'differsFromSerialFiles': sorted(differing),
                                   'arithmeticCheck': kind})
    result['runtimeFrameworks'] = sorted(seen_runtime)
    result['runtimeGitLabels'] = sorted(git_labels)
    result['provenanceNote'] = 'Source identity is taken from the verified package sourceCommits and executed DLL hashes. Runtime Git labels scan parent directories and may be null or describe the surrounding meta-repository.'
    result['scopeNote'] = 'Forwarding engines have independent per-message event recounts. Load balancing is reconciled against backend CSV and histograms; no per-message ledger is exported by that engine.'
    result['status'] = 'PASS' if all(a['scientificMatch'] for a in result['attempts']) else 'SCIENTIFIC_MISMATCH'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--diagnostic', action='store_true', help='Record scientific mismatches; seals, inputs, DLLs and arithmetic remain strict')
    args = parser.parse_args()
    value = verify(args.export, args.manifest, diagnostic=args.diagnostic)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(value['status'] + ':', value['attemptsChecked'], 'attempts,', value['sealedFilesChecked'], 'sealed files,',
          value['primaryEventRecounts'], 'primary-event recounts,', value['balancingReconciliations'], 'balancing reconciliations')
