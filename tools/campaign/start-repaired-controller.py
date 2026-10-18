"""Validate the formerly failing native point before admitting the repaired series."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import zipfile

root = pathlib.Path(sys.argv[sys.argv.index('--series') + 1]).resolve()
plan = json.loads((root / 'series.json').read_text())
state = json.loads((root / 'state.json').read_text())
diagnostic = root.parent / 'diagnostics/ActiveMQ-telemetry'
diagnostic.mkdir(parents=True)
config = json.loads((root / 'inputs/ActiveMQ-calibration.json').read_text())
config.update(messageBytes=[16], qos=[0], publishers=[128], repeats=1, maxAttempts=1)
config_path = diagnostic / 'config.json'
config_path.write_text(json.dumps(config, indent=2))
argv = ['dotnet', plan['benchmarkCli'], 'matrix', '--config', str(config_path), '--stand', str(root / 'inputs/ActiveMQ-stand.json'),
    '--benchmark-repo', plan['benchmarkRepository'], '--stand-script', plan['standScript'], '--campaign', str(diagnostic / 'raw'),
    '--archive-root', str(diagnostic / 'archives'), '--archive-mount', plan['archiveMount'], '--archive-device', plan['archiveDevice']]
try:
    with (diagnostic / 'run.log').open('w') as output:
        child = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT)
        state.update(status='instrumentation-pilot', active={'tag': 'ActiveMQ-telemetry-pilot', 'pid': child.pid, 'log': str(diagnostic / 'run.log')})
        (root / 'state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
        code = child.wait()
    if code:
        raise RuntimeError('Repaired native telemetry pilot failed; inspect ' + str(diagnostic / 'run.log'))
    receipts = list((diagnostic / 'raw').glob('attempts/*/attempt-*/archive.json'))
    if len(receipts) != 1:
        raise RuntimeError('Unexpected instrumentation-pilot coverage')
    receipt = json.loads(receipts[0].read_text())
    with pathlib.Path(receipt['archivePath']).open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != receipt['sha256']:
            raise RuntimeError('Pilot archive SHA mismatch')
    with zipfile.ZipFile(receipt['archivePath']) as archive:
        end = json.loads(archive.read('stand/sampler-ended.json'))
        fence = json.loads(archive.read('stand/workload-end-fence.json'))
        result = json.loads(archive.read('result.json'))
    if result['status'] != 'success' or end.get('completionMode') != 'workload-fence' or end['lastSampleMonotonicSeconds'] < fence['monotonicSeconds']:
        raise RuntimeError('Pilot did not establish strict completion coverage')
    (diagnostic / 'verification.json').write_text(json.dumps({'verified': True, 'end': end, 'fence': fence,
        'archiveSha256': receipt['sha256'], 'argv': argv, 'scope': 'Single heavy point, not part of scientific matrix'}, indent=2))
    state.pop('active', None)
    state['status'] = 'prepared'
    (root / 'state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    os.execv(sys.executable, [sys.executable, str(root / 'source/compact-campaign.py'), '--series', str(root), '--resume'])
except BaseException as error:
    state.pop('active', None)
    state.update(status='technical-failure', error=str(error))
    (root / 'state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    raise
