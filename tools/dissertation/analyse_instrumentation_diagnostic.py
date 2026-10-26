"""Independently recount all diagnostic repeats; no scientific threshold changes."""
import argparse
import json
import math
from pathlib import Path
import tarfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from independent_twin_recount import calculate, connection_and_subscription, read_events, sha


def analyse(directory, output):
    archive_root = Path(directory)
    index_path = archive_root/'article1-instrumentation-diagnostic-v1-raw-001.json'
    index = json.loads(index_path.read_text())
    archive = archive_root/'article1-instrumentation-diagnostic-v1-raw-001.tar.gz'
    if sha(archive) != index['archiveSha256']:
        raise ValueError('Archive hash mismatch')
    raw = archive_root/'raw-001'
    if raw.exists():
        raise FileExistsError('Raw extraction must be new')
    raw.mkdir()
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            target = (raw/member.name).resolve()
            if raw.resolve() not in target.parents or not member.isfile():
                raise ValueError('Unsafe archive member')
            stream.extract(member, raw)
    for name, expected in index['files'].items():
        if sha(raw/name) != expected:
            raise ValueError('Raw file mismatch: '+name)
    rows = []
    comparisons = 0
    for case in sorted(raw.glob('ActiveMQ.repeat-*')):
        review = json.loads((case/'review.json').read_text())
        native = case/'native'
        phases = json.loads((native/'phases.json').read_text())
        metrics = calculate(read_events(native/'publishes.ndjson'), read_events(native/'deliveries.ndjson'), phases)
        for key, value in metrics.items():
            old = review['metrics'][key]
            if value != old and not (isinstance(value, float) and isinstance(old, (int, float)) and
                                    math.isclose(value, old, rel_tol=1e-12, abs_tol=1e-12)):
                raise ValueError(case.name+'/'+key+' arithmetic mismatch')
            comparisons += 1
        rows.append(dict(repeat=review['repeat'], metrics=metrics,
                         connections=connection_and_subscription(native, phases, 'native'),
                         process=review['process'], gate=review['gate']))
    output = Path(output)
    output.mkdir(exist_ok=False)
    receipt = dict(status='verified', cases=len(rows), rawFilesVerified=len(index['files']),
                   metricValuesCompared=comparisons, archiveSha256=index['archiveSha256'],
                   archiveBytes=index['archiveBytes'], indexSha256=sha(index_path), calculatorSha256=sha(__file__),
                   elapsedSeconds=json.loads((raw/'summary.json').read_text())['elapsedSeconds'],
                   rows=rows, overheadIsolated=False, mainCampaignAdmitted=False)
    (output/'verification.json').write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k!='rows'}))
    for row in rows:
        print(json.dumps(row))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    analyse(args.directory, args.output)
