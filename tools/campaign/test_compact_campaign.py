import datetime
import importlib.util
import pathlib
import signal
import subprocess
import tempfile
import unittest
from unittest import mock


def load(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compact, launcher = load('compact-campaign'), load('launch-compact')


class CompactTests(unittest.TestCase):
    def test_comparison_uses_matching_points_and_keeps_missing_coverage_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, full = pathlib.Path(temporary) / 'short', pathlib.Path(temporary) / 'full'
            compact.write(full / 'series.json', {'seriesId': 'full'})
            compact.write(root / 'series.json', {'seriesId': 'short', 'predecessorSeries': str(full)})
            compact.write(root / 'state.json', {'status': 'running'})
            def observations(rate):
                return {'runCount': 36, 'points': [{'messageBytes': 16, 'qos': 0, 'publishers': 1,
                    'completedRps': {'mean': rate, 'ci95HalfWidth': 2}, 'observedLatencyQuantiles': None,
                    'publishFailureRate': {'mean': 0}, 'deliveryLossRate': {'mean': 0}}]}
            for base, rate in ((root, 110), (full, 100)):
                compact.write(base / 'brokers/Aedes/calibration/broker-observations.v3.json', observations(rate))
            compact.comparison_report(root)
            row = compact.read(root / 'reports/Full-vs-Compact.json')['rows'][0]
            self.assertEqual(10, row['rpsDeltaPercent'])
            self.assertIsNone(row['p99DeltaPercent'])
            text = (root / 'reports/Full-vs-Compact.md').read_text(encoding='utf-8')
            self.assertIn('| Mosquitto | 0/72 | 0/36 |', text)
            self.assertIn('notApplicable', text)
            self.assertEqual(64, len(row['fullObservationSha256']))

    def test_protocol_preserves_repeats_all_qos_and_payload_but_separates_control(self):
        source = {'guard': {'cpu': 99}, 'publishers': [1, 16, 64, 128], 'measurementSeconds': 30}
        calibration = compact.native_config(source, 'Aedes', 'calibration')
        validation = compact.native_config(source, 'Aedes', 'validation')
        self.assertEqual(36, len(calibration['publishers']) * len(calibration['messageBytes']) * len(calibration['qos']) * calibration['repeats'])
        self.assertNotEqual(calibration['seed'], validation['seed'])
        self.assertEqual(99, validation['guard']['cpu'])
        self.assertEqual(30, source['measurementSeconds'])
        self.assertEqual(24, sum(calibration[key] for key in ('warmupSeconds', 'measurementSeconds', 'cooldownSeconds', 'drainTimeoutSeconds', 'telemetryCaptureReserveSeconds')))
        pilot = compact.native_config(source, 'EMQX', 'pilot')
        self.assertEqual(3, len(pilot['qos']) * pilot['repeats'] * len(pilot['publishers']) * len(pilot['messageBytes']))

    def test_budget_leaves_reporting_and_hard_stop_inside_six_hours(self):
        now = datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc)
        value = launcher.timing(now)
        self.assertEqual(21600, (datetime.datetime.fromisoformat(value['deadlineUtc']) - now).total_seconds())
        work = (datetime.datetime.fromisoformat(value['workloadDeadlineUtc']) - now).total_seconds()
        self.assertLess(work, value['runtimeMaxSeconds'])
        self.assertLess(value['runtimeMaxSeconds'] + 2 * value['stopTimeoutSeconds'], 21600)

    def test_child_is_reaped_after_graceful_stop_and_after_timeout(self):
        process = mock.Mock()
        process.poll.return_value = None
        compact.stop_child(process)
        process.send_signal.assert_called_once_with(signal.SIGINT)
        process.kill.assert_not_called()
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('owned', 20), 0]
        compact.stop_child(process)
        process.kill.assert_called_once()
        self.assertEqual(2, process.wait.call_count)


if __name__ == '__main__':
    unittest.main()
