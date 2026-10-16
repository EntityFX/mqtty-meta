import importlib.util
import base64
import html
import json
import pathlib
import re
import tempfile
import types
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('accelerated', pathlib.Path(__file__).with_name('accelerated-resume.py'))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class AdapterTests(unittest.TestCase):
    def test_native_window_note_distinguishes_preserved_and_amended_cohorts(self):
        note = adapter.native_window_note({'nativeMeasurementSecondsByBroker': {'Aedes': 30, 'ActiveMQ': 20}})
        self.assertIn('Aedes — 30 с', note)
        self.assertIn('ActiveMQ — 20 с', note)
        self.assertIn('30 секунд', adapter.native_window_note({}))
        with self.assertRaises(ValueError):
            adapter.native_window_note({'nativeMeasurementSecondsByBroker': {'Aedes': 0}})

    def test_four_relay_workers_have_separate_reservation_without_changing_fidelity(self):
        plan = {'simulationMemoryPolicy': {'reservationPerJobBytes': 2},
                'simulationRelayMemoryPolicy': {'reservationPerJobBytes': 1.25}}
        self.assertEqual(2, adapter.simulation_memory_policy(plan, False)['reservationPerJobBytes'])
        self.assertEqual(1.25, adapter.simulation_memory_policy(plan, True)['reservationPerJobBytes'])

    def test_working_set_keeps_anon_and_dirty_cache_in_memory_budget(self):
        self.assertEqual(350, adapter.memory_working_set(1000, {'inactive_file': 800, 'file_dirty': 100, 'file_writeback': 50}))
        self.assertEqual(1000, adapter.memory_working_set(1000, {'inactive_file': 20, 'file_dirty': 100}))
        self.assertEqual(0, adapter.memory_working_set(1000, {'inactive_file': 2000}))

    def test_admission_reserves_for_starting_jobs_before_their_heaps_grow(self):
        policy = {'reservationPerJobBytes': 200, 'maxWorkingSetBytes': 600, 'minSystemAvailableBytes': 100}
        self.assertTrue(adapter.memory_admission(50, 50, 1, 1000, policy))
        self.assertFalse(adapter.memory_admission(50, 50, 2, 1000, policy))
        self.assertFalse(adapter.memory_admission(450, 50, 1, 1000, policy))
        self.assertFalse(adapter.memory_admission(50, 50, 0, 299, policy))

    def test_cohort_names_reject_path_escape_and_rows_exclude_old_model(self):
        for name in ('../old', '/tmp', 'a/b', '', 'ratefix-bd34c7c'):
            if name in ('', 'ratefix-bd34c7c'):
                self.assertEqual(name, adapter.cohort_name({'simulationCohort': name}))
            else:
                with self.assertRaises(ValueError):
                    adapter.cohort_name({'simulationCohort': name})
        rows = [{'directory': '/evidence/fidelity-10s/key/run'},
                {'directory': '/evidence/fidelity-10s-ratefix-bd34c7c/key/run'}]
        self.assertEqual(rows[1:], adapter.cohort_rows(rows, 'ratefix-bd34c7c'))

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

    def campaign(self, machine_description=None, cohort=None):
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
        if cohort:
            plan = read(self.root / 'series.json')
            plan.update(simulationCohort=cohort, simulatorRevision='fixed-commit')
            write(self.root / 'series.json', plan)
        if machine_description:
            plan = read(self.root / 'series.json')
            plan['reportMachineDescription'] = machine_description
            write(self.root / 'series.json', plan)
        fake_run = types.SimpleNamespace(Campaign=object, read=read, atomic_json=write, BROKERS=('Aedes',),
            GIB=1024**3, digest=lambda path: 'frozen-sha')
        captured = []
        fake_run.main = lambda: captured.append(fake_run.Campaign)
        fake_report = types.SimpleNamespace(ET=types.SimpleNamespace(parse=ET.parse),
            experiments=lambda root, broker: [], table=lambda headers, rows: (headers, rows), LIMITATIONS='')

        def build(directory, broker, entry):
            reports = pathlib.Path(directory) / 'reports'
            reports.mkdir(exist_ok=True)
            (reports / (broker + '.md')).write_text('# Report\n\nLinux 192.168.5.1 выполняет контроллер old CPU layout\n\nResult: completed', encoding='utf-8')
            (reports / (broker + '.html')).write_text('<!doctype html><html><style>body{}</style>old</html>', encoding='utf-8')

        fake_run.build = build
        fake_report.markdown = types.SimpleNamespace(markdown=lambda text, extensions: '<p>' + html.escape(text) + '</p>')
        fake_report.html, fake_report.base64, fake_report.re = html, base64, re
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
        campaign.test_run = fake_run
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

    def test_new_model_cohort_reruns_complete_legacy_without_overwriting_it(self):
        campaign, write, read = self.campaign(cohort='ratefix-bd34c7c')
        legacy = self.root / 'brokers/Aedes/simulations/fidelity/index.json'
        old = {'stage': 'fidelity', 'runs': [{'key': str(i)} for i in range(72)]}
        write(legacy, old)
        _, processes = self.execute_fake(campaign, 'fidelity')
        self.assertEqual(72, len(processes))
        self.assertEqual(old, read(legacy))
        base = self.root / 'brokers/Aedes/simulations/fidelity-10s-ratefix-bd34c7c'
        self.assertEqual(72, len(read(base / 'index.json')['runs']))
        self.assertEqual(100_000, read(base / 'm16.q0.p1.s42.json')['experiment']['measurementTicks'])

    def test_memory_reservations_limit_pool_before_child_memory_is_visible(self):
        campaign, _, read = self.campaign()
        campaign.plan['simulationFidelityWorkers'] = 4
        campaign.plan['simulationMemoryPolicy'] = {'reservationPerJobBytes': 2 * 1024**3,
            'maxWorkingSetBytes': 6 * 1024**3, 'minSystemAvailableBytes': 2 * 1024**3}
        snapshot = {'current': 5 * 1024**3, 'workingSet': 100 * 1024**2,
                    'systemAvailable': 10 * 1024**3, 'anon': 100 * 1024**2, 'file': 4 * 1024**3}
        with mock.patch.object(adapter, 'memory_snapshot', return_value=snapshot):
            maximum, processes = self.execute_fake(campaign, 'fidelity')
        self.assertEqual(2, maximum)
        self.assertEqual(72, len(processes))
        self.assertEqual(72, len(read(self.root / 'brokers/Aedes/simulations/fidelity-10s/index.json')['runs']))

    def test_relay_four_processes_fit_separate_budget_and_preserve_matrix(self):
        campaign, _, read = self.campaign()
        campaign.plan['simulationRelayWorkers'] = 4
        campaign.plan['simulationMemoryPolicy'] = {'reservationPerJobBytes': 2 * 1024**3,
            'maxWorkingSetBytes': 6 * 1024**3, 'minSystemAvailableBytes': 2 * 1024**3}
        campaign.plan['simulationRelayMemoryPolicy'] = dict(campaign.plan['simulationMemoryPolicy'],
            reservationPerJobBytes=1280 * 1024**2)
        snapshot = {'current': 100 * 1024**2, 'workingSet': 100 * 1024**2,
                    'systemAvailable': 10 * 1024**3}
        with mock.patch.object(adapter, 'memory_snapshot', return_value=snapshot):
            maximum, processes = self.execute_fake(campaign, 'relay')
        self.assertEqual(4, maximum)
        self.assertEqual(72, len(processes))
        self.assertEqual(72, len(read(self.root / 'brokers/Aedes/simulations/relay-10s/index.json')['runs']))

    def test_memory_guard_cancels_started_children_without_indexing_incomplete_work(self):
        campaign, _, _ = self.campaign()
        campaign.plan['simulationMemoryPolicy'] = {'reservationPerJobBytes': 2 * 1024**3,
            'maxWorkingSetBytes': 6 * 1024**3, 'minSystemAvailableBytes': 2 * 1024**3}
        low = {'current': 100, 'workingSet': 100, 'systemAvailable': 10 * 1024**3}
        high = dict(low, workingSet=7 * 1024**3)
        with mock.patch.object(adapter, 'memory_snapshot', side_effect=[low, low, high]):
            with self.assertRaisesRegex(RuntimeError, 'working-set memory guard'):
                self.execute_fake(campaign, 'holdout')
        self.assertNotIn('activeSimulations', campaign.state)
        self.assertFalse((self.root / 'brokers/Aedes/simulations/holdout-10s/index.json').exists())

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

    def test_portable_report_replaces_old_host_cpu_layout_in_markdown_and_html(self):
        campaign, _, _ = self.campaign(machine_description='Linux target-machine; broker CPU 8; controller CPU 1,3,7,9')
        campaign.test_run.build(self.root, 'Aedes', {'stage': 'pending'})
        for suffix in ('md', 'html'):
            text = (self.root / 'reports' / ('Aedes.' + suffix)).read_text(encoding='utf-8')
            self.assertIn('broker CPU 8', text)
            self.assertIn('controller CPU 1,3,7,9', text)
            self.assertNotIn('192.168.5.1', text)
            self.assertIn('Result: completed', text)

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
