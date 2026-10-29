"""Admission contracts: second factor, unsafe restart and exhausted budget fail closed."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class QueuePilotTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((HERE / 'run_queue_pilot.py').exists(), 'New bounded launcher is missing')
        spec = importlib.util.spec_from_file_location('queue_pilot', HERE / 'run_queue_pilot.py')
        self.pilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pilot)

    def pair(self):
        reference = json.loads((HERE / 'configs/q1024.json').read_text())
        small = copy.deepcopy(reference)
        small['queueCapacity'] = 1
        return small, reference

    def test_second_factor_is_rejected(self):
        small, reference = self.pair()
        small['network']['queueCapacity'] = 1
        with self.assertRaises(ValueError):
            self.pilot.validate_pair(small, reference)

    def test_wrong_common_window_is_rejected(self):
        small, reference = self.pair()
        small['measurementSeconds'] = reference['measurementSeconds'] = 4
        with self.assertRaises(ValueError):
            self.pilot.validate_pair(small, reference)

    def test_valid_pair_keeps_original_cohort(self):
        small, reference = self.pair()
        self.pilot.validate_pair(small, reference)
        self.assertEqual(reference['offeredRps'] * reference['measurementSeconds'], 2000)

    def test_existing_output_cannot_restart_or_reset_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'new-run'
            self.pilot.claim_run(root, 100)
            original = (root / 'run-identity.json').read_bytes()
            with self.assertRaises(FileExistsError):
                self.pilot.claim_run(root, 200)
            self.assertEqual((root / 'run-identity.json').read_bytes(), original)

    def test_expired_budget_admits_no_new_process(self):
        self.assertEqual(self.pilot.remaining_model_budget(601, 100), 0)
        self.assertEqual(self.pilot.remaining_model_budget(579, 100), 1)
        self.assertEqual(self.pilot.remaining_model_budget(101, 100), 240)

    def test_classification_does_not_turn_missing_overflow_into_success(self):
        self.assertEqual(self.pilot.classify([{'queueDrops': 0}, {'queueDrops': 0}]), 'noninformativeNoSmallQueueOverflow')
        self.assertEqual(self.pilot.classify([{'queueDrops': 2}, {'queueDrops': 1}]), 'noninformativeReferenceOverflow')
        self.assertEqual(self.pilot.classify([{'queueDrops': 2}, {'queueDrops': 0}]), 'queueCapacityContrast')

    def test_unit_runtime_and_kill_grace_together_fit_shared_budget(self):
        self.assertEqual(self.pilot.unit_budget('9min 55s', '5s'), 600)
        with self.assertRaises(ValueError):
            self.pilot.unit_budget('10min', '5s')
        with self.assertRaises(ValueError):
            self.pilot.unit_budget('infinity', '5s')


if __name__ == '__main__':
    unittest.main()
