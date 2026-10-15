import importlib.util
import json
import pathlib
import tempfile
import types
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('accelerated', pathlib.Path(__file__).with_name('accelerated-resume.py'))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name) / 'controller'
        self.root.mkdir()

    def test_graphml_declared_utf16_but_utf8_is_read_without_rewriting(self):
        path = self.root / 'topology.graphml'
        data = '<?xml version="1.0" encoding="utf-16"?><graphml><node id="узел"/></graphml>'.encode('utf-8')
        path.write_bytes(data)
        self.assertEqual('узел', adapter.graphml_parse(ET.parse, path).getroot()[0].attrib['id'])
        self.assertEqual(data, path.read_bytes())

    def test_real_utf16_and_malformed_xml_are_not_silently_changed(self):
        path = self.root / 'topology.graphml'
        path.write_bytes('<?xml version="1.0" encoding="utf-16"?><graphml/>'.encode('utf-16'))
        self.assertEqual('graphml', adapter.graphml_parse(ET.parse, path).getroot().tag)
        path.write_bytes(b'<?xml version="1.0" encoding="utf-16"?><broken>')
        with self.assertRaises(ET.ParseError):
            adapter.graphml_parse(ET.parse, path)

    def campaign(self):
        def read(path):
            return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))

        def write(path, value):
            path = pathlib.Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding='utf-8')

        example = self.root / 'example.json'
        write(example, {'experiment': {}, 'network': {}})
        write(self.root / 'series.json', {'benchmarkRepository': str(self.root), 'simulatorExample': str(example),
            'simulationMeasurementSeconds': 10, 'mqttYRepository': str(self.root), 'simulatorCli': 'frozen.dll'})
        fake_run = types.SimpleNamespace(Campaign=object, read=read, atomic_json=write, BROKERS=('Aedes',),
            GIB=1024**3, digest=lambda path: 'frozen-sha')
        captured = []
        fake_run.main = lambda: captured.append(fake_run.Campaign)
        fake_report = types.SimpleNamespace(ET=types.SimpleNamespace(parse=ET.parse),
            experiments=lambda root, broker: [], table=lambda headers, rows: (headers, rows), LIMITATIONS='')
        with mock.patch.dict('sys.modules', {'run': fake_run, 'report': fake_report}), mock.patch('sys.argv', ['adapter', '--series', str(self.root)]):
            adapter.main()
        cls = captured[0]
        campaign = cls()
        campaign.root, campaign.plan = self.root, read(self.root / 'series.json')
        campaign.storage = self.root.parent / 'evidence'
        campaign.state = {'profiles': {'Aedes': 'frozen-sha'}, 'controllerRuntime': {'cgroup': '/test'}}
        campaign.profile = lambda name: self.root / 'profile.json'
        campaign.save = lambda: None
        campaign.storage_check = lambda: None
        campaign.system_disk = lambda: self.root
        campaign.report = lambda broker, stage: None
        points = [{'messageBytes': size, 'qos': qos, 'publishers': n, 'attemptedRps': {'mean': 100}}
                  for size in (16, 256) for qos in range(3) for n in (1, 16, 64, 128)]
        for stage in ('calibration', 'validation'):
            write(self.root / 'brokers/Aedes' / stage / 'broker-observations.v3.json', {'points': points})
        write(self.root / 'transport.json', {'network': {'transferTicks': 2}, 'transferTicksByBroker': {'Aedes': 2}})
        return campaign, write, read

    def execute_fake(self, campaign, stage, code=3, free_gib=100):
        live = set()
        processes = []
        maximum = [0]
        original_read = pathlib.Path.read_text

        def memory_read(path, *args, **kwargs):
            if str(path).replace('\\', '/').endswith('/test/memory.current'):
                return '0'
            return original_read(path, *args, **kwargs)

        class Process:
            def __init__(self, args, **kwargs):
                self.pid = 100 + len(processes)
                self.code = None
                live.add(self.pid)
                maximum[0] = max(maximum[0], len(live))
                processes.append(self)
                output = pathlib.Path(args[args.index('--output') + 1]) / 'run'
                output.mkdir(parents=True)
                (output / 'summary.json').write_text('{"status":"completed"}')

            def poll(self):
                if self.code is None:
                    self.code = code
                    live.discard(self.pid)
                return self.code

            def send_signal(self, value):
                self.code = -2
                live.discard(self.pid)

            def wait(self, **kwargs):
                live.discard(self.pid)
                return self.code

            def kill(self):
                self.code = -9
                live.discard(self.pid)

        with mock.patch.object(pathlib.Path, 'read_text', memory_read), mock.patch.object(adapter.subprocess, 'Popen', Process), mock.patch.object(adapter.time, 'sleep'), mock.patch.object(adapter.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=free_gib * 1024**3)):
            campaign.simulate('Aedes', stage)
        return maximum[0], processes

    def test_four_workers_preserve_all_seeds_and_short_window_has_separate_directory(self):
        campaign, write, read = self.campaign()
        maximum, processes = self.execute_fake(campaign, 'holdout')
        self.assertEqual(4, maximum)
        self.assertEqual(72, len(processes))
        base = self.root / 'brokers/Aedes/simulations/holdout-10s'
        index = read(base / 'index.json')
        self.assertEqual(72, len({r['key'] for r in index['runs']}))
        config = read(base / 'm16.q0.p1.s42.json')
        self.assertEqual(100000, config['experiment']['measurementTicks'])
        self.assertEqual(50000, config['experiment']['warmupTicks'])
        self.assertEqual(300000, config['experiment']['drainTimeoutTicks'])
        self.assertFalse(config['experiment']['parallel'])
        self.assertEqual(0, config['experiment']['refreshStrategy'])
        self.assertTrue(pathlib.Path(config['referenceObservationsPath']).as_posix().endswith('validation/broker-observations.v3.json'))
        self.assertNotIn('activeSimulations', campaign.state)

    def test_relay_starts_only_two_workers(self):
        campaign, _, read = self.campaign()
        maximum, processes = self.execute_fake(campaign, 'relay')
        self.assertEqual(2, maximum)
        config = read(self.root / 'brokers/Aedes/simulations/relay-10s/m16.q0.p1.s42.json')
        self.assertEqual(400, config['experiment']['offeredRps'])
        self.assertEqual(4, config['experiment']['brokers'])

    def test_explicit_relay_limit_admits_four_independent_processes(self):
        campaign, _, read = self.campaign()
        campaign.plan['simulationRelayWorkers'] = 4
        maximum, processes = self.execute_fake(campaign, 'relay')
        self.assertEqual(4, maximum)
        self.assertEqual(72, len(processes))
        self.assertEqual(72, len(read(self.root / 'brokers/Aedes/simulations/relay-10s/index.json')['runs']))

    def test_complete_legacy_cohort_is_reused_without_rerunning_or_relabeling(self):
        campaign, write, read = self.campaign()
        runs = [{'key': f'm{b}.q{q}.p{n}.s{s}'} for b in (16, 256) for q in range(3) for n in (1, 16, 64, 128) for s in (42, 43, 44)]
        index = self.root / 'brokers/Aedes/simulations/fidelity/index.json'
        write(index, {'stage': 'fidelity', 'runs': runs})
        before = index.read_bytes()
        _, processes = self.execute_fake(campaign, 'fidelity')
        self.assertEqual([], processes)
        self.assertEqual(before, index.read_bytes())
        self.assertFalse(index.parent.with_name('fidelity-10s').exists())

    def test_config_tampering_is_rejected_without_overwriting(self):
        campaign, write, _ = self.campaign()
        path = self.root / 'brokers/Aedes/simulations/holdout-10s/m16.q0.p1.s42.json'
        write(path, {'tampered': True})
        with self.assertRaisesRegex(RuntimeError, 'config changed'):
            self.execute_fake(campaign, 'holdout')
        self.assertEqual({'tampered': True}, json.loads(path.read_text()))

    def test_technical_failure_never_enters_success_index_and_clears_active_children(self):
        campaign, _, _ = self.campaign()
        with self.assertRaisesRegex(RuntimeError, 'CLI exit 2'):
            self.execute_fake(campaign, 'holdout', code=2)
        self.assertFalse((self.root / 'brokers/Aedes/simulations/holdout-10s/index.json').exists())
        self.assertNotIn('activeSimulations', campaign.state)

    def test_low_disk_cancels_children_without_deleting_evidence(self):
        campaign, _, _ = self.campaign()
        with self.assertRaisesRegex(RuntimeError, 'Low disk space'):
            self.execute_fake(campaign, 'holdout', free_gib=5)
        self.assertTrue(list(campaign.storage.rglob('summary.json')))
        self.assertFalse((self.root / 'brokers/Aedes/simulations/holdout-10s/index.json').exists())
        self.assertNotIn('activeSimulations', campaign.state)


if __name__ == '__main__':
    unittest.main()
