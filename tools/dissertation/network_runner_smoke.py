"""Bounded real-CLI runner check; Linux uses the actual dedicated mount."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_network_study as runner
from network_study_inputs import read, sha, stage, write
from prepare_network_study import configuration

ROOT = Path(__file__).resolve().parents[2]


def smoke(work, mount=None):
    work = Path(work).resolve()
    linux = sys.platform.startswith("linux")
    if work.exists():
        raise ValueError("Smoke output already exists")
    if linux:
        if mount is None:
            raise ValueError("Linux smoke requires an actual dedicated --mount-root")
        mount = Path(mount).resolve()
        if not mount.is_mount() or mount == Path(mount.anchor) or not work.is_relative_to(mount) or work == mount:
            raise ValueError("Smoke output must be below a dedicated mounted evidence disk")
    elif os.name != "nt" or not work.is_relative_to(ROOT / "MqttBenchmark/artifacts"):
        raise ValueError("Use a new ignored artifacts directory on Windows for this local test")
    program = work / "program-source"; (program / "inputs").mkdir(parents=True)
    (program / "configs").mkdir()
    shutil.copyfile(ROOT / "reports/data/Mosquitto/profile/broker-calibration.v3.json", program / "inputs/mosquitto-profile.json")
    jobs = []
    for phase in ("pilot", "matrix", "replay"):
        for mode in ("single", "relay", "federation"):
            for qos in ((0, 1, 2) if phase == "pilot" else (2,)):
                identity = f"{phase}.{mode}.q{qos}"; relative = "configs/" + identity + ".json"
                o = configuration(mode, "ring", qos, 1000, .006, 42 if phase == "pilot" else 43)
                o.update(brokers=3, publishers=6, receiverSites=[0, 1, 2], warmupSeconds=.001)
                write(program / relative, o)
                job = {"id": identity, "kind": phase, "config": relative, "sha256": sha(program / relative)}
                if phase == "replay": job["replayOf"] = f"matrix.{mode}.q{qos}"
                jobs.append(job)
    write(program / "plan.json", {"schemaVersion": 1, "purpose": "Local runner smoke; not article observations",
          "pilotJobs": 9, "matrixJobs": 3, "replayJobs": 3, "optionalDiagnosticJobs": 0, "jobs": jobs,
          "profileSha256": sha(program / "inputs/mosquitto-profile.json")})
    runtime = ROOT / "mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/bin/Release/net6.0"
    study = work / "study"
    original_execute = runner.execute
    def fast(*args, **kwargs):
        return original_execute(*args, **kwargs, sample_seconds=.05)
    # Windows development only simulates the mount predicate. Linux never does.
    with ExitStack() as stack:
        if not linux:
            stack.enter_context(patch("network_study_inputs.Path.is_mount", return_value=True))
        stack.enter_context(patch("run_network_study.execute", side_effect=fast))
        manifest = stage(program, runtime, study, mount if linux else work, ROOT, timeout=30)
        pilot = runner.run(study, "pilot")
        runner.run(study, "pilot", resume=True)
        decision = work / "smoke-decision.json"
        write(decision, {"decision": "accept", "executionManifestSha256": sha(study / "execution-manifest.json"),
              "pilotResultsSha256": sha(study / "results-pilot.json"), "reviewer": "automated local smoke",
              "reason": "Exercise phase gate only; no acceptance of long experiment duration or scientific findings"})
        matrix = runner.run(study, "matrix", decision_path=decision)
        replay = runner.run(study, "replay")
        runner.run(study, "replay", resume=True)
    rows = [read(p) for p in (study / "rows").glob("*.json")]
    assert len(rows) == 15 and not any(r["technicalError"] for r in rows)
    assert all(r["summary"]["uniqueDeliveries"] == r["summary"]["expectedDeliveries"] == 18 for r in rows)
    receipt = {"status": "passed", "observedAtUtc": datetime.now(timezone.utc).isoformat(),
               "scope": "Bounded runner test with actual MqttY CLI; not dissertation study coverage",
               "mountPredicateSimulated": not linux, "sourceCleanCheckReal": True, "linuxExecutionTested": linux,
               "hardCgroupLimitTested": False, "linuxPolled": False, "simulationsExecuted": 15,
               "pilot": pilot, "matrix": matrix, "replay": replay, "exactReplays": 3,
               "resumeDidNotRerunCompletedJobs": len(list((study / "runs").glob("*/attempt-*"))) == 15,
               "sourceRevisions": manifest["sourceRevisions"], "executionManifestSha256": sha(study / "execution-manifest.json"),
               "sourceSha256": {name: value for name, value in manifest["files"].items() if name.startswith("tools/")},
               "runtimeSha256": {name: value for name, value in manifest["files"].items() if name.startswith("runtime/")},
               "scientificResults": [{"id": r["id"], "sha256": r["scientificSha256"], "warnings": r["warnings"]} for r in rows]}
    write(work / "smoke-verification.json", receipt)
    print(json.dumps({k: v for k, v in receipt.items() if k not in ("sourceSha256", "runtimeSha256", "scientificResults")}, ensure_ascii=False))
    return receipt


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--work-dir", type=Path, required=True)
    p.add_argument("--mount-root", type=Path)
    args = p.parse_args()
    smoke(args.work_dir, args.mount_root)
