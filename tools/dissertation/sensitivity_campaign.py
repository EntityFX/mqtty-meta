"""Frozen BrokerFidelity sensitivity study: prepare once, run/resume on evidence storage."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time

BROKERS = ("Aedes", "Mosquitto", "ActiveMQ", "EMQX")
VARIANTS = ("baseline", "halfTick", "doubleWindow")
RUNTIME_SHA = "60c5a79fca5291b061af345f3fb98e758c62ae551763693d5f39f74d40e9cebd"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""): value.update(chunk)
    return value.hexdigest()


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def storage(mount, output):
    mount, output = Path(mount).resolve(), Path(output).resolve()
    if not mount.is_mount() or mount == Path(mount.anchor) or not output.is_relative_to(mount):
        raise ValueError("Output must be on the explicitly mounted evidence disk")
    if shutil.disk_usage(mount).free < 5 * 1024**3:
        raise ValueError("Evidence disk has less than 5 GiB free; no cleanup attempted")


def tick_units(text):
    # TimeSpan at the 100 ns resolution supported by the existing CLI.
    hours, minutes, seconds = text.split(":")
    whole, dot, fraction = seconds.partition(".")
    if not hours.isdigit() or len(fraction) > 7:
        raise ValueError("Unsupported tick period")
    return ((int(hours) * 3600 + int(minutes) * 60 + int(whole)) * 10_000_000
            + int(fraction.ljust(7, "0") or "0"))


def variant_config(original, variant):
    config = copy.deepcopy(original)
    if variant not in VARIANTS: raise ValueError("Unknown variant")
    if variant == "doubleWindow":
        config["experiment"]["measurementTicks"] *= 2
    if variant == "halfTick":
        units = tick_units(config["ticks"]["tickPeriod"])
        if units <= 0 or units % 2 or units >= 10_000_000:
            raise ValueError("Halved tick must be representable below one second")
        config["ticks"]["tickPeriod"] = f"00:00:00.{units // 2:07d}"
        for field in ("warmupTicks", "measurementTicks", "drainTimeoutTicks"):
            config["experiment"][field] *= 2
        config["ticks"]["outgoingWaitTicks"] *= 2
        for field in ("transferTicks", "throughputWindowTicks"):
            config["network"][field] *= 2
    return config


def prepare(controller, models_path, output, mount):
    controller, output = Path(controller).resolve(), Path(output).resolve()
    storage(mount, output)
    if output.exists(): raise ValueError("Study already exists; refusing overwrite")
    series, models = read(controller / "series.json"), read(models_path)
    runtime = Path(series["simulatorCli"])
    if sha(runtime) != RUNTIME_SHA: raise ValueError("Campaign runtime identity differs")
    revisions = {}
    for name in ("mqttYRepository", "benchmarkRepository"):
        repo = series[name]
        if subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True).strip():
            raise ValueError("Simulation provenance repository is dirty: " + name)
        revisions[name] = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip()
    # Validate source configs before creating the output tree.
    sources = []
    for broker, qos, publishers, seed in itertools.product(BROKERS, (0, 1, 2), (1, 128), (42, 43, 44)):
        key = f"m16.q{qos}.p{publishers}.s{seed}"
        saved = next(r for r in models[broker] if r["stage"] == "holdout" and r["key"] == key)
        path = controller / "brokers" / broker / "simulations/holdout-2s-overnight-v1" / (key + ".json")
        config = read(path)
        if sha(path) != saved["configSha256"] or sha(config["brokerProfilePath"]) not in saved["profileHashes"]:
            raise ValueError("Source config/profile differs: " + broker + "/" + key)
        sources.append((broker, key, config, saved, sha(path)))
    output.mkdir(parents=True)
    (output / "runtime").mkdir(); (output / "inputs").mkdir(); (output / "configs").mkdir()
    for path in runtime.parent.iterdir():
        if path.is_file(): shutil.copyfile(path, output / "runtime" / path.name)
    shutil.copyfile(models_path, output / "inputs/model-evidence.json")
    shutil.copyfile(Path(__file__), output / "inputs/sensitivity_campaign.py")
    expected, jobs = {}, []
    for broker in BROKERS:
        sample = next(c for b, _, c, _, _ in sources if b == broker)
        for field, name in (("brokerProfilePath", "profile.json"), ("referenceObservationsPath", "reference.json")):
            destination = output / "inputs" / (broker + "-" + name)
            shutil.copyfile(sample[field], destination)
    for broker, key, original, saved, source_sha in sources:
        for variant in VARIANTS:
            identifier = f"{broker}.{key}.{variant}"
            config = variant_config(original, variant)
            config["brokerProfilePath"] = str(output / "inputs" / (broker + "-profile.json"))
            config["referenceObservationsPath"] = str(output / "inputs" / (broker + "-reference.json"))
            path = output / "configs" / (identifier + ".json")
            write(path, config)
            jobs.append({"id": identifier, "broker": broker, "key": key, "variant": variant,
                         "kind": "matrix", "config": str(path), "configSha256": sha(path),
                         "originalConfigSha256": source_sha, "expectedOriginalSummary": saved["summary"] if variant == "baseline" else None})
    pilots = []
    for broker, publishers in itertools.product(BROKERS, (1, 128)):
        item = next(j for j in jobs if j["broker"] == broker and j["key"] == f"m16.q1.p{publishers}.s42" and j["variant"] == "baseline")
        pilots.append(dict(item, id="pilot." + item["id"], kind="pilot"))
    selectors = [("Aedes", "m16.q0.p1.s42", "baseline"), ("Mosquitto", "m16.q1.p1.s42", "baseline"),
                 ("ActiveMQ", "m16.q2.p1.s42", "baseline"), ("EMQX", "m16.q1.p128.s42", "baseline"),
                 ("Aedes", "m16.q1.p128.s42", "halfTick"), ("ActiveMQ", "m16.q0.p128.s42", "doubleWindow")]
    repeats = []
    for broker, key, variant in selectors:
        item = next(j for j in jobs if (j["broker"], j["key"], j["variant"]) == (broker, key, variant))
        repeats.append(dict(item, id="replay." + item["id"], kind="replay", replayOf=item["id"]))
    for folder in ("inputs", "runtime"):
        for path in sorted((output / folder).iterdir()): expected[path.relative_to(output).as_posix()] = sha(path)
    plan = {"schemaVersion": 1, "purpose": "Tick/window sensitivity; not new blind validation or profile fitting",
            "payloadBytes": 16, "publishers": [1, 128], "seeds": [42, 43, 44],
            "baselineTickUs": 10, "halfTickUs": 5, "baselineMeasurementSeconds": 2,
            "longMeasurementSeconds": 4, "warmupSeconds": 1, "drainTimeoutSeconds": 5,
            "sourceRevisions": revisions, "modelsSha256": sha(models_path),
            "runtime": str(output / "runtime" / runtime.name), "immutableFiles": expected,
            "jobs": pilots + jobs + repeats, "pilotJobs": 8, "matrixJobs": 216, "replayJobs": 6,
            "criteriaUnchanged": {"rps": .15, "p99": .2, "failure": .05, "loss": .05},
            "jobTimeoutSeconds": 1800, "jobRssLimitBytes": 3 * 1024**3}
    write(output / "plan.json", plan)
    return {"plan": str(output / "plan.json"), "pilots": 8, "matrix": 216, "replays": 6}


def rss(pid):
    try:
        line = next(s for s in Path(f"/proc/{pid}/status").read_text().splitlines() if s.startswith("VmRSS:"))
        return int(line.split()[1]) * 1024
    except (FileNotFoundError, StopIteration): return 0


def execute(job, root, plan, dotnet, on_state):
    storage(plan["mount"], root)
    if sha(job["config"]) != job["configSha256"]: raise ValueError("Config changed: " + job["id"])
    folder = root / "runs" / job["id"]; folder.mkdir(parents=True, exist_ok=True)
    attempt = 1 + len(list(folder.glob("attempt-*.json")))
    destination = folder / f"output-{attempt:03d}"
    log_path = folder / f"attempt-{attempt:03d}.log"
    started = time.monotonic(); peak = 0; stopped = None
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([dotnet, plan["runtime"], "experiment", "--config", job["config"], "--output", str(destination)], stdout=log, stderr=subprocess.STDOUT)
        while process.poll() is None:
            peak = max(peak, rss(process.pid))
            elapsed = time.monotonic() - started
            on_state(job["id"], {"pid": process.pid, "elapsedSeconds": elapsed, "rssBytes": rss(process.pid), "peakRssBytes": peak})
            if elapsed > plan["jobTimeoutSeconds"]: stopped = "jobTimeout"
            if peak > plan["jobRssLimitBytes"]: stopped = "jobRssLimit"
            if stopped:
                process.kill(); break
            time.sleep(2)
        code = process.wait()
    row = {"id": job["id"], "kind": job["kind"], "variant": job["variant"],
           "configSha256": job["configSha256"], "returnCode": code,
           "wallSeconds": time.monotonic() - started, "peakRssBytes": peak, "technicalError": stopped}
    outputs = list(destination.glob("*/summary.json"))
    if code not in (0, 3) or len(outputs) != 1:
        row["technicalError"] = row["technicalError"] or "CLI failed or unique summary unavailable"
    else:
        folder_out = outputs[0].parent
        summary = read(outputs[0]); row["summary"] = summary
        row["outputDirectory"] = str(folder_out)
        row["summarySha256"] = sha(outputs[0])
        row["histogramSha256"] = sha(folder_out / "latency-histogram.json")
        counters = read(folder_out / "counters.json")
        scientific = {k: counters[k] for k in ("brokers", "receivers", "receiverScope")}
        row["scientificCountersSha256"] = hashlib.sha256(json.dumps(scientific, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        row["fidelityFailed"] = any(c["status"] == "failed" for c in summary["acceptanceCriteria"])
        if summary["status"] != "completed": row["technicalError"] = "Simulation did not complete: " + summary["status"]
        if job["expectedOriginalSummary"] is not None and summary != job["expectedOriginalSummary"]:
            row["technicalError"] = "Baseline differs from original frozen campaign"
        if job["kind"] == "replay":
            prior = read(root / "rows" / (job["replayOf"] + ".json"))
            row["replayEqual"] = all(row[k] == prior[k] for k in ("summarySha256", "histogramSha256", "scientificCountersSha256"))
            if not row["replayEqual"]: row["technicalError"] = "Scientific replay bytes differ"
    write(folder / f"attempt-{attempt:03d}.json", row)
    write(root / "rows" / (job["id"] + ".json"), row)
    return row


def run(plan_path, mount, dotnet, workers, resume):
    root = plan_path.resolve().parent; storage(mount, root)
    if not 1 <= workers <= 2: raise ValueError("Only 1 or 2 simultaneous models supported")
    lock = (root / "run.lock").open("a")
    import fcntl
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan = read(plan_path); original_sha = sha(plan_path); plan["mount"] = str(mount)
    if sha(root / "inputs/sensitivity_campaign.py") != sha(Path(__file__)):
        raise ValueError("Runner source changed")
    for relative, expected in plan["immutableFiles"].items():
        if sha(root / relative) != expected: raise ValueError("Snapshot changed: " + relative)
    (root / "rows").mkdir(exist_ok=True)
    if (root / "progress.json").exists() and not resume: raise ValueError("Use explicit --resume")
    results = {job["id"]: read(root / "rows" / (job["id"] + ".json")) for job in plan["jobs"] if (root / "rows" / (job["id"] + ".json")).exists()}
    if any(r["technicalError"] for r in results.values()): raise ValueError("Prior technical failure; preserve it and inspect before retry")
    running, mutex = {}, threading.Lock(); began = time.monotonic()

    def publish(identifier=None, values=None, status="running", reason=None):
        with mutex:
            if identifier:
                if values is None: running.pop(identifier, None)
                else: running[identifier] = values
            state = {"status": status, "reason": reason, "planSha256": original_sha,
                     "elapsedSeconds": time.monotonic() - began, "workers": workers,
                     "counts": {kind: {"done": sum(r["kind"] == kind and not r["technicalError"] for r in results.values()),
                                        "failedCriteria": sum(r["kind"] == kind and r.get("fidelityFailed", False) for r in results.values()),
                                        "total": sum(j["kind"] == kind for j in plan["jobs"])} for kind in ("pilot", "matrix", "replay")},
                     "technicalErrors": sum(bool(r["technicalError"]) for r in results.values()), "active": dict(running)}
            pilots = [r for r in results.values() if r["kind"] == "pilot" and not r["technicalError"]]
            if len(pilots) == 8:
                cost = sum(r["wallSeconds"] for r in pilots)
                state["estimatedFullMatrixWallHoursAfterPilot"] = {"lower": cost * 27 / workers / 3600, "upper": cost * 54 / workers / 3600,
                    "assumption": "Extrapolate QoS1 baseline timing to 3 QoS, 3 seeds and 3 variants; not a guaranteed deadline"}
            write(root / "progress.json", state)

    publish()
    try:
        for kind in ("pilot", "matrix", "replay"):
            pending = [j for j in plan["jobs"] if j["kind"] == kind and j["id"] not in results]
            for start in range(0, len(pending), workers):
                storage(mount, root)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(execute, job, root, plan, dotnet, publish) for job in pending[start:start + workers]]
                    for future in as_completed(futures):
                        row = future.result()
                        with mutex: results[row["id"]] = row
                        publish(row["id"], None)
                if any(r["technicalError"] for r in results.values()): raise RuntimeError("Technical validation failed; see rows and attempt logs")
        for relative, expected in plan["immutableFiles"].items():
            if sha(root / relative) != expected: raise ValueError("Input changed during study: " + relative)
        write(root / "results.json", {"planSha256": original_sha, "rows": [results[j["id"]] for j in plan["jobs"]]})
        publish(status="completed")
    except BaseException as error:
        publish(status="failed", reason=type(error).__name__ + ": " + str(error))
        raise
    finally: lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    for name in ("controller", "models", "output", "mount-root"): prep.add_argument("--" + name, type=Path, required=True)
    execute_parser = sub.add_parser("run")
    for name in ("plan", "mount-root"): execute_parser.add_argument("--" + name, type=Path, required=True)
    execute_parser.add_argument("--dotnet", default="dotnet"); execute_parser.add_argument("--workers", type=int, default=2)
    execute_parser.add_argument("--resume", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "prepare": print(json.dumps(prepare(arguments.controller, arguments.models, arguments.output, arguments.mount_root)))
    else: run(arguments.plan, arguments.mount_root, arguments.dotnet, arguments.workers, arguments.resume)
