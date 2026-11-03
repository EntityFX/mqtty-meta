"""Summarize every counted attempt; failed or unequal parallel runs stay diagnostic."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def summarize(root):
    report = json.loads((root / 'result.json').read_text(encoding='utf-8-sig'))
    result = {'status': report['status'], 'packageSha256': report['packageSha256'],
              'resultSha256': hashlib.sha256((root / 'result.json').read_bytes()).hexdigest(),
              'timeUnit': 'seconds', 'measurement': 'Process launch through completion, including JIT and artifact writing; validation excluded',
              'rows': [], 'scope': 'Parallel means Refresh(true, 0) within one graph; no concurrent independent jobs'}
    for engine in ('relay', 'federation', 'load-balancer'):
        item = {'engine': engine}
        for execution in ('serial', 'parallel'):
            attempts = [r for r in report['rows'] if r['case'] == engine and r['execution'] == execution and r['phase'] == 'main']
            successful = [r for r in attempts if r['technicalSuccess']]
            seconds = [r['wallSeconds'] for r in successful]
            item[execution] = {'attempts': len(attempts), 'completed': len(successful),
                'secondsAllAttempts': [r['wallSeconds'] for r in attempts],
                'secondsCompleted': seconds,
                'medianCompletedSeconds': statistics.median(seconds) if seconds else None,
                'minCompletedSeconds': min(seconds) if seconds else None,
                'maxCompletedSeconds': max(seconds) if seconds else None,
                'allScientificResultsMatchSerial': len(attempts) == 5 and all(r['technicalSuccess'] and r['matchesSerialScience'] and r['matchesLocalRepeat'] for r in attempts)}
        equivalent = all(item[m]['allScientificResultsMatchSerial'] for m in ('serial', 'parallel'))
        item['parallelComparisonValid'] = equivalent
        item['validatedSpeedup'] = item['serial']['medianCompletedSeconds'] / item['parallel']['medianCompletedSeconds'] if equivalent else None
        result['rows'].append(item)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('resultDirectory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.resultDirectory)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    for row in result['rows']:
        print(row['engine'], 'serial=', row['serial']['medianCompletedSeconds'],
              'parallel=', row['parallel']['medianCompletedSeconds'],
              'parallelCompleted=', row['parallel']['completed'], '/5',
              'equivalent=', row['parallelComparisonValid'])
