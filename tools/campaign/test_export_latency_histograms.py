import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('exporter', pathlib.Path(__file__).with_name('export-latency-histograms.py'))
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class HistogramExportTests(unittest.TestCase):
    def archive(self, root, mismatch=False, duplicate=False):
        latencies = [.10001, .2, 200]
        events = []
        for index, latency in enumerate(latencies):
            key = '0' if duplicate else str(index)
            events.extend([{'event': 'attempt', 'id': key}, {'event': 'completed', 'id': key, 'latencyMs': latency}])
        measurement = {'attemptedPublishes': 3, 'completedPublishes': 3, 'failedPublishes': 0,
            'actualMeasurementSeconds': 1, 'publishLatencyMs': {'minMs': latencies[0], 'p50Ms': .2,
                'p75Ms': 200, 'p95Ms': 200, 'p99Ms': 199 if mismatch else 200, 'maxMs': 200}}
        result = {'status': 'success', 'key': {'repeat': 1}, 'observation': {'measurement': measurement}}
        files = {'result.json': json.dumps(result).encode(), 'publishes.ndjson': b''.join(json.dumps(e).encode() + b'\n' for e in events)}
        path = pathlib.Path(root) / 'raw.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr('sha256.json', json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()}))
        return path, exporter.digest(path)

    def test_bins_keep_rare_maximum_and_round_up_by_at_most_one_microsecond(self):
        with tempfile.TemporaryDirectory() as root:
            path, digest = self.archive(root)
            _, value = exporter.extract(path, digest)
            self.assertEqual([[101, 1], [200, 1], [200000, 1]], value['bins'])
            self.assertEqual(3, value['completedPublishes'])

    def test_wrong_summary_and_duplicate_id_are_rejected_even_with_valid_seals(self):
        with tempfile.TemporaryDirectory() as root:
            path, digest = self.archive(root, mismatch=True)
            with self.assertRaisesRegex(ValueError, 'quantiles'):
                exporter.extract(path, digest)
            path, digest = self.archive(root, duplicate=True)
            with self.assertRaisesRegex(ValueError, 'Duplicate attempt'):
                exporter.extract(path, digest)


if __name__ == '__main__':
    unittest.main()
