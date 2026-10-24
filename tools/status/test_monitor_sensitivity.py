import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("monitor_sensitivity", HERE / "monitor_sensitivity.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class MonitorPhaseCountsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = HERE.parents[1] / "reports/audits/dissertation/sensitivity-final-v1/observation.json"
        cls.observation = json.loads(path.read_text(encoding="utf-8"))
        cls.rows = cls.observation["rows"]

    def test_final_snapshot_230_rows_are_disjoint_cohorts(self):
        totals = {kind: sum(monitor.row_identity(row)[0] == kind for row in self.rows)
                  for kind in ("matrix", "pilot", "replay")}
        self.assertEqual(totals, {"matrix": 216, "pilot": 8, "replay": 6})
        stats = monitor.broker_stats(self.rows)
        self.assertEqual(set(stats), {"Aedes", "Mosquitto", "ActiveMQ", "EMQX"})
        self.assertTrue(all(st["runs"] == 54 for st in stats.values()))
        self.assertEqual(sum(st["failed"] for st in stats.values()), 55)
        self.assertEqual(sum(st["technical"] for st in stats.values()), 0)

    def test_replay_fidelity_failure_does_not_mean_repeat_mismatch(self):
        self.assertEqual(monitor.replay_stats(self.rows), {
            "equal": 6, "mismatched": 0, "unchecked": 0, "fidelityFailed": 2})
        text = monitor.replay_detail(self.rows, 6)
        self.assertIn("equal=6/6 mismatch=0", text)
        self.assertIn("fidelity failed=2 (separate test)", text)

    def test_broker_table_has_correct_denominators_without_replay_broker(self):
        lines = monitor.broker_lines(self.rows, self.observation["progress"]["counts"])
        text = "\n".join(lines)
        self.assertEqual(text.count("matrix 54/54"), 4)
        self.assertEqual(text.count("pilot 2/2"), 4)
        self.assertNotIn("/56", text)
        self.assertNotIn("replay", text)

    def test_dimensions_only_include_matrix(self):
        for dimension, expected in ((4, {"baseline": 72, "doubleWindow": 72, "halfTick": 72}),
                                    (1, {"q0": 72, "q1": 72, "q2": 72}),
                                    (2, {"p1": 108, "p128": 108}),
                                    (3, {"s42": 72, "s43": 72, "s44": 72})):
            stats = monitor.group_stats(dimension, set(), self.rows)
            self.assertEqual({key: value["runs"] for key, value in stats.items()}, expected)
            self.assertEqual(sum(st["failed"] for st in stats.values()), 55)

    def test_replay_prefix_is_parsed_as_kind_not_broker(self):
        replay = next(row for row in self.rows if row["kind"] == "replay")
        kind, broker, rest = monitor.row_identity(replay)
        self.assertEqual(kind, "replay")
        self.assertIn(broker, ("Aedes", "Mosquitto", "ActiveMQ", "EMQX"))
        self.assertEqual(rest[0], "m16")
        self.assertEqual(sum(len(items) for items in monitor.collect_runs(set(), self.rows)["replay"].values()), 6)

    def test_technical_error_is_not_inferred_as_fidelity_failure(self):
        row = {"id": "Aedes.m16.q1.p1.s42.baseline", "technicalError": "killed", "wallSeconds": 1}
        stats = monitor.broker_stats([row])["Aedes"]
        self.assertEqual(stats["technical"], 1)
        self.assertEqual(stats["failed"], 0)
        runs = monitor.collect_runs(set(), [row])
        self.assertEqual(next(iter(runs["matrix"]["Aedes"].values())), "technical")
        lines = []
        monitor.render_matrix_block(lines, "Matrix", "matrix", runs, set())
        self.assertIn("criteriaFail=0 technical=1", "\n".join(lines))

    def test_unchecked_repeat_not_counted_as_equal_or_mismatch(self):
        rows = [{"id": "replay.Aedes.m16.q0.p1.s42.baseline"},
                {"id": "replay.Mosquitto.m16.q0.p1.s42.baseline", "replayEqual": False}]
        self.assertEqual(monitor.replay_stats(rows), {
            "equal": 0, "mismatched": 1, "unchecked": 1, "fidelityFailed": 0})

    def test_active_row_excluded_from_finished_stats(self):
        row = self.rows[0]
        actual = monitor.group_stats(1, {row["id"]}, [row], kind=row["kind"])
        self.assertEqual(actual, {})

    def test_bad_row_file_does_not_hide_valid_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "bad.json").write_text("{", encoding="utf-8")
            Path(temp, "good.json").write_text(json.dumps(self.rows[0]), encoding="utf-8")
            with patch.object(monitor, "ROWS_DIR", temp):
                self.assertEqual(len(monitor.read_rows()), 1)

    def test_system_readers_still_used(self):
        with patch.object(monitor, "_read_cpu_times", return_value=(200, 50)), \
             patch.object(monitor, "_CPU_PREV", (100, 25)), \
             patch.object(monitor, "_read_mem", return_value=(3.7, 15.5)), \
             patch.object(monitor, "_read_cpu_temp", return_value=42), \
             patch.object(monitor, "_read_rapl_watts", return_value=None), \
             patch.object(monitor, "_read_gpu_power", return_value=7.7):
            self.assertEqual(monitor.system_stats(), {
                "cpu_percent": 75.0, "mem": (3.7, 15.5), "cpu_temp": 42,
                "cpu_energy_w": None, "gpu_power_w": 7.7})

    def test_one_console_frame_uses_separate_cohorts_and_preserves_system_line(self):
        output = io.StringIO()
        system = {"cpu_percent": 12, "mem": (3.7, 15.5), "cpu_temp": 42,
                  "cpu_energy_w": None, "gpu_power_w": 7.7}
        with patch.object(monitor, "read_rows", return_value=self.rows), \
             patch.object(monitor, "service_status", return_value="ActiveState=inactive"), \
             patch.object(monitor, "system_stats", return_value=system), \
             patch.object(monitor.shutil, "get_terminal_size", return_value=SimpleNamespace(columns=100)), \
             patch.object(monitor, "open", unittest.mock.mock_open(read_data=json.dumps(self.observation["progress"])), create=True), \
             patch.object(monitor.time, "sleep", side_effect=KeyboardInterrupt), \
             patch.object(monitor.sys, "stdout", output):
            with self.assertRaises(KeyboardInterrupt):
                monitor.main()
        text = output.getvalue()
        self.assertIn("matrix 54/54", text)
        self.assertIn("equal=6/6 mismatch=0", text)
        self.assertIn("Matrix criteria failures: 55/216 (25.5%)", text)
        self.assertIn("Stats by dimension (matrix only):", text)
        self.assertIn("Forecast: complete", text)
        self.assertIn("System: CPU 12% (42°C)   Mem 3.7/15.5 GiB", text)
        self.assertIn("CPU power n/a (no perm)   GPU power 7.7 W", text)
        self.assertNotIn("/56", text)


if __name__ == "__main__":
    unittest.main()
