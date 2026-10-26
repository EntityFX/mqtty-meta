"""Repeat the independent on-only ID audit from downloaded raw; off has no ID log."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]/'tools/dissertation'))
from independent_twin_recount import calculate, read_events, connection_and_subscription

parser = argparse.ArgumentParser()
parser.add_argument('--raw', required=True)
parser.add_argument('--index', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
raw = Path(args.raw)
index_path = Path(args.index)
index = json.loads(index_path.read_text())
digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
for name, expected in index['files'].items():
    if digest(raw/name) != expected:
        raise ValueError('Raw SHA mismatch: '+name)
summary = json.loads((raw/'summary.json').read_text())
compared = 0
for row in summary['rows']:
    directory = raw/('logging-repeat-'+str(row['repeat']))/'native'
    phases = json.loads((directory/'phases.json').read_text())
    connection_and_subscription(directory, phases, 'native')
    if not row['writeMessageEventLogs']:
        continue
    metrics = calculate(read_events(directory/'publishes.ndjson'), read_events(directory/'deliveries.ndjson'), phases)
    for key, actual in metrics.items():
        expected = row['rawOnRecount'][key]
        if actual != expected and not (isinstance(actual, float) and isinstance(expected, (int, float)) and
                                       math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)):
            raise ValueError('On arithmetic mismatch: '+key)
        compared += 1
result = dict(status='verified', rawFilesVerified=len(index['files']), onMetricValuesRecounted=compared,
              offIdRecount='notAvailable', indexSha256=digest(index_path), calculatorSha256=digest(__file__))
with Path(args.output).open('x', encoding='utf-8') as stream:
    json.dump(result, stream, indent=2)
print(json.dumps(result))
