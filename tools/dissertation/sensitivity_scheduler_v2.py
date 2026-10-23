"""Operational scheduler v2; immutable v1 scientific plan and results stay in place.

Linux only for execution/locking. Checkpoint is an explicit, stopped-controller
handoff, never a live takeover. No automatic technical retries or cleanup.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time

GIB = 1024 ** 3


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def locked(root):
    import fcntl
    with (root / "run.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def storage(mount, root, minimum=5 * GIB):
    mount, root = Path(mount).resolve(), Path(root).resolve()
    if not mount.is_mount() or mount == Path(mount.anchor) or not root.is_relative_to(mount):
        raise ValueError("Evidence mount absent or output outside evidence disk")
    if shutil.disk_usage(mount).free < minimum:
        raise RuntimeError("Insufficient evidence-disk space; sources retained")


def memory_available():
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(fields["MemAvailable"].split()[0]) * 1024


def rss(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except FileNotFoundError:
        return 0
    return 0


def assert_no_children(root):
    # Lock excludes the controller; this additionally catches surviving dotnet children.
    needle = str(root).encode()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            command = (entry / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError):
            continue
        if any(needle in part for part in command) and any(b"dotnet" in part for part in command[:1]):
            raise RuntimeError("Prior model process remains active: " + entry.name)


def verify_inputs(plan_path, expected):
    plan_path = Path(plan_path).resolve(); root = plan_path.parent
    if sha(plan_path) != expected:
        raise ValueError("Frozen plan SHA differs")
    plan = read(plan_path)
    ids = [job["id"] for job in plan["jobs"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate job ID")
    for relative, digest in plan["immutableFiles"].items():
        file = (root / relative).resolve()
        if not file.is_relative_to(root) or sha(file) != digest:
            raise ValueError("Immutable input differs: " + relative)
    if not Path(plan["runtime"]).resolve().is_relative_to(root / "runtime"):
        raise ValueError("Runtime outside snapshot")
    for job in plan["jobs"]:
        if Path(job["id"]).name != job["id"] or "/" in job["id"] or "\\" in job["id"]:
            raise ValueError("Unsafe job ID")
        config = Path(job["config"]).resolve()
        if not config.is_relative_to(root / "configs") or sha(config) != job["configSha256"]:
            raise ValueError("Config differs: " + job["id"])
    return plan


def science(output):
    summary = read(output / "summary.json")
    counters = read(output / "counters.json")
    subset = {key: counters[key] for key in ("brokers", "receivers", "receiverScope")}
    return {"summary": summary, "summarySha256": sha(output / "summary.json"),
            "histogramSha256": sha(output / "latency-histogram.json"),
            "scientificCountersSha256": hashlib.sha256(json.dumps(subset, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            "fidelityFailed": any(item["status"] == "failed" for item in summary["acceptanceCriteria"])}


def verified_row(root, job, row):
    if row["id"] != job["id"] or row["configSha256"] != job["configSha256"] or row["kind"] != job["kind"]:
        raise ValueError("Row identity differs: " + job["id"])
    if row.get("technicalError"):
        return row  # Technical failures retained; scheduler refuses automatic retry.
    if row["returnCode"] not in (0, 3):
        raise ValueError("Invalid successful return code")
    output = Path(row["outputDirectory"]).resolve()
    if not output.is_relative_to(root / "runs" / job["id"]):
        raise ValueError("Output outside job")
    actual = science(output)
    for field, value in actual.items():
        if row.get(field) != value:
            raise ValueError("Scientific output differs: " + field)
    if actual["summary"]["status"] != "completed":
        raise ValueError("Incomplete scientific summary")
    if job.get("expectedOriginalSummary") is not None and actual["summary"] != job["expectedOriginalSummary"]:
        raise ValueError("Baseline differs from frozen original")
    receipts = [p for p in (root / "runs" / job["id"]).glob("attempt-*.json") if read(p) == row]
    if len(receipts) != 1:
        raise ValueError("Successful row has no unique matching attempt receipt")
    return row


def load_rows(root, plan):
    rows = {}
    for job in plan["jobs"]:
        path = root / "rows" / (job["id"] + ".json")
        if path.exists():
            rows[job["id"]] = verified_row(root, job, read(path))
    for job in plan["jobs"]:
        if job["id"] in rows and job["kind"] == "replay" and not rows[job["id"]].get("technicalError"):
            prior = rows.get(job["replayOf"])
            if prior is None or any(prior.get(k) != rows[job["id"]].get(k) for k in
                    ("summarySha256", "histogramSha256", "scientificCountersSha256")):
                raise ValueError("Replay does not match verified source")
    return rows


def checkpoint(plan_path, mount, expected, reason, abandon=()):
    root = Path(plan_path).resolve().parent
    storage(mount, root)
    with locked(root):
        assert_no_children(root)
        plan = verify_inputs(plan_path, expected); rows = load_rows(root, plan)
        if any(row.get("technicalError") for row in rows.values()):
            raise ValueError("Prior technical error requires investigation; migration refused")
        incomplete = []
        for job in plan["jobs"]:
            folder = root / "runs" / job["id"]
            incomplete.extend((job["id"], log) for log in folder.glob("attempt-*.log")
                              if not log.with_suffix(".json").exists())
        if set(abandon) != {identifier for identifier, _ in incomplete}:
            raise ValueError("Explicit abandoned job IDs must match incomplete attempts exactly")
        if not reason.strip():
            raise ValueError("Handoff reason required")
        folder = root / "scheduler-v2"; folder.mkdir(exist_ok=True)
        if (folder / "checkpoint.json").exists():
            raise ValueError("Checkpoint already exists; do not overwrite history")
        for identifier, log in incomplete:
            write(log.with_suffix(".json"), {"id": identifier, "technicalError": "operationalHandoffInterrupted",
                "operationalReason": reason, "logSha256": sha(log), "approvedResume": True})
        hashes = {str(path.relative_to(root)): sha(path) for path in sorted((root / "runs").glob("*/attempt-*")) if path.is_file()}
        hashes.update({str(path.relative_to(root)): sha(path) for path in sorted((root / "rows").glob("*.json"))})
        result = {"planSha256": expected, "schedulerSha256": sha(__file__), "reason": reason,
                  "verifiedCompleted": len(rows), "abandonedAttempts": sorted(abandon), "evidenceHashes": hashes}
        write(folder / "checkpoint.json", result)
        return result


def execute(job, root, plan, dotnet, callback, guard, sample_seconds=1):
    folder = root / "runs" / job["id"]; folder.mkdir(parents=True, exist_ok=True)
    attempts = [int(p.stem.split("-")[1]) for p in folder.glob("attempt-*.*") if p.suffix in (".log", ".json")]
    number = max(attempts, default=0) + 1
    receipt = folder / f"attempt-{number:03d}.json"
    output = folder / f"output-{number:03d}"
    row = {"id": job["id"], "kind": job["kind"], "variant": job["variant"],
           "configSha256": job["configSha256"], "technicalError": None, "returnCode": None}
    start = time.monotonic(); peak = 0; process = None
    try:
        guard()
        if sha(job["config"]) != job["configSha256"]:
            raise ValueError("Config changed before launch")
        with (folder / f"attempt-{number:03d}.log").open("x", encoding="utf-8") as log:
            process = subprocess.Popen([dotnet, plan["runtime"], "experiment", "--config", job["config"],
                "--output", str(output)], stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
            while process.poll() is None:
                guard(); used = rss(process.pid); peak = max(peak, used)
                elapsed = time.monotonic() - start
                callback(job["id"], {"pid": process.pid, "elapsedSeconds": elapsed, "rssBytes": used, "peakRssBytes": peak})
                if elapsed > plan["jobTimeoutSeconds"]:
                    raise TimeoutError("jobTimeout")
                if peak > plan["jobRssLimitBytes"]:
                    raise RuntimeError("jobRssLimit")
                time.sleep(sample_seconds)
            row["returnCode"] = process.wait()
        guard()
        outputs = list(output.glob("*/summary.json"))
        if row["returnCode"] not in (0, 3) or len(outputs) != 1:
            raise RuntimeError("CLI failed or unique summary unavailable")
        row.update(science(outputs[0].parent)); row["outputDirectory"] = str(outputs[0].parent)
        if row["summary"]["status"] != "completed":
            raise RuntimeError("Simulation not completed")
        if job.get("expectedOriginalSummary") is not None and row["summary"] != job["expectedOriginalSummary"]:
            raise RuntimeError("Baseline differs from frozen campaign")
        if job["kind"] == "replay":
            prior = read(root / "rows" / (job["replayOf"] + ".json"))
            row["replayEqual"] = all(row[k] == prior[k] for k in ("summarySha256", "histogramSha256", "scientificCountersSha256"))
            if not row["replayEqual"]:
                raise RuntimeError("Scientific replay bytes differ")
    except BaseException as error:
        row["technicalError"] = type(error).__name__ + ": " + str(error)
    finally:
        if process is not None and process.poll() is None:
            if os.name == "nt":
                process.kill()
            else:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            process.wait()
        if process is not None:
            row["returnCode"] = process.returncode
        row.update(wallSeconds=time.monotonic() - start, peakRssBytes=peak)
        write(receipt, row); write(root / "rows" / (job["id"] + ".json"), row)
    return row


def pooled(jobs, workers, launch, accept, admission=lambda active: True, stop=lambda: False):
    """Fill the free slot immediately; stop admitting on the first technical error."""
    pending = iter(jobs); futures = {}; exhausted = False; failed = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while futures or not exhausted:
            while not exhausted and not failed and not stop() and len(futures) < workers and admission(len(futures)):
                job = next(pending, None)
                if job is None: exhausted = True; break
                futures[pool.submit(launch, job)] = job
            if not futures:
                return "failed" if failed else "checkpointed" if stop() else "resourcePaused" if not exhausted else "completed"
            completed, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
            for future in completed:
                job = futures.pop(future)
                row = future.result(); accept(job, row)
                failed = failed or bool(row.get("technicalError"))
            if failed or stop():
                exhausted = True  # Drain already admitted processes, never rerun them automatically.
    return "failed" if failed else "checkpointed" if stop() else "completed"


def run(plan_path, mount, expected, dotnet="dotnet", workers=4, memory_budget=8 * GIB,
        reserve=2 * GIB, reservation=2 * GIB, min_free=5 * GIB):
    if not 1 <= workers <= 8 or min(memory_budget, reserve, reservation, min_free) <= 0:
        raise ValueError("Invalid scheduler limits")
    root = Path(plan_path).resolve().parent
    storage(mount, root, min_free)
    with locked(root):
        assert_no_children(root)
        plan = verify_inputs(plan_path, expected)
        receipt = read(root / "scheduler-v2/checkpoint.json")
        if receipt["planSha256"] != expected or receipt["schedulerSha256"] != sha(__file__):
            raise ValueError("Explicit checkpoint/scheduler identity differs")
        for relative, digest in receipt["evidenceHashes"].items():
            if sha(root / relative) != digest:
                raise ValueError("Checkpoint evidence changed: " + relative)
        rows = load_rows(root, plan)
        if any(row.get("technicalError") for row in rows.values()):
            raise ValueError("Prior technical failure; investigate before a new explicit migration")
        active = {}; mutex = threading.RLock(); began = time.monotonic()
        prior_elapsed = read(root / "progress.json").get("elapsedSeconds", 0) if (root / "progress.json").exists() else 0
        stop_file = root / "scheduler-v2/stop-after-active"
        def publish(identifier=None, values=None, status="running", reason=None):
            with mutex:
                if identifier:
                    if values is None: active.pop(identifier, None)
                    else: active[identifier] = values
                write(root / "progress.json", {"status": status, "reason": reason, "schedulerVersion": 2,
                    "planSha256": expected, "workers": workers, "elapsedSeconds": prior_elapsed + time.monotonic() - began,
                    "technicalErrors": sum(bool(row.get("technicalError")) for row in rows.values()), "active": dict(active),
                    "counts": {kind: {"done": sum(r["kind"] == kind and not r.get("technicalError") for r in rows.values()),
                        "failedCriteria": sum(r["kind"] == kind and r.get("fidelityFailed", False) for r in rows.values()),
                        "total": sum(j["kind"] == kind for j in plan["jobs"])} for kind in ("pilot", "matrix", "replay")}})
        def guard():
            storage(mount, root, min_free)
            with mutex:
                total = sum(item.get("rssBytes", 0) for item in active.values())
            if total > memory_budget:
                raise RuntimeError("aggregateRssLimit")
        def admission(count):
            guard()
            with mutex:
                used = sum(item.get("rssBytes", 0) for item in active.values())
            # Reserve each running model's full estimate, including not-yet-observed launches.
            return count * reservation + reservation <= memory_budget and used + reservation <= memory_budget and memory_available() >= reserve + reservation
        def accept(job, row):
            with mutex: rows[job["id"]] = row
            publish(job["id"], None)
        publish()
        try:
            for kind in ("pilot", "matrix", "replay"):
                pending = [job for job in plan["jobs"] if job["kind"] == kind and job["id"] not in rows]
                status = pooled(pending, workers, lambda job: execute(job, root, plan, dotnet, publish, guard),
                                accept, admission, stop_file.exists)
                if status != "completed":
                    publish(status=status, reason="See attempts, stop marker or resource limits")
                    return status
            verify_inputs(plan_path, expected)
            write(root / "results.json", {"planSha256": expected, "rows": [rows[j["id"]] for j in plan["jobs"]]})
            publish(status="completed"); return "completed"
        except BaseException as error:
            publish(status="failed", reason=type(error).__name__ + ": " + str(error)); raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("checkpoint", "run"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--mount-root", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument("--abandon-incomplete", action="append", default=[])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--memory-budget-gib", type=float, default=8)
    parser.add_argument("--memory-reserve-gib", type=float, default=2)
    parser.add_argument("--per-worker-reservation-gib", type=float, default=2)
    parser.add_argument("--min-free-gib", type=float, default=5)
    args = parser.parse_args()
    if args.command == "checkpoint":
        print(json.dumps(checkpoint(args.plan, args.mount_root, args.expected_plan_sha256,
                                    args.reason, args.abandon_incomplete)))
    else:
        result = run(args.plan, args.mount_root, args.expected_plan_sha256, args.dotnet, args.workers,
                     int(args.memory_budget_gib * GIB), int(args.memory_reserve_gib * GIB),
                     int(args.per_worker_reservation_gib * GIB), int(args.min_free_gib * GIB))
        if result != "completed": raise SystemExit(2)


if __name__ == "__main__":
    main()
