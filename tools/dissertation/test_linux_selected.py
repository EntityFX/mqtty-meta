import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import prepare_graph_execution_probe as build
import run_linux_selected as selected


class SelectedTests(unittest.TestCase):
    def test_exact_adapter_and_unknown_source_fail_closed(self):
        original = 'public sealed class FederationRunner\nRun(FederationInput input, string? output = null\nRefresh(false, 0)'
        adapted = build.adapt(original)
        self.assertEqual(adapted, 'public sealed class GraphExecutionRunner\nRun(FederationInput input, bool parallel, string? output = null\nRefresh(parallel, 0)')
        with self.assertRaises(ValueError): build.adapt(original+'\nRefresh(false, 0)')

    def test_queue_only_one_factor(self):
        first = {'queueCapacity': 1024, 'network': {'queueCapacity': 100000}, 'offeredRps': 5000}
        second = dict(first, queueCapacity=4096)
        selected.check_queue_pair(first, second)
        with self.assertRaises(ValueError): selected.check_queue_pair(first, dict(second, offeredRps=1000))

    def test_exact_12_temporal_jobs_each_kind(self):
        jobs = selected.temporal_jobs()
        self.assertEqual(len(jobs), 24)
        self.assertEqual(len({(j['kind'], j['broker'], j['repeat']) for j in jobs}), 24)
        self.assertEqual([j['kind'] for j in jobs[:12]], ['native']*12)
        self.assertEqual({j['seed'] for j in jobs}, {42, 43, 44})

    def test_no_model_from_partial_control(self):
        with self.assertRaises(ValueError): selected.require_three([{'repeat':1}, {'repeat':2}])
        with self.assertRaises(ValueError): selected.require_three([{'repeat':1}]*3)
        selected.require_three([{'repeat':1},{'repeat':2},{'repeat':3}])

    def test_absolute_subbudget_does_not_reset(self):
        self.assertEqual(selected.stage_deadline(100, 1000, 420), 520)
        self.assertEqual(selected.stage_deadline(900, 1000, 420), 1000)


if __name__ == '__main__': unittest.main()
