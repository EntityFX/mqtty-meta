"""One bounded, separately pinned QoS1 diagnostic; never resumes the frozen study."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from network_study_inputs import StorageGuard, lock, read, sha
from network_study_process import execute

JOB = "pilot.star.federation.q1.r5000.w10.s42"
SOURCE = "12601f534f9da3673057b072f8bd6a0c67fd8aed"
FAILED_SHA = "4bdb82104b24d1aad85921ad36e1f50153bb817a6bfd6b5b731366335fc6ebd6"
SUMMARY_SHA = "0abb3711ec412cd91f2c24886a05bc274a45cd1f92686399ad57b9827908ab76"


def selected_job(manifest):
    job = manifest.get("jobId", JOB)
    if job not in (JOB, "pilot.star.federation.q2.r5000.w10.s42"):
        raise ValueError("Unsupported diagnostic job")
    return job


def units_snapshot():
    units = {}
    for name in ("mqtty-sensitivity-v1.service", "mqtty-network-pilot-v1.service",
                 "mqtty-network-pilot-tail-v1.service", "mqtty-network-writer-check-v1.service",
                 "mqtty-network-writer-check-v2.service"):
        result = subprocess.run(["systemctl", "--user", "show", name, "-p", "LoadState,ActiveState,SubState,MainPID,Result"],
                                capture_output=True, text=True, timeout=15)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        # Missing collected transient units are allowed only in conjunction with
        # terminal persisted progress and the process scan below, never alone.
        if result.returncode or values.get("MainPID", "0") != "0" or values.get("ActiveState") not in ("inactive", "failed"):
            raise RuntimeError("Old model unit is live or unobservable: " + name)
        units[name] = values
    return units


def validate_original(original, manifest):
    attempt = original / "runs" / JOB / "attempt-001"
    if sha(attempt / "result.json") != FAILED_SHA or sha(attempt / "output/summary.json") != SUMMARY_SHA:
        raise ValueError("Original failed evidence changed")
    frozen = read(original / "execution-manifest.json")
    if sha(original / "execution-manifest.json") != manifest["originalExecutionManifestSha256"]:
        raise ValueError("Original manifest changed")
    for relative, expected in frozen["files"].items():
        if relative.startswith(("tools/", "program/")) and sha(original / relative) != expected:
            raise ValueError("Original frozen input/verifier changed: " + relative)
    plan = read(original / "program/plan.json")
    job = next(j for j in plan["jobs"] if j["id"] == selected_job(manifest))
    config = original / "program" / job["config"]
    if sha(config) != manifest.get("originalConfigSha256", manifest["configSha256"]) or sha(config) != job["sha256"]:
        raise ValueError("Original configuration differs from the diagnostic")
    return config


def validate_diagnostic_config(original_config, config, manifest):
    """Allow only an explicitly pinned drain extension in a separate study."""
    if sha(config) != manifest["configSha256"]:
        raise ValueError("Diagnostic configuration hash changed")
    extension = manifest.get("drainExtensionSeconds")
    if extension is None:
        if config.read_bytes() != original_config.read_bytes():
            raise ValueError("Configuration bytes changed")
        return
    baseline = read(original_config)
    candidate = read(config)
    if isinstance(extension, bool) or not isinstance(extension, (int, float)) or not baseline["drainTimeoutSeconds"] < extension <= 30:
        raise ValueError("Invalid bounded drain extension")
    expected = dict(baseline, drainTimeoutSeconds=extension)
    if candidate != expected:
        raise ValueError("Only the explicitly declared drain extension is allowed")


def admission(root, original, manifest):
    units = units_snapshot()
    progress_roots = [original, original.parent / "dissertation-sensitivity-v1",
                      original.parent / "network-writer-check-v1", original.parent / "network-writer-check-v2"]
    terminal = {}
    for path in progress_roots:
        progress = path / "progress.json"
        if progress.exists():
            data = read(progress)
            if data.get("status") in ("running", "pending") or data.get("active"):
                raise RuntimeError("Old persisted progress is not terminal: " + str(path))
            terminal[str(path)] = {"sha256": sha(progress), "status": data.get("status")}
    active = []
    for proc in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = proc.read_bytes().split(b"\0")
            if not args or int(proc.parent.name) == os.getpid():
                continue
            text = b" ".join(args).decode(errors="replace")
            if any(marker in text for marker in ("EntityFX.MqttY.Federation.App.dll", "run_network_study.py", "continue_network_pilot_tail.py")):
                active.append({"pid": int(proc.parent.name), "command": text})
        except FileNotFoundError:
            pass
    if active:
        raise RuntimeError("Another model runner exists: " + json.dumps(active))
    return {"units": units, "terminalProgress": terminal, "matchingModelProcesses": active,
            "missingUnitPolicy": "MainPID zero plus terminal persisted progress plus process scan plus shared lock",
            "matrixAdmitted": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(); manifest = read(root / "staging-manifest.json")
    job_id = selected_job(manifest)
    guard = StorageGuard(manifest["mount"], root, 10 * 1024**3)
    original = Path(manifest["originalRoot"]).resolve()
    if manifest["sourceCommit"] != SOURCE or not original.is_relative_to(guard.mount):
        raise ValueError("Unexpected source or original boundary")
    expected = {name for name in manifest["files"] if name.startswith(("runtime/", "tools/", "inputs/"))}
    actual = {p.relative_to(root).as_posix() for folder in ("runtime", "tools", "inputs")
              for p in (root / folder).rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("Unexpected or missing staged runtime/input/helper file")
    for relative, expected in manifest["files"].items():
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root) or sha(path) != expected:
            raise ValueError("Staged input changed: " + relative)
    if sha(Path(manifest["dotnet"])) != manifest["dotnetSha256"]:
        raise ValueError("Dotnet host changed")
    with lock(root / "run.lock"), lock(guard.mount / ".mqtty-network-study.lock"):
        original_config = validate_original(original, manifest)
        admitted = admission(root, original, manifest)
        guard.save(root / "admission.json", admitted)
        attempt = root / "attempt-001"; attempt.mkdir(exist_ok=False)
        config = root / "inputs/config.json"
        validate_diagnostic_config(original_config, config, manifest)
        command = [manifest["dotnet"], str(root / "runtime/EntityFX.MqttY.Federation.App.dll"),
                   "run", "--config", str(config), "--output", "output", "--diagnostics"]
        guard.save(attempt / "command.json", {"argv": command, "jobId": job_id,
                   "stagingManifestSha256": sha(root / "staging-manifest.json"), "sourceCommit": SOURCE})
        def live(state):
            guard.save(attempt / "last-process-state.json", state)
            guard.save(root / "progress.json", {"status": "running", "jobId": job_id, "active": state,
                       "observedAtUtc": datetime.now(timezone.utc).isoformat(), "matrixAdmitted": False})
        result = execute(command, attempt, guard, 1800, 2 * 1024**3, 4 * 1024**3, live)
        result.update({"jobId": job_id, "sourceCommit": SOURCE, "configSha256": sha(config),
                       "matrixAdmitted": False, "originalFailureRetained": True,
                       "drainExtensionSeconds": manifest.get("drainExtensionSeconds")})
        try:
            output = attempt / "output"
            if (output / "summary.json").exists():
                # Reuse the original independently sealed raw recount for partial
                # output as well; a model drain failure is not silently promoted.
                module_path = original / "tools/dissertation/run_network_study.py"
                sys.path.insert(0, str(module_path.parent))
                spec = importlib.util.spec_from_file_location("qos1_frozen_verifier", module_path)
                verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
                summary, warnings = verifier.verify_output(output, config, original)
                diagnostics = read(output / "drain-diagnostics.json")
                if not diagnostics["snapshots"]:
                    raise ValueError("Drain diagnostics are empty")
                result.update({"outputVerified": True, "warnings": warnings,
                               "summary": summary, "diagnosticsSha256": sha(output / "drain-diagnostics.json"),
                               "outputSealSha256": sha(output / "sha256.json"), "verifierSha256": sha(module_path)})
        except Exception as error:
            result["verificationError"] = repr(error)
        finally:
            validate_original(original, manifest)
            guard.save(attempt / "result.json", result)
            guard.save(root / "progress.json", {"status": "diagnosticFinished", "active": None,
                       "result": result, "matrixAdmitted": False, "observedAtUtc": datetime.now(timezone.utc).isoformat()})
        return 0 if result["technicalError"] is None and result.get("outputVerified") and not result.get("verificationError") else 1


if __name__ == "__main__":
    raise SystemExit(main())
