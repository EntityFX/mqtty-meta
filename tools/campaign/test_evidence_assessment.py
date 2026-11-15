import copy
import importlib.util
from pathlib import Path
import unittest


spec=importlib.util.spec_from_file_location('assessment',Path(__file__).with_name('assess-evidence.py'))
assessment=importlib.util.module_from_spec(spec)
spec.loader.exec_module(assessment)


class EvidenceAssessmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[2]/'reports/data'
        cls.runs=assessment.read(root/'native-evidence.json')['runs']
        cls.observations={(b,s):assessment.read(root/b/s/'observations.json')
                          for b in assessment.BROKERS for s in ('calibration','validation')}

    def test_mutated_time_denominator_is_detected(self):
        runs=copy.deepcopy(self.runs)
        runs[0]['observation']['measurement']['actualMeasurementSeconds']*=2
        with self.assertRaisesRegex(ValueError,'Rps'):
            assessment.audit_native(runs,self.observations)

    def test_delivery_denominators_cannot_be_substituted(self):
        runs=copy.deepcopy(self.runs)
        measurement=next(r['observation']['measurement'] for r in runs
                         if r['observation']['measurement']['failedPublishes']>0)
        measurement['deliveryLossRate']=1-measurement['uniqueDeliveries']/measurement['attemptedPublishes']
        with self.assertRaisesRegex(ValueError,'deliveryLossRate'):
            assessment.audit_native(runs,self.observations)

    def test_duplicate_repeat_is_rejected(self):
        runs=copy.deepcopy(self.runs)
        runs[0]=copy.deepcopy(runs[1])
        with self.assertRaisesRegex(ValueError,'matrix or duplicate'):
            assessment.audit_native(runs,self.observations)

    def test_qos0_cannot_gain_fictitious_zero_ack_latency(self):
        runs=copy.deepcopy(self.runs)
        r=next(r for r in runs if r['key']['qos']==0)
        r['observation']['measurement']['publishLatencyMs']={k:0 for k in ('minMs','p50Ms','p95Ms','p99Ms','maxMs')}
        with self.assertRaisesRegex(ValueError,'QoS0 ACK latency'):
            assessment.audit_native(runs,self.observations)

    def test_strict_est_and_qos0_adaptation_match_hand_calculation(self):
        values=[[10,.2,1,.9],[20,.4,2,.8]]
        a,b=assessment.evaluate_est(values,1)
        self.assertAlmostEqual(a,.675)
        self.assertAlmostEqual(b,.6)
        a,b=assessment.evaluate_est([[10,.2,None,.9],[20,.4,None,.8]],0)
        self.assertAlmostEqual(a,2.2/3)
        self.assertAlmostEqual(b,2.4/3)

    def test_invalid_fraction_is_rejected(self):
        with self.assertRaises(ValueError):
            assessment.evaluate_est([[1,1.01,1,1]],1)


if __name__=='__main__': unittest.main()
