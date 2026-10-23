import copy
import unittest

from network_review import check_comparison, ideal_counts, physical_edges


class NetworkReviewTests(unittest.TestCase):
    def options(self, mode="relay", topology="ring", n=3):
        return dict(mode=mode, topology=topology, brokers=n, publishers=12, links=None,
                    receiverSites=None, payloadBytes=16, qos=1, offeredRps=1000, seed=42,
                    warmupSeconds=.01, measurementSeconds=.2, drainTimeoutSeconds=1,
                    tickPeriod="00:00:00.0001000", queueCapacity=1024, cacheCapacity=100000,
                    hopLimit=None, ideal=True, brokerType="Mosquitto", network={"queue": 1000})

    def summary(self, forwards, suppressed=0, receivers=3):
        return dict(apiAttempts=200, sourceCompleted=200, status="completed",
                    uniqueDeliveries=200 * receivers, expectedDeliveries=200 * receivers,
                    duplicateDeliveries=0, queueDrops=0, hopLimitDrops=0, forwardRejected=0,
                    forwardAttempts=forwards, suppressedDuplicates=suppressed)

    def test_complete_export_and_flooding_have_different_cost(self):
        ideal_counts(self.options(), self.summary(400))
        ideal_counts(self.options(mode="federation"), self.summary(800, 400))
        ideal_counts(self.options(n=10, topology="star"), self.summary(1800, receivers=10))
        ideal_counts(self.options(mode="federation", n=10, topology="star"), self.summary(16200, 14400, 10))

    def test_tree_has_no_repeated_arrivals_and_no_receivers_changes_pairs(self):
        o = self.options(mode="federation", topology="line")
        ideal_counts(o, self.summary(400))
        o["receiverSites"] = []
        ideal_counts(o, self.summary(400, receivers=0))

    def test_resealed_wrong_forward_or_duplicate_counts_are_rejected(self):
        o = self.options(mode="federation")
        for key in ("forwardAttempts", "suppressedDuplicates", "uniqueDeliveries"):
            s = self.summary(800, 400); s[key] += 1
            with self.assertRaises(AssertionError):
                ideal_counts(o, s)

    def test_comparison_rejects_changed_physical_network_or_useful_load(self):
        options = [self.options(mode=mode) for mode in ("single", "relay", "federation")]
        check_comparison(options)
        for key, value in (("publishers", 64), ("receiverSites", [0]), ("network", {"queue": 1}), ("offeredRps", 999)):
            changed = copy.deepcopy(options); changed[1][key] = value
            with self.assertRaises(AssertionError):
                check_comparison(changed)
        with self.assertRaises(AssertionError):
            check_comparison(options[:2])

    def test_star_transit_is_physical_and_not_a_broker(self):
        edges = physical_edges(self.options(n=10, topology="star"))
        self.assertEqual(len(edges), 10)
        self.assertTrue(all("transit" in edge for edge in edges))
        self.assertNotIn(("site0", "site1"), edges)


if __name__ == "__main__":
    unittest.main()
