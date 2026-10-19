import importlib.util
from pathlib import Path
import unittest
import numpy as np


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    value=importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


report=module('final_report','render-final-reports.py')
collector=module('final_collector','collect-final-evidence.py')


class FinalReportTests(unittest.TestCase):
    def test_est_matches_hand_calculation(self):
        values=[[10,.2,1,.9],[20,.4,2,.8]]
        np.testing.assert_allclose(report.score(values,1),[.675,.6],rtol=0,atol=1e-14)

    def test_qos0_removes_latency_without_rewarding_missing_ack(self):
        values=[[10,.2,float('nan'),.9],[20,.4,float('nan'),.8]]
        np.testing.assert_allclose(report.score(values,0),[2.2/3,2.4/3])
        with self.assertRaises(ValueError): report.score(values,1)

    def test_zero_rates_and_zero_applicable_latencies_are_defined(self):
        np.testing.assert_allclose(report.score([[0,0,0,0],[0,1,0,0]],1),[.5,.25])

    def test_bad_input_is_rejected_roundoff_only_is_allowed(self):
        with self.assertRaises(ValueError): report.score([[1,1.01,1,1]],1)
        self.assertGreaterEqual(report.score([[1,1+2e-16,1,1]],1)[0],0)

    def test_reliability_weight_can_change_the_winner(self):
        values=[[10,.2,1,.9],[20,.4,2,.2]]
        self.assertEqual(1,int(np.argmax(report.score(values,1,(.9,.03,.04,.03)))))
        self.assertEqual(0,int(np.argmax(report.score(values,1,(.1,.1,.1,.7)))))

    def test_window_excludes_boundary_and_idle_capture(self):
        samples=[dict(timestamp=f'2026-10-19T00:00:{i:02d}Z',monotonicSeconds=i,
                      unitId='1',processCpuPercent=90 if 3<=i<=20 else 0,processRssBytes=100)
                 for i in range(30)]
        observation=dict(measurementStartedUtc='2026-10-19T00:00:02.5Z',measurementEndedUtc='2026-10-19T00:00:22.5Z')
        result=collector.window_resources(samples,observation)
        self.assertEqual(18,result['intervals']) # interval ends 4..21; +1 second must be inside
        self.assertEqual(85,result['cpuPercent'])
        self.assertLess(result['captureCpuMeanPercent'],result['cpuPercent'])

    def test_foreign_and_traversal_evidence_paths_rejected(self):
        with self.assertRaises(ValueError): collector.mapped('/tmp/x',Path('.'))
        with self.assertRaises(ValueError): collector.mapped('/run/media/entityfx/hd250/mqtty-evidence/../x',Path('.'))


if __name__=='__main__': unittest.main()
