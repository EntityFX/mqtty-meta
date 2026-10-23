import unittest

from estimate_sensitivity import batch_remaining, point


class TimingEstimateTests(unittest.TestCase):
    def test_batch_barrier_includes_idle_worker(self):
        jobs = [{"id": n, "cost": c} for n, c in [("a", 10), ("b", 100), ("c", 20), ("d", 80)]]
        self.assertEqual(batch_remaining(jobs, set(), {}, lambda j: j["cost"]), 180)
        self.assertEqual(batch_remaining(jobs, {"a"}, {"b": {"elapsedSeconds": 60}}, lambda j: j["cost"]), 120)

    def test_completed_and_overdue_jobs_do_not_make_negative_eta(self):
        jobs = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(batch_remaining(jobs, {"b"}, {"a": {"elapsedSeconds": 20}}, lambda j: 10), 0)

    def test_replay_keeps_original_conditions(self):
        self.assertEqual(point("replay.EMQX.m16.q1.p128.s42.baseline"), ("replay", "EMQX", 1, 128, "baseline"))
        with self.assertRaises(ValueError):
            point("unknown")


if __name__ == "__main__":
    unittest.main()
