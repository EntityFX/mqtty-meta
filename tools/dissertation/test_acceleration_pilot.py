"""Portable contract tests plus actual Linux child accounting and cleanup."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import run_acceleration_pilot as pilot


class ContractTests(unittest.TestCase):
    def test_changed_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'input').write_bytes(b'original')
            seal = {'input': hashlib.sha256(b'original').hexdigest()}
            pilot.verify_files(root, seal)
            (root / 'input').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                pilot.verify_files(root, seal)

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                pilot.verify_files(Path(tmp), {'../outside': 'fake'})

    def test_existing_output_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'
            pilot.claim_output(root)
            (root / 'keep').write_bytes(b'raw')
            with self.assertRaises(FileExistsError):
                pilot.claim_output(root)
            self.assertEqual((root / 'keep').read_bytes(), b'raw')

    def test_scientific_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            pilot.check_equivalence({'summary': 'a'}, {'summary': 'b'})

    def test_aa_spread_gate(self):
        self.assertEqual(pilot.aa_classification(100, 109), 'measurementReady')
        self.assertEqual(pilot.aa_classification(100, 110), 'inconclusiveAaSpread')
        with self.assertRaises(ValueError):
            pilot.aa_classification(0, 100)


@unittest.skipUnless(sys.platform.startswith('linux'), 'GNU time and /proc required')
class LinuxProcessTests(unittest.TestCase):
    def test_cpu_and_memory_measured_and_nonzero_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = [sys.executable, '-B', '-c',
                       'import time; x=bytearray(12*1024*1024); end=time.monotonic()+.25\nwhile time.monotonic()<end: sum(range(1000))']
            result = pilot.measure_jobs([command, command], Path(tmp) / 'pair', 2,
                                        time.monotonic()+5, job_timeout=3)
            self.assertEqual(len(result['jobs']), 2)
            self.assertTrue(all(j['userSeconds'] > 0 and j['maxRssBytes'] > 12*1024**2 for j in result['jobs']))
            self.assertGreater(result['simultaneousSampledRssPeakBytes'], 0)
            bad = pilot.measure_jobs([[sys.executable, '-c', 'raise SystemExit(7)']],
                                     Path(tmp) / 'bad', 1, time.monotonic()+5)
            self.assertEqual(bad['jobs'][0]['returnCode'], 7)
            self.assertEqual(bad['error'], 'childFailed')

    def test_deadline_kills_and_reaps_grandchild(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pidfile = root / 'grandchild.pid'
            command = [sys.executable, '-B', '-c',
                       'import subprocess,time,pathlib; p=subprocess.Popen(["sleep","60"]); '
                       f'pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(60)']
            result = pilot.measure_jobs([command], root / 'timeout', 1, time.monotonic()+.4)
            self.assertEqual(result['error'], 'deadlineExceeded')
            self.assertIsNotNone(result['jobs'][0]['returnCode'])
            pid = int(pidfile.read_text())
            status = Path(f'/proc/{pid}/status')
            # A killed orphan may await PID1's reap; it must not be running.
            self.assertTrue(not status.exists() or '\nState:\tZ' in status.read_text())


if __name__ == '__main__':
    unittest.main()
