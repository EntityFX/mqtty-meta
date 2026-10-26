import unittest
from run_logging_overhead import planned_cases, require_time, comparison_config, measurement_metrics


class LoggingOverheadTests(unittest.TestCase):
    def test_exact_abba(self):
        self.assertEqual([p['writeMessageEventLogs'] for p in planned_cases()], [True, False, False, True])
        self.assertEqual([p['repeat'] for p in planned_cases()], [1, 2, 3, 4])

    def test_deadline_not_reset(self):
        with self.assertRaises(TimeoutError):
            require_time(950, 900)
        with self.assertRaises(TimeoutError):
            require_time(850, 900)
        self.assertEqual(require_time(100, 900), 800)

    def test_only_event_logging_differs_except_attempt_identity(self):
        configs = [comparison_config(p, 'runtime-sha') for p in planned_cases()]
        cleaned = [{k:v for k,v in c.items() if k not in ('id','repeat','writeMessageEventLogs')} for c in configs]
        self.assertTrue(all(c == cleaned[0] for c in cleaned))
        self.assertTrue(all(c['recordPublicationCohort'] and c['observeFullDrainWindow'] for c in configs))
        self.assertTrue(all(c['measureRttBaseline'] is True for c in configs))

    def test_rps_uses_actual_time(self):
        trial = dict(measurement=dict(attemptedPublishes=120, completedPublishes=100, failedPublishes=20,
                    actualMeasurementSeconds=5, completedRps=20, deliveryLossRate=.2,
                    publishLatencyMs=dict(p99Ms=3), duplicateDeliveries=0))
        self.assertEqual(measurement_metrics(trial)['completedRps'], 20)
        trial['measurement']['completedRps'] = 25
        with self.assertRaises(ValueError):
            measurement_metrics(trial)


if __name__ == '__main__':
    unittest.main()
