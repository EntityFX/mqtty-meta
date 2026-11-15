"""Admission and measurement contracts for the separate workers1/2 study."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import run_acceleration_comparison as comparison


class ComparisonTests(unittest.TestCase):
    def test_fixed_interleaved_pairs(self):
        self.assertEqual(comparison.schedule('pilot'), [2])
        self.assertEqual(comparison.schedule('main'), [1, 2, 2, 1, 1, 2, 2, 1, 1, 2])
        with self.assertRaises(ValueError):
            comparison.schedule('unknown')

    def test_main_budget_requires_verified_completed_pilot(self):
        pilot = {'programIdentity': comparison.IDENTITY, 'mode': 'pilot',
                 'status': 'completed', 'equivalencePassed': True,
                 'primaryWalls': [100.0], 'finishedModels': 2}
        self.assertEqual(comparison.main_budget(pilot), math.ceil(1.35*5*(179.007+100)+120))
        for key, value in [('status', 'failed'), ('equivalencePassed', False),
                           ('finishedModels', 1), ('mode', 'main'),
                           ('primaryWalls', [float('nan')]), ('primaryWalls', [0]),
                           ('primaryWalls', [400])]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                comparison.main_budget(dict(pilot, **{key: value}))

    def test_summary_keeps_slowdown_and_uses_pair_order(self):
        # A/B, B/A: second pair slows down, not silently inverted or omitted.
        rows = [{'workers': w, 'primaryWallSeconds': t} for w, t in
                [(1, 10), (2, 5), (2, 20), (1, 10), (1, 10), (2, 10),
                 (2, 10), (1, 10), (1, 10), (2, 10)]]
        result = comparison.analyse(rows)
        self.assertEqual(result['pairedSpeedups'], [2, .5, 1, 1, 1])
        self.assertAlmostEqual(result['geometricMean'], 1)
        self.assertFalse(result['practicalGainConfirmed'])
        with self.assertRaises(ValueError):
            comparison.analyse(rows[:-1])


if __name__ == '__main__':
    unittest.main()
