import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('memory_pilot', pathlib.Path(__file__).with_name('pilot-parallel-memory.py'))
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class ScientificComparisonTests(unittest.TestCase):
    def test_only_wall_clock_diagnostic_graph_is_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            paths = [pathlib.Path(root) / name for name in ('first', 'second')]
            for i, path in enumerate(paths):
                path.mkdir()
                (path / 'summary.json').write_text('{"completedRps": 36088.9}')
                (path / 'latency-histogram.json').write_text('{"bins": [1, 2]}')
                (path / 'counters.json').write_text(json.dumps({'brokers': {'completed': 42},
                    'receivers': {'delivered': 42}, 'graph': 'RealTime=' + str(i)}))
            self.assertEqual(pilot.scientific_output(paths[0]), pilot.scientific_output(paths[1]))
            counters = json.loads((paths[1] / 'counters.json').read_text())
            counters['receivers']['delivered'] = 41
            (paths[1] / 'counters.json').write_text(json.dumps(counters))
            self.assertNotEqual(pilot.scientific_output(paths[0]), pilot.scientific_output(paths[1]))
            counters['receivers']['delivered'] = 42
            counters['brokers']['completed'] = 41
            (paths[1] / 'counters.json').write_text(json.dumps(counters))
            self.assertNotEqual(pilot.scientific_output(paths[0]), pilot.scientific_output(paths[1]))


if __name__ == '__main__':
    unittest.main()
