import copy
import json
import unittest
from revise_article1_analysis import ROOT, condition_rows, threshold_pass, quantile


class RevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models=json.loads((ROOT/'reports/data/model-evidence.json').read_bytes())

    def test_seed_runs_are_not_counted_as_conditions(self):
        rows=condition_rows(self.models)
        control=[r for r in rows if r['stage']=='holdout']
        self.assertEqual(len(control),96)
        self.assertEqual(sum(r['passedSeeds']==3 for r in control),64)
        self.assertEqual(sum(0<r['passedSeeds']<3 for r in control),8)
        self.assertEqual(sum(r['passedSeeds']==0 for r in control),24)

    def test_duplicate_seed_is_rejected(self):
        rows=copy.deepcopy(self.models)
        rows['Aedes'][1]['seed']=rows['Aedes'][0]['seed']
        with self.assertRaisesRegex(ValueError,'seed'):condition_rows(rows)

    def test_threshold_boundary_and_not_applicable(self):
        row=dict(rpsMaxError=.15,latencyP99MsMaxError=None,publishFailureRateMaxError=0,deliveryLossRateMaxError=.05)
        self.assertTrue(threshold_pass(row,1))
        self.assertFalse(threshold_pass(row,.99))

    def test_empirical_quantiles_do_not_interpolate_false_precision(self):
        self.assertEqual(quantile([0,1,2],.025),0)
        self.assertEqual(quantile([0,1,2],.975),2)


if __name__=='__main__':unittest.main()
