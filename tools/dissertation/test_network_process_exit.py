"""An exiting child must not become a false RSS-observation failure."""
import itertools
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import network_study_process as process_tools


class ExitTransitionTests(unittest.TestCase):
    def run_transition(self, exit_code):
        child = Mock(pid=12345, returncode=None)
        child.poll.side_effect = lambda: child.returncode

        def wait(timeout=None):
            if exit_code is None:
                raise subprocess.TimeoutExpired(['synthetic-child'], timeout)
            child.returncode = exit_code
            return exit_code

        child.wait.side_effect = wait

        def cleanup(value):
            if value.returncode is None:
                value.returncode = -9

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(process_tools.subprocess, 'Popen', return_value=child), \
             patch.object(process_tools, 'kill_tree', side_effect=cleanup) as kill, \
             patch.object(process_tools.time, 'monotonic', side_effect=itertools.count(0, 3)):
            result = process_tools.execute(['synthetic-child'], Path(directory), Mock(),
                30, 1024**3, 1024**3, lambda _: None, sample_seconds=.05,
                memory_reader=lambda _: 0)
            kill.assert_called_once_with(child)
            child.wait.assert_any_call(timeout=.05)
        return result

    def test_rss_disappears_during_successful_exit(self):
        result = self.run_transition(0)
        self.assertEqual(result['returnCode'], 0)
        self.assertIsNone(result['technicalError'])

    def test_rss_disappears_during_failed_exit(self):
        result = self.run_transition(-6)
        self.assertEqual(result['returnCode'], -6)
        self.assertIsNone(result['technicalError'])

    def test_live_process_without_rss_still_fails_and_is_cleaned_up(self):
        result = self.run_transition(None)
        self.assertEqual(result['returnCode'], -9)
        self.assertIn('processRssUnavailable', result['technicalError'])


if __name__ == '__main__':
    unittest.main()
