"""Read-only audit of saved final native/model events; never launches a broker."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_twin_cohort import events, file_digest, recount
from audit_twin_connections import subscriber_coverage


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def audit(base, summary_path, expected_sha, output, limit_pairs=48):
    base, summary_path, output = base.resolve(), summary_path.resolve(), output.resolve()
    mount = Path('/run/media/entityfx/hd250')
    evidence = mount / 'mqtty-evidence'
    if not mount.is_mount() or not base.is_relative_to(evidence) or not summary_path.is_relative_to(base):
        raise ValueError('Expected mounted evidence disk and source summary inside study')
    if output.parent != base or output.exists() or shutil.disk_usage(mount).free < 1024**3:
        raise ValueError('Require a new direct child of study and at least 1GiB free')
    if file_digest(summary_path) != expected_sha:
        raise ValueError('Final summary checksum mismatch')
    summary = json.loads(summary_path.read_text())
    rows = summary['rows']
    if len(rows) != 48 or len({r['id'] for r in rows}) != 48:
        raise ValueError('Expected 48 distinct final pairs')
    if not 1 <= limit_pairs <= 48:
        raise ValueError('Expected limit-pairs between 1 and 48')
    rows = rows[:limit_pairs]
    output.mkdir()
    started = time.monotonic()
    results = []

    def progress(status, active=None, error=None):
        state = dict(status=status, completed=len(results), total=2*len(rows), active=active,
                     elapsedSeconds=time.monotonic()-started, error=error,
                     observedAtUtc=datetime.now(timezone.utc).isoformat())
        temporary = output / 'progress.tmp'
        temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
        temporary.replace(output / 'progress.json')

    try:
        progress('running')
        for row in rows:
            parent = base / 'series-attempt-001/runs' / row['id']
            case = None
            for candidate in sorted(parent.glob('attempt-*/result.json'), reverse=True):
                saved = json.loads(candidate.read_text())
                if saved.get('native') == row['native'] and saved.get('model') == row['model']:
                    case = candidate.parent
                    break
            if case is None:
                raise ValueError('Cannot resolve exact final pair: ' + row['id'])
            trial = json.loads((case / 'model/trial.json').read_text())
            directories = {'native': case / 'native', 'model': Path(trial['runDirectory'])}
            for scope, directory in directories.items():
                if time.monotonic()-started > 1740:
                    raise TimeoutError('Audit cleanup reserve reached')
                if not directory.resolve().is_relative_to(base):
                    raise ValueError('Raw data resolved outside study')
                progress('running', row['id'] + '/' + scope)
                phases = json.loads((directory / 'phases.json').read_text())
                actual = recount(events(directory/'publishes.ndjson'), events(directory/'deliveries.ndjson'), phases)
                actual['rawSha256'] = {name: file_digest(directory/name) for name in row[scope]['rawSha256']}
                if actual != row[scope]:
                    raise ValueError('Final metrics or raw hashes differ: ' + row['id'] + '/' + scope)
                connection_events = list(events(directory/'connections.ndjson'))
                coverage = subscriber_coverage(connection_events, phases['measurementStartSeconds'], phases['measurementEndSeconds'])
                result = dict(id=row['id'], scope=scope, rawDirectory=str(directory),
                              hashesAndMetricsMatch=True, subscriber=coverage)
                save(output / (row['id'] + '.' + scope + '.json'), result)
                results.append(result)
                progress('running')
        if file_digest(summary_path) != expected_sha:
            raise ValueError('Source summary changed during audit')
        counts = {scope:dict(Counter(r['subscriber']['status'] for r in results if r['scope']==scope)) for scope in ('native', 'model')}
        save(output/'summary.json', dict(status='completed', sourceSummarySha256=expected_sha,
            auditedRawSets=len(results), connectionCounts=counts, cases=results,
            newExperiments=0, scientificCriteriaChanged=False,
            auditCodeSha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                [Path(__file__),Path(__file__).with_name('audit_twin_cohort.py'),Path(__file__).with_name('audit_twin_connections.py')]}))
        progress('completed')
        return dict(status='completed', auditedRawSets=len(results), connectionCounts=counts)
    except Exception as error:
        save(output/'failure.json', dict(error=repr(error), traceback=traceback.format_exc(), auditedRawSets=len(results)))
        progress('failed', error=repr(error))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--summary', required=True, type=Path)
    parser.add_argument('--summary-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--limit-pairs', type=int, default=48, help='Bounded smoke audit; default checks all 48 pairs')
    args = parser.parse_args()
    print(json.dumps(audit(args.base,args.summary,args.summary_sha256,args.output,args.limit_pairs)))
