"""Recount one future compact trial with explicit phase boundaries.

This consumes event logs; it neither launches experiments nor converts old
uncertain boundaries into exact ones. Standard-library implementation only.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid nonnegative finite {name}")
    return value


def recount(publishes, deliveries, phases):
    """One subscriber; all times use the SAME monotonic seconds origin.

    Attempts in [start, end) define the measurement cohort. Completions and
    deliveries through drainEnd (inclusive) belong to that cohort. Missing
    outcomes remain pending, never silently become failures or successes.
    """
    start = number(phases["measurementStartSeconds"], "measurement start")
    end = number(phases["measurementEndSeconds"], "measurement end")
    drain = number(phases["drainEndSeconds"], "drain end")
    if not start < end <= drain:
        raise ValueError("Expected start < end <= drain end")
    if phases.get("timeOrigin") != "sharedMonotonic":
        raise ValueError("Explicit shared monotonic time origin is required")
    qos = phases["qos"]
    if isinstance(qos, bool) or qos not in (0, 1, 2):
        raise ValueError("Invalid QoS")
    attempts, outcomes = {}, {}
    for row in publishes:
        identifier = row["id"]
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Nonempty string ID is required")
        stamp = number(row["elapsedSeconds"], "event time")
        kind = row["event"]
        if kind == "attempt":
            if identifier in attempts:
                raise ValueError("Duplicate attempt ID")
            attempts[identifier] = stamp
        elif kind in ("completed", "failed"):
            if identifier in outcomes:
                raise ValueError("More than one outcome for an ID")
            if kind == "completed" and qos:
                number(row["latencyMs"], "latency")
            outcomes[identifier] = row
        else:
            raise ValueError("Unknown publish event")
    for identifier, row in outcomes.items():
        if identifier not in attempts or row["elapsedSeconds"] < attempts[identifier]:
            raise ValueError("Outcome has no preceding attempt")
    cohort = {key for key, stamp in attempts.items() if start <= stamp < end}
    completed = {key for key in cohort if key in outcomes and
                 outcomes[key]["event"] == "completed" and outcomes[key]["elapsedSeconds"] <= drain}
    failed = {key for key in cohort if key in outcomes and
              outcomes[key]["event"] == "failed" and outcomes[key]["elapsedSeconds"] <= drain}
    first, duplicates, outside = {}, 0, 0
    for row in deliveries:
        stamp = number(row["elapsedSeconds"], "delivery time")
        identifier = row["id"]
        if identifier not in cohort or not start <= stamp <= drain:
            outside += 1
            continue
        if stamp < attempts[identifier]:
            raise ValueError("Delivery precedes attempt")
        if identifier in first:
            duplicates += 1
        first[identifier] = min(first.get(identifier, stamp), stamp)
    duration = end - start
    latencies = sorted(outcomes[key]["latencyMs"] for key in completed) if qos else []
    completed_in_window = sum(outcomes[key]["elapsedSeconds"] < end for key in completed)
    delivered_completed = len(completed & first.keys())
    pending = len(cohort - completed - failed)
    return {
        "scope": "One subscriber; attempts in [measurementStart, measurementEnd); observed through drainEnd",
        "attempted": len(cohort), "completed": len(completed), "failed": len(failed), "pending": pending,
        "measurementSeconds": duration,
        "attemptedRps": len(cohort) / duration,
        "completedInWindow": completed_in_window,
        "completedRpsInWindow": completed_in_window / duration,
        "completedDuringDrain": len(completed) - completed_in_window,
        "completedCohortPerSubmissionSecond": len(completed) / duration,
        "uniqueDeliveries": len(first), "completedIdsDelivered": delivered_completed,
        "deliveredAfterFailedPublish": len(failed & first.keys()),
        "firstDeliveriesDuringDrain": sum(stamp >= end for stamp in first.values()),
        "duplicates": duplicates, "outsideCohortDeliveryEvents": outside,
        "publishFailureRate": len(failed) / len(cohort) if cohort else None,
        "deliveryLossRate": 1 - delivered_completed / len(completed) if completed else None,
        "p99PublishMs": latencies[math.ceil(.99 * len(latencies)) - 1] if latencies else None,
        "latencyStatus": "notApplicable" if qos == 0 else "observed" if latencies else "unobserved",
        "outcomesSettled": pending == 0,
    }


def events(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publishes", required=True)
    parser.add_argument("--deliveries", required=True)
    parser.add_argument("--phases", required=True)
    parser.add_argument("--output", required=True, help="New JSON file; existing files are not overwritten")
    args = parser.parse_args()
    phases = json.loads(Path(args.phases).read_text(encoding="utf-8-sig"))
    result = recount(events(args.publishes), events(args.deliveries), phases)
    result["inputsSha256"] = {
        name: file_digest(path)
        for name, path in (("publishes", args.publishes), ("deliveries", args.deliveries), ("phases", args.phases))
    }
    with Path(args.output).open("x", encoding="utf-8", newline="\n") as target:
        json.dump(result, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")


if __name__ == "__main__":
    main()
