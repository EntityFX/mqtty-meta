import unittest
from run_instrumentation_diagnostic import planned_cases, require_time, require_idle


class DiagnosticSafetyTests(unittest.TestCase):
    def test_four_identical_windows_no_on_off_claim(self):
        self.assertEqual(planned_cases(), [dict(repeat=i, window=4, recordPublicationCohort=True) for i in range(1, 5)])

    def test_no_deadline_reset(self):
        with self.assertRaises(TimeoutError):
            require_time(100, 99, reserve=1)
        with self.assertRaises(TimeoutError):
            require_time(90, 100, reserve=20)
        self.assertEqual(require_time(50, 100, reserve=20), 50)

    def test_live_jobs_block(self):
        for state in ('Running', 'Queued', 'Paused', 'Cancelling'):
            with self.assertRaises(RuntimeError):
                require_idle([dict(id='unrelated', state=state)])
        require_idle([dict(id='done', state='Completed')])


if __name__ == '__main__':
    unittest.main()
