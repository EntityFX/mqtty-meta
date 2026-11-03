import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import threading
import os

import benchmark


class BenchmarkTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows destination sharing semantics')
    def test_atomic_save_survives_temporary_reader_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'result.json'
            benchmark.save(path, {'status': 'running'})
            reader = path.open('rb')
            unlock = threading.Timer(.15, reader.close)
            unlock.start()
            try:
                benchmark.save(path, {'status': 'completed'})
                self.assertEqual(benchmark.read(path), {'status': 'completed'})
                self.assertFalse(path.with_suffix('.json.tmp').exists())
            finally:
                unlock.join()
                reader.close()

    def test_measure_captures_completed_process_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = benchmark.measure([sys.executable, '-c',
                'import time; data=bytearray(8*1024*1024); end=time.monotonic()+.2\nwhile time.monotonic()<end: pass'],
                root, root / 'run', 5)
            self.assertEqual(result['returnCode'], 0)
            self.assertFalse(result['timedOut'])
            self.assertGreater(result['wallSeconds'], .1)
            self.assertGreater(result['userSeconds'] + result['systemSeconds'], 0)
            self.assertGreater(result['peakMemoryBytes'], 1024 * 1024)

    def test_timeout_retains_failed_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = benchmark.measure([sys.executable, '-c', 'import time; time.sleep(10)'], root, root / 'run', .1)
            self.assertTrue(result['timedOut'])
            self.assertNotEqual(result['returnCode'], 0)
            self.assertTrue((root / 'run/measurement.json').is_file())

    def test_fingerprint_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            benchmark.save(root / 'summary.json', {'status': 'completed', 'apiAttempts': 10})
            benchmark.save(root / 'sha256.json', {'summary.json': benchmark.digest(root / 'summary.json')})
            self.assertIn('summary.json', benchmark.fingerprint(root))
            benchmark.save(root / 'summary.json', {'status': 'completed', 'apiAttempts': 11})
            with self.assertRaisesRegex(ValueError, 'seal mismatch'):
                benchmark.fingerprint(root)

    def test_baseline_requires_same_package_and_complete_serial_coverage(self):
        value = {'status': 'completed', 'packageSha256': 'a', 'fingerprints': {'relay:serial': {}}}
        benchmark.validate_baseline(value, 'a', ['relay'])
        with self.assertRaises(ValueError): benchmark.validate_baseline(value, 'b', ['relay'])
        with self.assertRaises(ValueError): benchmark.validate_baseline(value, 'a', ['relay', 'federation'])


if __name__ == '__main__':
    unittest.main()
