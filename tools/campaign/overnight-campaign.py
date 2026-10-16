"""Prepare a separate full-grid, bounded overnight cohort using the empirical model."""
import argparse
import datetime
import importlib.util
import json
import pathlib
import shutil
import subprocess


def module(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def native_config(source, broker, stage):
    result = module('compact-campaign').native_config(source, broker, stage)
    if stage != 'pilot':
        result.update(publishers=[1, 16, 64, 128], measurementSeconds=20,
                      drainTimeoutSeconds=10, quietPeriodSeconds=10)
    return result


def timing(now, deadline):
    remaining = int((deadline - now).total_seconds())
    if now.tzinfo is None or deadline.tzinfo is None or not 3600 <= remaining <= 16 * 3600:
        raise ValueError('Need an aware absolute deadline between one and sixteen hours ahead')
    return dict(startedAtUtc=now.isoformat(), deadlineUtc=deadline.isoformat(),
                workloadDeadlineUtc=(deadline - datetime.timedelta(minutes=15)).isoformat(),
                runtimeMaxSeconds=remaining - 120, stopTimeoutSeconds=30,
                budgetSeconds=remaining, workloadBudgetSeconds=remaining - 900)


def prepare(args):
    compact = module('compact-campaign')
    source = pathlib.Path(args.source)
    if compact.read(source / 'state.json')['status'] != 'completed':
        raise RuntimeError('Previous campaign must be technically completed')
    for broker in compact.BROKERS:
        for stage in ('calibration', 'validation'):
            if not compact.read(source / 'brokers' / broker / stage / 'archive-verification.json').get('verified'):
                raise RuntimeError('Previous native archives must have verified reaggregation')
        if not (source / 'reports' / (broker + '-Analysis.md')).is_file():
            raise RuntimeError('Write per-broker analysis before preparing the larger campaign')
    diagnostic = compact.read(pathlib.Path(args.diagnostic) / 'state.json')
    if diagnostic.get('status') != 'completed' or len(diagnostic.get('runs', [])) != 24:
        raise RuntimeError('Wait for the complete native diagnostic boundary')
    if any(r.get('status') != 'success' for r in diagnostic['runs']):
        raise RuntimeError('Native diagnostic has technical failures')
    clock = timing(datetime.datetime.now(datetime.timezone.utc), datetime.datetime.fromisoformat(args.deadline))
    root = compact.prepare(source, args.destination, args.simulator, args.repository, args.orchestration_commit)
    for name in ('export-latency-histograms.py', 'analyse-campaign.py', 'overnight-campaign.py', 'launch-owned-job.py'):
        shutil.copy2(pathlib.Path(__file__).with_name(name), root / 'source' / name)
    for broker in compact.BROKERS:
        for stage in ('pilot', 'calibration', 'validation'):
            compact.write(root / 'inputs' / f'{broker}-{stage}.json', native_config(compact.read(source / 'inputs' / f'{broker}-{stage}.json'), broker, stage))
    plan = compact.read(root / 'series.json')
    plan.update(clock)
    plan.update(coverageProtocol='overnight-v1', expectedRealRuns=576, expectedFidelityRuns=576,
        expectedRelayRuns=360, expectedRunsPerStage=72, simulationCohort='overnight-v1',
        nativeMeasurementSecondsByBroker={broker: 20 for broker in compact.BROKERS},
        empiricalProfiles=True, histogramExporter=str(root / 'source/export-latency-histograms.py'),
        nativeCommandTimeoutSeconds=7200, simulationCommandTimeoutSeconds=1800,
        modelRevisionNote='Новая schema 4: эмпирическая CDF; Fidelity closed-loop, tick 10 мкс и калибровочный RTT budget. Relay open-loop, tick 100 мкс и фиксированная модель транспорта; натурная точность Relay не установлена.',
        programAmendmentNote='overnight-v1: полная сетка 16/256 байт × QoS0/1/2 × publishers 1/16/64/128 × 3 повтора. Native warmup/measurement/cooldown/drain 5/20/1/10 с; capture envelope 39 с. Модель warmup/measurement/drain 1/2/5 с. Существующие провалы критериев не скрываются. Native-настройки брокеров сохранены без подбора по контролю; Mosquitto outgoing queue loss остаётся предметом измерения. QoS0 ACK latency неприменима. Симуляции не пересекаются с native; CPU/RSS не моделируются. Незавершённое покрытие при deadline сохраняется явно.',
        simulationMemoryPolicy={'reservationPerJobBytes': 1280 * 1024**2, 'maxWorkingSetBytes': 6 * 1024**3, 'minSystemAvailableBytes': 2 * 1024**3},
        simulationRelayMemoryPolicy={'reservationPerJobBytes': 1280 * 1024**2, 'maxWorkingSetBytes': 6 * 1024**3, 'minSystemAvailableBytes': 2 * 1024**3})
    compact.write(root / 'series.json', plan)
    frozen_paths = list((root / 'source').glob('*.py')) + list((root / 'inputs').glob('*.json'))
    frozen_paths += list(pathlib.Path(args.simulator).parent.glob('*.dll'))
    frozen_paths += list(pathlib.Path(plan['benchmarkCli']).parent.glob('*.dll'))
    compact.write(root / 'frozen-files.json', {str(p): compact.digest(p) for p in frozen_paths})
    environment = compact.read(root / 'environment.json')
    environment['inputSha256'] = {str(p.relative_to(root)): compact.digest(p) for p in (root / 'inputs').glob('*.json')}
    environment['diagnosticStateSha256'] = compact.digest(pathlib.Path(args.diagnostic) / 'state.json')
    compact.write(root / 'environment.json', environment)
    compact.write(root / 'state.json', {'status': 'prepared', 'completed': [], 'brokers': {}, 'coverageProtocol': 'overnight-v1'})
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('source', 'destination', 'simulator', 'repository', 'orchestration-commit', 'deadline', 'diagnostic'):
        parser.add_argument('--' + field, required=True)
    print(prepare(parser.parse_args()))
