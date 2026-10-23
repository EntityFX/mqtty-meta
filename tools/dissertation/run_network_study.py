"""Stage and run isolated network-study phases with verified resume."""
import argparse
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from network_study_inputs import StorageGuard, contained, lock, program_check, read, sha, stage, verify_snapshot
from network_study_process import execute, rss_bytes, tree_bytes
from network_study_verification import verify_loaded_run
import network_review as checks

if not __debug__:
    raise RuntimeError("Evidence verification requires Python without -O")

SCIENTIFIC_FILES = ("summary.json", "histograms.json", "publications.json", "delivery-receipts.json", "transmissions.json", "network-evidence.json")


def isolation(units):
    for unit in units:
        result = subprocess.run(["systemctl", "--user", "show", unit, "-p", "LoadState,ActiveState,Result"], capture_output=True, text=True)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode or values.get("LoadState") != "loaded" or values.get("ActiveState") != "inactive" or values.get("Result") != "success":
            raise RuntimeError("Dependency is running, failed or unavailable: " + unit)


def verify_output(output, config, root):
    s = verify_loaded_run(output)
    o, resolved = read(config), read(output / "run-config.json")
    if (output / "inputs/config.json").read_bytes() != config.read_bytes():
        raise ValueError("Saved input differs from the frozen job")
    for key, value in o.items():
        if key != "brokerProfilePath" and resolved.get(key) != value:
            raise ValueError("Executed option differs: " + key)
    if o.get("brokerProfilePath") is not None:
        if sha(output / "inputs/profile.json") != sha(root / "program/inputs/mosquitto-profile.json"):
            raise ValueError("Runtime profile differs from snapshot")
    publications = read(output / "publications.json")
    if len({p["id"] for p in publications}) != len(publications):
        raise ValueError("Duplicated publication ID")
    receivers = o.get("receiverSites")
    if receivers is None:
        receivers = list(range(o["brokers"]))
    if sorted(r["site"] for r in s["receivers"]) != sorted(receivers):
        raise ValueError("Result changed the expected receiver set")
    if sorted(p["publisher"] for p in s["sources"]) != list(range(o["publishers"])):
        raise ValueError("Result changed the publisher set")
    for source in s["sources"]:
        site = source["publisher"] % o["brokers"]
        if source["site"] != site or source["broker"] != (0 if o["mode"] == "single" else site):
            raise ValueError("Source placement or broker target changed")
    checks.check_graph(output / "topology.graphml", resolved)
    warnings = [key for key in ("backpressureUnsent", "sendRejected", "queueDrops", "hopLimitDrops", "forwardRejected", "duplicateDeliveries", "deliveryLoss") if s[key]]
    if s["sourceCompleted"] < s["apiAttempts"]:
        warnings.append("sourceNotCompleted")
    # Delivery failure in a loaded ideal model is data, not a process failure.
    if o["ideal"] and not warnings and not s["sendRejected"]:
        checks.ideal_counts(resolved, s)
    return s, warnings


def load_rows(root, manifest_sha, indexed, guard):
    latest, verified = {}, {}
    receipts = sorted((root / "runs").glob("*/attempt-*/result.json"))
    indexed_rows = list((root / "rows").glob("*.json"))
    for path in receipts + indexed_rows:
        row = read(path); job = indexed.get(row["id"])
        if not job or row["executionManifestSha256"] != manifest_sha or row["configSha256"] != job["sha256"]:
            raise ValueError("Row identity/configuration differs from study")
        receipt = contained(root, row["attempt"] + "/result.json")
        if read(receipt) != row:
            raise ValueError("Index differs from attempt receipt")
        key = (row["id"], row["attemptNumber"])
        if key in verified:
            continue
        if not row["technicalError"]:
            output = contained(root, row["attempt"] + "/output")
            summary, _ = verify_output(output, contained(root / "program", job["config"]), root)
            if summary["status"] != "completed" or summary != row["summary"] or row["returnCode"] != 0:
                raise ValueError("Completed row differs from original simulation")
            if {name: sha(output / name) for name in SCIENTIFIC_FILES if (output / name).exists()} != row["scientificSha256"]:
                raise ValueError("Scientific result changed")
        if row["id"] not in latest or row["attemptNumber"] > latest[row["id"]]["attemptNumber"]:
            latest[row["id"]] = row
        verified[key] = row
    # A killed parent may have no final receipt; do not silently rerun its child.
    for attempt in (root / "runs").glob("*/attempt-*"):
        if not (attempt / "result.json").exists():
            raise RuntimeError("Unfinished attempt requires inspection: " + str(attempt))
    # Recover only the index of a durable, already verified receipt after a parent
    # crash. The simulator is never rerun to repair an index.
    for path in receipts:
        row = read(path)
        index = root / "rows" / f"{row['id']}.attempt-{row['attemptNumber']:03d}.json"
        if not index.exists():
            (root / "rows").mkdir(exist_ok=True)
            guard.save(index, row)
    return latest


def check_phase_result(root, phase, plan, rows, manifest_sha):
    jobs = [j for j in plan["jobs"] if j["kind"] == phase]
    if any(j["id"] not in rows or rows[j["id"]]["technicalError"] for j in jobs):
        raise ValueError("Phase coverage is incomplete")
    expected = {"executionManifestSha256": manifest_sha, "phase": phase, "rows": [rows[j["id"]] for j in jobs]}
    if read(root / f"results-{phase}.json") != expected:
        raise ValueError("Phase result differs from verified attempts")


def pilot_decision(root, path, manifest_sha, plan, rows):
    if path is None:
        raise ValueError("A recorded pilot assessment is required before the matrix or diagnostic")
    expected = {j["id"] for j in plan["jobs"] if j["kind"] == "pilot"}
    if any(k not in rows or rows[k]["technicalError"] for k in expected) or not (root / "results-pilot.json").exists():
        raise ValueError("All pilot results must be technically verified first")
    check_phase_result(root, "pilot", plan, rows, manifest_sha)
    decision = read(path)
    if decision.get("decision") != "accept" or decision.get("executionManifestSha256") != manifest_sha or decision.get("pilotResultsSha256") != sha(root / "results-pilot.json"):
        raise ValueError("Decision is not bound to this pilot and program")
    if not decision.get("reviewer", "").strip() or not decision.get("reason", "").strip():
        raise ValueError("Pilot assessment needs reviewer and technical rationale")
    return decision


def attempt(root, job, manifest, manifest_sha, guard, on_state, retry_reason=None):
    guard.check()
    folder = root / "runs" / job["id"]; folder.mkdir(parents=True, exist_ok=True)
    number = max([int(p.name.split("-")[-1]) for p in folder.glob("attempt-*")] or [0]) + 1
    destination = folder / f"attempt-{number:03d}"; destination.mkdir(exist_ok=False)
    config = contained(root / "program", job["config"])
    command = [manifest["dotnet"], str(root / "runtime/EntityFX.MqttY.Federation.App.dll"), "run", "--config", str(config), "--output", "output"]
    guard.save(destination / "command.json", {"argv": command, "job": job["id"], "executionManifestSha256": manifest_sha, "retryReason": retry_reason})
    began = time.monotonic()
    available = manifest["diskBudgetBytes"] - tree_bytes(root)
    row = {"id": job["id"], "kind": job["kind"], "attemptNumber": number,
           "attempt": destination.relative_to(root).as_posix(), "configSha256": job["sha256"],
           "executionManifestSha256": manifest_sha, "observedAtUtc": datetime.now(timezone.utc).isoformat()}
    def live(state):
        guard.save(destination / "last-process-state.json", state)
        on_state(state)
    row.update(execute(command, destination, guard, manifest["jobTimeoutSeconds"], manifest["jobRssLimitBytes"], available, live))
    verification_started = time.monotonic()
    if row["technicalError"] is None:
        try:
            if row["returnCode"] != 0:
                raise RuntimeError("Federation CLI exit code " + str(row["returnCode"]))
            summary, warnings = verify_output(destination / "output", config, root)
            if summary["status"] != "completed":
                raise RuntimeError("Incomplete simulation: " + str(summary.get("error")))
            row["summary"], row["warnings"] = summary, warnings
            row["scientificSha256"] = {name: sha(destination / "output" / name) for name in SCIENTIFIC_FILES if (destination / "output" / name).exists()}
            if job["kind"] == "replay":
                prior = [row for row in map(read, (root / "rows").glob("*.json")) if row["id"] == job["replayOf"]]
                original = max(prior, key=lambda r: r["attemptNumber"])
                if original["technicalError"] or row["scientificSha256"] != original["scientificSha256"]:
                    raise ValueError("Scientific replay bytes differ")
        except Exception as error:
            row["technicalError"] = type(error).__name__ + ": " + str(error)
    row["verificationWallSeconds"] = time.monotonic() - verification_started
    row["supervisorPeakRssBytes"] = rss_bytes(os.getpid(), peak=True)
    row["retryReason"] = retry_reason
    row["totalWallSeconds"] = time.monotonic() - began
    row["attemptBytes"] = tree_bytes(destination)
    guard.save(destination / "result.json", row)
    guard.save(root / "rows" / f"{job['id']}.attempt-{number:03d}.json", row)
    return row


def publish(root, phase, rows, guard, status, manifest_sha, active=None, reason=None):
    guard.save(root / "progress.json", {"status": status, "phase": phase, "executionManifestSha256": manifest_sha,
               "verified": sum(not r["technicalError"] for r in rows.values()),
               "technicalErrors": sum(bool(r["technicalError"]) for r in rows.values()),
               "warnings": sum(bool(r.get("warnings")) for r in rows.values()), "active": active, "reason": reason,
               "observedAtUtc": datetime.now(timezone.utc).isoformat()})


def phase_results(root, phase, jobs, rows, guard, manifest_sha):
    selected = [rows[j["id"]] for j in jobs]
    fields = ("id", "measurementSeconds", "scheduledPublishes", "apiAttempts", "sendRejected", "sourceCompleted",
              "sourceCompletedRps", "sourceCompletedBeforeDrain", "sourceCompletedDuringDrain", "backpressureUnsent",
              "uniqueDeliveries", "expectedDeliveries", "deliveryLoss", "fullyDeliveredPublications", "uniqueGoodput",
              "uniqueBeforeDrain", "uniqueDuringDrain", "publishP50Ms", "publishP95Ms", "publishP99Ms",
              "receiverP50Ms", "receiverP95Ms", "receiverP99Ms", "forwardAttempts", "forwardRejected",
              "forwardTopicPayloadBytes", "duplicateDeliveries", "suppressedDuplicates", "queueDrops", "hopLimitDrops",
              "totalWallSeconds", "peakRssBytes", "supervisorPeakRssBytes", "attemptBytes", "warnings")
    stream = io.StringIO(newline=""); writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
    for row in selected:
        values = {k: row.get(k, row.get("summary", {}).get(k)) for k in fields}
        values["warnings"] = ";".join(row.get("warnings", [])); writer.writerow(values)
    guard.identity(); (root / f"summary-{phase}.csv").write_text(stream.getvalue(), encoding="utf-8", newline="")
    body = f"# Этап сетевой программы: {phase}\n\nПроверено {len(selected)}/{len(jobs)} опытов. Технических отказов 0; с предупреждениями {sum(bool(r.get('warnings')) for r in selected)}.\n\n"
    body += "Предупреждения о недоставке, очередях и backpressure сохранены как результаты модели. Они не доказывают дефект настоящего брокера. Следующий этап автоматически не запускается.\n\n"
    body += f"Данные: [JSON](results-{phase}.json), [CSV](summary-{phase}.csv).\n\n"
    for row in selected:
        body += f"- [{row['id']}]({row['attempt']}/output/report.md): предупреждения {', '.join(row.get('warnings', [])) or 'нет'}.\n"
    guard.identity(); (root / f"report-{phase}.md").write_text(body, encoding="utf-8", newline="\n")
    guard.save(root / f"results-{phase}.json", {"executionManifestSha256": manifest_sha, "phase": phase, "rows": selected})


def run(root, phase, resume=False, decision_path=None, retry_job=None, retry_reason=None):
    root = Path(root).resolve(); manifest = verify_snapshot(root); manifest_sha = sha(root / "execution-manifest.json")
    if sha(Path(__file__)) != manifest["files"]["tools/dissertation/run_network_study.py"]:
        raise ValueError("Execute the saved runner snapshot")
    guard = StorageGuard(manifest["mountRoot"], root, manifest["reserveBytes"])
    if guard.device != manifest["mountDevice"]:
        raise ValueError("Study moved to another filesystem; stage a new execution snapshot")
    plan = program_check(root / "program"); indexed = {j["id"]: j for j in plan["jobs"]}
    if retry_job and (not resume or not retry_reason or not retry_reason.strip()):
        raise ValueError("Explicit retry requires --resume and a nonempty --retry-reason")
    if retry_reason and not retry_job:
        raise ValueError("Retry reason requires a failed job ID")
    with lock(root / "run.lock"), lock(guard.mount / ".mqtty-network-study.lock"):
        isolation(manifest["afterUnits"])
        rows = load_rows(root, manifest_sha, indexed, guard)
        jobs = [j for j in plan["jobs"] if j["kind"] == phase]
        if not jobs:
            raise ValueError("No jobs for phase")
        if (root / f"results-{phase}.json").exists():
            if not resume:
                raise ValueError("Phase already exists; use explicit --resume to reverify")
            check_phase_result(root, phase, plan, rows, manifest_sha)
            publish(root, phase, rows, guard, "completed", manifest_sha)
            return {"status": "completed", "phase": phase, "verified": len(jobs), "reverifiedOnly": True}
        if any(j["id"] in rows for j in jobs) and not resume:
            raise ValueError("Use --resume for a partial phase")
        failures = [r for r in rows.values() if r["technicalError"]]
        if failures and (len(failures) != 1 or failures[0]["id"] != retry_job or indexed[retry_job]["kind"] != phase):
            raise ValueError("Prior technical failure requires an explicit inspected retry; old evidence retained")
        if retry_job and (retry_job not in rows or not rows[retry_job]["technicalError"]):
            raise ValueError("Retry must identify a failed job")
        if phase in ("matrix", "diagnostic"):
            decision = pilot_decision(root, decision_path, manifest_sha, plan, rows)
            digest = checks.evidence.sha(decision_path); folder = root / "decisions"; folder.mkdir(exist_ok=True)
            saved = folder / (digest + ".json"); guard.identity()
            if saved.exists():
                if sha(saved) != digest:
                    raise ValueError("Saved decision changed")
            else:
                with saved.open("xb") as stream:
                    stream.write(Path(decision_path).read_bytes())
        if phase == "replay" and any(j.get("replayOf") not in rows or rows[j["replayOf"]]["technicalError"] for j in jobs):
            raise ValueError("Replay originals are incomplete")
        (root / "rows").mkdir(exist_ok=True)
        try:
            publish(root, phase, rows, guard, "running", manifest_sha)
            for job in jobs:
                if job["id"] in rows and not rows[job["id"]]["technicalError"]:
                    continue
                isolation(manifest["afterUnits"]); guard.check()
                if sha(root / "execution-manifest.json") != manifest_sha:
                    raise ValueError("Execution manifest changed")
                verify_snapshot(root)
                row = attempt(root, job, manifest, manifest_sha, guard,
                              lambda state: publish(root, phase, rows, guard, "running", manifest_sha, {"id": job["id"], **state}),
                              retry_reason if job["id"] == retry_job else None)
                rows[job["id"]] = row
                if row["technicalError"]:
                    raise RuntimeError(row["technicalError"])
                verify_snapshot(root)
            phase_results(root, phase, jobs, rows, guard, manifest_sha)
            publish(root, phase, rows, guard, "completed", manifest_sha)
            return {"status": "completed", "phase": phase, "verified": len(jobs)}
        except BaseException as error:
            try:
                guard.save(root / f"partial-{phase}.json", {"status": "failed", "phase": phase,
                           "executionManifestSha256": manifest_sha, "reason": repr(error),
                           "expectedJobs": [j["id"] for j in jobs], "rows": [rows[j["id"]] for j in jobs if j["id"] in rows]})
                publish(root, phase, rows, guard, "failed", manifest_sha, reason=repr(error))
            except Exception:
                print("Cannot persist failure on unavailable evidence mount: " + repr(error), file=sys.stderr)
            raise


def processes_in_attempt(attempt):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Interrupted-process inspection requires Linux /proc")
    active = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if proc.stat().st_uid == os.getuid() and (proc / "cwd").resolve(strict=True) == attempt:
                active.append(int(proc.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return active


def recover_interruption(root, identifier, reason):
    if not reason.strip():
        raise ValueError("Record the inspected cause of interruption")
    root = Path(root).resolve(); manifest = verify_snapshot(root); manifest_sha = sha(root / "execution-manifest.json")
    guard = StorageGuard(manifest["mountRoot"], root, manifest["reserveBytes"])
    if guard.device != manifest["mountDevice"]:
        raise ValueError("Evidence filesystem changed")
    plan = program_check(root / "program"); indexed = {j["id"]: j for j in plan["jobs"]}
    if identifier not in indexed:
        raise ValueError("Unknown job")
    with lock(root / "run.lock"), lock(guard.mount / ".mqtty-network-study.lock"):
        attempts = sorted((root / "runs" / identifier).glob("attempt-*"))
        if not attempts or (attempts[-1] / "result.json").exists():
            raise ValueError("Job has no unfinished attempt")
        destination = attempts[-1]
        if processes_in_attempt(destination):
            raise RuntimeError("Owned child still uses this attempt; do not retry")
        job = indexed[identifier]
        command = read(destination / "command.json")
        if command["job"] != identifier or command["executionManifestSha256"] != manifest_sha:
            raise ValueError("Interrupted command belongs to a different snapshot")
        row = {"id": identifier, "kind": job["kind"], "attemptNumber": int(destination.name.split("-")[-1]),
               "attempt": destination.relative_to(root).as_posix(), "configSha256": job["sha256"],
               "executionManifestSha256": manifest_sha, "technicalError": "Interrupted attempt: " + reason,
               "returnCode": None, "exitCodeUnavailable": True, "recoveryIsExplicit": True,
               "observedAtUtc": datetime.now(timezone.utc).isoformat(),
               "availableFileSha256": {p.relative_to(destination).as_posix(): sha(p) for p in destination.rglob("*") if p.is_file()}}
        guard.save(destination / "result.json", row)
        (root / "rows").mkdir(exist_ok=True)
        guard.save(root / "rows" / f"{identifier}.{destination.name}.json", row)
        rows = load_rows(root, manifest_sha, indexed, guard)
        publish(root, job["kind"], rows, guard, "failed", manifest_sha, reason=row["technicalError"])
        return {"status": "interruptionRecorded", "id": identifier, "attempt": row["attempt"], "retryExecuted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("stage")
    for name in ("program", "runtime", "output", "mount-root", "source-root"):
        s.add_argument("--" + name, type=Path, required=True)
    s.add_argument("--after-unit", action="append", default=[])
    s.add_argument("--timeout-seconds", type=float, default=1800)
    s.add_argument("--rss-limit-gib", type=float, default=3)
    s.add_argument("--reserve-gib", type=float, default=10)
    s.add_argument("--disk-budget-gib", type=float, default=40)
    r = sub.add_parser("run"); r.add_argument("--study", type=Path, required=True)
    r.add_argument("--phase", choices=("pilot", "matrix", "replay", "diagnostic"), required=True)
    r.add_argument("--resume", action="store_true"); r.add_argument("--decision", type=Path)
    r.add_argument("--retry-job"); r.add_argument("--retry-reason")
    i = sub.add_parser("recover-interruption"); i.add_argument("--study", type=Path, required=True)
    i.add_argument("--job", required=True); i.add_argument("--reason", required=True)
    a = parser.parse_args()
    if a.command == "stage":
        result = stage(a.program, a.runtime, a.output, a.mount_root, a.source_root, timeout=a.timeout_seconds,
                       rss_limit=int(a.rss_limit_gib * 1024**3), reserve=int(a.reserve_gib * 1024**3),
                       disk_budget=int(a.disk_budget_gib * 1024**3), after_units=a.after_unit)
        print(json.dumps({"status": "staged", "files": len(result["files"]), "manifestSha256": sha(a.output / "execution-manifest.json")}))
    elif a.command == "run":
        print(json.dumps(run(a.study, a.phase, a.resume, a.decision, a.retry_job, a.retry_reason)))
    else:
        print(json.dumps(recover_interruption(a.study, a.job, a.reason)))


if __name__ == "__main__":
    main()
