import importlib.util
import pathlib
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('native_diag', pathlib.Path(__file__).with_name('mosquitto-native-diagnostic.py'))
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


class NativeDiagnosticTests(unittest.TestCase):
    def test_prepare_keeps_baseline_immutable_and_changes_one_named_factor(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = pathlib.Path(temporary)
            baseline = mount / 'baseline/controller'
            diag.write(baseline / 'series.json', {'archiveMount': str(mount), 'archiveDevice': '/dev/fake',
                'deadlineUtc': '2026-10-16T15:54:18+00:00', 'benchmarkRepository': '/code/benchmark', 'benchmarkCli': '/tools/cli.dll'})
            diag.write(baseline / 'inputs/Mosquitto-stand.json', {'brokers': [{'name': 'Mosquitto', 'executable': '/usr/sbin/mosquitto', 'executableSha256': 'a' * 64, 'cpuset': '0'}]})
            config = {'measurementSeconds': 30, 'cpuThresholdPercent': 99}
            diag.write(baseline / 'inputs/Mosquitto-calibration.json', config)
            with mock.patch.object(diag.subprocess, 'check_output', return_value=f'{mount}\t/dev/fake\text4\n'), mock.patch.object(diag.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100 * 1024**3)):
                root = diag.prepare(baseline, mount / 'diagnostic')
            plan = diag.read(root / 'series.json')
            self.assertEqual(24, len(plan['cases']))
            self.assertEqual('2026-10-16T15:54:18+00:00', plan['deadlineUtc'])
            for block in (1, 2, 3):
                cases = [c for c in plan['cases'] if c['block'] == block]
                self.assertEqual(8, len({(c['variant'], c['publishers']) for c in cases}))
            base = (root / 'inputs/baseline.conf').read_text()
            self.assertNotIn('max_queued_messages', base)
            self.assertIn('log_dest stderr', base)
            self.assertEqual(base + 'set_tcp_nodelay true\n', (root / 'inputs/tcp-nodelay.conf').read_text())
            self.assertNotIn('max_queued_messages', (root / 'inputs/inflight-200.conf').read_text())
            self.assertEqual(config, diag.read(baseline / 'inputs/Mosquitto-calibration.json'))
            self.assertEqual(99, diag.read(plan['cases'][0]['config'])['cpuThresholdPercent'])
            self.assertEqual(30, diag.read(plan['cases'][0]['config'])['quietPeriodSeconds'])


if __name__ == '__main__':
    unittest.main()
