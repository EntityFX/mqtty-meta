"""Read-only source/document audit; never starts a model or contacts Linux.

Run --capture once before editing to seal protected text and frozen source copies.
Run without arguments after editing to verify the receipt and document links.
Only this audit directory receives output. Existing evidence JSON is read only.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import re
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
COMMIT = '4c658536586f88a89cd3ff5ab0915903640cf3c9'
PREFIX = 'src/EntityFX.MqttSimulator/'
SOURCE_PATHS = [
    PREFIX + 'EntityFX.MqttY.Plugin.Mqtt/' + name for name in [
        'MqttBroker.cs', 'MqttClient.cs', 'BrokerProfile/BrokerRateLimiter.cs',
        'BrokerProfile/BrokerProcessingQueue.cs', 'BrokerProfile/DeterministicMqttSampler.cs',
        'BrokerProfile/BrokerCalibrationV3.cs', 'BrokerProfile/BrokerCalibrationEmpirical.cs',
        'Experiments/MqttRelayExperimentRunner.cs', 'Counter/BrokerMeasurement.cs',
    ]
] + [
    PREFIX + 'EntityFX.MqttY.Contracts.Mqtt/BrokerProfile/EmpiricalLatencyDistribution.cs',
    PREFIX + 'EntityFX.MqttY.Contracts.Mqtt/BrokerProfile/MqttBrokerProfile.cs',
    PREFIX + 'EntityFX.MqttY.MqttRelay.App/MqttRelayExperimentCommand.cs',
]
ARTICLE = 'docs/dissertation/articles/article-1-twin-validation.md'
NOTES = 'reports/analysis/Article-1-technical-notes.md'
METHOD = 'docs/dissertation/article-1-current-model-method.md'
MANIFEST = 'reports/audits/dissertation/article-1-main-execution-v1/runtime-manifest.json'
RAW = Path('C:/temp/mqtty-evidence/article1-matched-main-v1/raw-001')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return path.read_text(encoding='utf-8-sig')


def protected():
    article, notes = read(ROOT / ARTICLE), read(ROOT / NOTES)
    return {
        'article-before-section-2': sha(article.split('## 2. ')[0].encode()),
        'article-from-section-3': sha(article[article.index('## 3. '):].encode()),
        'notes-historical-boundary-and-archive': sha(notes[notes.index('**Всё ниже'):].encode()),
        MANIFEST: sha((ROOT / MANIFEST).read_bytes()),
    }


def write_tsv(path, header, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow(header)
        writer.writerows(rows)


def run(capture):
    rows, outputs = [], []
    for relative in SOURCE_PATHS:
        result = subprocess.run(['git', '-C', str(ROOT / 'mqtty'), 'show',
                                 f'{COMMIT}:{relative}'], capture_output=True, check=True)
        snapshot = 'sources/' + Path(relative).name + '.txt'
        numbered = ''.join(f'{i:04d} {line}\n' for i, line in
                           enumerate(result.stdout.decode('utf-8-sig').splitlines(), 1))
        if capture:
            (HERE / snapshot).parent.mkdir(exist_ok=True)
            (HERE / snapshot).write_text(numbered, encoding='utf-8', newline='\n')
        assert read(HERE / snapshot) == numbered, snapshot
        rows.append((relative, COMMIT, sha(result.stdout), snapshot, sha(numbered.encode())))
    if capture:
        write_tsv(HERE / 'sources.tsv', ['source', 'commit', 'source_sha256', 'snapshot', 'snapshot_sha256'], rows)
        write_tsv(HERE / 'protected-text.tsv', ['scope', 'sha256'], protected().items())
    else:
        with (HERE / 'sources.tsv').open(encoding='utf-8', newline='') as stream:
            assert list(csv.reader(stream, delimiter='\t'))[1:] == [list(row) for row in rows]
    outputs.append(f'Снимки исходников из git show: проверено {len(rows)}/{len(SOURCE_PATHS)}.')
    with (HERE / 'protected-text.tsv').open(encoding='utf-8', newline='') as stream:
        baseline = dict(list(csv.reader(stream, delimiter='\t'))[1:])
    assert baseline == protected(), 'Protected text or runtime manifest changed'
    outputs.append('Текст статьи вне раздела 2, архив технических заметок и runtime-манифест не изменились.')
    manifest = json.loads(read(ROOT / MANIFEST))
    assert manifest['mqttyCommit'] == COMMIT
    for relative, digest in manifest['probeSourceSha256'].items():
        assert sha((ROOT / relative.replace('\\', '/')).read_bytes()) == digest, relative
    outputs.append(f"Исходники Probe: {len(manifest['probeSourceSha256'])} хеша совпадают с зафиксированным манифестом.")
    assert (ROOT / MANIFEST).read_bytes() == (RAW / 'runtime-manifest.json').read_bytes()
    freeze = json.loads(read(RAW / 'profile-freeze.json'))
    input_rows, parameter_rows = [], []
    for broker in ['Aedes', 'Mosquitto', 'ActiveMQ', 'EMQX']:
        profile_rel = f'attempt-001/profiles/{broker}.profile.json'
        profile_bytes = (RAW / profile_rel).read_bytes()
        assert sha(profile_bytes) == freeze['profiles'][broker]['sha256']
        profile = json.loads(profile_bytes)['calibration']
        assert profile['provenance']['mqttYCommitSha'] == COMMIT
        point, = profile['points']
        assert (point['messageBytes'], point['qos'], point['publishers']) == (16, 1, 16)
        assert point['rateRejectionRate'] == 0
        assert point['capacityRps'] == point['attemptedRps']
        assert point['conditionalPublishFailureRate'] == point['targetPublishFailureRate']
        input_rows.append((str(RAW / profile_rel), sha(profile_bytes)))
        resolutions = []
        for repeat in [1, 2, 3]:
            job_rel = f'attempt-001/jobs/model.{broker}.r{repeat}'
            for filename in ['config.json', 'raw/transport-resolution.json']:
                relative = f'{job_rel}/{filename}'
                data = (RAW / relative).read_bytes()
                input_rows.append((str(RAW / relative), sha(data)))
            config = json.loads(read(RAW / job_rel / 'config.json'))
            assert config['profileSha256'] == sha(profile_bytes)
            assert config['runtimeIdentity'] == 'runtime-manifest-sha256:' + sha((ROOT / MANIFEST).read_bytes())
            assert config['tickSeconds'] == 1e-5
            assert (config['warmupSeconds'], config['measurementSeconds'], config['drainSeconds']) == (1, 4, 5)
            resolution = json.loads(read(RAW / job_rel / 'raw/transport-resolution.json'))
            assert resolution['method'] == 'empirical-service-budget-v1'
            assert resolution['capacityPacing'] is True
            assert resolution['calibrationRttMs'] == point['rttBaselineMs']
            resolutions.append(resolution)
        assert resolutions[0] == resolutions[1] == resolutions[2]
        resolution = resolutions[0]
        assert resolution['protocolFloorTicks'] == (8 if broker == 'EMQX' else 6)
        parameter_rows.append((broker, point['capacityRps'], point['targetCompletedRps'],
                               point['conditionalPublishFailureRate'], point['conditionalDeliveryLossRate'],
                               point['rttBaselineMs'], resolution['resolvedTransferTicks'],
                               resolution['protocolFloorTicks']))
    write_tsv(HERE / 'inputs.tsv', ['input', 'sha256'], input_rows)
    write_tsv(HERE / 'parameters.tsv', ['broker', 'capacity_attempted_rps', 'target_completed_rps',
              'publish_failure_rate', 'conditional_delivery_loss_rate', 'rtt_ms', 'transfer_ticks', 'protocol_floor_ticks'], parameter_rows)
    outputs.append('Локально проверены четыре зафиксированных профиля и 12 пар конфигураций/настроек транспорта.')
    if not capture:
        link_count = 0
        for relative in [METHOD, ARTICLE, NOTES]:
            path = ROOT / relative
            for target in re.findall(r'\]\(([^)]+)\)', read(path)):
                if '://' in target or target.startswith('#'):
                    continue
                clean = target.split('#')[0]
                assert (path.parent / clean).exists(), f'{relative}: {target}'
                link_count += 1
        outputs.append(f'Относительные ссылки документов: существуют все {link_count} целей.')
    outputs.append('Сборки, симуляции, опыты и сетевые обращения не запускались; бинарное соответствие не проверялось.')
    report = '# Проверка исходников и границ правки\n\n' + '\n'.join('- ' + line for line in outputs) + '\n'
    (HERE / 'verification.md').write_text(report, encoding='utf-8', newline='\n')
    print('\n'.join(outputs))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', action='store_true')
    run(parser.parse_args().capture)
