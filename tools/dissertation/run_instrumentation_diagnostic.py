"""Bounded native stability diagnostic; deliberately not a logging overhead A/B."""
import argparse
import json
from pathlib import Path
import shutil
import time

from audit_twin_cohort import events, file_digest, recount
from run_fresh_twin_pilot import write_new
from run_fresh_twin_series import available_port, broker_start, execute, terminate
from run_matched_twin_pilot import review_observations
from run_remaining_twin_pilots import api, require_idle, verify_files


def planned_cases():
    return [dict(repeat=i, window=4, recordPublicationCohort=True) for i in range(1, 5)]


def require_time(now, deadline, reserve=90):
    if deadline - now < reserve:
        raise TimeoutError('Original diagnostic deadline exhausted or insufficient reserve; never reset')
    return deadline - now


def run(root, base_program):
    root = Path(root).resolve()
    expected_parent = Path('/run/media/entityfx/hd250/mqtty-evidence')
    if root.parent != expected_parent or root.name != 'article1-instrumentation-diagnostic-v1':
        raise ValueError('Require isolated diagnostic root on evidence disk')
    if root.exists():
        raise FileExistsError('Diagnostic attempts must not overwrite or auto-retry')
    if not expected_parent.parent.is_mount() or shutil.disk_usage(expected_parent).free < 10 * 1024**3:
        raise RuntimeError('Evidence mount or 10 GiB reserve missing')
    root.mkdir()
    started = time.time()
    deadline = started + 900
    results = []
    active = None
    def progress(status, error=None):
        value = dict(status=status, active=active, completed=len(results), total=4,
                     startedEpoch=started, deadlineEpoch=deadline, elapsedSeconds=time.time()-started,
                     error=error, mainCampaignAdmitted=False, overheadIsolated=False)
        temporary = root/'progress.tmp'
        temporary.write_text(json.dumps(value, indent=2))
        temporary.replace(root/'progress.json')
    try:
        progress('preflight')
        old = json.loads(Path(base_program).read_text())
        verify_files(Path(base_program).parent, old['launcherFiles'])
        frozen = Path(old['frozenRoot'])
        manifest = frozen/'runtime-manifest.json'
        if file_digest(manifest) != old['runtimeManifestSha256']:
            raise ValueError('Frozen runtime manifest mismatch')
        verify_files(frozen, json.loads(manifest.read_text())['files'])
        for path, sha in old['pinnedFiles'].items():
            if file_digest(path) != sha:
                raise ValueError('Pinned input changed: '+path)
        require_idle(api('/simulations'))
        item = old['brokers']['ActiveMQ']
        metadata = json.loads(Path(item['metadata']).read_text())['brokers'][0]
        program = dict(cases=planned_cases(), budgetSeconds=900, warmupSeconds=1, drainSeconds=5,
                       baseProgramSha256=file_digest(base_program), runtimeManifestSha256=old['runtimeManifestSha256'],
                       pinnedFiles=old['pinnedFiles'], runtimeFiles=json.loads(manifest.read_text())['files'],
                       recordPublicationCohort=True, observeFullDrainWindow=True,
                       overheadIsolated=False, mainCampaignAdmitted=False,
                       launcherSha256=file_digest(__file__),
                       protocolSha256=file_digest(Path(__file__).parent/'article-1-instrumentation-diagnostic-protocol.md'))
        write_new(root/'program.json', program)
        for point in planned_cases():
            require_time(time.time(), deadline)
            require_idle(api('/simulations'))
            port = 33083
            available_port(port)
            active = 'ActiveMQ.repeat-'+str(point['repeat'])
            case = root/active
            case.mkdir()
            config = dict(id=active, campaignId=root.name, broker='ActiveMQ', qos=1,
                          publishers=16, payloadBytes=16, seed=42, repeat=point['repeat'],
                          warmupSeconds=1, measurementSeconds=4, drainSeconds=5, tickSeconds=.00001,
                          observeFullDrainWindow=True, endpoint=f'mqtt://127.0.0.1:{port}',
                          runtimeIdentity='runtime-manifest-sha256:'+old['runtimeManifestSha256'])
            write_new(case/'config.json', config)
            before = {job['id'] for job in api('/simulations')}
            progress('running')
            broker = log = None
            try:
                broker, log = broker_start(metadata, case, port, min(70, require_time(time.time(), deadline, 45)-45))
                receipt = execute(['dotnet', str(frozen/'runtime/TwinValidationProbe.dll'), 'native',
                                   str(case/'config.json'), str(case/'native')], case/'native.log', '2-3',
                                  min(40, require_time(time.time(), deadline, 10)-10))
            finally:
                if broker is not None:
                    terminate(broker)
                if log is not None:
                    log.close()
            after = api('/simulations')
            require_idle(after)
            if {job['id'] for job in after}-before:
                raise RuntimeError('Other Web job overlapped native observation')
            native = case/'native'
            phases = json.loads((native/'phases.json').read_text())
            gate = review_observations(phases, list(events(native/'connections.ndjson')),
                                       list(events(native/'subscriptions.ndjson')), scope='native', window=4, drain=5)
            metrics = recount(events(native/'publishes.ndjson'), events(native/'deliveries.ndjson'), phases)
            result = dict(repeat=point['repeat'], gate=gate, metrics=metrics, process=receipt,
                          rawSha256={path.name:file_digest(path) for path in native.iterdir() if path.is_file()})
            write_new(case/'review.json', result)
            results.append(result)
            if not gate['admitted'] or not metrics['outcomesSettled'] or not metrics['attempted']:
                raise RuntimeError('Instrumentation gate rejected: '+str(gate.get('issues')))
            progress('running')
        verify_files(frozen, program['runtimeFiles'])
        for path, sha in program['pinnedFiles'].items():
            if file_digest(path) != sha:
                raise ValueError('Frozen source changed during diagnostic: '+path)
        active = None
        write_new(root/'summary.json', dict(status='completed', cases=results, elapsedSeconds=time.time()-started,
                                           overheadIsolated=False, mainCampaignAdmitted=False))
        progress('completed')
    except BaseException as error:
        progress('failed', repr(error))
        write_new(root/'failure.json', dict(error=repr(error), active=active, completed=results, autoRetry=False))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--base-program', required=True)
    arguments = parser.parse_args()
    run(arguments.root, arguments.base_program)
