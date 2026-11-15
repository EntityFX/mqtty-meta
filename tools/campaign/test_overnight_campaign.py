import datetime
import importlib.util
import pathlib
import unittest


def load(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


overnight, adapter = load('overnight-campaign'), load('accelerated-resume')


class OvernightTests(unittest.TestCase):
    def test_full_matrix_and_independent_seed_preserve_guard(self):
        original = {'guard': {'cpu': 99}}
        calibration = overnight.native_config(original, 'Mosquitto', 'calibration')
        control = overnight.native_config(original, 'Mosquitto', 'validation')
        self.assertEqual(72, len(control['publishers']) * len(control['qos']) * len(control['messageBytes']) * control['repeats'])
        self.assertNotEqual(calibration['seed'], control['seed'])
        self.assertEqual(39, sum(control[k] for k in ('warmupSeconds', 'measurementSeconds', 'cooldownSeconds', 'drainTimeoutSeconds', 'telemetryCaptureReserveSeconds')))
        self.assertEqual(99, control['guard']['cpu'])
        self.assertNotIn('publishers', original)

    def test_deadline_has_finalization_margin_and_does_not_roll_over(self):
        now = datetime.datetime(2026, 10, 16, 16, tzinfo=datetime.timezone.utc)
        deadline = now + datetime.timedelta(hours=13)
        timing = overnight.timing(now, deadline)
        self.assertEqual(deadline.isoformat(), timing['deadlineUtc'])
        self.assertLess(timing['runtimeMaxSeconds'] + 2 * timing['stopTimeoutSeconds'], timing['budgetSeconds'])
        self.assertLess(timing['workloadBudgetSeconds'], timing['runtimeMaxSeconds'])
        for value in (now, now + datetime.timedelta(hours=17)):
            with self.assertRaises(ValueError):
                overnight.timing(now, value)

    def test_fidelity_timing_uses_only_calibration_and_relay_stays_fixed(self):
        def config():
            return {'experiment': {}, 'ticks': {}, 'network': {}}
        plan = {'empiricalProfiles': True, 'simulationMeasurementSeconds': 2}
        result = adapter.empirical_options(config(), plan, False, [{'rttBaselineMs': .1}])
        self.assertEqual('calibratedRttBudget', result['experiment']['transportTiming'])
        self.assertEqual('closedLoopPerPublisher', result['experiment']['loadMode'])
        self.assertEqual('00:00:00.0000100', result['ticks']['tickPeriod'])
        self.assertEqual(200000, result['experiment']['measurementTicks'])
        self.assertEqual(5, result['network']['transferTicks'])
        relay = adapter.empirical_options(config(), plan, True, [{'rttBaselineMs': .1}])
        self.assertEqual('fixed', relay['experiment']['transportTiming'])
        self.assertEqual('openLoopRate', relay['experiment']['loadMode'])
        self.assertEqual(20000, relay['experiment']['measurementTicks'])
        self.assertEqual('00:00:00.0001000', relay['ticks']['tickPeriod'])
        plan['empiricalTransportTiming'] = 'empiricalServiceBudget'
        service = adapter.empirical_options(config(), plan, False, [{'rttBaselineMs': .1}])
        self.assertEqual('empiricalServiceBudget', service['experiment']['transportTiming'])
        self.assertEqual('fixed', adapter.empirical_options(config(), plan, True, [{'rttBaselineMs': .1}])['experiment']['transportTiming'])
        plan['empiricalTransportTiming'] = 'unknown'
        with self.assertRaises(ValueError):
            adapter.empirical_options(config(), plan, False, [{'rttBaselineMs': .1}])


if __name__ == '__main__':
    unittest.main()
