import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('handoff', pathlib.Path(__file__).with_name('relay-concurrency-handoff.py'))
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class BoundaryTests(unittest.TestCase):
    def test_completed_boundary_without_active_work_is_eligible(self):
        self.assertTrue(handoff.eligible({'status': 'running', 'completed': ['Aedes/relay']}, 'Aedes/relay'))

    def test_active_native_or_simulations_or_incomplete_stage_prevent_handoff(self):
        base = {'status': 'running', 'completed': ['Aedes/relay']}
        for override in ({'active': {'pid': 42}}, {'activeSimulations': [{'pid': 42}]},
                         {'completed': []}, {'status': 'technical-failure'}, {'status': 'completed'}):
            with self.subTest(override=override):
                self.assertFalse(handoff.eligible(base | override, 'Aedes/relay'))


if __name__ == '__main__':
    unittest.main()
