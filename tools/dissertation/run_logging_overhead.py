"""Four bounded native trials; only message-event file writing changes."""
import argparse
import json
import math
from pathlib import Path
import shutil
import time

from audit_twin_cohort import file_digest, events, recount
from independent_twin_recount import connection_and_subscription
from run_fresh_twin_pilot import write_new
from run_fresh_twin_series import available_port, broker_start, execute, terminate
from run_remaining_twin_pilots import api, require_idle, verify_files


def planned_cases():
    return [dict(repeat=i, writeMessageEventLogs=enabled) for i, enabled in enumerate((True, False, False, True), 1)]


def require_time(now, deadline):
    remaining = deadline-now
    if remaining < 90:
        raise TimeoutError('Insufficient original budget; deadline cannot reset')
    return remaining


def comparison_config(point, runtime_sha):
    return dict(id='logging-repeat-'+str(point['repeat']), campaignId='article1-logging-overhead-v1',
                broker='ActiveMQ', payloadBytes=16, qos=1, publishers=16, seed=42, repeat=point['repeat'],
                warmupSeconds=1, measurementSeconds=4, drainSeconds=5, tickSeconds=.00001,
                recordPublicationCohort=True, observeFullDrainWindow=True,
                measureRttBaseline=True,
                writeMessageEventLogs=point['writeMessageEventLogs'], endpoint='mqtt://127.0.0.1:33083',
                runtimeIdentity='runtime-manifest-sha256:'+runtime_sha)


def measurement_metrics(trial):
    m = trial['measurement']
    seconds = m['actualMeasurementSeconds']
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('Invalid actual measurement duration')
    computed = m['completedPublishes']/seconds
    if not math.isclose(computed, m['completedRps'], rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError('Stored RPS differs from completed/actual time')
    if m['attemptedPublishes'] != m['completedPublishes']+m['failedPublishes']:
        raise ValueError('Unsettled publication counts')
    return dict(attempted=m['attemptedPublishes'], completed=m['completedPublishes'], failed=m['failedPublishes'],
                actualMeasurementSeconds=seconds, completedRps=computed, p99Ms=m['publishLatencyMs']['p99Ms'],
                deliveryLossRate=m['deliveryLossRate'], duplicateDeliveries=m['duplicateDeliveries'])


def run(root, base_program, runtime_manifest, runtime_sha):
    root = Path(root).resolve()
    evidence = Path('/run/media/entityfx/hd250/mqtty-evidence')
    if root.parent != evidence or root.name != 'article1-logging-overhead-v1':
        raise ValueError('Require isolated evidence root')
    if root.exists():
        raise FileExistsError('No overwrite or automatic restart')
    if not evidence.parent.is_mount() or shutil.disk_usage(evidence).free < 10*1024**3:
        raise RuntimeError('Evidence mount/reserve missing')
    root.mkdir()
    started = time.time()
    deadline = started+900
    rows = []
    active = None
    def progress(status, error=None):
        temporary = root/'progress.tmp'
        temporary.write_text(json.dumps(dict(status=status, completed=len(rows), total=4, active=active,
            startedEpoch=started, deadlineEpoch=deadline, elapsedSeconds=time.time()-started, error=error,
            mainCampaignAdmitted=False), indent=2))
        temporary.replace(root/'progress.json')
    try:
        progress('preflight')
        old = json.loads(Path(base_program).read_text())
        verify_files(Path(base_program).parent, old['launcherFiles'])
        for path, sha in old['pinnedFiles'].items():
            if file_digest(path) != sha:
                raise ValueError('Historical pin mismatch: '+path)
        manifest_path = Path(runtime_manifest)
        if file_digest(manifest_path) != runtime_sha:
            raise ValueError('New runtime manifest changed')
        manifest = json.loads(manifest_path.read_text())
        verify_files(manifest_path.parent, manifest['files'])
        probe = manifest_path.parent/'runtime/TwinValidationProbe.dll'
        if not probe.exists():
            raise ValueError('New runtime probe absent')
        require_idle(api('/simulations'))
        program = dict(cases=[comparison_config(p, runtime_sha) for p in planned_cases()], budgetSeconds=900,
                       baseProgramSha256=file_digest(base_program), runtimeManifestSha256=runtime_sha,
                       runtimeManifest=str(manifest_path), runtimeFiles=manifest['files'],
                       launcherSha256=file_digest(__file__),
                       protocolSha256=file_digest(Path(__file__).parent/'article-1-logging-overhead-protocol.md'),
                       mainCampaignAdmitted=False, loggingScope='Publication/delivery message event logs only')
        write_new(root/'program.json', program)
        metadata = json.loads(Path(old['brokers']['ActiveMQ']['metadata']).read_text())['brokers'][0]
        for config in program['cases']:
            remaining = require_time(time.time(), deadline)
            require_idle(api('/simulations'))
            before_ids = {j['id'] for j in api('/simulations')}
            available_port(33083)
            active = config['id']
            case = root/active
            case.mkdir()
            write_new(case/'config.json', config)
            progress('running')
            broker = log = None
            try:
                broker, log = broker_start(metadata, case, 33083, min(70, remaining-45))
                process = execute(['dotnet', str(probe), 'native', str(case/'config.json'), str(case/'native')],
                                  case/'native.log', '2-3', min(40, deadline-time.time()-10))
            finally:
                if broker is not None:
                    terminate(broker)
                if log is not None:
                    log.close()
            jobs = api('/simulations')
            require_idle(jobs)
            if {j['id'] for j in jobs}-before_ids:
                raise RuntimeError('Another Web job overlapped native trial')
            native = case/'native'
            trial = json.loads((native/'trial.json').read_text())
            preflight = json.loads((native/'protocol-preflight.json').read_text())
            if (not preflight.get('success') or len(preflight.get('rttSamplesMs', [])) != 20 or
                not math.isfinite(trial['rttBaselineMs']) or
                trial['rttBaselineMs'] <= 0 or trial['rttBaselineMs'] != preflight.get('rttBaselineMs')):
                raise ValueError('Measured protocol RTT preflight missing or inconsistent')
            phases = json.loads((native/'phases.json').read_text())
            if (not phases.get('completed') or
                not math.isclose(phases['measurementEndSeconds']-phases['measurementStartSeconds'], 4, abs_tol=1e-10) or
                not math.isclose(phases['drainEndSeconds']-phases['measurementEndSeconds'], 5, abs_tol=1e-10)):
                raise ValueError('Planned phase windows changed')
            connections = connection_and_subscription(native, phases, 'native')
            metrics = measurement_metrics(trial)
            raw_on = None
            for name in ('publishes.ndjson', 'deliveries.ndjson'):
                path = native/name
                if config['writeMessageEventLogs'] and (not path.exists() or not path.stat().st_size):
                    raise ValueError('Message logging enabled but raw absent: '+name)
                if not config['writeMessageEventLogs'] and path.exists() and path.stat().st_size:
                    raise ValueError('Message logging disabled but events written: '+name)
            if config['writeMessageEventLogs']:
                raw_on = recount(events(native/'publishes.ndjson'), events(native/'deliveries.ndjson'), phases)
            row = dict(repeat=config['repeat'], writeMessageEventLogs=config['writeMessageEventLogs'],
                       metrics=metrics, connections=connections, process=process, rawOnRecount=raw_on,
                       rttBaselineMs=trial['rttBaselineMs'],
                       rawOffRecount='notAvailable' if raw_on is None else 'notApplicable',
                       filesSha256={p.name:file_digest(p) for p in native.iterdir() if p.is_file()})
            write_new(case/'review.json', row)
            rows.append(row)
            progress('running')
        verify_files(manifest_path.parent, manifest['files'])
        for path, sha in old['pinnedFiles'].items():
            if file_digest(path) != sha:
                raise ValueError('Historical input changed: '+path)
        active = None
        write_new(root/'summary.json', dict(status='completed', rows=rows, elapsedSeconds=time.time()-started,
                                           mainCampaignAdmitted=False, statisticalEquivalenceEstablished=False))
        progress('completed')
    except BaseException as error:
        progress('failed', repr(error))
        write_new(root/'failure.json', dict(error=repr(error), active=active, rows=rows, autoRetry=False))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--base-program', required=True)
    parser.add_argument('--runtime-manifest', required=True)
    parser.add_argument('--runtime-sha', required=True)
    args = parser.parse_args()
    run(args.root, args.base_program, args.runtime_manifest, args.runtime_sha)
