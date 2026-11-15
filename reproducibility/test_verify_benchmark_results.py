import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import verify_benchmark_results as verifier


class BenchmarkVerificationTests(unittest.TestCase):
    def test_raw_seal_rejects_tampering_and_unregistered_files(self):
        artifacts = Path(__file__).resolve().parent / 'artifacts'
        with tempfile.TemporaryDirectory(dir=artifacts, prefix='verifier-test-') as folder:
            root = Path(folder).resolve()
            self.assertTrue(root.is_relative_to(artifacts.resolve()))
            (root / 'summary.json').write_text('{"value":1}', encoding='utf-8')
            registry = {'summary.json': verifier.sha(root / 'summary.json')}
            (root / 'sha256.json').write_text(json.dumps(registry), encoding='utf-8')
            count, _ = verifier.seal(root)
            self.assertEqual(count, 1)
            (root / 'summary.json').write_text('{"value":2}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'SHA mismatch'):
                verifier.seal(root)
            (root / 'summary.json').write_text('{"value":1}', encoding='utf-8')
            (root / 'extra.json').write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Incomplete raw seal'):
                verifier.seal(root)

    def test_seal_member_cannot_escape_result_directory(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe member'):
            verifier.member(Path(__file__).resolve().parent, '../escape.json')

    def test_upper_rank_p99_and_tick_units(self):
        self.assertAlmostEqual(verifier.p99({10: 99, 20: 1}, .1), 1)
        self.assertAlmostEqual(verifier.p99({10: 98, 20: 2}, .1), 2)
        with self.assertRaisesRegex(ValueError, 'Empty applicable'):
            verifier.p99({}, .1)


if __name__ == '__main__':
    unittest.main()
