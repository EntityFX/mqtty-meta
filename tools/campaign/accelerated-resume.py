"""Parallelize independent experiments without changing the frozen model or native CLI.

Native measurements remain sequential. Only this adapter and report decoding change;
configs, simulator binaries, profiles, seeds and scientific summaries stay frozen.
"""
import datetime
import hashlib
import io
import json
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid


def graphml_parse(original, source, parser=None):
    """Read legacy UTF-8 GraphML with a UTF-16 declaration, preserving raw evidence."""
    if isinstance(source, (str, pathlib.Path)) and str(source).endswith('.graphml'):
        data = pathlib.Path(source).read_bytes()
        declaration = b'encoding="utf-16"'
        if data.startswith(b'<?xml ') and declaration in data.split(b'?>', 1)[0]:
            data.decode('utf-8')  # Reject other byte encodings; do not mask broken XML.
            return original(io.BytesIO(data.replace(declaration, b'encoding="utf-8"', 1)), parser)
    return original(source, parser)


def scientific_result(directory):
    return {name: json.loads((pathlib.Path(directory) / name).read_text(encoding='utf-8-sig'))
            for name in ('summary.json', 'latency-histogram.json', 'counters.json')}


def cohort_name(plan):
    value = plan.get('simulationCohort', '')
    if not isinstance(value, str) or (value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value)):
        raise ValueError('Invalid simulation cohort name')
    return value


def cohort_rows(rows, cohort):
    if not cohort:
        return rows
    return [row for row in rows if pathlib.Path(row['directory']).parents[1].name.endswith('-' + cohort)]


def memory_working_set(current, statistics):
    # memory.current includes reclaimable file cache populated by archive checks.
    # Keep dirty/writeback pages in the budget until they can be reclaimed.
    reclaimable = max(0, statistics.get('inactive_file', 0) - statistics.get('file_dirty', 0)
                      - statistics.get('file_writeback', 0))
    return current - min(current, reclaimable)


def memory_admission(working, baseline, active, available, policy):
    reserve = int(policy['reservationPerJobBytes'])
    projected = max(working + reserve, baseline + (active + 1) * reserve)
    return (projected <= int(policy['maxWorkingSetBytes']) and
            available >= reserve + int(policy['minSystemAvailableBytes']))


def memory_snapshot(group):
    current = int((group / 'memory.current').read_text())
    stat = {k: int(v) for k, v in (line.split() for line in (group / 'memory.stat').read_text().splitlines())}
    available = int(next(line.split()[1] for line in pathlib.Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))) * 1024
    return {'current': current, 'workingSet': memory_working_set(current, stat),
            'anon': stat.get('anon', 0), 'file': stat.get('file', 0), 'systemAvailable': available}


def native_window_note(plan):
    windows = plan.get('nativeMeasurementSecondsByBroker')
    if not windows:
        return 'Натурные измерения: окно 30 секунд.'
    if any(not isinstance(value, (int, float)) or value <= 0 for value in windows.values()):
        raise ValueError('Invalid native measurement window')
    return 'Натурное измеряемое окно: ' + '; '.join(f'{name} — {seconds:g} с' for name, seconds in windows.items()) + '.'


def simulation_memory_policy(plan, relay):
    return plan.get('simulationRelayMemoryPolicy', plan.get('simulationMemoryPolicy')) if relay else plan.get('simulationMemoryPolicy')


def empirical_options(config, plan, relay, calibration_points):
    """Keep RTT fitting calibration-only; Relay retains explicitly assumed fixed transport."""
    if not plan.get('empiricalProfiles'):
        return config
    tick_us = 100 if relay else 10
    per_second = 1000000 // tick_us
    config.setdefault('ticks', {}).update(tickPeriod=f'00:00:00.{tick_us * 10:07d}', outgoingWaitTicks=1)
    config['experiment'].update(loadMode='openLoopRate' if relay else 'closedLoopPerPublisher',
        transportTiming='fixed' if relay else 'calibratedRttBudget',
        warmupTicks=per_second, measurementTicks=int(plan['simulationMeasurementSeconds']) * per_second,
        drainTimeoutTicks=5 * per_second)
    rtt = sum(p['rttBaselineMs'] for p in calibration_points) / len(calibration_points)
    config['network'].update(transferTicks=max(1, round(rtt / 2 / (tick_us / 1000))),
                             throughputWindowTicks=per_second // 100)
    return config


def main():
    root = pathlib.Path(sys.argv[sys.argv.index('--series') + 1]).resolve()
    plan = json.loads((root / 'series.json').read_text(encoding='utf-8-sig'))
    cohort = cohort_name(plan)
    sys.path.insert(0, str(pathlib.Path(plan['benchmarkRepository']) / 'scripts/campaign'))
    import run
    import report

    original_parse = report.ET.parse
    report.ET.parse = lambda source, parser=None: graphml_parse(original_parse, source, parser)
    read, atomic_json = run.read, run.atomic_json
    original_experiments, original_table = report.experiments, report.table
    durations = {}

    def experiments_with_duration(root, broker):
        rows = cohort_rows(original_experiments(root, broker), cohort)
        durations.clear()
        for row in rows:
            manifest = read(pathlib.Path(row['directory']) / 'experiment-manifest.json')
            h, m, s = manifest['tickPeriod'].split(':')
            tick = int(h) * 3600 + int(m) * 60 + float(s)
            row['measurementSeconds'] = (row['summary']['measurementEndTick'] - row['summary']['measurementStartTick']) * tick
            durations[tuple(row[k] for k in ('stage', 'bytes', 'qos', 'publishers', 'seed'))] = row['measurementSeconds']
        return rows

    def table_with_duration(headers, rows):
        if headers[:2] == ['Этап', 'Байт']:
            return original_table(headers + ['Нагрузка, с'], [list(row) + [durations[tuple(row[:5])]] for row in rows])
        return original_table(headers, rows)

    report.experiments, report.table = experiments_with_duration, table_with_duration
    compact_protocol = plan.get('coverageProtocol') == 'compact-v1'
    report.LIMITATIONS += (f' Программа серии: новые симуляции измеряют {plan.get("simulationMeasurementSeconds", 30)} секунд после warmup {1 if compact_protocol else 5} секунд; '
        f'drain максимум {5 if compact_protocol else 30} секунд, tick 0,1 мс, seeds, пороги и профили сохранены. ' + native_window_note(plan) + ' '
        'Выборки с разной длительностью сохраняются отдельно; длительность каждой строки указана в таблице и JSON. '
        'Сокращённое окно уменьшает число наблюдений и устойчивость p99; эти выборки нельзя считать одинаковыми по длительности. '
        'Параллельны независимые процессы экспериментов; внутри каждого сохранён последовательный refresh. Натурные измерения с ними не пересекаются.')
    if cohort:
        report.LIMITATIONS += (f' Ревизия симуляций: {cohort}; MqttY commit {plan["simulatorRevision"]}. '
            'Таблицы и графики включают только эту ревизию модели. ' +
            ('Натурные observations и профили получены заново по сокращённому протоколу.' if compact_protocol else
             'Результаты прежней модели сохранены отдельно; натурные observations и замороженные профили используются повторно без перекалибровки.'))
        if plan.get('modelRevisionNote'):
            report.LIMITATIONS += ' ' + plan['modelRevisionNote']
    if plan.get('programAmendmentNote'):
        report.LIMITATIONS += ' ' + plan['programAmendmentNote']
    if plan.get('empiricalProfiles'):
        # Replace the legacy timing sentence rather than appending contradictory timings.
        report.LIMITATIONS = report.LIMITATIONS.replace(
            f'warmup {1 if compact_protocol else 5} секунд; drain максимум {5 if compact_protocol else 30} секунд, tick 0,1 мс',
            'warmup 1 секунды; drain максимум 5 секунд; tick Fidelity 0,01 мс, Relay 0,1 мс')
    if plan.get('reportMachineDescription'):
        original_build = run.build

        def build_portable_report(directory, broker, entry):
            original_build(directory, broker, entry)
            reports = pathlib.Path(directory) / 'reports'
            md = reports / (broker + '.md')
            text = md.read_text(encoding='utf-8')
            paragraphs = text.split('\n\n')
            original = next((p for p in paragraphs if p.startswith('Linux 192.168.5.1 выполняет контроллер')), None)
            if original is None:
                raise RuntimeError('Unknown native report layout; machine description was not replaced')
            text = text.replace(original, plan['reportMachineDescription'], 1)
            md.write_text(text, encoding='utf-8')
            body = report.markdown.markdown(text, extensions=['tables', 'fenced_code'])

            def inline_image(match):
                path = reports / report.html.unescape(match.group(1))
                if not path.is_file():
                    return match.group(0)
                return 'src="data:image/png;base64,' + report.base64.b64encode(path.read_bytes()).decode() + '"'

            body = report.re.sub(r'src="([^"]+)"', inline_image, body)
            target = reports / (broker + '.html')
            header, separator, _ = target.read_text(encoding='utf-8').partition('</style>')
            if not separator:
                raise RuntimeError('Unknown native HTML report layout')
            target.write_text(header + separator + body + '</html>', encoding='utf-8')

        run.build = build_portable_report

    if compact_protocol:
        original_compact_build = run.build

        def build_compact_report(directory, broker, entry):
            original_compact_build(directory, broker, entry)
            reports = pathlib.Path(directory) / 'reports'
            md = reports / (broker + '.md')
            text = md.read_text(encoding='utf-8')
            text = text.replace('/72 успешных ключей**', '/36 успешных ключей**').replace('/24 точек.', '/12 точек.')
            text = text.replace('python scripts/campaign/run.py --series <абсолютный каталог серии> --resume',
                                'python ' + plan['orchestrationAdapter'] + ' --series <абсолютный каталог серии> --resume')
            text += '\n\nПродолжение compact-v1 допускается только в пределах исходного шестичасового бюджета; повторный запуск не обнуляет deadline.\n'
            md.write_text(text, encoding='utf-8')
            body = report.markdown.markdown(text, extensions=['tables', 'fenced_code'])

            def inline_image(match):
                path = reports / report.html.unescape(match.group(1))
                if not path.is_file():
                    return match.group(0)
                return 'src="data:image/png;base64,' + report.base64.b64encode(path.read_bytes()).decode() + '"'

            body = report.re.sub(r'src="([^"]+)"', inline_image, body)
            target = reports / (broker + '.html')
            header, separator, _ = target.read_text(encoding='utf-8').partition('</style>')
            if not separator:
                raise RuntimeError('Unknown native HTML report layout')
            target.write_text(header + separator + body + '</html>', encoding='utf-8')

        run.build = build_compact_report

    class AcceleratedCampaign(run.Campaign):
        def real(self, broker, stage, pilot=False):
            if self.plan.get('coverageProtocol') != 'compact-v1':
                return super().real(broker, stage, pilot=pilot)
            cli = ['dotnet', self.plan['benchmarkCli']]
            raw = self.raw / f'{broker}-{stage}'
            self.invoke(cli + ['preflight'] + self.common(broker, stage) +
                        ['--output', self.root / 'preflight' / (broker + '-' + stage + '-' + uuid.uuid4().hex[:8])],
                        broker + '-' + stage + '-preflight')
            args = cli + ['matrix'] + self.common(broker, stage) + ['--campaign', raw, '--archive-root', self.storage / 'attempts',
                    '--archive-mount', self.plan['archiveMount'], '--archive-device', self.plan['archiveDevice']]
            if (raw / 'campaign.json').exists(): args += ['--resume']
            self.invoke(args, broker + '-' + stage + '-matrix')
            self.registry()
            if not pilot:
                output = self.root / 'brokers' / broker / stage
                if not (output / 'broker-observations.v3.json').exists():
                    self.invoke(cli + ['aggregate'] + self.common(broker, stage) + ['--raw', raw, '--output', output], broker + '-' + stage + '-aggregate')
                observations = read(output / 'broker-observations.v3.json')
                if observations['runCount'] != 36 or len(observations['points']) != 12:
                    raise RuntimeError('Incomplete compact native matrix')
                self.report(broker, stage)
                self.verify_stage(broker, stage)

        def profile(self, broker):
            cli = pathlib.Path(self.plan['calibratorCli'])
            with cli.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != self.plan['calibratorCliSha256']:
                    raise RuntimeError('Frozen calibrator CLI changed')
            original = self.plan['simulatorCli'], self.plan['mqttYRepository']
            try:
                self.plan['simulatorCli'] = str(cli)
                self.plan['mqttYRepository'] = self.plan['calibratorRepository']
                profile = self.root / 'brokers' / broker / 'broker-calibration.v3.json'
                if self.plan.get('empiricalProfiles') and not profile.exists():
                    histograms = profile.with_name('latency-histograms.json')
                    observations = profile.parent / 'calibration/broker-observations.v3.json'
                    if not histograms.exists():
                        self.invoke([sys.executable, self.plan['histogramExporter'], '--observations', observations,
                                     '--raw', self.raw / (broker + '-calibration'), '--output', histograms], broker + '-histograms')
                    self.invoke(['dotnet', str(cli), 'calibrate', '--observations', observations,
                                 '--latency-histograms', histograms, '--output', profile,
                                 '--mqtty-repository', self.plan['calibratorRepository'], '--broker', broker], broker + '-calibrate-empirical')
                if self.plan.get('coverageProtocol') == 'compact-v1' and not profile.exists():
                    self.invoke(['dotnet', str(cli), 'calibrate', '--observations', self.root / 'brokers' / broker / 'calibration/broker-observations.v3.json',
                                 '--output', profile, '--mqtty-repository', self.plan['calibratorRepository'],
                                 '--broker', broker, '--coverage-protocol', 'compact-v1'], broker + '-calibrate-compact')
                return super().profile(broker)
            finally:
                self.plan['simulatorCli'], self.plan['mqttYRepository'] = original

        def execute(self):
            self.state['status'] = 'running'
            self.state.pop('error', None)
            self.state.pop('active', None)
            self.state.pop('activeSimulations', None)
            self.save()
            super().execute()

        def simulate(self, broker, stage):
            mixed = broker == 'MqttRelay-Mixed'
            relay = stage == 'relay' or mixed
            parent = self.root / ('mixed' if mixed else 'brokers/' + broker) / 'simulations'
            measurement_seconds = int(self.plan.get('simulationMeasurementSeconds', 30))
            compact = self.plan.get('coverageProtocol') == 'compact-v1'
            if measurement_seconds not in ((2,) if compact or self.plan.get('empiricalProfiles') else (10, 30)):
                raise RuntimeError('Only explicitly supported 10s or 30s simulation windows are allowed')
            legacy = parent / stage / 'index.json'
            # Reuse a complete original cohort, preserving its original window and files.
            complete_legacy = not cohort and legacy.exists() and len(read(legacy)['runs']) == 72
            if complete_legacy:
                measurement_seconds = 30
            base = parent / (stage if measurement_seconds == 30 else stage + f'-{measurement_seconds}s')
            if cohort:
                base = base.with_name(base.name + '-' + cohort)
            base.mkdir(parents=True, exist_ok=True)
            index = read(base / 'index.json') if (base / 'index.json').exists() else {'stage': stage, 'measurementSeconds': measurement_seconds, 'runs': []}
            names = list(run.BROKERS) if mixed else [broker]
            profiles = [self.profile(name) for name in names]
            reference_stage = 'validation' if stage == 'holdout' else 'calibration'
            observations = {name: read(self.root / 'brokers' / name / reference_stage / 'broker-observations.v3.json') for name in names}
            transport = read(self.root / 'transport.json')
            jobs = []
            for payload in (16, 256):
                for qos in range(3):
                    for publishers in ((1, 16) if compact else (1, 16, 64, 128)):
                        points = {name: next(p for p in observations[name]['points'] if (p['messageBytes'], p['qos'], p['publishers']) == (payload, qos, publishers)) for name in names}
                        offered = (4 if relay else 1) * min(p['attemptedRps']['mean'] for p in points.values())
                        for seed in (42, 43, 44):
                            key = f'm{payload}.q{qos}.p{publishers}.s{seed}'
                            if any(item['key'] == key for item in index['runs']):
                                continue
                            config = read(self.plan['simulatorExample'])
                            config['experiment'].update(topologyMode='mqttRelay' if relay else 'brokerFidelity', brokers=4 if relay else 1,
                                networkLength=2 if relay else 1, clientsPerBroker=publishers, payloadBytes=payload,
                                publishQos=['atMostOnce', 'atLeastOnce', 'exactlyOnce'][qos], subscribeQos=['atMostOnce', 'atLeastOnce', 'exactlyOnce'][qos],
                                brokerAssignmentMode='mixed' if mixed else broker[0].lower() + broker[1:], mixedBrokerTypes=list(run.BROKERS) if mixed else [],
                                offeredRps=offered, randomSeed=seed, parallel=False, refreshStrategy=0, profileClientCountMode='publishers',
                                warmupTicks=10000 if compact else 50000, measurementTicks=measurement_seconds * 10000,
                                drainTimeoutTicks=50000 if compact else 300000)
                            config['network'].update(transport['network'])
                            config['network']['transferTicks'] = transport['transferTicksByBroker'][names[0]] if not mixed else max(transport['transferTicksByBroker'].values())
                            if self.plan.get('empiricalProfiles'):
                                calibration_points = [point for name in names for point in read(self.root / 'brokers' / name / 'calibration/broker-observations.v3.json')['points']]
                                empirical_options(config, self.plan, relay, calibration_points)
                            config.update(mqttYRepository=self.plan['mqttYRepository'], mqttBenchmarkRepository=self.plan['benchmarkRepository'])
                            config.pop('brokerProfilePath', None)
                            if mixed:
                                config['brokerProfilePaths'] = [str(p) for p in profiles]
                            else:
                                config['brokerProfilePath'] = str(profiles[0])
                            if stage == 'holdout':
                                config['referenceObservationsPath'] = str(self.root / 'brokers' / broker / 'validation/broker-observations.v3.json')
                                if compact: config['referenceCoverageProtocol'] = 'compact-v1'
                            path = base / (key + '.json')
                            if path.exists() and read(path) != config:
                                raise RuntimeError('Simulation config changed')
                            if not path.exists():
                                atomic_json(path, config)
                            output = self.storage / ('mixed' if mixed else 'brokers/' + broker) / 'simulations' / base.name / key
                            jobs.append({'key': key, 'output': output, 'config': path, 'bytes': payload, 'qos': qos, 'publishers': publishers, 'seed': seed, 'offeredRps': offered})
            workers = int(self.plan.get('simulationRelayWorkers' if relay else 'simulationFidelityWorkers', 2 if relay else 4))
            if not 1 <= workers <= 4:
                raise RuntimeError('Simulation concurrency must be between one and four')
            active = []
            policy = simulation_memory_policy(self.plan, relay)
            group = pathlib.Path('/sys/fs/cgroup' + self.state['controllerRuntime']['cgroup'])
            baseline = memory_snapshot(group)['workingSet'] if policy else 0
            last_memory_log = 0

            def memory_ready():
                nonlocal last_memory_log
                if not policy:
                    used = int((group / 'memory.current').read_text())
                    if used > 7 * run.GIB:
                        raise RuntimeError('Simulator process group exceeded 7 GiB; evidence retained')
                    return used < 5 * run.GIB
                snapshot = memory_snapshot(group)
                self.state['simulationResources'] = snapshot | {'activeProcesses': len(active), 'workerLimit': workers}
                if time.monotonic() - last_memory_log >= 10:
                    with (self.root / 'simulation-memory.ndjson').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
                                                | self.state['simulationResources']) + '\n')
                    last_memory_log = time.monotonic()
                if snapshot['workingSet'] > int(policy['maxWorkingSetBytes']):
                    raise RuntimeError('Simulator working-set memory guard; evidence retained')
                if snapshot['systemAvailable'] < int(policy['minSystemAvailableBytes']):
                    raise RuntimeError('Host available-memory guard; evidence retained')
                return memory_admission(snapshot['workingSet'], baseline, len(active), snapshot['systemAvailable'], policy)

            try:
                while jobs or active:
                    self.storage_check()
                    ready = memory_ready()
                    while jobs and len(active) < workers and ready:
                        self.storage_check()
                        job = jobs.pop(0)
                        tag = broker + '-' + stage + '-' + job['key']
                        log = self.root / 'logs' / (tag + '-' + uuid.uuid4().hex[:8] + '.log')
                        log.parent.mkdir(exist_ok=True)
                        args = ['dotnet', self.plan['simulatorCli'], 'experiment', '--config', str(job['config']), '--output', str(job['output'])]
                        with (self.root / 'commands.ndjson').open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'argv': args, 'cwd': str(self.root), 'log': str(log), 'processParallelWorkers': workers}) + '\n')
                        output_stream = log.open('w', encoding='utf-8')
                        try:
                            process = subprocess.Popen(args, cwd=self.root, stdout=output_stream, stderr=subprocess.STDOUT)
                        except BaseException:
                            output_stream.close()
                            raise
                        active.append((job, process, output_stream, log, tag))
                        job['startedMonotonic'] = time.monotonic()
                        self.state['activeSimulations'] = [{'tag': t, 'pid': p.pid, 'log': str(l)} for _, p, _, l, t in active]
                        self.state['active'] = self.state['activeSimulations'][0]
                        self.save()
                        ready = memory_ready()
                    if not active and jobs:
                        raise RuntimeError('Insufficient memory to admit another simulator')
                    time.sleep(2)
                    if shutil.disk_usage(self.system_disk()).free < 6 * run.GIB or shutil.disk_usage(self.storage).free < 12 * run.GIB:
                        raise RuntimeError('Low disk space; simulator evidence retained')
                    for item in list(active):
                        job, process, output_stream, log, tag = item
                        code = process.poll()
                        if code is None:
                            if self.plan.get('simulationCommandTimeoutSeconds') and time.monotonic() - job['startedMonotonic'] > self.plan['simulationCommandTimeoutSeconds']:
                                raise RuntimeError('Simulation command timed out; partial evidence retained: ' + tag)
                            continue
                        output_stream.close()
                        if code not in (0, 3):
                            raise RuntimeError(f'{tag}: CLI exit {code}; evidence retained: {log}')
                        directory = max(job['output'].iterdir(), key=lambda p: p.name)
                        if read(directory / 'summary.json')['status'] != 'completed':
                            raise RuntimeError('Simulation phase failed: ' + tag)
                        for name, profile in zip(names, profiles):
                            if run.digest(profile) != self.state['profiles'][name]:
                                raise RuntimeError('Frozen twin changed during simulation')
                        index['runs'].append({k: job[k] for k in ('key', 'bytes', 'qos', 'publishers', 'seed', 'offeredRps')} | {'directory': str(directory)})
                        index['runs'].sort(key=lambda r: (r['bytes'], r['qos'], r['publishers'], r['seed']))
                        atomic_json(base / 'index.json', index)
                        active.remove(item)
                    self.state['activeSimulations'] = [{'tag': t, 'pid': p.pid, 'log': str(l)} for _, p, _, l, t in active]
                    if active:
                        self.state['active'] = self.state['activeSimulations'][0]
                    else:
                        self.state.pop('active', None)
                    self.save()
            finally:
                # Reap every owned child on failure, including exceptions during admission.
                for _, process, _, _, _ in active:
                    if process.poll() is None:
                        process.send_signal(signal.SIGINT)
                deadline = time.monotonic() + 20
                for _, process, output_stream, _, _ in active:
                    try:
                        process.wait(timeout=max(.1, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    output_stream.close()
                self.state.pop('active', None)
                self.state.pop('activeSimulations', None)
                self.save()
            self.report(broker, stage)

    run.Campaign = AcceleratedCampaign
    run.main()


if __name__ == '__main__':
    main()
