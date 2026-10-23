"""Scenario ETA from completed timings and the actual pair-barrier scheduler."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from statistics import median


def point(identity):
    match = re.fullmatch(r"(?:(pilot|replay)\.)?(Aedes|Mosquitto|ActiveMQ|EMQX)\.m16\.q([012])\.p(1|128)\.s(42|43|44)\.(baseline|halfTick|doubleWindow)", identity)
    if not match:
        raise ValueError("Unknown sensitivity job: " + identity)
    prefix, broker, qos, publishers, seed, variant = match.groups()
    return prefix or "matrix", broker, int(qos), int(publishers), variant


def batch_remaining(jobs, completed, active, predict, multiplier=1):
    # Initial run has no resume/regrouping. Retain original pair boundaries,
    # including a finished mate of a still-active job.
    seconds = 0
    for start in range(0, len(jobs), 2):
        pending = [max(0, predict(j) * multiplier - active.get(j["id"], {}).get("elapsedSeconds", 0))
                   for j in jobs[start:start + 2] if j["id"] not in completed]
        seconds += max(pending, default=0)
    return seconds


def estimate(observation):
    rows = [r["data"] for r in observation["rows"]]
    completed = {r["id"] for r in rows if not r["technicalError"]}
    baselines, ratios = defaultdict(list), defaultdict(list)
    for r in rows:
        kind, broker, qos, n, variant = point(r["id"])
        if variant == "baseline":
            baselines[(broker, qos, n)].append(r["wallSeconds"])
    for r in rows:
        kind, broker, qos, n, variant = point(r["id"])
        if variant != "baseline" and baselines[(broker, qos, n)]:
            ratios[(n, variant)].append(r["wallSeconds"] / median(baselines[(broker, qos, n)]))

    def predict(job):
        _, broker, qos, n, variant = point(job["id"])
        base = baselines[(broker, qos, n)] or baselines[(broker, 1, n)]
        if not base:
            raise ValueError("No measured baseline for " + job["id"])
        factor = 1 if variant == "baseline" else median(ratios[(n, variant)])
        return median(base) * factor

    active = observation["progress"]["active"]
    jobs = observation["plan"]["jobs"]
    matrix = [j for j in jobs if point(j["id"])[0] == "matrix"]
    replay = [j for j in jobs if point(j["id"])[0] == "replay"]
    scenarios = {}
    now = datetime.fromisoformat(observation["observedAtUtc"])
    for label, factor in (("optimistic", .8), ("central", 1), ("slow", 1.3)):
        main = batch_remaining(matrix, completed, active, predict, factor)
        repeats = batch_remaining(replay, completed, active, predict, factor)
        scenarios[label] = {"matrixRemainingHours": main / 3600, "replayRemainingHours": repeats / 3600,
                            "totalRemainingHours": (main + repeats) / 3600,
                            "estimatedFinishUtc": (now + timedelta(seconds=main + repeats)).isoformat()}
    per_broker = {}
    cumulative = 0
    for broker in ("Aedes", "Mosquitto", "ActiveMQ", "EMQX"):
        block = [j for j in matrix if point(j["id"])[1] == broker]
        cost = batch_remaining(block, completed, active, predict)
        cumulative += cost
        per_broker[broker] = {"done": sum(j["id"] in completed for j in block), "total": len(block),
                              "remainingCentralHours": cost / 3600,
                              "blockFinishCentralUtc": (now + timedelta(seconds=cumulative)).isoformat()}
    return {"observedAtUtc": observation["observedAtUtc"], "method": "Measured per-broker/QoS baseline, otherwise QoS1 pilot; measured per-N variant ratio; sum of pair maxima",
            "assumptions": ["Initial scheduler without resume/regrouping", "Other QoS and brokers extrapolated where unmeasured",
                            "0.8/1.0/1.3 are scenario multipliers, not confidence intervals", "No new failures, pauses or competing workload"],
            "completedMatrix": len(completed.intersection(j["id"] for j in matrix)), "scenarios": scenarios,
            "perBrokerMatrix": per_broker,
            "variantRatios": {f"p{n}.{v}": median(values) for (n, v), values in ratios.items()},
            "jobTimeoutSeconds": observation["plan"]["jobTimeoutSeconds"],
            "predictedJobsAboveTimeout": [j["id"] for j in jobs if predict(j) > observation["plan"]["jobTimeoutSeconds"]]}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--observation", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = estimate(json.loads(a.observation.read_text(encoding="utf-8-sig")))
    result["observationSha256"] = hashlib.sha256(a.observation.read_bytes()).hexdigest()
    result["estimatorSha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
