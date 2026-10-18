import datetime as dt
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('extension', Path(__file__).with_name('extend-campaign-deadline.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DeadlineExtensionTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 18, 23, tzinfo=dt.timezone.utc)
        self.plan = {'startedAtUtc': '2026-10-18T20:31:43+00:00',
                     'deadlineUtc': '2026-10-19T05:00:00+00:00',
                     'workloadDeadlineUtc': '2026-10-19T04:45:00+00:00',
                     'model': {'sha': 'unchanged'}, 'resumptions': [{'reason': 'previous'}]}

    def test_extension_preserves_inputs_start_and_history(self):
        result = module.extended_plan(self.plan, '2026-10-19T17:00:00+00:00', 'user requested', self.now)
        self.assertEqual(result['workloadDeadlineUtc'], '2026-10-19T16:45:00+00:00')
        self.assertEqual(result['startedAtUtc'], self.plan['startedAtUtc'])
        self.assertEqual(result['model'], self.plan['model'])
        self.assertEqual(len(result['resumptions']), 2)
        self.assertEqual(result['resumptions'][-1]['previousDeadlineUtc'], self.plan['deadlineUtc'])
        self.assertEqual(self.plan['deadlineUtc'], '2026-10-19T05:00:00+00:00')
        self.assertEqual(len(self.plan['resumptions']), 1)
        self.assertEqual(result['runtimeMaxSeconds'], 18*3600-60)

    def test_shortening_or_same_deadline_is_rejected(self):
        for value in ['2026-10-19T04:00:00+00:00', '2026-10-19T05:00:00+00:00']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.extended_plan(self.plan, value, 'reason', self.now)

    def test_naive_or_expired_deadline_is_rejected(self):
        for value in ['2026-10-19T17:00:00', '2026-10-18T22:00:00+00:00']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.extended_plan(self.plan, value, 'reason', self.now)

    def test_workload_reserve_must_remain_positive(self):
        self.plan['workloadDeadlineUtc'] = self.plan['deadlineUtc']
        with self.assertRaises(ValueError):
            module.extended_plan(self.plan, '2026-10-19T17:00:00+00:00', 'reason', self.now)

    def test_active_children_or_inactive_state_prevent_handoff(self):
        self.assertTrue(module.idle({'status': 'running'}))
        self.assertFalse(module.idle({'status': 'running', 'active': {'pid': 123}}))
        self.assertFalse(module.idle({'status': 'running', 'activeSimulations': [{'pid': 123}]}))
        self.assertFalse(module.idle({'status': 'completed'}))


if __name__ == '__main__':
    unittest.main()
