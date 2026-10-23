from collections import Counter, defaultdict
import unittest

from prepare_network_study import cases


class NetworkProgramTests(unittest.TestCase):
    def test_counts_and_unique_ids_separate_pilots_and_optional_profiles(self):
        jobs = cases()
        self.assertEqual(Counter(j["kind"] for j in jobs), {"pilot": 30, "matrix": 162, "replay": 6, "diagnostic": 54})
        self.assertEqual(len({j["id"] for j in jobs}), len(jobs))
        self.assertTrue(all(j["configuration"]["ideal"] for j in jobs if j["kind"] != "diagnostic"))

    def test_comparison_keeps_clients_physical_resources_and_load(self):
        groups = defaultdict(list)
        for job in cases():
            if job["kind"] != "matrix":
                continue
            o = job["configuration"].copy(); o.pop("mode")
            groups[(o["topology"], o["qos"], o["offeredRps"], o["seed"])].append(o)
        self.assertEqual(len(groups), 54)
        for configs in groups.values():
            self.assertEqual(len(configs), 3)
            self.assertTrue(all(o == configs[0] for o in configs))

    def test_cache_and_delivery_budget_cover_warmup_and_measurement(self):
        for j in cases():
            o = j["configuration"]
            messages = (o["warmupSeconds"] + o["measurementSeconds"]) * o["offeredRps"]
            self.assertLessEqual(messages, o["cacheCapacity"])
            self.assertLessEqual(messages * len(o["receiverSites"]), 1000000)
            if not o["ideal"]:
                self.assertEqual(o["publishers"] if o["mode"] == "single" else o["publishers"] // o["brokers"],
                                 64 if o["mode"] == "single" else 16)

    def test_replays_keep_exact_original_configuration(self):
        jobs = {j["id"]: j for j in cases()}
        for j in jobs.values():
            if j["kind"] == "replay":
                self.assertEqual(j["configuration"], jobs[j["replayOf"]]["configuration"])


if __name__ == "__main__":
    unittest.main()
