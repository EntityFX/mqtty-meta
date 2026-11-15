"""Tests for final matched-series checks, independent of launcher aggregation."""
import unittest
from audit_matched_main_results import compare, histogram, summarize, assert_close, calculate
import test_independent_twin_recount as known_ids


class MainAuditTests(unittest.TestCase):
    def test_known_ids_separate_drain_failures_pending_and_duplicates(self):
        fixture=known_ids.IndependentRecountTests();fixture.setUp()
        actual=calculate(fixture.publications,fixture.deliveries,fixture.phases)
        for key,value in dict(attempted=4,completed=2,failed=1,pending=1,completedInWindow=1,
            completedDuringDrain=1,uniqueDeliveries=3,completedIdsDelivered=2,duplicates=1,
            outsideCohortDeliveryEvents=3,deliveryLossRate=0,publishFailureRate=.25,p99PublishMs=1500,
            firstDeliveriesDuringDrain=2).items():self.assertEqual(value,actual[key],key)

    def test_bins_only_include_measured_successes_before_horizon(self):
        phases=dict(measurementStartSeconds=1,measurementEndSeconds=5,drainEndSeconds=10)
        rows=[dict(event='attempt',id='warm',elapsedSeconds=.9),
              dict(event='completed',id='warm',elapsedSeconds=1.1,latencyMs=9),
              dict(event='attempt',id='a',elapsedSeconds=1),
              dict(event='completed',id='a',elapsedSeconds=6,latencyMs=1.234),
              dict(event='attempt',id='late',elapsedSeconds=5),
              dict(event='completed',id='late',elapsedSeconds=6,latencyMs=7)]
        self.assertEqual([[1234,1]],histogram(rows,phases))

    def test_three_native_runs_are_not_pooled_with_model_seeds(self):
        r=summarize([1,2,3]);self.assertEqual(2,r['mean']);self.assertEqual(3,r['count'])
        self.assertGreater(r['ci95HalfWidth'],2)
        with self.assertRaises(ValueError):summarize([1,2,3,4,5,6])

    def test_each_seed_must_pass_and_loss_is_absolute(self):
        native=dict(rps=100,p99=10,failure=0,loss=.1)
        model=dict(rps=110,p99=11,failure=0,loss=.14)
        self.assertTrue(compare(native,model)['allCriteriaPassed'])
        self.assertFalse(compare(native,{**model,'loss':.16})['allCriteriaPassed'])
        self.assertFalse(compare({**native,'rps':0},model)['allCriteriaPassed'])

    def test_boolean_cannot_equal_numeric_count(self):
        with self.assertRaises(ValueError):assert_close(True,1,'count')


if __name__=='__main__':unittest.main()
