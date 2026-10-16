"""Explicit compact-v1 campaign with a six-hour wall-clock cap and preserved evidence."""
import argparse
import csv
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
import uuid

BROKERS = ('Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX')
GIB = 1024**3


class BudgetExpired(RuntimeError):
    pass


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def native_config(source, broker, stage):
    value = dict(source)
    pilot = stage == 'pilot'
    value.update(brokers=[broker], messageBytes=[16] if pilot else [16, 256], qos=[0, 1, 2],
                 publishers=[1] if pilot else [1, 16], repeats=1 if pilot else 3,
                 seed=20260910 if stage == 'validation' else 20260909,
                 warmupSeconds=1 if pilot else 5, measurementSeconds=2 if pilot else 10,
                 cooldownSeconds=0 if pilot else 1, drainTimeoutSeconds=3 if pilot else 5,
                 quietPeriodSeconds=1, telemetryCaptureReserveSeconds=3,
                 preflightSysWaitSeconds=1 if pilot else 2, maxAttempts=1 if pilot else 2)
    return value


def prepare(source, destination, simulator, repository, orchestration_commit):
    source, destination = pathlib.Path(source).resolve(), pathlib.Path(destination).resolve()
    original = read(source / 'series.json')
    mount = pathlib.Path(original['archiveMount'])
    if not destination.is_relative_to(mount) or destination == mount or (destination / 'controller').exists():
        raise ValueError('Need a new separate series directory on the evidence mount')
    observed = subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE', '-T', str(mount)], text=True).split()
    if observed != [str(mount), original['archiveDevice'], 'ext4']:
        raise RuntimeError('Evidence mount identity changed')
    if shutil.disk_usage(mount).free < 20 * GIB or shutil.disk_usage('/').free < 10 * GIB:
        raise RuntimeError('Insufficient free disk space')
    root = destination / 'controller'
    root.mkdir(parents=True)
    (root / 'logs').mkdir()
    (destination / 'evidence/attempts').mkdir(parents=True)
    snapshots = root / 'source'
    snapshots.mkdir()
    for name in ('compact-campaign.py', 'accelerated-resume.py'):
        shutil.copy2(pathlib.Path(__file__).with_name(name), snapshots / name)
    (root / 'inputs').mkdir()
    for broker in BROKERS:
        shutil.copy2(source / 'inputs' / (broker + '-stand.json'), root / 'inputs' / (broker + '-stand.json'))
        for stage in ('pilot', 'calibration', 'validation'):
            write(root / 'inputs' / f'{broker}-{stage}.json', native_config(read(source / 'inputs' / f'{broker}-{stage}.json'), broker, stage))
    revision = subprocess.check_output(['git', '-C', repository, 'rev-parse', 'HEAD'], text=True).strip()
    if subprocess.check_output(['git', '-C', repository, 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
        raise RuntimeError('Simulator source is dirty')
    plan = dict(original)
    for key in ('revisionTransition', 'accelerationProvenance', 'relayConcurrencyTransition', 'parallelMemoryTransition', 'modelRevisionTransition', 'programAmendment'):
        plan.pop(key, None)
    plan.update(seriesId=destination.name, createdAtUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        coverageProtocol='compact-v1', expectedRealRuns=288, expectedFidelityRuns=288, expectedRelayRuns=180,
        expectedRunsPerStage=36, rawControllerRoot=str(destination / 'raw'),
        archiveStorageRoot=str(destination / 'evidence'), archiveLinuxRoot=str(destination / 'evidence'),
        simulatorCli=simulator, mqttYRepository=repository, simulatorRevision=revision,
        calibratorCli=simulator, calibratorRepository=repository, calibratorCliSha256=digest(simulator),
        simulationMeasurementSeconds=2, simulationFidelityWorkers=4, simulationRelayWorkers=4,
        simulationCohort='compact-v1', nativeMeasurementSecondsByBroker={broker: 10 for broker in BROKERS},
        simulationMemoryPolicy={'reservationPerJobBytes': 1280 * 1024**2, 'maxWorkingSetBytes': 6 * GIB, 'minSystemAvailableBytes': 2 * GIB},
        simulationRelayMemoryPolicy={'reservationPerJobBytes': 1280 * 1024**2, 'maxWorkingSetBytes': 6 * GIB, 'minSystemAvailableBytes': 2 * GIB},
        orchestrationAdapter=str(snapshots / 'compact-campaign.py'), orchestrationAdapterSha256=digest(snapshots / 'compact-campaign.py'),
        simulationAdapter=str(snapshots / 'accelerated-resume.py'), simulationAdapterSha256=digest(snapshots / 'accelerated-resume.py'),
        modelRevisionNote='Новые короткие натурные серии и новые профили compact-v1; независимые observations не меняют профиль. Полная исходная матрица не подменяется короткой.',
        programAmendmentNote='Проверка стенда compact-v1: payload 16/256, QoS 0/1/2, publishers 1/16, три повтора. Нагрузка 64/128 publishers не проверяется. Натурное окно 10 с, модельное 2 с; p99 и стационарная точность ограничены. Пороговые провалы сохраняются; CPU/RSS не моделируются. Это не подтверждение предельной производительности. Короткие результаты нельзя объединять с полными без обозначения протокола.',
        orchestrationSourceCommit=orchestration_commit,
        predecessorSeries=str(source), borrowedBrokerAssets=str(source.parent / 'brokers-native'), budgetSeconds=21600,
        workloadBudgetSeconds=20100, commandTimeoutSeconds=1800)
    write(root / 'series.json', plan)
    write(root / 'environment.json', {'capturedUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'originalStandEnvironment': str(source / 'environment.json'), 'originalStandEnvironmentSha256': digest(source / 'environment.json'),
        'uname': subprocess.check_output(['uname', '-a'], text=True), 'cpu': subprocess.check_output(['lscpu'], text=True),
        'simulatorCommit': revision, 'benchmarkCommit': subprocess.check_output(['git', '-C', plan['benchmarkRepository'], 'rev-parse', 'HEAD'], text=True).strip(),
        'inputSha256': {str(f.relative_to(root)): digest(f) for f in (root / 'inputs').glob('*.json')}})
    write(root / 'state.json', {'status': 'prepared', 'completed': [], 'brokers': {}, 'coverageProtocol': 'compact-v1'})
    return root


def stop_child(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def comparison_report(root):
    """Compare only matching native points; never merge full and compact observations."""
    root = pathlib.Path(root)
    plan = read(root / 'series.json')
    previous = pathlib.Path(plan['predecessorSeries'])
    previous_plan = read(previous / 'series.json')
    state = read(root / 'state.json')
    reports = root / 'reports'
    reports.mkdir(exist_ok=True)
    rows, coverage = [], []
    for broker in BROKERS:
        counts = []
        for stage in ('calibration', 'validation'):
            paths = [base / 'brokers' / broker / stage / 'broker-observations.v3.json' for base in (previous, root)]
            values = [read(path) if path.exists() else None for path in paths]
            counts.extend(value['runCount'] if value else 0 for value in values)
            if not all(values):
                continue
            full, short = values
            indexed = {(p['messageBytes'], p['qos'], p['publishers']): p for p in full['points']}
            for point in short['points']:
                key = (point['messageBytes'], point['qos'], point['publishers'])
                baseline = indexed.get(key)
                if baseline is None:
                    continue
                real, abbreviated = baseline['completedRps']['mean'], point['completedRps']['mean']
                p99 = [p['observedLatencyQuantiles']['p99Ms'] if p['observedLatencyQuantiles'] else None for p in (baseline, point)]
                row = {'broker': broker, 'stage': stage, 'bytes': key[0], 'qos': key[1], 'publishers': key[2],
                    'fullCompletedRps': real, 'compactCompletedRps': abbreviated,
                    'fullRpsCi95HalfWidth': baseline['completedRps']['ci95HalfWidth'],
                    'compactRpsCi95HalfWidth': point['completedRps']['ci95HalfWidth'],
                    'rpsDeltaPercent': 100 * (abbreviated - real) / real if real else None,
                    'fullP99Ms': p99[0], 'compactP99Ms': p99[1],
                    'p99DeltaPercent': 100 * (p99[1] - p99[0]) / p99[0] if p99[0] else None,
                    'failureDelta': point['publishFailureRate']['mean'] - baseline['publishFailureRate']['mean'],
                    'lossDelta': point['deliveryLossRate']['mean'] - baseline['deliveryLossRate']['mean'],
                    'fullObservationSha256': digest(paths[0]), 'compactObservationSha256': digest(paths[1])}
                rows.append(row)
        coverage.append([broker, f'{counts[0]}/72', f'{counts[1]}/36', f'{counts[2]}/72', f'{counts[3]}/36'])

    def table(headers, items):
        return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'] +
                         ['| ' + ' | '.join(str(cell) for cell in item) + ' |' for item in items])

    def number(value):
        return 'notApplicable' if value is None else f'{value:.4g}'

    document = ['# Полная и сокращённая кампании: сравнение',
        f"Обновлено UTC: {datetime.datetime.now(datetime.timezone.utc).isoformat()}. Статус compact: **{state['status']}**.",
        f"Полная серия: `{previous_plan['seriesId']}`. Короткая: `{plan['seriesId']}`. Полная серия остановлена с сохранением результатов; незавершённые измерения не подменяются нулями.",
        table(['Параметр', 'Полная программа', 'compact-v1'], [
            ['Payload, байт', '16 /256', '16 /256'], ['QoS', '0 /1 /2', '0 /1 /2'],
            ['Publishers', '1 /16 /64 /128', '1 /16'], ['Повторов /seeds', '3 /42,43,44', '3 /42,43,44'],
            ['Точек /запусков на этап и брокер', '24 /72', '12 /36'],
            ['Натурная нагрузка', str(previous_plan.get('nativeMeasurementSecondsByBroker', '30 с')), '10 с'],
            ['Модельная нагрузка', str(previous_plan.get('simulationMeasurementSeconds', 30)) + ' с', '2 с'],
            ['Натурных запусков всего', '576', '288'], ['Fidelity + контроль', '576', '288'],
            ['Relay, включая mixed', '360', '180'], ['Всего основных', '1512', '756'],
            ['Ограничение реального времени', 'Без шестичасового лимита', '6 ч с приоритетом остановки']]),
        '## Фактически доступные натурные серии',
        table(['Брокер', 'Полная калибровка', 'Короткая калибровка', 'Полный контроль', 'Короткий контроль'], coverage),
        '## Сопоставление общих точек',
        'Разность = compact − full; относительная разность делится на full. Это диагностическое сравнение двух отдельных натурных серий, а не критерии Fidelity и не доказательство эквивалентности. CI95 — Student-t по трём повторам. При нулевом знаменателе относительная разность неприменима; QoS0 p99 ACK неприменима. Нагрузки 64/128 publishers исключены из компактного протокола. Короткие окна менее устойчивы для p99; разность может зависеть также от времени запуска и состояния ОС. Профили и пороги по этому сравнению не меняются.',
        table(['Брокер /этап', 'Байт /QoS /N', 'Full RPS ±CI95', 'Compact RPS ±CI95', 'ΔRPS, %', 'Full p99, мс', 'Compact p99, мс', 'Δp99, %', 'Δfailure', 'Δloss'],
              [[r['broker'] + '/' + r['stage'], f"{r['bytes']}/{r['qos']}/{r['publishers']}",
                number(r['fullCompletedRps']) + ' ±' + number(r['fullRpsCi95HalfWidth']),
                number(r['compactCompletedRps']) + ' ±' + number(r['compactRpsCi95HalfWidth']),
                *[number(r[key]) for key in ('rpsDeltaPercent', 'fullP99Ms', 'compactP99Ms', 'p99DeltaPercent', 'failureDelta', 'lossDelta')]] for r in rows]) if rows else
        'Пока нет двух завершённых сопоставимых серий. Таблица будет дополнена автоматически после агрегации.',
        '[Числа и SHA-256 observations](Full-vs-Compact.json) · [CSV](Full-vs-Compact.csv)',
        '## Проверка двойников',
        'Независимые проверки MqttY, критерии RPS ≤15%, p99 ≤20%, |Δfailure/loss| ≤0,05, графики и фактическая доставка Relay представлены отдельно в [Aedes](Aedes.md), [Mosquitto](Mosquitto.md), [ActiveMQ](ActiveMQ.md), [EMQX](EMQX.md), [mixed](MqttRelay-Mixed.md). Failed и notApplicable сохраняются. Натурная точность relay-сети этой кампанией не подтверждается.']
    (reports / 'Full-vs-Compact.md').write_text('\n\n'.join(document) + '\n', encoding='utf-8')
    write(reports / 'Full-vs-Compact.json', {'fullSeries': previous_plan['seriesId'], 'compactSeries': plan['seriesId'], 'rows': rows})
    with (reports / 'Full-vs-Compact.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['broker', 'stage', 'bytes', 'qos', 'publishers'])
        writer.writeheader()
        writer.writerows(rows)


def run_series(root):
    root = pathlib.Path(root).resolve()
    plan = read(root / 'series.json')
    if plan.get('coverageProtocol') not in ('compact-v1', 'overnight-v1'):
        raise ValueError('Unsupported bounded campaign protocol')
    for name in ('simulationAdapter', 'orchestrationAdapter'):
        if digest(plan[name]) != plan[name + 'Sha256']:
            raise RuntimeError(name + ' changed')
    if (root / 'frozen-files.json').exists():
        for name, sha in read(root / 'frozen-files.json').items():
            if digest(name) != sha:
                raise RuntimeError('Frozen campaign file changed: ' + name)
    sys.path.insert(0, str(pathlib.Path(plan['benchmarkRepository']) / 'scripts/campaign'))
    import run
    import report
    original_main = run.main

    def install():
        parent = run.Campaign

        class CompactCampaign(parent):
            def storage_check(self):
                deadline = self.plan.get('workloadDeadlineUtc')
                if deadline and datetime.datetime.now(datetime.timezone.utc) >= datetime.datetime.fromisoformat(deadline):
                    raise BudgetExpired('Compact workload budget exhausted; evidence preserved')
                return super().storage_check()

            def invoke(self, args, tag, cwd=None, allow_fidelity_failure=False):
                self.storage_check()
                log = self.root / 'logs' / (tag + '-' + uuid.uuid4().hex[:8] + '.log')
                with (self.root / 'commands.ndjson').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'argv': list(map(str, args)), 'log': str(log)}) + '\n')
                command = str(args[2]) if len(args) > 2 else ''
                timeout = self.plan.get('nativeCommandTimeoutSeconds', 1800) if command == 'matrix' else 600 if command == 'aggregate' else 180
                if tag.endswith('-histograms'):
                    timeout = 1200
                process = None
                try:
                    with log.open('w') as output:
                        process = subprocess.Popen(list(map(str, args)), cwd=cwd or self.root, stdout=output, stderr=subprocess.STDOUT)
                        self.state['active'] = {'tag': tag, 'pid': process.pid, 'log': str(log)}
                        self.save()
                        started = time.monotonic()
                        while process.poll() is None:
                            self.storage_check()
                            if time.monotonic() - started > timeout:
                                raise RuntimeError('Compact command timed out: ' + tag)
                            time.sleep(1)
                        code = process.returncode
                    if code and not (allow_fidelity_failure and code == 3):
                        raise RuntimeError(f'{tag}: CLI exit {code}; {log}\n' + log.read_text(errors='replace')[-1500:])
                    return code
                finally:
                    if process is not None:
                        stop_child(process)
                    self.state.pop('active', None)
                    self.save()

            def checked_report(self, broker, stage, status, error=None):
                try:
                    self.report(broker, stage, status, error)
                    if self.plan['coverageProtocol'] == 'compact-v1':
                        comparison_report(self.root)
                        for path in (self.root / 'reports').glob('Full-vs-Compact.*'):
                            shutil.copy2(path, self.storage / 'reports' / path.name)
                    else:
                        spec = importlib.util.spec_from_file_location('broker_analysis', self.root / 'source/analyse-campaign.py')
                        analysis = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(analysis)
                        analysis.analyse(self.root)
                except Exception as failure:
                    self.state.setdefault('reportErrors', []).append({'broker': broker, 'error': str(failure)})
                    self.save()

            def stage(self, broker, stage):
                key = broker + '/' + stage
                if key in self.state['completed']:
                    return True
                self.state['brokers'][broker] = {'stage': stage, 'status': 'в работе'}
                self.save()
                try:
                    self.storage_check()
                    if stage in ('pilot', 'calibration', 'validation'):
                        self.real(broker, stage, pilot=stage == 'pilot')
                    elif stage == 'profile':
                        self.profile(broker)
                        self.freeze_transport(broker)
                    else:
                        self.simulate(broker, stage)
                    self.state['completed'].append(key)
                    self.state.get('stageFailures', {}).pop(key, None)
                    self.save()
                    self.checked_report(broker, stage, 'завершён' if stage == 'relay' else 'этап завершён')
                    return True
                except BudgetExpired:
                    raise
                except Exception as failure:
                    self.state.setdefault('stageFailures', {})[key] = str(failure)
                    self.state['brokers'][broker] = {'stage': stage, 'status': 'failed', 'error': str(failure)}
                    self.save()
                    self.checked_report(broker, stage, 'failed', str(failure))
                    return False

            def execute(self):
                self.state.update(status='running', coverageProtocol=self.plan['coverageProtocol'], startedAtUtc=self.plan['startedAtUtc'],
                                  deadlineUtc=self.plan['deadlineUtc'])
                self.state.pop('active', None)
                self.state.pop('activeSimulations', None)
                self.save()
                for broker in (*BROKERS, 'MqttRelay-Mixed'):
                    if broker not in self.state['brokers']:
                        self.checked_report(broker, 'pending', 'не начат')
                exhausted = False
                try:
                    healthy = [broker for broker in BROKERS if self.stage(broker, 'pilot')]
                    for broker in healthy:
                        for stage in ('calibration', 'profile', 'fidelity', 'validation', 'holdout', 'relay'):
                            if not self.stage(broker, stage):
                                break
                    if all(broker + '/relay' in self.state['completed'] for broker in BROKERS):
                        self.stage('MqttRelay-Mixed', 'relay')
                    else:
                        self.checked_report('MqttRelay-Mixed', 'relay', 'notRun', 'Не все четыре брокера прошли необходимые этапы')
                except BudgetExpired as failure:
                    exhausted = True
                    self.state['budgetReason'] = str(failure)
                except KeyboardInterrupt:
                    exhausted = True
                    self.state['budgetReason'] = 'Controller interrupted; evidence retained'
                finally:
                    required = {broker + '/' + stage for broker in BROKERS for stage in ('pilot', 'calibration', 'profile', 'fidelity', 'validation', 'holdout', 'relay')} | {'MqttRelay-Mixed/relay'}
                    self.state['status'] = ('time-budget-exhausted' if exhausted else
                        'completed-with-failures' if self.state.get('stageFailures') or self.state.get('reportErrors') or not required.issubset(self.state['completed']) else 'completed')
                    self.state['finishedAtUtc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    self.save()
                    for broker, entry in list(self.state['brokers'].items()):
                        if exhausted and entry['status'] not in ('завершён', 'failed'):
                            self.checked_report(broker, entry['stage'], 'notCompleted', self.state['budgetReason'])
                    self.registry()
                    if self.plan['coverageProtocol'] == 'compact-v1':
                        comparison_report(self.root)
                    write(self.root / 'completion.json', {'status': self.state['status'], 'completed': self.state['completed'],
                        'stageFailures': self.state.get('stageFailures', {}), 'deadlineUtc': self.plan['deadlineUtc'],
                        'elapsedSeconds': (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(self.plan['startedAtUtc'])).total_seconds()})

        run.Campaign = CompactCampaign
        original_main()

    run.main = install
    spec = importlib.util.spec_from_file_location('compact_adapter', plan['simulationAdapter'])
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    adapter.main()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'prepare':
        parser = argparse.ArgumentParser()
        parser.add_argument('prepare')
        parser.add_argument('--source', required=True)
        parser.add_argument('--destination', required=True)
        parser.add_argument('--simulator', required=True)
        parser.add_argument('--repository', required=True)
        parser.add_argument('--orchestration-commit', required=True)
        args = parser.parse_args()
        if len(args.orchestration_commit) != 40 or any(char not in '0123456789abcdef' for char in args.orchestration_commit):
            raise ValueError('Expected a full orchestration Git commit')
        print(prepare(args.source, args.destination, args.simulator, args.repository, args.orchestration_commit))
    elif len(sys.argv) > 1 and sys.argv[1] == 'cleanup':
        root = pathlib.Path(sys.argv[sys.argv.index('--series') + 1]).resolve()
        plan = read(root / 'series.json')
        outcomes = []
        candidates = [path for base in (pathlib.Path(plan['rawControllerRoot']), root / 'preflight')
                      if base.exists() for path in base.rglob('run-provenance.json')]
        for provenance in candidates:
            metadata = read(provenance)
            broker = metadata.get('broker')
            if broker not in BROKERS or not (provenance.parent / 'stand.selected.json').is_file():
                continue
            if not provenance.resolve().is_relative_to(root.parent):
                raise RuntimeError('Owned broker cleanup path escaped series')
            try:
                os.kill(int(metadata['unitId']), 0)
            except ProcessLookupError:
                continue
            command = [os.environ['MQB_STAND_SHELL'], '-NoLogo', '-NoProfile', '-NonInteractive',
                       '-File', str(pathlib.Path(plan['benchmarkRepository']) / 'scripts/BrokerStand.ps1'),
                       '-Action', 'stop', '-ConfigPath', str(provenance.parent / 'stand.selected.json'),
                       '-OutputDirectory', str(provenance.parent)]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=7)
                outcomes.append({'broker': broker, 'exitCode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
            except Exception as error:
                outcomes.append({'broker': broker, 'error': str(error)})
        write(root / 'cleanup.json', outcomes)
    else:
        run_series(sys.argv[sys.argv.index('--series') + 1])


if __name__ == '__main__':
    main()
