import unittest

from analyse_accuracy import native_point


class AccuracyAnalysisTests(unittest.TestCase):
    def rows(self):
        result = []
        for repeat, attempts, delivered, loss, rate in [(1, 10, 10, 0, 5), (2, 100, 50, .5, 50), (3, 1000, 0, 1, 500)]:
            result.append({"key": {"repeat": repeat}, "observation": {"measurement": {
                "completedRps": rate, "publishFailureRate": 0, "deliveryLossRate": loss,
                "uniqueDeliveries": delivered, "attemptedPublishes": attempts, "actualMeasurementSeconds": 2}}})
        return result

    def test_repeats_have_equal_weight_not_pooled_event_weight(self):
        value = native_point(self.rows())
        self.assertEqual(value["attemptDeliveryFraction"], .5)
        self.assertAlmostEqual(value["completedRps"], 185)
        self.assertEqual(value["deliveryLossRange"], 1)
        self.assertEqual(value["deliveryLossRate"], .5)

    def test_duplicate_repeat_cannot_replace_missing_repeat(self):
        rows = self.rows(); rows[2]["key"]["repeat"] = 2
        with self.assertRaisesRegex(ValueError, "exactly repeats"):
            native_point(rows)

    def test_incomplete_point_is_not_accepted(self):
        with self.assertRaises(ValueError): native_point(self.rows()[:2])


if __name__ == "__main__":
    unittest.main()
