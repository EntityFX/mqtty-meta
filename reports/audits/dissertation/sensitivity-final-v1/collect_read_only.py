"""Read-only evidence collection; run using python3 - on the frozen Linux host."""
import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path('/run/media/entityfx/hd250/mqtty-evidence/dissertation-sensitivity-v1')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


plan, results, progress = [read(ROOT / name) for name in ('plan.json', 'results.json', 'progress.json')]
assert results['planSha256'] == progress['planSha256'] == sha(ROOT / 'plan.json')
assert progress['status'] == 'completed' and not progress['active']
assert len(results['rows']) == len(plan['jobs']) == 230
rows = {row['id']: row for row in results['rows']}
assert len(rows) == 230
immutable = []
for relative, expected in plan['immutableFiles'].items():
    actual = sha(ROOT / relative)
    assert actual == expected, relative
    immutable.append({'path': relative, 'sha256': actual})
evidence = {}
for job in plan['jobs']:
    row = rows[job['id']]
    assert not row['technicalError'] and row['returnCode'] in (0, 3)
    config_path = Path(job['config'])
    config = read(config_path)
    assert sha(config_path) == job['configSha256'] == row['configSha256']
    assert read(ROOT / 'rows' / (job['id'] + '.json')) == row
    directory = Path(row['outputDirectory'])
    directory.resolve().relative_to((ROOT / 'runs' / job['id']).resolve())
    summary = read(directory / 'summary.json')
    histogram = read(directory / 'latency-histogram.json')
    counters = read(directory / 'counters.json')
    scientific = {key: counters[key] for key in ('brokers', 'receivers', 'receiverScope')}
    scientific_sha = hashlib.sha256(json.dumps(scientific, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert summary == row['summary']
    assert sha(directory / 'summary.json') == row['summarySha256']
    assert sha(directory / 'latency-histogram.json') == row['histogramSha256']
    assert scientific_sha == row['scientificCountersSha256']
    if job['expectedOriginalSummary'] is not None:
        assert summary == job['expectedOriginalSummary']
    manifest = read(directory / 'experiment-manifest.json')
    expected_options = dict(config['experiment'])
    # Enum parsing is case-insensitive; the manifest serializes its canonical spelling.
    expected_options['brokerAssignmentMode'] = expected_options['brokerAssignmentMode'].lower()
    actual_options = dict(manifest['options'])
    actual_options['brokerAssignmentMode'] = actual_options['brokerAssignmentMode'].lower()
    assert actual_options == expected_options
    assert manifest['tickPeriod'] == config['ticks']['tickPeriod']
    profile_sha = sha(Path(config['brokerProfilePath']))
    assert profile_sha == plan['immutableFiles']['inputs/' + job['broker'] + '-profile.json']
    assert manifest['provenance']['brokerProfileSha256'].lower() == profile_sha
    assert manifest['provenance']['brokerProfiles'][0]['sha256'].lower() == profile_sha
    assert manifest['provenance']['mqttYCommitSha'] == plan['sourceRevisions']['mqttYRepository']
    assert manifest['provenance']['mqttBenchmarkCommitSha'] == plan['sourceRevisions']['benchmarkRepository']
    evidence[job['id']] = {'config': config, 'histogram': histogram,
        'scientificCounters': scientific, 'manifestSha256': sha(directory / 'experiment-manifest.json'),
        'profileSha256': profile_sha, 'summarySha256': row['summarySha256'],
        'histogramSha256': row['histogramSha256'], 'scientificCountersSha256': scientific_sha,
        'rowFileSha256': sha(ROOT / 'rows' / (job['id'] + '.json'))}
replays = []
for job in plan['jobs']:
    if job['kind'] == 'replay':
        replay, original = rows[job['id']], rows[job['replayOf']]
        checks = {key: replay[key] == original[key] for key in ('summarySha256', 'histogramSha256', 'scientificCountersSha256')}
        assert all(checks.values()) and replay['replayEqual']
        replays.append({'id': job['id'], 'originalId': job['replayOf'], 'checks': checks,
                        'hashes': {key: replay[key] for key in checks}})
snapshot = {'observedAtUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'root': str(ROOT), 'plan': plan, 'progress': progress, 'rows': results['rows'],
    'sourceFiles': {name: sha(ROOT / name) for name in ('plan.json', 'results.json', 'progress.json')},
    'verifiedImmutableFiles': immutable, 'verifiedRuns': evidence, 'verifiedReplays': replays}
json.dump(snapshot, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
sys.stdout.write('\n')
