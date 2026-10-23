"""Prepare portable candidate configurations; never launch or overwrite a study."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def configuration(mode, topology, qos, rate, seconds, seed=42, profiled=False):
    return {"schemaVersion": 1, "mode": mode, "brokers": 4, "topology": topology,
            "publishers": 64, "receiverSites": [0, 1, 2, 3], "payloadBytes": 16,
            "qos": qos, "offeredRps": rate, "seed": seed, "warmupSeconds": 1,
            "measurementSeconds": seconds, "drainTimeoutSeconds": 5,
            "tickPeriod": "00:00:00.0001000", "queueCapacity": 1024,
            "cacheCapacity": 100000, "hopLimit": 4, "ideal": not profiled,
            "brokerType": "Mosquitto", "brokerProfilePath": "../../inputs/mosquitto-profile.json" if profiled else None,
            "network": {"capacityBytesPerSecond": 125000000, "throughputWindowTicks": 100,
                        "queueCapacity": 100000, "transferTicks": 2}}


def cases():
    modes = ("single", "relay", "federation")
    jobs = []

    def add(kind, config, original=None):
        o = config
        identity = f"{kind}.{o['topology']}.{o['mode']}.q{o['qos']}.r{o['offeredRps']}.w{o['measurementSeconds']}.s{o['seed']}"
        job = {"id": identity, "kind": kind, "configuration": o}
        if original:
            job["replayOf"] = original
        jobs.append(job)

    for topology in ("line", "star"):
        for mode in modes:
            for rate in (1000, 5000):
                for window in (2, 10):
                    add("pilot", configuration(mode, topology, 1, rate, window))
    for mode in modes:
        for qos in (0, 2):
            add("pilot", configuration(mode, "star", qos, 5000, 10))
    for topology in ("line", "ring", "star"):
        for mode in modes:
            for qos in (0, 1, 2):
                for rate in (1000, 5000):
                    for seed in (42, 43, 44):
                        add("matrix", configuration(mode, topology, qos, rate, 10, seed))
    for job in list(jobs):
        o = job["configuration"]
        if job["kind"] == "matrix" and o["topology"] == "star" and o["qos"] == 2 and o["seed"] == 42:
            add("replay", deepcopy(o), job["id"])
    # Separate optional diagnostic. Extra service traffic is outside calibration.
    for mode in modes:
        for qos in (0, 1, 2):
            for rate in (1000, 5000):
                for seed in (42, 43, 44):
                    add("diagnostic", configuration(mode, "ring", qos, rate, 10, seed, True))
    return jobs


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def prepare(output):
    if output.exists():
        raise ValueError("Choose a new output directory; previous evidence must not be overwritten")
    jobs = cases()
    output.mkdir(parents=True, exist_ok=False)
    (output / "inputs").mkdir()
    profile = ROOT / "reports/data/Mosquitto/profile/broker-calibration.v3.json"
    shutil.copyfile(profile, output / "inputs/mosquitto-profile.json")
    for job in jobs:
        relative = Path("configs") / job["kind"] / (job["id"] + ".json")
        (output / relative).parent.mkdir(parents=True, exist_ok=True)
        write(output / relative, job.pop("configuration"))
        job["config"] = relative.as_posix()
        job["sha256"] = hashlib.sha256((output / relative).read_bytes()).hexdigest()
    manifest = {"status": "candidateProgramPreparedNotExecuted", "schemaVersion": 1,
                "purpose": "Longer ideal-network mechanism comparison; optional Mosquitto diagnostic kept separate",
                "pilotJobs": 30, "matrixJobs": 162, "replayJobs": 6, "optionalDiagnosticJobs": 54,
                "requiresCleanCommittedSourcesBeforeExecution": True,
                "requiresRuntimeSnapshotBeforeExecution": True,
                "matrixGate": "Review all pilots: technical validity, phase sufficiency, disk and measured wall/RSS budget; no automatic promotion",
                "linuxIsolationGate": "Wait for sensitivity campaign to finish; do not launch concurrent studies",
                "proposedWorkerCount": 1, "jobs": jobs,
                "profileSha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
                "generatorSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write(output / "plan.json", manifest)
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = prepare(a.output)
    print(json.dumps({k: v for k, v in result.items() if k != "jobs"}))
