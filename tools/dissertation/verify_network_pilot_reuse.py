"""Read-only composite pilot audit; does not admit a matrix or rewrite source runs."""
from datetime import datetime, timezone
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def main():
    base = Path("/run/media/entityfx/hd250/mqtty-evidence")
    original = base / "network-long-v1"
    frozen_manifest = read(original / "execution-manifest.json")
    # Frozen verifier source, supporting modules and original program are all
    # pinned before loading or applying the verifier to any selected run.
    checked_sources = {}
    for relative, expected in frozen_manifest["files"].items():
        if relative.startswith(("tools/", "program/")):
            actual = sha(original / relative)
            if actual != expected:
                raise ValueError("Frozen source changed: " + relative)
            checked_sources[str(original / relative)] = actual
    module = original / "tools/dissertation/run_network_study.py"
    sys.path.insert(0, str(module.parent))
    spec = importlib.util.spec_from_file_location("pilot_reuse_frozen_verifier", module)
    verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
    plan = read(original / "program/plan.json")
    pilots = [j for j in plan["jobs"] if j["kind"] == "pilot"]
    if len(pilots) != 30 or len({j["id"] for j in pilots}) != 30:
        raise ValueError("Expected exactly 30 unique original pilot points")
    special = {
        "pilot.star.federation.q0.r5000.w10.s42": (
            base / "network-writer-check-v2",
            "runs/pilot.writer.star.federation.q0.r5000.w10.s42/attempt-001"),
        "pilot.star.federation.q1.r5000.w10.s42": (base / "qos1-linux-diagnostics-v2", "attempt-001"),
        "pilot.star.federation.q2.r5000.w10.s42": (base / "network-qos2-final-pilot-v1", "attempt-001"),
    }
    points, preserved = [], []
    for job in pilots:
        identity = job["id"]; old_config = original / "program" / job["config"]
        if sha(old_config) != job["sha256"]:
            raise ValueError("Original job configuration changed")
        if identity in special:
            study, attempt_relative = special[identity]
            attempt = study / attempt_relative
            config = study / "inputs/config.json"
            if not config.exists():
                manifest = read(study / "execution-manifest.json")
                single = read(study / "program/plan.json")["jobs"]
                if len(single) != 1:
                    raise ValueError("Ambiguous diagnostic configuration")
                config = study / "program" / single[0]["config"]
        else:
            study = original
            matches = [p for p in (original / "runs" / identity).glob("attempt-*/result.json")
                       if not read(p).get("technicalError")]
            if len(matches) != 1:
                raise ValueError("Successful point missing or ambiguous: " + identity)
            attempt = matches[0].parent; config = old_config
        output = attempt / "output"; receipt_path = attempt / "result.json"
        receipt = read(receipt_path)
        if receipt.get("returnCode") != 0:
            raise ValueError("Model CLI did not succeed: " + identity)
        config_data = read(config); old_data = read(old_config)
        differences = [k for k in set(old_data) | set(config_data) if old_data.get(k) != config_data.get(k)]
        if set(differences) - {"drainTimeoutSeconds"}:
            raise ValueError("Diagnostic changed scientific inputs beyond drain: " + identity)
        summary, warnings = verifier.verify_output(output, config, original)
        if summary["status"] != "completed":
            raise ValueError("Output is not completed: " + identity)
        limit = config_data["drainTimeoutSeconds"]
        if not (0 < limit <= 30):
            raise ValueError("Unsupported existing drain limit")
        phase_seconds = {}
        tick_seconds = summary["tickMilliseconds"] / 1000
        for name in ("warmupDrain", "drain"):
            phases = [p for p in summary["phases"] if p["name"] == name]
            if len(phases) != 1 or phases[0]["status"] != "completed":
                raise ValueError("Drain phase missing or incomplete: " + identity)
            duration = (phases[0]["endTick"] - phases[0]["startTick"]) * tick_seconds
            if duration > limit + 1e-9:
                raise ValueError("Observed phase exceeds original limit")
            phase_seconds[name] = duration
        reuse = dict(config_data); reuse["drainTimeoutSeconds"] = 30
        if {k for k in set(reuse) | set(old_data) if reuse.get(k) != old_data.get(k)} - {"drainTimeoutSeconds"}:
            raise ValueError("Reusable configuration changed another field")
        output_manifest = read(output / "manifest.json")
        deployment = next((p for p in (study / "staging-manifest.json", study / "execution-manifest.json") if p.exists()), None)
        deployment_data = read(deployment) if deployment else {}
        file_hashes = read(output / "sha256.json")
        # The saved command and original supervisor result remain intact even
        # when completed raw output is independently verified after a guard race.
        if receipt.get("technicalError"):
            if identity != "pilot.star.federation.q0.r5000.w10.s42" or receipt["technicalError"] != "RuntimeError: processRssUnavailable":
                raise ValueError("Unreviewed supervisor failure: " + identity)
            preserved.append({"id": identity, "receiptAbsolute": str(receipt_path), "receiptSha256": sha(receipt_path),
                              "technicalError": receipt["technicalError"], "modelReturnCode": receipt["returnCode"]})
        points.append({
            "id": identity, "originalOutputAbsolute": str(output), "originalConfigAbsolute": str(config),
            "originalConfigSha256": sha(config), "referenceOriginalPilotConfigAbsolute": str(old_config),
            "referenceOriginalPilotConfigSha256": sha(old_config),
            "reusableConfigRelative": "configs/" + identity + ".json",
            "reusableConfigSha256Drain30": hashlib.sha256(canonical(reuse)).hexdigest(),
            "reusableConfiguration": reuse, "configurationDifferenceFields": ["drainTimeoutSeconds"],
            "originalSummarySha256": sha(output / "summary.json"), "outputSealSha256": sha(output / "sha256.json"),
            "originalManifestSha256": sha(output / "manifest.json"),
            "deploymentManifestAbsolute": str(deployment), "deploymentManifestSha256": sha(deployment),
            "sourceCommit": deployment_data.get("sourceCommit", output_manifest["runtime"].get("mqttYCommit")),
            "deploymentSourceRevisions": deployment_data.get("sourceRevisions"),
            "runtime": output_manifest["runtime"], "originalModelDrainLimit": limit,
            "observedWarmupDrainSeconds": phase_seconds["warmupDrain"], "observedDrainSeconds": phase_seconds["drain"],
            "warnings": warnings, "rawRecountPassed": True, "runStatus": summary["status"],
            "sourceReceiptAbsolute": str(receipt_path), "sourceReceiptSha256": sha(receipt_path),
            "savedCommandAbsolute": str(attempt / "command.json"), "savedCommandSha256": sha(attempt / "command.json"),
            "sealedFileHashes": file_hashes,
        })
        del summary; gc.collect()
    if len(points) != 30 or len({p["id"] for p in points}) != 30:
        raise ValueError("Coverage changed")
    print(json.dumps({"schemaVersion": 1, "status": "verifiedCompositeTechnicalBasis", "selectedCount": 30,
          "observedAtUtc": datetime.now(timezone.utc).isoformat(), "scientificApproval": False,
          "homogeneousNewRuntimeSeries": False, "matrixAutomaticallyAdmitted": False,
          "verificationScope": "Completed sealed raw output, original frozen verifier, identical scientific inputs except bounded maximum drain30",
          "points": points, "discrepancies": [], "preservedSupervisorFailures": preserved,
          "checkedFrozenSourceHashes": checked_sources}, ensure_ascii=False))


if __name__ == "__main__":
    main()
