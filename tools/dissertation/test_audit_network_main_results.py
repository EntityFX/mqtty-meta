import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from audit_network_main_results import matched_cases, comparison_rows
import test_network_review as fixtures


def record(mode, seed=42, rps=1000, loss=0, p99=10):
    options=fixtures.NetworkReviewTests().options(mode=mode)
    options.update(seed=seed,offeredRps=rps)
    return dict(id=f'{mode}.{seed}.{rps}',options=options,summary=dict(
        sourceCompletedRps=1000,uniqueGoodput=3000,deliveryLoss=loss,
        receiverP99Ms=p99,publishP99Ms=None,forwardAttempts=400,
        forwardTopicPayloadBytes=14400,uniqueDuringDrain=20,
        expectedDeliveries=600,uniqueDeliveries=600,
        fullyDeliveredPublications=200,sourceCompleted=200,
        sourceCompletedDuringDrain=0,apiAttempts=200,
        queueDrops=0,hopLimitDrops=0,forwardRejected=0,
        backpressureUnsent=0,duplicateDeliveries=0,suppressedDuplicates=0))


class MatchedMainAuditTests(unittest.TestCase):
    def test_cli_imports_checker_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('audit_network_main_results.py')),'--help'],cwd=directory,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--root',result.stdout)

    def test_partial_mode_seed_does_not_leak_into_comparison(self):
        rows=[record(m) for m in ('single','relay','federation')]+[record('single',43)]
        selected,excluded=matched_cases(rows)
        self.assertEqual(len(selected),3)
        self.assertEqual(excluded,['single.43.1000'])
        result=comparison_rows(rows)
        self.assertTrue(all(r['matchedSeeds']==[42] for r in result))

    def test_different_loads_cannot_form_a_triplet(self):
        rows=[record('single'),record('relay'),record('federation',rps=5000)]
        selected,excluded=matched_cases(rows)
        self.assertFalse(selected);self.assertEqual(len(excluded),3)

    def test_changed_physical_resources_rejected_even_for_matching_keys(self):
        rows=[record(m) for m in ('single','relay','federation')]
        rows[-1]['options']['publishers']=64
        with self.assertRaises(AssertionError):matched_cases(rows)

    def test_absent_latency_not_replaced_with_zero_or_other_mode(self):
        rows=[record(m,s,p99=None if m=='federation' and s==43 else 10)
              for s in (42,43,44) for m in ('single','relay','federation')]
        federation=next(r for r in comparison_rows(rows) if r['mode']=='federation')
        self.assertEqual(federation['matchedSeeds'],[42,43,44])
        self.assertEqual(federation['metrics']['receiverP99Ms'],dict(n=2,mean=10,min=10,max=10))
        self.assertEqual(federation['metrics']['publishP99Ms'],dict(n=0,mean=None,min=None,max=None))

    def test_seed_rates_get_equal_weight_not_message_weight(self):
        rows=[record(m,s,loss=loss) for s,loss in ((42,0),(43,1)) for m in ('single','relay','federation')]
        for row in rows:row['summary']['expectedDeliveries']=10 if row['options']['seed']==42 else 1000
        self.assertTrue(all(r['metrics']['deliveryLoss']['mean']==.5 for r in comparison_rows(rows)))

    def test_duplicate_mode_in_same_group_is_rejected(self):
        rows=[record(m) for m in ('single','relay','federation')]
        rows.append(copy.deepcopy(rows[0]))
        with self.assertRaises(ValueError):matched_cases(rows)


if __name__=='__main__':unittest.main()
