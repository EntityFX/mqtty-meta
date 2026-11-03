"""Local node-count sweep using an existing sealed, portable MqttY package.

Keeps total offered traffic fixed; changes brokers/backend and publishers only.
Runs pilots first, then five alternating serial/parallel pairs per topology.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import benchmark as bench
import summarize_benchmark
import verify_benchmark_results as audit


def run(package, output, mode, baseline=None, admission=None):
    argv = [sys.executable, str(package / 'benchmark.py'), '--mode', mode,
            '--output', str(output)]
    if baseline:
        argv += ['--baseline', str(baseline)]
    if admission:
        argv += ['--admission', str(admission)]
    subprocess.run(argv, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sizes', nargs='+', type=int, default=[4, 8, 16])
    parser.add_argument('--pilot-only', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Reuse completed, matching pilots; never overwrite attempts')
    args = parser.parse_args()
    source, output = args.package.resolve(), args.output.resolve()
    if not args.sizes or len(set(args.sizes)) != len(args.sizes) or any(n < 4 or n > 16 for n in args.sizes):
        raise ValueError('Distinct node counts in 4..16 required (federation API limit)')
    # Use the packaged verifier so its self-hash check remains meaningful.
    preflight = output / 'source-preflight/result.json'
    if args.resume and preflight.exists():
        prior = bench.read(preflight)
        if prior['status'] != 'completed' or prior['packageSha256'] != bench.digest(source / 'manifest.json'):
            raise ValueError('Resume source package mismatch')
    else:
        run(source, output / 'source-preflight', 'preflight')
    receipt = {'sourcePackageSha256': bench.digest(source / 'manifest.json'),
               'policy': 'Fixed total offeredRps; publishers=4*brokers/backend; strategy 0; fresh processes',
               'sizes': args.sizes, 'status': 'running', 'scales': []}
    bench.save(output / 'sweep.json', receipt)
    for count in args.sizes:
        root = output / ('nodes-%d' % count)
        package = root / 'package'
        cases = bench.read(source / 'cases.json')
        for engine, spec in cases.items():
            spec['backendCount' if engine == 'load-balancer' else 'brokers'] = count
            spec['totalPublishers' if engine == 'load-balancer' else 'publishers'] = count * 4
        if args.resume and root.exists():
            manifest = bench.verify(package)
            if (bench.read(package / 'cases.json') != cases or
                    manifest.get('parentPackageSha256') != receipt['sourcePackageSha256']):
                raise ValueError('Resume case/package mismatch')
            pilot = bench.read(root / 'pilot/result.json')
            if (pilot['status'] != 'completed' or not pilot['parallelScienceEquivalent'] or
                    len(pilot['rows']) != 6 or pilot['packageSha256'] != bench.digest(package / 'manifest.json')):
                raise ValueError('Resume requires complete, matching pilot')
            for row in pilot['rows']:
                name = 'pilot-r1-%s-%s' % (row['case'], row['execution'])
                if bench.fingerprint(root / 'pilot' / name / 'raw') != pilot['fingerprints'][row['case'] + ':' + row['execution']]:
                    raise ValueError('Resume pilot evidence changed')
            receipt['scales'].append({'brokersOrBackends': count, 'publishers': count * 4,
                                      'packageSha256': bench.digest(package / 'manifest.json'),
                                      'pilotStatus': 'completed', 'pilotReused': True})
            bench.save(output / 'sweep.json', receipt)
            continue
        root.mkdir()
        shutil.copytree(source, package, ignore=shutil.ignore_patterns('bin', 'obj'))
        shutil.copyfile(Path(bench.__file__), package / 'benchmark.py')
        bench.save(package / 'cases.json', cases)
        manifest = bench.read(package / 'manifest.json')
        manifest.update(parentPackageSha256=receipt['sourcePackageSha256'],
                        casesStatus='Local node scaling at fixed offered traffic',
                        scalingDriverSha256=bench.digest(__file__))
        manifest['files']['cases.json'] = bench.digest(package / 'cases.json')
        manifest['files']['benchmark.py'] = bench.digest(package / 'benchmark.py')
        bench.save(package / 'manifest.json', manifest)
        run(package, root / 'pilot', 'pilot')
        pilot = bench.read(root / 'pilot/result.json')
        if not pilot['parallelScienceEquivalent']:
            raise ValueError('Pilot scientific mismatch at size %d' % count)
        shutil.copyfile(root / 'pilot/result.json', root / 'baseline-local.json')
        bench.save(root / 'admission.json', {'packageSha256': bench.digest(package / 'manifest.json'),
                                           'timeoutSeconds': 180})
        receipt['scales'].append({'brokersOrBackends': count, 'publishers': count * 4,
                                  'packageSha256': bench.digest(package / 'manifest.json'),
                                  'pilotStatus': pilot['status']})
        bench.save(output / 'sweep.json', receipt)
    if not args.pilot_only:
        for item in receipt['scales']:
            root = output / ('nodes-%d' % item['brokersOrBackends'])
            run(root / 'package', root / 'results', 'campaign',
                root / 'baseline-local.json', root / 'admission.json')
            bench.save(root / 'summary.json', summarize_benchmark.summarize(root / 'results'))
            verification = audit.verify(root, root / 'package/manifest.json', diagnostic=True)
            bench.save(root / 'verification.json', verification)
            item['verification'] = verification['status']
            bench.save(output / 'sweep.json', receipt)
    receipt['status'] = ('pilots-completed' if args.pilot_only else
                         'completed-with-scientific-mismatch' if any(s['verification'] != 'PASS' for s in receipt['scales']) else 'completed')
    bench.save(output / 'sweep.json', receipt)


if __name__ == '__main__':
    main()
