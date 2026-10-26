import unittest

from article2_evidence_package import check_run, check_campaign


class EvidenceTests(unittest.TestCase):
    def example(self):
        return {"id": "known", "family": "ideal", "mode": "single", "qos": 0,
                "summary": {"status": "completed", "measurementSeconds": 2,
                "apiAttempts": 4, "sourceCompleted": 4,
                "sourceCompletedBeforeDrain": 3, "sourceCompletedDuringDrain": 1,
                "sourceCompletedRps": 2, "expectedDeliveries": 8,
                "uniqueDeliveries": 6, "uniqueBeforeDrain": 4,
                "uniqueDuringDrain": 2, "uniqueGoodput": 3, "deliveryLoss": .25,
                "publishP99Ms": None, "receivers": [{"uniqueDeliveries": 3},
                {"uniqueDeliveries": 3}], "sources": [{"metrics": {"attempted": 4,
                "completed": 4}}], "forwardAttempts": 0, "forwardAccepted": 0,
                "forwardRejected": 0}}

    def test_multiple_receivers_and_drain(self):
        self.assertEqual(check_run(self.example()), [])

    def test_wrong_loss_denominator_rejected(self):
        row = self.example()
        row["summary"]["deliveryLoss"] = 1 - 6 / 4
        self.assertIn("deliveryLoss", " ".join(check_run(row)))

    def test_missing_drain_rejected(self):
        row = self.example()
        row["summary"]["uniqueDuringDrain"] = 0
        self.assertIn("uniqueDrain", " ".join(check_run(row)))

    def test_qos0_ack_cannot_be_zero(self):
        row = self.example()
        row["summary"]["publishP99Ms"] = 0
        self.assertIn("qos0Ack", " ".join(check_run(row)))

    def test_failed_attempts_are_not_missing_cases(self):
        p = {"status": "budgetExhausted", "verified": 106,
             "counts": {"matrix": {"done": 106, "total": 162, "unresolvedErrors": 2},
                        "replay": {"done": 0, "total": 6}},
             "attempts": {"matrix": {"total": 109, "failed": 3}}}
        result = check_campaign(p)
        self.assertEqual(result["notVerifiedCases"], 56)
        self.assertEqual(result["unresolvedErrorCases"], 2)
        self.assertEqual(result["otherNotVerifiedCases"], 54)
        self.assertEqual(result["failedAttempts"], 3)
        self.assertFalse(result["acceptedAsComplete"])


if __name__ == "__main__":
    unittest.main()
