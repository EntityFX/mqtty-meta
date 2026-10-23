import copy
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import sensitivity_scheduler_v2 as scheduler


class SchedulerTests(unittest.TestCase):
    def fixture(self, root):
        for name in ("runtime", "inputs", "configs", "rows", "runs"):
            (root / name).mkdir()
        runtime = root / "runtime/fake.py"
        runtime.write_text('''import json,sys
from pathlib import Path
config=json.loads(Path(sys.argv[sys.argv.index('--config')+1]).read_text())
if config.get('sleep'): import time; time.sleep(config['sleep'])
out=Path(sys.argv[sys.argv.index('--output')+1])/'run';out.mkdir(parents=True)
(out/'summary.json').write_text(json.dumps({'status':'completed','acceptanceCriteria':[{'status':'failed'}]}))
(out/'latency-histogram.json').write_text('{}')
(out/'counters.json').write_text(json.dumps({'brokers':{},'receivers':{},'receiverScope':'test'}))
sys.exit(config.get('exitCode',3))
''', encoding="utf-8")
        config = root / "configs/test.json"; scheduler.write(config, {})
        profile = root / "inputs/profile.json"; profile.write_text("{}")
        job = {"id": "test", "kind": "matrix", "variant": "baseline", "config": str(config),
               "configSha256": scheduler.sha(config), "expectedOriginalSummary": None}
        plan = {"runtime": str(runtime), "immutableFiles": {"runtime/fake.py": scheduler.sha(runtime),
                "inputs/profile.json": scheduler.sha(profile)}, "jobs": [job],
                "jobTimeoutSeconds": 10, "jobRssLimitBytes": scheduler.GIB}
        path = root / "plan.json"; scheduler.write(path, plan)
        return plan, job, path

    def test_free_slot_refilled_before_slow_sibling_finishes(self):
        slow_done = threading.Event(); replacement_started = threading.Event()
        def launch(job):
            if job == "slow":
                self.assertTrue(replacement_started.wait(2)); slow_done.set()
            if job == "replacement":
                self.assertFalse(slow_done.is_set()); replacement_started.set()
            return {"technicalError": None}
        result = scheduler.pooled(["slow", "fast", "replacement"], 2, launch, lambda *args: None)
        self.assertEqual(result, "completed")
        self.assertTrue(replacement_started.is_set())

    def test_pool_is_bounded_and_technical_failure_blocks_new_admission(self):
        launched = []
        def launch(job):
            launched.append(job)
            return {"technicalError": "bad" if job == 0 else None}
        result = scheduler.pooled(range(8), 1, launch, lambda *args: None)
        self.assertEqual(result, "failed"); self.assertEqual(launched, [0])
        self.assertEqual(scheduler.pooled([1], 4, launch, lambda *args: None,
                                         admission=lambda count: False), "resourcePaused")

    def test_explicit_stop_drains_admitted_models_and_launches_no_more(self):
        event = threading.Event(); launched = []
        def launch(job):
            launched.append(job); event.set(); return {"technicalError": None}
        self.assertEqual(scheduler.pooled(range(4), 1, launch, lambda *args: None,
                                         stop=event.is_set), "checkpointed")
        self.assertEqual(launched, [0])

    def test_plan_and_profile_and_config_mutations_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, job, path = self.fixture(root); digest = scheduler.sha(path)
            scheduler.verify_inputs(path, digest)
            for target in (Path(job["config"]), root / "inputs/profile.json", path):
                original = target.read_bytes(); target.write_bytes(original + b" ")
                with self.assertRaises(ValueError): scheduler.verify_inputs(path, digest)
                target.write_bytes(original)

    def test_real_child_fidelity_failure_reused_but_artifact_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, job, _ = self.fixture(root)
            row = scheduler.execute(job, root, plan, sys.executable, lambda *args: None,
                                    lambda: None, sample_seconds=.01)
            self.assertIsNone(row["technicalError"]); self.assertTrue(row["fidelityFailed"])
            self.assertEqual(row["returnCode"], 3)
            self.assertEqual(scheduler.load_rows(root, plan)["test"], row)
            histogram = Path(row["outputDirectory"]) / "latency-histogram.json"
            histogram.write_text('{"tampered":true}')
            with self.assertRaisesRegex(ValueError, "Scientific output differs"):
                scheduler.load_rows(root, plan)

    def test_failure_and_its_log_preserved_without_automatic_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, job, _ = self.fixture(root)
            job["expectedOriginalSummary"] = {"wrong": True}
            row = scheduler.execute(job, root, plan, sys.executable, lambda *args: None,
                                    lambda: None, sample_seconds=.01)
            self.assertIn("Baseline differs", row["technicalError"])
            self.assertEqual(len(list((root / "runs/test").glob("attempt-*.json"))), 1)
            self.assertTrue((root / "runs/test/attempt-001.log").exists())
            self.assertTrue(scheduler.load_rows(root, plan)["test"]["technicalError"])

    def test_resource_failure_reaps_child_and_writes_technical_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, job, _ = self.fixture(root)
            scheduler.write(Path(job["config"]), {"sleep": 5}); job["configSha256"] = scheduler.sha(job["config"])
            calls = []
            def guard():
                calls.append(1)
                if len(calls) > 1: raise RuntimeError("disk full")
            started = time.monotonic()
            row = scheduler.execute(job, root, plan, sys.executable, lambda *args: None,
                                    guard, sample_seconds=.01)
            self.assertLess(time.monotonic() - started, 3)
            self.assertIn("disk full", row["technicalError"])
            self.assertIsNotNone(row["returnCode"])
            self.assertTrue((root / "runs/test/attempt-001.json").exists())

    def test_checkpoint_requires_explicit_interrupted_ids_and_retains_attempts(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, job, path = self.fixture(root)
            folder = root / "runs/test"; folder.mkdir()
            (folder / "attempt-001.log").write_text("interrupted process")
            with patch.object(scheduler, "storage"), patch.object(scheduler, "locked", return_value=nullcontext()), patch.object(scheduler, "assert_no_children"):
                with self.assertRaisesRegex(ValueError, "Explicit abandoned"):
                    scheduler.checkpoint(path, root, scheduler.sha(path), "Reviewed interrupted controller")
                receipt = scheduler.checkpoint(path, root, scheduler.sha(path), "Reviewed interrupted controller", ["test"])
            self.assertEqual(receipt["abandonedAttempts"], ["test"])
            self.assertEqual(receipt["verifiedCompleted"], 0)
            original = scheduler.read(folder / "attempt-001.json")
            self.assertTrue(original["approvedResume"])
            result = scheduler.execute(job, root, plan, sys.executable, lambda *args: None, lambda: None, sample_seconds=.01)
            self.assertIsNone(result["technicalError"])
            self.assertEqual(scheduler.read(folder / "attempt-001.json"), original)
            self.assertTrue((folder / "attempt-002.json").exists())

    def test_replay_reuses_verified_source_and_detects_different_histogram(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, job, _ = self.fixture(root)
            scheduler.execute(job, root, plan, sys.executable, lambda *args: None, lambda: None, sample_seconds=.01)
            replay = dict(job, id="replay.test", kind="replay", replayOf="test")
            row = scheduler.execute(replay, root, plan, sys.executable, lambda *args: None, lambda: None, sample_seconds=.01)
            self.assertTrue(row["replayEqual"])
            prior_path = root / "rows/test.json"; prior = scheduler.read(prior_path)
            prior["histogramSha256"] = "bad"; scheduler.write(prior_path, prior)
            failed = scheduler.execute(replay, root, plan, sys.executable, lambda *args: None, lambda: None, sample_seconds=.01)
            self.assertIn("Scientific replay bytes differ", failed["technicalError"])

    def test_full_resume_reuses_receipts_and_phase_dependencies(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, job, path = self.fixture(root)
            pilot = dict(job, id="pilot.test", kind="pilot")
            replay = dict(job, id="replay.test", kind="replay", replayOf="test")
            plan["jobs"] = [pilot, job, replay]; scheduler.write(path, plan)
            digest = scheduler.sha(path)
            with patch.object(scheduler, "storage"), patch.object(scheduler, "locked", side_effect=lambda _: nullcontext()), patch.object(scheduler, "assert_no_children"), patch.object(scheduler, "memory_available", return_value=100 * scheduler.GIB):
                scheduler.checkpoint(path, root, digest, "Stopped old controller; no incomplete attempts")
                self.assertEqual(scheduler.run(path, root, digest, sys.executable, workers=4), "completed")
                before = {str(p): scheduler.sha(p) for p in root.glob("runs/*/attempt-*.json")}
                self.assertEqual(len(before), 3)
                self.assertEqual(scheduler.run(path, root, digest, sys.executable, workers=4), "completed")
                after = {str(p): scheduler.sha(p) for p in root.glob("runs/*/attempt-*.json")}
            self.assertEqual(before, after)
            progress = scheduler.read(root / "progress.json")
            self.assertEqual(progress["technicalErrors"], 0)
            self.assertEqual(progress["counts"]["replay"]["done"], 1)
            self.assertEqual(progress["counts"]["matrix"]["failedCriteria"], 1)

    def test_preexisting_technical_failure_refuses_checkpoint(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, job, path = self.fixture(root)
            scheduler.write(root / "rows/test.json", {"id": "test", "kind": "matrix",
                "configSha256": job["configSha256"], "technicalError": "priorTimeout"})
            with patch.object(scheduler, "storage"), patch.object(scheduler, "locked", return_value=nullcontext()), patch.object(scheduler, "assert_no_children"):
                with self.assertRaisesRegex(ValueError, "Prior technical error"):
                    scheduler.checkpoint(path, root, scheduler.sha(path), "Handoff")
            self.assertFalse((root / "scheduler-v2/checkpoint.json").exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux flock primitive")
    def test_linux_flock_excludes_second_fd_and_other_process(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = """import fcntl,sys
with open(sys.argv[1], 'a') as stream:
    try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: sys.exit(42)
"""
            with scheduler.locked(root):
                with (root / "run.lock").open("a") as second:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = subprocess.run([sys.executable, "-c", child, str(root / "run.lock")],
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 42, result.stderr.decode())
            result = subprocess.run([sys.executable, "-c", child, str(root / "run.lock")],
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux process groups")
    def test_linux_resource_abort_kills_owned_grandchild_group(self):
        import signal
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, job, _ = self.fixture(root)
            pid_file = root / "grandchild.pid"
            Path(plan["runtime"]).write_text("""import subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(60)
""".replace('Path(sys.argv[1])', 'Path(' + repr(str(pid_file)) + ')'), encoding="utf-8")
            def running(pid):
                try:
                    # A transient zombie has terminated and cannot retain a worker slot.
                    state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
                    return state not in ("Z", "X")
                except FileNotFoundError:
                    return False
            def guard():
                if pid_file.exists(): raise RuntimeError("injected disk guard")
            states = []
            try:
                row = scheduler.execute(job, root, plan, sys.executable,
                    lambda _, state: states.append(state), guard, sample_seconds=.02)
                self.assertIn("injected disk guard", row["technicalError"])
                self.assertTrue(pid_file.exists())
                grandchild = int(pid_file.read_text())
                deadline = time.monotonic() + 3
                while running(grandchild) and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertFalse(running(grandchild), "Owned grandchild survived process-group abort")
                for state in states:
                    self.assertFalse(running(state["pid"]), "Owned parent survived abort")
                self.assertTrue((root / "runs/test/attempt-001.json").exists())
            finally:
                if pid_file.exists():
                    grandchild = int(pid_file.read_text())
                    if running(grandchild):
                        os.kill(grandchild, signal.SIGKILL)


if __name__ == "__main__": unittest.main()
