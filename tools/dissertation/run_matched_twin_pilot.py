"""Bounded Mosquitto pilot of NEW instrumentation; never starts the main 40 runs."""
import argparse
import json
import math
from pathlib import Path
import shutil
import sys
import time

from audit_twin_cohort import events, file_digest, recount
from audit_twin_connections import subscriber_coverage
from run_fresh_twin_pilot import write_new
from run_fresh_twin_series import available_port, broker_start, execute, terminate


def review_observations(phases, connections, subscriptions, *, scope, window, drain):
    start, end = phases['measurementStartSeconds'], phases['measurementEndSeconds']
    issues = []
    if not phases.get('completed') or phases.get('timeOrigin') != 'sharedMonotonic':
        issues.append('Incomplete phases or unsupported clock origin')
    if not math.isclose(end - start, window, abs_tol=1e-9, rel_tol=0):
        issues.append('Submission duration differs from planned duration')
    if not math.isclose(phases['drainEndSeconds'] - end, drain, abs_tol=1e-9, rel_tol=0):
        issues.append('Observation horizon differs from planned duration')
    connection = subscriber_coverage(connections, start, phases['drainEndSeconds'])
    if connection['status'] != 'continuousConnected':
        issues.append('Subscriber connection is not continuously observed')
    identities = {e.get('clientId') for e in connection['subscriberEvents']}
    if len(identities) != 1 or not all(identities):
        issues.append('Missing or ambiguous subscriber MQTT client ID')
    confirmed = [s for s in subscriptions if s.get('state', 'subscribed') == 'subscribed']
    # BrokerFidelity's receiver is configured with two filters by MqttNetworkBuilder.
    # Check the exact contract, rather than infer expected coverage from observed rows.
    expected = 2
    if len(confirmed) != expected or any(s.get('clientId') not in identities or
            s.get('requestedQos') != 1 or s.get('grantedQos') != 1 or
            not 0 <= s['elapsedSeconds'] <= start for s in confirmed):
        issues.append('Missing, mismatched or late subscriber SUBACK evidence')
    if scope == 'native' and sum(s['topic'].endswith('/warmup') for s in confirmed) != 1:
        issues.append('Warmup subscription not independently confirmed')
    topics = {s.get('topic') for s in confirmed}
    if scope == 'model' and topics != {'telemetry/+', 'local/telemetry/+'}:
        issues.append('Confirmed model filters differ from configured BrokerFidelity filters')
    if scope == 'native':
        measured_topics = [topic for topic in topics if isinstance(topic, str) and not topic.endswith('/warmup')]
        if len(measured_topics) != 1 or topics != {measured_topics[0], measured_topics[0] + '/warmup'}:
            issues.append('Native warmup and measurement subscriptions do not form the configured topic pair')
    if scope == 'model' and phases.get('connectionObservation') != 'everyModelTick':
        issues.append('No evidence of connection sampling at every model tick')
    return dict(admitted=not issues, issues=issues, connection=connection,
                classification='systemObservation' if connection['status'] in ('interrupted', 'disconnectedAtStart')
                else 'instrumentationObservation', scope='Technical pilot gate, not an accuracy claim')


def validate_pilot_selection(windows, attempt, budget_seconds):
    if (tuple(windows) not in ((2,), (4,), (2, 4)) or type(attempt) is not int
            or not 1 <= attempt <= 999 or type(budget_seconds) is not int or not 30 <= budget_seconds <= 575):
        raise ValueError('Require planned windows in order, a positive attempt <=999 and budget 30..575s')
    return f'pilot-attempt-{attempt:03d}'


def run(base, root, windows=(2, 4), attempt=1, budget_seconds=575):
    attempt_name = validate_pilot_selection(windows, attempt, budget_seconds)
    base, root = base.resolve(), root.resolve()
    mount = Path('/run/media/entityfx/hd250')
    if not mount.is_mount() or root.parent != mount/'mqtty-evidence' or root == base:
        raise ValueError('Require a separate study on the mounted evidence disk')
    if shutil.disk_usage(mount).free < 2 * 1024**3:
        raise ValueError('Less than 2 GiB free')
    sealed = json.loads((root/'runtime-manifest.json').read_text())
    for relative, digest in sealed['files'].items():
        path = (root/relative).resolve()
        if not path.is_relative_to(root) or file_digest(path) != digest:
            raise ValueError('Runtime snapshot mismatch: '+relative)
    old = json.loads((base/'inputs/input-manifest.json').read_text())
    profile = base/'inputs/profiles/Mosquitto.json'
    if file_digest(profile) != old['files']['profiles/Mosquitto.json']:
        raise ValueError('Historical profile changed')
    program = json.loads((base/'launcher-v2/program.json').read_text())
    metadata = json.loads(Path(program['standMetadata']['Mosquitto']).read_text())['brokers'][0]
    if file_digest(metadata['configFiles'][0]) != old['mosquittoConfigSha256']:
        raise ValueError('Historical Mosquitto configuration changed')
    output = root/attempt_name
    output.mkdir(exist_ok=False)
    started, results = time.monotonic(), []

    def progress(status, active=None, error=None):
        value = dict(status=status, active=active, completed=len(results), total=len(windows),
                     selectedWindows=list(windows), budgetSeconds=budget_seconds,
                     elapsedSeconds=time.monotonic()-started, error=error,
                     mainCampaignStarted=False, scope='Instrumentation pilot; historical profile only')
        temporary = output/'progress.tmp'
        temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
        temporary.replace(output/'progress.json')

    try:
        for window in windows:
            case = output/('window-'+str(window)); case.mkdir()
            progress('running', 'native/window-'+str(window))
            remaining = budget_seconds - (time.monotonic()-started)
            if remaining < 60:
                raise TimeoutError('Pilot total budget reserve reached')
            port = 33103
            available_port(port)
            config = dict(id='matched-pilot-Mosquitto-w'+str(window), broker='Mosquitto', qos=1,
                campaignId='article1-matched-validation-v1',
                publishers=16, payloadBytes=16, seed=42, repeat=1, warmupSeconds=1,
                measurementSeconds=window, drainSeconds=5, tickSeconds=.00001,
                observeFullDrainWindow=True, endpoint=f'mqtt://127.0.0.1:{port}',
                profilePath=str(profile), profileSha256=file_digest(profile),
                runtimeIdentity='runtime-manifest-sha256:'+file_digest(root/'runtime-manifest.json'))
            config_path = case/'config.json'; write_new(config_path, config)
            command = ['dotnet', str(root/'runtime/TwinValidationProbe.dll')]
            broker = log = None
            try:
                broker, log = broker_start(metadata, case, port, 30)
                native_process = execute(command+['native', str(config_path), str(case/'native')],
                                         case/'native.log', '2-3', 35)
            finally:
                if broker is not None: terminate(broker)
                if log is not None: log.close()
            progress('running', 'model/window-'+str(window))
            remaining = min(230, budget_seconds-(time.monotonic()-started))
            model_process = execute(command+['model', str(config_path), str(case/'model')],
                                    case/'model.log', '2-3', remaining)
            model_trial = json.loads((case/'model/trial.json').read_text())
            directories = dict(native=case/'native', model=Path(model_trial['runDirectory']))
            reviewed = {}
            for scope, directory in directories.items():
                if not directory.resolve().is_relative_to(case):
                    raise ValueError('Raw trial directory escaped pilot case')
                phases = json.loads((directory/'phases.json').read_text())
                gate = review_observations(phases, list(events(directory/'connections.ndjson')),
                    list(events(directory/'subscriptions.ndjson')), scope=scope, window=window, drain=5)
                metrics = recount(events(directory/'publishes.ndjson'), events(directory/'deliveries.ndjson'), phases)
                if metrics['attempted'] == 0 or metrics['completed'] == 0:
                    gate['admitted'] = False; gate['issues'].append('No completed measurement publications')
                if not metrics['outcomesSettled']:
                    gate['admitted'] = False; gate['issues'].append('Pending publication IDs remain at the observation horizon')
                reviewed[scope] = dict(gate=gate, metrics=metrics, phases=phases,
                    rawSha256={name:file_digest(directory/name) for name in
                        ('publishes.ndjson','deliveries.ndjson','connections.ndjson','subscriptions.ndjson','phases.json')})
            row = dict(windowSeconds=window, observations=reviewed,
                       processes=dict(native=native_process, model=model_process))
            write_new(case/'review.json', row); results.append(row)
            if not all(value['gate']['admitted'] for value in reviewed.values()):
                raise RuntimeError('Pilot gate blocked; retain system outcomes and inspect review.json')
        if file_digest(profile) != old['files']['profiles/Mosquitto.json']:
            raise ValueError('Historical profile changed during pilot')
        write_new(output/'summary.json', dict(status='completed', cases=results, historicalProfileUnchanged=True,
            elapsedSeconds=time.monotonic()-started, mainCampaignAdmitted=False,
            next='Check other brokers, logging overhead and frozen new calibration before the main series'))
        progress('completed')
    except Exception as error:
        progress('failed', error=repr(error))
        write_new(output/'failure.json', dict(error=repr(error), completedCases=results))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--windows', nargs='+', type=int, choices=(2, 4), default=[2, 4])
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--budget-seconds', type=int, default=575)
    args = parser.parse_args()
    run(args.base, args.root, tuple(args.windows), args.attempt, args.budget_seconds)
