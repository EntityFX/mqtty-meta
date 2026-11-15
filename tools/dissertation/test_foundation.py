import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from foundation import (check_raw_audit, collect_files, coverage_csv, equivalent, freeze, inside,
                        profile_registry, sha, verify_files)


class FoundationTests(unittest.TestCase):
    def test_runtime_roundoff_does_not_allow_changed_counts_decisions_or_missing_fields(self):
        expected = {"score": .7, "passed": 576, "status": "passed", "present": True}
        self.assertTrue(equivalent(dict(expected, score=.7000000000000001), expected))
        for bad in [dict(expected, score=.701), dict(expected, passed=575),
                    dict(expected, passed=576.0), dict(expected, status="failed"),
                    dict(expected, present=1), {"score": .7}]:
            with self.subTest(bad=bad): self.assertFalse(equivalent(bad, expected))

    def fixture(self):
        identity = {"campaignId": "Aedes-calibration", "commit": "measured-code"}
        key = {"broker": "Aedes", "messageBytes": 16, "qos": 1, "publishers": 1, "repeat": 1}
        m = {"attemptedPublishes": 100, "completedPublishes": 80, "failedPublishes": 20,
             "uniqueDeliveries": 70, "completedIdsDelivered": 60, "deliveredAfterFailedPublish": 10,
             "attemptedRps": 50., "completedRps": 40., "publishFailureRate": .2,
             "deliveryLossRate": .25, "publishLatencyMs": {"p99Ms": 5.}}
        scientific = {"archiveSha256": "hash", "key": key, "identity": identity,
                      "status": "success", "eventCoverage": "complete", "sealedFilesVerified": 4,
                      "differences": {}, "anomalies": {}, "unsettled": 0,
                      "recounted": {k: v for k, v in m.items() if isinstance(v, int)},
                      "rates": {k: v for k, v in m.items() if isinstance(v, float)},
                      "publishLatencyMs": m["publishLatencyMs"]}
        row = {"archivePath": "/evidence/a.zip", "expectedSha256": "hash", "error": None,
               "selectedPublishedRun": True, "scientific": scientific}
        audit = {"results": [row], "counts": {"processed": 1, "total": 1, "selectedPassed": 1,
                 "selectedDiscrepancies": 0, "technicalErrors": 0, "nonSuccessArchives": 0,
                 "nonSelectedSuccessArchives": 0},
                 "manifest": {"pilotLimit": None, "archives": 1, "publishedRuns": 1}}
        native = {"runs": [{"broker": "Aedes", "stage": "calibration", "key": key,
                  "archive": {"archivePath": "/evidence/a.zip", "sha256": "hash"},
                  "observation": {"measurement": m}}]}
        registry = [{"archivePath": "/evidence/a.zip", "sha256": "hash"}]
        return audit, native, registry, {("Aedes", "calibration"): identity}

    def test_complete_archive_selection_is_bound_to_keys_identities_and_rates(self):
        values = self.fixture()
        self.assertEqual(check_raw_audit(*values)["selectedPassed"], 1)
        text = coverage_csv(values[0], {r["archive"]["archivePath"]: r for r in values[1]["runs"]})
        self.assertIn("Aedes,calibration,16,1,1,1,True,success,complete,4", text)

    def test_duplicate_missing_and_relabelled_archives_are_rejected(self):
        for change in ("duplicate", "missing", "relabel", "hash", "identity", "key", "pilot"):
            with self.subTest(change=change):
                a, n, r, ids = self.fixture()
                if change == "duplicate": a["results"].append(copy.deepcopy(a["results"][0]))
                if change == "missing": a["results"].clear()
                if change == "relabel": a["results"][0]["selectedPublishedRun"] = False
                if change == "hash": a["results"][0]["expectedSha256"] = "other"
                if change == "identity": a["results"][0]["scientific"]["identity"] = {"campaignId": "other"}
                if change == "key": a["results"][0]["scientific"]["key"] = {"broker": "other"}
                if change == "pilot": a["manifest"]["pilotLimit"] = 1
                with self.assertRaises(ValueError): check_raw_audit(a, n, r, ids)

    def test_forged_clean_flags_do_not_hide_changed_counters_or_quantiles(self):
        for change in ("rate", "counts", "latency", "error", "journals", "totals"):
            with self.subTest(change=change):
                a, n, r, ids = self.fixture()
                s = a["results"][0]["scientific"]
                if change == "rate": s["rates"]["completedRps"] = 999.
                if change == "counts": s["recounted"]["uniqueDeliveries"] = 100
                if change == "latency": s["publishLatencyMs"] = {"p99Ms": 0.}
                if change == "error": a["results"][0]["error"] = {"message": "cannot read"}
                if change == "journals": s["eventCoverage"] = "journalsUnavailable"
                if change == "totals": a["counts"]["processed"] = 2
                with self.assertRaises(ValueError): check_raw_audit(a, n, r, ids)

    def test_frozen_inputs_detect_mutation_and_relocation_without_editing_them(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "input.json"
            path.write_text('{"value": 1}')
            entries = collect_files(root, [path])
            self.assertEqual(verify_files(root, entries), 1)
            path.write_text('{"value": 2}')
            with self.assertRaisesRegex(ValueError, "Frozen input changed"):
                verify_files(root, entries)
            path.unlink()
            with self.assertRaises(ValueError): verify_files(root, entries)

    def test_paths_cannot_escape_manifest_and_archives_cannot_enter_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "escapes"): inside(root, "../outside")
            p = root / "raw.zip"
            p.write_bytes(b"archive")
            with self.assertRaisesRegex(ValueError, "outside"): collect_files(root, [p])

    def test_existing_package_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            existing = root / "manifest.json"
            existing.write_text("keep")
            with self.assertRaisesRegex(ValueError, "already exists"):
                freeze(root, root, root / "absent-audit.json")
            self.assertEqual(existing.read_text(), "keep")

    def test_profile_identity_cannot_be_reused_as_holdout_or_hash_substituted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "data"
            for stage in ("calibration", "validation", "profile"):
                (data / "Aedes" / stage).mkdir(parents=True)
            obs = data / "Aedes/calibration/observations.json"
            ident = {"campaignId": "cal"}
            obs.write_text(json.dumps({"provenance": {"identity": ident}}))
            held = data / "Aedes/validation/observations.json"
            held.write_text(json.dumps({"provenance": {"identity": ident}}))
            profile = data / "Aedes/profile/broker-calibration.v3.json"
            profile.write_text(json.dumps({"provenance": {"observationsSha256": sha(obs), "observations": {"identity": ident}}}))
            (data / "state.json").write_text(json.dumps({"profiles": {"Aedes": sha(profile)}}))
            with self.assertRaisesRegex(ValueError, "identity merged"):
                profile_registry(root, data)
            profile.write_text("{}")
            with self.assertRaisesRegex(ValueError, "changed"):
                profile_registry(root, data)


class EstMeaningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "campaign/assess-evidence.py"
        spec = importlib.util.spec_from_file_location("checked_assessor", path)
        cls.assessor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.assessor)

    def test_equal_weight_est_has_a_hand_computed_reference(self):
        # max R=100, max L=20; first score=(1+.5+.5+.9)/4=.725.
        rows = [[100, .5, 10, .9], [50, .25, 20, 1], [100, 1, 5, .5], [25, 0, 1, .8]]
        score = self.assessor.evaluate_est(rows, 1)
        self.assertAlmostEqual(score[0], .725)
        self.assertAlmostEqual(score[1], .5625)

    def test_qos0_removes_latency_and_renormalizes_three_terms(self):
        rows = [[100, .5, None, .9], [50, .25, None, 1]]
        self.assertAlmostEqual(self.assessor.evaluate_est(rows, 0)[0], .8)

    def test_missing_qos1_latency_is_not_a_zero_delay(self):
        with self.assertRaises((ValueError, TypeError)):
            self.assessor.evaluate_est([[100, .5, None, .9]], 1)


if __name__ == "__main__":
    unittest.main()
