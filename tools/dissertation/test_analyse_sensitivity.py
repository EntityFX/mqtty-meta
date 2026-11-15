import copy
import unittest
import json
from pathlib import Path
import tempfile

from analyse_sensitivity import analyse, identity, render, verify_final, write_final_artifacts


def row(job_id, values=None, failed=False, technical=None):
    dimensions = identity(job_id)
    return {"id": job_id, "kind": dimensions["kind"], "variant": dimensions["variant"],
            "technicalError": technical, "returnCode": 3 if failed else 0,
            "fidelityFailed": failed, "wallSeconds": 10, "peakRssBytes": 1024,
            "summary": {"status": "completed", "simulated": values or {
                "completedRps": 100, "latencyP99Ms": 10, "publishFailureRate": 0, "deliveryLossRate": 0},
                "acceptanceCriteria": [{"name": "latencyP99Ms", "status": "failed" if failed else "passed"}]}}


def snapshot(*rows):
    return {"observedAtUtc": "snapshot", "plan": {"jobs": [{"id": r["id"]} for r in rows]},
            "rows": [{"data": r} for r in rows]}


class SensitivityAnalysisTests(unittest.TestCase):
    def test_scientific_failure_is_completed_not_technical(self):
        result = analyse(snapshot(row("Aedes.m16.q1.p1.s42.baseline", failed=True)))
        self.assertEqual(result["coverage"]["matrix"]["completed"], 1)
        self.assertEqual(result["coverage"]["matrix"]["scientificFailed"], 1)
        self.assertEqual(result["coverage"]["matrix"]["technicalErrors"], 0)

    def test_technical_rows_cannot_form_pairs_or_count_completed(self):
        a = row("Aedes.m16.q1.p1.s42.baseline")
        b = row("Aedes.m16.q1.p1.s42.halfTick", technical="timeout")
        result = analyse(snapshot(a, b))
        self.assertEqual(result["coverage"]["matrix"]["completed"], 1)
        self.assertEqual(result["coverage"]["matrix"]["technicalErrors"], 1)
        self.assertFalse(result["matchedPairs"])

    def test_pairing_requires_same_seed_and_excludes_pilots(self):
        rows = [row("pilot.Aedes.m16.q1.p1.s42.baseline"),
                row("Aedes.m16.q1.p1.s43.baseline"), row("Aedes.m16.q1.p1.s42.halfTick")]
        self.assertFalse(analyse(snapshot(*rows))["matchedPairs"])

    def test_metric_deltas_independently_calculated_zero_is_undefined(self):
        baseline = row("Aedes.m16.q1.p1.s42.baseline")
        changed = row("Aedes.m16.q1.p1.s42.doubleWindow", {
            "completedRps": 120, "latencyP99Ms": 8, "publishFailureRate": .03, "deliveryLossRate": 0})
        result = analyse(snapshot(baseline, changed))
        metrics = result["matchedPairs"][0]["metrics"]
        self.assertAlmostEqual(metrics["completedRps"]["relativeDelta"], .2)
        self.assertAlmostEqual(metrics["latencyP99Ms"]["relativeDelta"], -.2)
        self.assertEqual(metrics["publishFailureRate"]["absoluteDelta"], .03)
        self.assertIsNone(metrics["publishFailureRate"]["relativeDelta"])

    def test_qos_zero_null_latency_stays_not_applicable(self):
        rows = [row("Aedes.m16.q0.p1.s42." + variant) for variant in ("baseline", "halfTick")]
        for r in rows:
            r["summary"]["simulated"]["latencyP99Ms"] = None
        result = analyse(snapshot(*rows))
        self.assertEqual(result["matchedPairs"][0]["metrics"]["latencyP99Ms"]["status"], "notApplicable")
        self.assertIn("неприменимо", render(result))

    def test_missing_results_remain_in_denominator(self):
        source = snapshot(row("Aedes.m16.q0.p1.s42.baseline"))
        source["plan"]["jobs"].append({"id": "Aedes.m16.q0.p1.s43.baseline"})
        self.assertEqual(analyse(source)["coverage"]["matrix"]["planned"], 2)

    def test_duplicate_and_inconsistent_failure_metadata_rejected(self):
        source = snapshot(row("Aedes.m16.q1.p1.s42.baseline"))
        duplicate = copy.deepcopy(source)
        duplicate["rows"].append(duplicate["rows"][0])
        with self.assertRaises(ValueError):
            analyse(duplicate)
        source["rows"][0]["data"]["fidelityFailed"] = True
        with self.assertRaises(ValueError):
            analyse(source)

    def test_canonical_pairs_order_does_not_depend_on_snapshot_row_order(self):
        rows = [row("Mosquitto.m16.q1.p128.s42." + variant) for variant in ("baseline", "halfTick", "doubleWindow")]
        a = analyse(snapshot(*rows))
        b = analyse(snapshot(*reversed(rows)))
        self.assertEqual(a, b)


class FinalSensitivityAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / 'reports/audits/dissertation/sensitivity-final-v1/observation.json'
        if not path.exists():
            raise unittest.SkipTest('Final frozen evidence snapshot is not installed')
        cls.source = json.loads(path.read_bytes())

    def test_complete_real_snapshot_and_exact_replays(self):
        result = verify_final(self.source)
        self.assertEqual(result['pairedComparisons'], 144)
        self.assertEqual(result['acceptanceFormulaChecks'], 920)
        self.assertEqual(result['exactReplays'], 6)

    def test_missing_row_rejected_even_when_progress_claims_completion(self):
        source = copy.deepcopy(self.source)
        source['rows'].pop()
        with self.assertRaises(ValueError): verify_final(source)

    def test_changed_threshold_rejected(self):
        source = copy.deepcopy(self.source)
        source['rows'][0]['summary']['acceptanceCriteria'][0]['limit'] = .5
        with self.assertRaisesRegex(ValueError, 'limit'): verify_final(source)

    def test_forged_rps_with_unchanged_counters_rejected(self):
        source = copy.deepcopy(self.source)
        source['rows'][0]['summary']['simulated']['completedRps'] += 1
        with self.assertRaisesRegex(ValueError, 'RPS formula'): verify_final(source)

    def test_changed_unrelated_variant_input_rejected(self):
        source = copy.deepcopy(self.source)
        key = 'Mosquitto.m16.q0.p1.s42.halfTick'
        source['verifiedRuns'][key]['config']['network']['queueCapacity'] += 1
        with self.assertRaisesRegex(ValueError, 'unrelated input'): verify_final(source)

    def test_forged_exact_replay_flag_is_not_sufficient(self):
        source = copy.deepcopy(self.source)
        source['verifiedReplays'][0]['hashes']['summarySha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Replay bytes'): verify_final(source)

    def test_counters_tampering_rejected(self):
        source = copy.deepcopy(self.source)
        key = next(iter(source['verifiedRuns']))
        source['verifiedRuns'][key]['scientificCounters']['receivers'] = {}
        with self.assertRaisesRegex(ValueError, 'counters modified'): verify_final(source)

    def test_report_csv_plot_deterministic_and_all_failures_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            a, b = [Path(temporary) / name for name in ('a', 'b')]
            a.mkdir(); b.mkdir()
            for output in (a, b): write_final_artifacts(self.source, output, 'same-snapshot-sha')
            self.assertEqual({p.name: p.read_bytes() for p in a.iterdir()}, {p.name: p.read_bytes() for p in b.iterdir()})
            result = json.loads((a / 'analysis.json').read_bytes())
            self.assertEqual(result['coverage']['matrix']['scientificFailed'], 55)
            self.assertEqual(len(result['matchedPairs']), 144)
            self.assertIn('55/216', (a / 'Sensitivity-final.md').read_text(encoding='utf-8'))


if __name__ == "__main__":
    unittest.main()
