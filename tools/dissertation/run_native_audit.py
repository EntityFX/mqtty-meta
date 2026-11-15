"""Resumable batch audit on an evidence mount; no extraction and no new MQTT load."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

from native_audit import VERSION, audit_archive, digest


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    with Path(path).open("rb") as stream:
        return digest(stream)


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def verify_storage(mount_root, output):
    root, output = Path(mount_root).resolve(), Path(output).resolve()
    if not root.is_mount() or root == Path(root.anchor) or not output.is_relative_to(root):
        raise ValueError("Output must be inside the explicitly mounted evidence disk")
    if shutil.disk_usage(root).free < 5 * 1024 ** 3:
        raise ValueError("Less than 5 GiB free on evidence disk; no cleanup is attempted")
    return root


def jobs_from_inputs(registry, native):
    paths = [r.get("linuxArchivePath", r["archivePath"]) for r in registry]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate registry paths")
    expected = {r["archive"]["archivePath"]: r for r in native["runs"]}
    if len(expected) != len(native["runs"]):
        raise ValueError("Duplicate published successful archive")
    if not set(expected) <= set(paths):
        raise ValueError("Published successful archive missing from registry")
    jobs = []
    for row, path in zip(registry, paths):
        selected = expected.get(path)
        if selected and row["sha256"] != selected["archive"]["sha256"]:
            raise ValueError("Registry and publication disagree on SHA-256")
        jobs.append({"path": path, "sha256": row["sha256"], "expectedRun": selected})
    return jobs


def validate_published_matrix(native):
    expected = set(itertools.product(("Aedes", "Mosquitto", "ActiveMQ", "EMQX"),
                                     ("calibration", "validation"), (16, 256), (0, 1, 2),
                                     (1, 16, 64, 128), (1, 2, 3)))
    actual = [(r["broker"], r["stage"], r["key"]["messageBytes"], r["key"]["qos"],
               r["key"]["publishers"], r["key"]["repeat"]) for r in native["runs"]]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("Published input is not the complete unique 576-run matrix")


def worker(job, memory_mib):
    if sys.platform == "linux":
        import resource
        limit = memory_mib * 1024 ** 2
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    began = time.monotonic()
    try:
        scientific = audit_archive(job["path"], job["sha256"], job["expectedRun"])
        output = {"scientific": scientific, "error": None}
    except Exception as error:
        output = {"scientific": None, "error": {"type": type(error).__name__, "message": str(error),
                  "traceback": traceback.format_exc(limit=8)}}
    output.update(archivePath=job["path"], expectedSha256=job["sha256"],
                  selectedPublishedRun=job["expectedRun"] is not None,
                  elapsedSeconds=time.monotonic() - began)
    return output


def counts(results, total):
    count = {"processed": len(results), "total": total, "selectedPassed": 0,
             "selectedDiscrepancies": 0, "technicalErrors": 0, "nonSuccessArchives": 0,
             "nonSelectedSuccessArchives": 0}
    for row in results:
        if row["error"]:
            count["technicalErrors"] += 1
            continue
        scientific = row["scientific"]
        if row["selectedPublishedRun"]:
            good = not scientific["differences"] and not scientific["anomalies"] and not scientific.get("unsettled")
            count["selectedPassed" if good else "selectedDiscrepancies"] += 1
        elif scientific["status"] == "success":
            count["nonSelectedSuccessArchives"] += 1
        else:
            count["nonSuccessArchives"] += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--native-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mount-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--worker-memory-mib", type=int, default=2048)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or args.worker_memory_mib < 512 or (args.limit is not None and args.limit < 1):
        parser.error("Invalid worker, memory or pilot limit")
    root = verify_storage(args.mount_root, args.output)
    native = read(args.native_evidence)
    validate_published_matrix(native)
    jobs = jobs_from_inputs(read(args.registry), native)
    if args.limit:
        jobs = jobs[:args.limit]
    for job in jobs:
        # Resolution also prevents a registry path from escaping via symlinks.
        if not Path(job["path"]).resolve().is_relative_to(root):
            raise ValueError("Archive path escapes evidence mount")
    target = args.output.resolve()
    if target.exists() and not args.resume:
        parser.error("Output exists; use a new directory or explicit --resume")
    target.mkdir(parents=True, exist_ok=True)
    lock = (target / "audit.lock").open("a")
    if sys.platform == "linux":
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = {"version": VERSION, "registrySha256": sha(args.registry),
                "nativeEvidenceSha256": sha(args.native_evidence), "pilotLimit": args.limit,
                "archives": len(jobs), "publishedRuns": sum(j["expectedRun"] is not None for j in jobs),
                "sourceSha256": {name: sha(Path(__file__).with_name(name))
                                 for name in ("native_audit.py", "run_native_audit.py")},
                "method": "Read-only event recount, all member seals, identity recomputation, all ACK quantiles",
                "rawExtraction": False}
    manifest_path = target / "manifest.json"
    if manifest_path.exists():
        if read(manifest_path) != manifest:
            raise ValueError("Resume inputs or audit source changed; start a separate audit")
    else:
        write(manifest_path, manifest)
        inputs = target / "inputs"
        inputs.mkdir()
        shutil.copyfile(args.registry, inputs / "archive-registry.json")
        shutil.copyfile(args.native_evidence, inputs / "native-evidence.json")
        for name in manifest["sourceSha256"]:
            shutil.copyfile(Path(__file__).with_name(name), inputs / name)
    results, pending, paths = [], [], {}
    rows_dir = target / "rows"
    rows_dir.mkdir(exist_ok=True)
    for job in jobs:
        # Include path as well as content hash: identical copies remain separate evidence entries.
        label = hashlib.sha256(job["path"].encode()).hexdigest()[:24]
        previous = sorted(rows_dir.glob(label + ".attempt-*.json"))
        if previous:
            cached = read(previous[-1])
            if cached["expectedSha256"] != job["sha256"] or cached["archivePath"] != job["path"]:
                raise ValueError("Cached row provenance mismatch")
            if not (args.retry_errors and cached["error"]):
                results.append(cached)
                continue
        pending.append(job)
        paths[job["path"]] = rows_dir / (label + f".attempt-{len(previous)+1:03}.json")
    started = time.monotonic()
    state = {"pid": os.getpid(), "status": "running", "workers": args.workers,
             "workerMemoryMiB": args.worker_memory_mib, **counts(results, len(jobs))}
    write(target / "progress.json", state)
    try:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(worker, job, args.worker_memory_mib): job for job in pending}
            for future in as_completed(futures):
                job = futures[future]
                verify_storage(args.mount_root, target)
                row = future.result()
                write(paths[job["path"]], row)
                results.append(row)
                state.update(counts(results, len(jobs)), elapsedSeconds=time.monotonic() - started)
                write(target / "progress.json", state)
                print(json.dumps({k: v for k, v in state.items() if k != "pid"}), flush=True)
        summary = {"manifest": manifest, "counts": counts(results, len(jobs)),
                   "results": sorted(results, key=lambda r: r["archivePath"])}
        write(target / "audit-results.json", summary)
        state.update(status="completed", elapsedSeconds=time.monotonic() - started)
        write(target / "progress.json", state)
    except BaseException as error:
        state.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                     reason=type(error).__name__ + ": " + str(error))
        write(target / "progress.json", state)
        raise
    finally:
        lock.close()
    if state["technicalErrors"] or state["selectedDiscrepancies"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
