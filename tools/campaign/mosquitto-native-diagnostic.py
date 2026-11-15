"""Separate bounded Mosquitto queue/TCP experiment; never changes the baseline stand."""
import datetime
import hashlib
import json
import os
import pathlib
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
import zipfile

GIB = 1024**3


def read(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def prepare(baseline, destination):
    baseline, destination = pathlib.Path(baseline), pathlib.Path(destination)
    old = read(baseline / 'series.json')
    if destination.exists() or not destination.resolve().is_relative_to(pathlib.Path(old['archiveMount']).resolve()):
        raise ValueError('Need a new directory on the evidence mount')
    actual = subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE', '-T', old['archiveMount']], text=True).split()
    if actual != [old['archiveMount'], old['archiveDevice'], 'ext4'] or shutil.disk_usage(old['archiveMount']).free < 20 * GIB:
        raise RuntimeError('Evidence disk guard')
    root = destination / 'controller'
    (root / 'inputs').mkdir(parents=True)
    (root / 'logs').mkdir()
    (destination / 'evidence/attempts').mkdir(parents=True)
    port = 54883
    variants = {'baseline': '', 'tcp-nodelay': 'set_tcp_nodelay true\n',
        'inflight-200': 'max_inflight_messages 200\n',
        'queue-100000': 'max_queued_messages 100000\nmax_queued_bytes 67108864\n'}
    for variant, amendment in variants.items():
        config = root / 'inputs' / (variant + '.conf')
        config.write_text(f'listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n'
            'log_dest stderr\nlog_type error\nlog_type warning\nlog_type notice\nlog_type subscribe\nsys_interval 1\n' + amendment)
        stand = read(baseline / 'inputs/Mosquitto-stand.json')
        broker = stand['brokers'][0]
        broker.update(mqttUri=f'mqtt://127.0.0.1:{port}', args=['-c', str(config)], configFiles=[str(config)],
                      singleHostMqttPort=port, distributedMqttPort=port)
        write(root / 'inputs' / (variant + '-stand.json'), stand)
    cases = []
    rng = random.Random(20261016)
    for block in (1, 2, 3):
        names = list(variants)
        rng.shuffle(names)
        for variant in names:
            publishers = [1, 16]
            rng.shuffle(publishers)
            for n in publishers:
                name = f'{variant}.block{block}.p{n}'
                config = read(baseline / 'inputs/Mosquitto-calibration.json')
                config.update(messageBytes=[16], qos=[1], publishers=[n], repeats=1, warmupSeconds=5,
                    measurementSeconds=10, cooldownSeconds=1, drainTimeoutSeconds=30,
                    quietPeriodSeconds=30,
                    telemetryCaptureReserveSeconds=3, preflightSysWaitSeconds=1, maxAttempts=1, seed=20261016)
                path = root / 'inputs' / (name + '.json')
                write(path, config)
                cases.append({'name': name, 'variant': variant, 'block': block, 'publishers': n,
                    'config': str(path), 'stand': str(root / 'inputs' / (variant + '-stand.json'))})
    plan = {key: old[key] for key in ('benchmarkRepository', 'benchmarkCli', 'archiveMount', 'archiveDevice', 'deadlineUtc')}
    plan.update(rawControllerRoot=str(destination / 'raw'), archiveStorageRoot=str(destination / 'evidence'),
        seriesId=destination.name, port=port, cases=cases, inputSha256={p.name: digest(p) for p in (root / 'inputs').iterdir()},
        boundary='after-complete-baseline', interpretation='Four native variants, randomized blocks, three logical repetitions; not model calibration coverage')
    write(root / 'series.json', plan)
    write(root / 'state.json', {'status': 'prepared', 'runs': []})
    return root


class SysObserver:
    def __init__(self, port, path):
        self.port, self.path = port, pathlib.Path(path)
        self.stop = threading.Event()
        self.process = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.pump, daemon=True)

    def pump(self):
        with self.path.open('a', encoding='utf-8') as stream:
            while not self.stop.is_set():
                with self.lock:
                    if self.stop.is_set():
                        break
                    self.process = subprocess.Popen(['mosquitto_sub', '-h', '127.0.0.1', '-p', str(self.port),
                        '-i', 'mb-diag-sys', '-t', '$SYS/broker/#', '-F', '%t\t%p'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in self.process.stdout:
                    topic, separator, payload = line.rstrip('\n').partition('\t')
                    stream.write(json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        'topic': topic if separator else None, 'payload': payload if separator else line.strip()}) + '\n')
                    stream.flush()
                self.process.wait()
                self.process.stdout.close()
                self.stop.wait(.5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *unused):
        with self.lock:
            self.stop.set()
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        self.thread.join(timeout=5)


def report(root, plan, state):
    rows = ['# Mosquitto: отдельная диагностика недоставки',
        f"Статус: **{state['status']}**. Выполнено {len(state['runs'])}/24. Исходная кампания не изменялась.",
        'Один subscriber, 16 байт, QoS1, publishers 1/16, три логических повтора в рандомизированных блоках. '
        'Во всех вариантах включены notice/subscribe и SYS 1 Гц. Это отдельный протокол с дополнительным наблюдателем. '
        'SYS counters глобальны; dropped>0 и notice локализуют отбрасывание, но sent нельзя напрямую приравнивать доставкам одного topic.',
        '| Вариант | Блок | N | Completed RPS | Недоставка | SYS dropped max | Notice об отбрасывании | Статус | Warnings |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in state['runs']:
        m = row.get('measurement', {})
        rows.append('| ' + ' | '.join(map(str, [row['variant'], row['block'], row['publishers'],
            round(m.get('completedRps', 0), 2), round(m.get('deliveryLossRate', 0), 6),
            row.get('sysDroppedMax', 'notObserved'), row.get('outgoingDropNotices', 0), row['status'], '; '.join(row.get('warnings', [])) or '0'])) + ' |')
    rows += ['Не получено к завершению drain не означает автоматически безвозвратную потерю. '
        'Архивы, неудачные попытки, SYS NDJSON и хэши сохранены на hd250. Неуспешные строки не входят в вывод об улучшении.']
    path = root / 'reports/Mosquitto-native-diagnostic.md'
    path.parent.mkdir(exist_ok=True)
    path.write_text('\n\n'.join(rows[:3]) + '\n\n' + '\n'.join(rows[3:]) + '\n', encoding='utf-8')


def run(root):
    root = pathlib.Path(root)
    plan, state = read(root / 'series.json'), read(root / 'state.json')
    deadline = datetime.datetime.fromisoformat(plan['deadlineUtc'])
    state['status'] = 'running'
    def storage_check():
        actual = subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE', '-T', plan['archiveMount']], text=True).split()
        if actual != [plan['archiveMount'], plan['archiveDevice'], 'ext4'] or shutil.disk_usage(root).free < 20 * GIB or shutil.disk_usage('/').free < 10 * GIB:
            raise RuntimeError('Disk/mount guard: sources retained')
        if datetime.datetime.now(datetime.timezone.utc) >= deadline:
            raise TimeoutError('Original six-hour deadline exhausted')
    child = None
    try:
        for case in plan['cases']:
            if any(row['name'] == case['name'] for row in state['runs']):
                continue
            storage_check()
            for name, expected in plan['inputSha256'].items():
                if digest(root / 'inputs' / name) != expected:
                    raise RuntimeError('Frozen native diagnostic input changed')
            raw = pathlib.Path(plan['rawControllerRoot']) / case['name']
            command = ['dotnet', plan['benchmarkCli'], 'matrix', '--config', case['config'], '--stand', case['stand'],
                '--benchmark-repo', plan['benchmarkRepository'], '--stand-script', str(pathlib.Path(plan['benchmarkRepository']) / 'scripts/BrokerStand.ps1'),
                '--campaign', str(raw), '--archive-root', str(pathlib.Path(plan['archiveStorageRoot']) / 'attempts'),
                '--archive-mount', plan['archiveMount'], '--archive-device', plan['archiveDevice']]
            if (raw / 'campaign.json').exists():
                command += ['--resume']
            syslog = root / 'logs' / (case['name'] + '-sys.ndjson')
            state['active'] = case
            write(root / 'state.json', state)
            with SysObserver(plan['port'], syslog), (root / 'logs' / (case['name'] + '.log')).open('w') as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                started = time.monotonic()
                while child.poll() is None:
                    storage_check()
                    if time.monotonic() - started > 180:
                        raise TimeoutError('Diagnostic native case exceeded 180 s')
                    time.sleep(1)
            row = dict(case, status='failed', exitCode=child.returncode)
            if child.returncode == 0:
                results = [p for p in raw.glob('attempts/*/attempt-*/result.json') if read(p)['status'] == 'success']
                if len(results) != 1:
                    raise RuntimeError('Incomplete native diagnostic case')
                observation = read(results[0])['observation']
                receipt = read(results[0].parent / 'archive.json')
                archive = pathlib.Path(receipt['archivePath'])
                if digest(archive) != receipt['sha256']:
                    raise RuntimeError('Native diagnostic archive hash mismatch')
                with zipfile.ZipFile(archive) as z:
                    logs = '\n'.join(z.read(n).decode(errors='replace') for n in z.namelist() if n.endswith(('broker.stderr.log', 'broker.stdout.log')))
                dropped = []
                for line in syslog.read_text().splitlines():
                    frame = json.loads(line)
                    if frame['topic'] in ('$SYS/broker/publish/messages/dropped', '$SYS/broker/mqtt/publish/dropped'):
                        try:
                            dropped.append(int(frame['payload']))
                        except ValueError:
                            pass
                row.update(status='success', measurement=observation['measurement'], drainReason=observation['drainReason'],
                    archiveSha256=receipt['sha256'], archivePath=str(archive), sysSha256=digest(syslog),
                    outgoingDropNotices=logs.count('Outgoing messages are being dropped'), sysDroppedMax=max(dropped) if dropped else None)
                row['warnings'] = [] if dropped else ['SYS dropped counter not observed; cause not established']
            state['runs'].append(row)
            state.pop('active', None)
            write(root / 'state.json', state)
            report(root, plan, state)
        state['status'] = 'completed' if all(r['status'] == 'success' for r in state['runs']) else 'completed-with-failures'
    except (Exception, KeyboardInterrupt) as error:
        state['status'] = 'interrupted'
        state['reason'] = str(error)
        if child and child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    finally:
        write(root / 'state.json', state)
        report(root, plan, state)


if __name__ == '__main__':
    if sys.argv[1] == 'prepare':
        print(prepare(sys.argv[2], sys.argv[3]))
    else:
        run(sys.argv[2])
