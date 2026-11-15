"""Read archived ActiveMQ attempts and model summaries without changing a campaign."""
import argparse
import collections
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile


SELECTED = {(16, 1, 16), (256, 0, 64), (256, 0, 128), (256, 1, 64)}
MARKERS = ("Remaining length exceeds", "Invalid message encoding", "Unknown MQTTFrame",
           "Unknown PUBREL", "ArrayIndexOutOfBoundsException", "Channel was inactive",
           "OutOfMemoryError", "ResourceAllocationException")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def warning_lines(lines):
    # A JVM command line containing -XX:+ExitOnOutOfMemoryError is not an OOM event.
    return [line for line in lines if "JVM args:" not in line
            and any(marker in line for marker in MARKERS)]


def mapped(path, prefix, destination):
    relative = PurePosixPath(path).relative_to(PurePosixPath(prefix))
    if ".." in relative.parts:
        raise ValueError("Evidence path escapes the supplied root")
    return destination.joinpath(*relative.parts)


def audit_events(archive):
    """Recount identities; distinguish failed calls from publications reaching MQTT transport."""
    attempts, completed, failed, delivered = {}, set(), set(), set()
    first_failures = {}
    error_counts = collections.Counter()
    duplicate_attempts = duplicate_outcomes = delivery_duplicates = 0
    completed_bins, delivered_bins = collections.Counter(), collections.Counter()
    failed_latency = collections.defaultdict(list)
    first_disconnected = {}
    disconnected = disconnected_after_first = 0
    for line in archive.open("publishes.ndjson"):
        event = json.loads(line)
        identity = event["id"]
        if event["event"] == "attempt":
            duplicate_attempts += identity in attempts
            attempts[identity] = (event["publisher"], event["elapsedSeconds"])
            continue
        if identity not in attempts:
            raise ValueError("Outcome without attempt")
        duplicate_outcomes += identity in completed or identity in failed
        if event["event"] == "completed":
            completed.add(identity)
            completed_bins[int(event["elapsedSeconds"])] += 1
        elif event["event"] == "failed":
            failed.add(identity)
            publisher, began = attempts[identity]
            error = event["error"]
            error_counts[error] += 1
            failed_latency[error].append(max(0, event["elapsedSeconds"] - began))
            first_failures.setdefault(publisher, dict(event, publisher=publisher))
            if "not connected" in error.lower():
                disconnected += 1
                disconnected_after_first += publisher in first_disconnected
                first_disconnected.setdefault(publisher, event["elapsedSeconds"])
        else:
            raise ValueError("Unknown publication outcome")
    for line in archive.open("deliveries.ndjson"):
        event = json.loads(line)
        delivery_duplicates += event["id"] in delivered
        delivered.add(event["id"])
        delivered_bins[int(event["elapsedSeconds"])] += 1
    expected = json.loads(archive.read("result.json"))["observation"]["measurement"]
    counts = dict(attemptedPublishes=len(attempts), completedPublishes=len(completed),
                  failedPublishes=len(failed), uniqueDeliveries=len(delivered & attempts.keys()),
                  completedIdsDelivered=len(delivered & completed), duplicateDeliveries=delivery_duplicates,
                  deliveredAfterFailedPublish=len(delivered & failed))
    differences = {k: dict(recorded=expected[k], recounted=v) for k, v in counts.items() if expected[k] != v}
    if dict(error_counts) != expected["errorReasons"]:
        differences["errorReasons"] = dict(recorded=expected["errorReasons"], recounted=dict(error_counts))
    return dict(counts=counts, differences=differences, unsettled=len(attempts.keys() - completed - failed),
                duplicateAttempts=duplicate_attempts, duplicateOutcomes=duplicate_outcomes,
                unexpectedDeliveryIds=len(delivered - attempts.keys()),
                disconnectedFailures=disconnected, repeatedDisconnectedFailures=disconnected_after_first,
                affectedPublishers=len(first_disconnected), firstFailures=list(first_failures.values()),
                failedCallDurationSeconds={error: dict(min=min(xs), max=max(xs), mean=sum(xs)/len(xs))
                                           for error, xs in failed_latency.items()},
                completedBySecond=dict(sorted(completed_bins.items())), deliveredBySecond=dict(sorted(delivered_bins.items())))


def inspect_archive(path, expected_sha, full):
    if full:
        raw = path.read_bytes()
        if digest(raw) != expected_sha:
            raise ValueError(f"Archive SHA mismatch: {path}")
        source = io.BytesIO(raw)
    else:
        source = path
    with zipfile.ZipFile(source) as archive:
        seals = json.loads(archive.read("sha256.json"))
        if full:
            for name, expected in seals.items():
                with archive.open(name) as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != expected.lower():
                        raise ValueError(f"Seal mismatch: {name}")

        def sealed(name):
            raw = archive.read(name)
            if name not in seals or digest(raw) != seals[name].lower():
                raise ValueError(f"Missing or invalid seal: {name}")
            return raw

        result = json.loads(sealed("result.json"))
        samples = [json.loads(x) for x in sealed("stand/telemetry.ndjson").splitlines()]
        fence = json.loads(sealed("stand/workload-end-fence.json"))
        guard = json.loads(sealed("load-generator-summary.json"))
        warnings = []
        for name in ("stand/logs/broker.stdout.log", "stand/logs/broker.stderr.log"):
            warnings += warning_lines(sealed(name).decode(errors="replace").splitlines())
        cpu = [x["processCpuPercent"] for x in samples if x.get("processCpuPercent") is not None]
        rss = [x["processRssBytes"] for x in samples if x.get("processRssBytes") is not None]
        assessment = guard.get("assessment", {})
        guard_cpu = [x["cpuPercent"] for x in assessment.get("intervals", []) if x.get("cpuPercent") is not None]
        coverage = bool(samples and samples[-1]["monotonicSeconds"] >= fence["monotonicSeconds"]
                        and all(str(x["unitId"]) == str(fence["unitId"]) for x in samples))
        value = dict(key=result["key"], measurement=result["observation"]["measurement"],
                     startedUtc=result["observation"]["measurementStartedUtc"],
                     endedUtc=result["observation"]["measurementEndedUtc"],
                     drainReason=result["observation"]["drainReason"], drainSeconds=result["observation"]["drainSeconds"],
                     archiveSha256=expected_sha, fullArchiveVerified=full, inspectedMembersSealed=True,
                     warnings=warnings, telemetry=dict(samples=len(samples), fenceCovered=coverage,
                         maxProcessCpuPercent=max(cpu) if cpu else None, maxRssBytes=max(rss) if rss else None,
                         guardSuccess=assessment.get("success"), guardFailures=assessment.get("failures"),
                         maxGuardCpuPercent=max(guard_cpu) if guard_cpu else None))
        if full:
            value["rawAudit"] = audit_events(archive)
            audit = value["rawAudit"]
            if audit["differences"] or audit["unsettled"] or audit["duplicateAttempts"] or audit["duplicateOutcomes"]:
                raise ValueError(f"Raw recount differs: {result['key']}")
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--archive-prefix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    broker = args.controller / "brokers/ActiveMQ"
    profile = broker / "broker-calibration.v3.json"
    frozen = digest(profile.read_bytes())
    (args.output / "profile.json").write_bytes(profile.read_bytes())
    snapshot = dict(broker="ActiveMQ", excludedFromCampaignCoverage=True, profileSha256=frozen,
                    native=[], models={}, inputs={})
    for stage in ("calibration", "validation"):
        source = broker / stage / "broker-observations.v3.json"
        raw = source.read_bytes()
        (args.output / f"{stage}-observations.json").write_bytes(raw)
        snapshot["inputs"][stage] = digest(raw)
        attempts = args.controller.parent / "raw" / f"ActiveMQ-{stage}" / "attempts"
        seen = set()
        for directory in sorted(attempts.glob("*/attempt-*")):
            result_path = directory / "result.json"
            if not result_path.exists():
                continue
            result = read(result_path)
            if result["status"] != "success":
                continue
            key = result["key"]
            if key["key"] in seen:
                raise ValueError("Duplicate successful attempt")
            seen.add(key["key"])
            receipt = read(directory / "archive.json")
            source = mapped(receipt["archivePath"], args.archive_prefix, args.evidence_root)
            full = (key["messageBytes"], key["qos"], key["publishers"]) in SELECTED
            value = inspect_archive(source, receipt["sha256"], full)
            if value["key"] != key or value["measurement"] != result["observation"]["measurement"]:
                raise ValueError("Archive and retained result differ")
            value.update(stage=stage, archivePath=receipt["archivePath"])
            snapshot["native"].append(value)
            (args.output / "diagnosis.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            print(stage, key["key"], "full-audit" if full else "metadata", flush=True)
        if len(seen) != 72:
            raise ValueError(f"Expected complete 72-run {stage}, got {len(seen)}")
    for stage in ("fidelity", "holdout"):
        index = read(broker / "simulations" / f"{stage}-2s-overnight-v1" / "index.json")
        rows = []
        for run in index["runs"]:
            source = mapped(str(PurePosixPath(run["directory"]) / "summary.json"), args.archive_prefix, args.evidence_root)
            raw = source.read_bytes()
            rows.append(dict(key=run["key"], summary=json.loads(raw), summarySha256=digest(raw), source=str(source)))
        snapshot["models"][stage] = rows
    if digest(profile.read_bytes()) != frozen:
        raise ValueError("Campaign profile changed during diagnosis")
    snapshot["profileUnchanged"] = True
    (args.output / "diagnosis.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print("COMPLETE", len(snapshot["native"]), "native attempts; full raw audits", sum(x["fullArchiveVerified"] for x in snapshot["native"]), flush=True)


if __name__ == "__main__":
    main()
