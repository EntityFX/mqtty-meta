"""Independent, read-only recount of sealed native MQTT event archives.

Only standard-library code is used. No benchmark aggregation or publisher scoring
functions are imported. Archives are streamed, never extracted. Completion time
and receiver time share the workload stopwatch; its exact measurement origin was
not saved, so receiver phase counts are conservative bounds, not exact drain counts.
"""
from array import array
from collections import Counter
from datetime import datetime
import hashlib
import io
import json
import math
from pathlib import Path
import re
import zipfile


VERSION = 1
COMPLETE, FAILED, DELIVERED = 1, 2, 4
QUANTILES = {"minMs": 0, "p50Ms": .5, "p75Ms": .75,
             "p95Ms": .95, "p99Ms": .99, "maxMs": 1}


def digest(stream):
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def quantiles(values):
    if not values:
        return None
    ordered = sorted(values)
    return {name: ordered[max(0, math.ceil(probability * len(ordered)) - 1)]
            for name, probability in QUANTILES.items()}


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid nonnegative finite number: " + label)
    return value


def compare(actual, recorded, prefix=""):
    """Compare independent scalar results; missing recorded values are differences."""
    differences = {}
    for key, value in actual.items():
        name = prefix + key
        if key not in recorded:
            differences[name] = {"recounted": value, "recorded": "<missing>"}
            continue
        other = recorded[key]
        equal = value == other
        if isinstance(value, float) and isinstance(other, (int, float)) and not isinstance(other, bool):
            equal = math.isfinite(other) and math.isclose(value, other, rel_tol=1e-10, abs_tol=1e-10)
        if not equal:
            differences[name] = {"recounted": value, "recorded": other}
    return differences


class SealedArchive:
    def __init__(self, path):
        self.archive = zipfile.ZipFile(path)
        names = self.archive.namelist()
        if len(names) != len(set(names)):
            self.archive.close()
            raise ValueError("Duplicate ZIP member names")
        self.seals = json.loads(self.archive.read("sha256.json"))
        if set(self.seals) != set(names) - {"sha256.json"}:
            self.archive.close()
            raise ValueError("Seal set does not cover exactly all ZIP members")
        self.verified = {}

    def close(self):
        self.archive.close()

    def read_json(self, name):
        raw = self.archive.read(name)
        self._verify(name, hashlib.sha256(raw).hexdigest())
        return json.loads(raw)

    def events(self, name):
        hasher = hashlib.sha256()
        with self.archive.open(name) as source, io.BufferedReader(source, 1024 * 1024) as stream:
            for number, line in enumerate(stream, 1):
                hasher.update(line)
                try:
                    yield json.loads(line)
                except (ValueError, UnicodeError) as error:
                    raise ValueError(f"Invalid NDJSON {name}:{number}") from error
        self._verify(name, hasher.hexdigest())

    def verify_remaining(self):
        for name in self.seals:
            if name not in self.verified:
                with self.archive.open(name) as stream:
                    self._verify(name, digest(stream))

    def _verify(self, name, actual):
        if actual.lower() != self.seals[name].lower():
            raise ValueError("Raw seal mismatch: " + name)
        self.verified[name] = actual


def audit_archive(path, expected_sha, expected_run=None):
    path = Path(path)
    with path.open("rb") as stream:
        actual_sha = digest(stream)
    if actual_sha.lower() != expected_sha.lower():
        raise ValueError("Archive SHA-256 mismatch")
    sealed = SealedArchive(path)
    try:
        return _recount(sealed, actual_sha, expected_run)
    finally:
        sealed.close()


def _recount(sealed, archive_sha, expected_run):
    result = sealed.read_json("result.json")
    identity, key = result["identity"], result["key"]
    measurement = (result.get("observation") or {}).get("measurement")
    observation = result.get("observation") or {}
    qos, publishers, payload_size = key["qos"], key["publishers"], key["messageBytes"]
    if qos not in (0, 1, 2) or publishers < 1 or payload_size < 16:
        raise ValueError("Invalid run key")
    canonical_key = f"{key['broker']}.m{payload_size}.q{qos}.p{publishers}.r{key['repeat']}"
    if key["key"] != canonical_key:
        raise ValueError("Run-key fields disagree with canonical key")
    campaign = identity["campaignId"]
    run_id = key["key"] + f".attempt-{result['attempt']:02}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", campaign + run_id):
        raise ValueError("Non-ASCII or escaped identity requires explicit serializer compatibility review")
    prefixes = [json.dumps([campaign, run_id, publisher], separators=(",", ":")).encode()[:-1] + b","
                for publisher in range(publishers)]
    anomalies = Counter()
    ids, unknown = {}, {}
    states = bytearray()
    attempted_at, completed_at, first_received_at = array("d"), array("d"), array("d")
    ack_latencies = array("d")
    sequences, per_publisher = Counter(), {p: Counter() for p in range(publishers)}
    error_reasons = Counter()
    completed_bins, delivery_bins = Counter(), Counter()
    minimum_attempt, maximum_outcome = math.inf, 0.0

    names = set(sealed.seals)
    if "started.json" in names:
        started = sealed.read_json("started.json")
        if any(started.get(name) != result.get(name) for name in ("identity", "key", "attempt")):
            raise ValueError("Start/result provenance mismatch")
    has_logs = {"publishes.ndjson", "deliveries.ndjson"} <= names
    if not has_logs:
        sealed.verify_remaining()
        if result.get("status") == "success":
            raise ValueError("Successful run has no complete event journals")
        return {"version": VERSION, "archiveSha256": archive_sha, "status": result.get("status"),
                "failure": result.get("failure"), "identity": identity, "key": key,
                "sealedFilesVerified": len(sealed.verified), "eventCoverage": "journalsUnavailable",
                "differences": {}, "anomalies": {}}

    # Successful publish IDs must be exactly those generated from this run,
    # publisher and sequence; this also excludes warmup IDs.
    for event in sealed.events("publishes.ndjson"):
        event_type = event["event"]
        stamp = finite(event["elapsedSeconds"], "publish elapsedSeconds")
        identifier = event["id"]
        if not re.fullmatch("[0-9a-f]{32}", identifier):
            raise ValueError("Invalid publish ID representation")
        identifier = bytes.fromhex(identifier)
        if event_type == "attempt":
            publisher, sequence = event["publisher"], event["sequence"]
            if type(publisher) is not int or not 0 <= publisher < publishers or type(sequence) is not int or sequence < 0:
                raise ValueError("Invalid publisher or sequence")
            expected_id = hashlib.sha256(prefixes[publisher] + str(sequence).encode() + b"]").digest()[:16]
            if identifier != expected_id:
                anomalies["identityHashMismatch"] += 1
            if sequence != sequences[publisher]:
                anomalies["sequenceDiscontinuity"] += 1
            sequences[publisher] = sequence + 1
            if event.get("payloadBytes") != payload_size:
                anomalies["publishPayloadSizeMismatch"] += 1
            if identifier in ids:
                anomalies["duplicateAttemptIds"] += 1
                continue
            ids[identifier] = (len(states), publisher)
            states.append(0)
            attempted_at.append(stamp)
            completed_at.append(math.nan)
            first_received_at.append(math.nan)
            per_publisher[publisher]["attempted"] += 1
            minimum_attempt = min(minimum_attempt, stamp)
        elif event_type in ("completed", "failed"):
            if identifier not in ids:
                raise ValueError("Outcome without preceding attempt")
            index, publisher = ids[identifier]
            if states[index] & (COMPLETE | FAILED):
                anomalies["duplicateOutcomes"] += 1
                continue
            if stamp < attempted_at[index]:
                anomalies["outcomeBeforeAttempt"] += 1
            maximum_outcome = max(maximum_outcome, stamp)
            if event_type == "completed":
                states[index] |= COMPLETE
                completed_at[index] = stamp
                per_publisher[publisher]["completed"] += 1
                completed_bins[math.floor(stamp)] += 1
                if qos == 0:
                    if event.get("latencyMs") is not None:
                        anomalies["qos0AckLatencyPresent"] += 1
                else:
                    latency = finite(event.get("latencyMs"), "ACK latency")
                    ack_latencies.append(latency)
                    if latency > (stamp - attempted_at[index]) * 1000 + 1e-5:
                        anomalies["ackLatencyExceedsLoggedAttemptToOutcome"] += 1
            else:
                states[index] |= FAILED
                per_publisher[publisher]["failed"] += 1
                error_reasons[event.get("error", "unknown")] += 1
        else:
            raise ValueError("Unknown publish event")

    duplicates = unexpected = received_after_failed = completed_received = received_unique = 0
    first_delivery, last_delivery = math.inf, 0.0
    all_delivery_times = array("d")
    end_to_end = array("d")
    first_receipt_times = array("d")
    for event in sealed.events("deliveries.ndjson"):
        stamp = finite(event["elapsedSeconds"], "delivery elapsedSeconds")
        identifier = event["id"]
        identifier = bytes.fromhex(identifier) if re.fullmatch("[0-9a-f]{32}", identifier) else identifier
        delivery_bins[math.floor(stamp)] += 1
        all_delivery_times.append(stamp)
        first_delivery, last_delivery = min(first_delivery, stamp), max(last_delivery, stamp)
        if event.get("payloadBytes") != payload_size:
            anomalies["deliveryPayloadSizeMismatch"] += 1
        if identifier not in ids:
            unexpected += 1
            duplicates += identifier in unknown
            unknown[identifier] = True
            continue
        index, publisher = ids[identifier]
        if states[index] & DELIVERED:
            duplicates += 1
            continue
        states[index] |= DELIVERED
        first_received_at[index] = stamp
        first_receipt_times.append(stamp)
        received_unique += 1
        per_publisher[publisher]["delivered"] += 1
        received_after_failed += bool(states[index] & FAILED)
        completed_received += bool(states[index] & COMPLETE)
        latency = (stamp - attempted_at[index]) * 1000
        if latency < 0:
            anomalies["deliveryBeforeAttempt"] += 1
        else:
            end_to_end.append(latency)

    attempted = len(states)
    completed = sum(bool(s & COMPLETE) for s in states)
    failed = sum(bool(s & FAILED) for s in states)
    unsettled = attempted - completed - failed
    counts = {"attemptedPublishes": attempted, "completedPublishes": completed,
              "failedPublishes": failed, "uniqueDeliveries": received_unique,
              "duplicateDeliveries": duplicates, "unexpectedDeliveries": unexpected,
              "deliveredAfterFailedPublish": received_after_failed,
              "completedIdsDelivered": completed_received}
    ack = quantiles(ack_latencies)
    status = "notApplicable" if qos == 0 else "observed" if completed else "unobserved"
    differences, rates, phase = {}, {}, {}
    if measurement is not None:
        duration = finite(measurement["actualMeasurementSeconds"], "measurement seconds")
        if duration == 0:
            raise ValueError("Zero measurement duration")
        rates = {"attemptedRps": attempted / duration, "completedRps": completed / duration,
                 "publishFailureRate": failed / attempted if attempted else 0.0,
                 "deliveryLossRate": 1 - completed_received / completed if completed else 0.0}
        differences.update(compare(counts | rates | {"latencyStatus": status, "errorReasons": dict(error_reasons)}, measurement))
        recorded_ack = measurement.get("publishLatencyMs")
        if ack is None or recorded_ack is None:
            if ack != recorded_ack:
                differences["publishLatencyMs"] = {"recounted": ack, "recorded": recorded_ack}
        else:
            differences.update(compare(ack, recorded_ack, "publishLatencyMs."))

        # Measurement begins before its first attempt and ends after all publish
        # tasks settle. Hence lastOutcome <= end <= firstAttempt + duration.
        # The exact stopwatch origin is absent; never label the ambiguous band
        # as either measurement or drain without further primary evidence.
        if attempted:
            upper = minimum_attempt + duration
            if maximum_outcome > upper + 1e-6:
                anomalies["measurementEndBoundsInconsistent"] += 1
            phase = {"method": "control-flow bounds; exact workload stopwatch origin not recorded",
                     "measurementEndElapsedBoundsSeconds": {"lower": maximum_outcome, "upper": upper},
                     "originalOutcomesDuringDrain": 0 if unsettled == 0 else None,
                     "uniqueDeliveriesDefinitelyBeforeEnd": sum(t <= maximum_outcome for t in first_receipt_times),
                     "uniqueDeliveriesDefinitelyAfterEnd": sum(t > upper for t in first_receipt_times),
                     "uniqueDeliveriesBoundaryUncertain": sum(maximum_outcome < t <= upper for t in first_receipt_times),
                     "allDeliveryEventsDefinitelyAfterEnd": sum(t > upper for t in all_delivery_times)}

    markers = {}
    marker_names = ("load-generator-started.json", "load-generator-measurement-started.json", "load-generator-measurement-ended.json")
    if set(marker_names) <= names:
        origin, start, end = [sealed.read_json(name) for name in marker_names]
        frequency = origin["timestampFrequency"]
        if frequency <= 0 or end["tick"] <= start["tick"]:
            raise ValueError("Invalid measurement clock markers")
        seconds = (end["tick"] - start["tick"]) / frequency
        utc_seconds = (datetime.fromisoformat(end["utc"]) - datetime.fromisoformat(start["utc"])).total_seconds()
        markers = {"monotonicMeasurementSeconds": seconds, "utcMeasurementSeconds": utc_seconds,
                   "utcMinusMonotonicMs": (utc_seconds - seconds) * 1000}
        if measurement is not None:
            differences.update(compare({"actualMeasurementSeconds": seconds}, measurement, "clock."))
    if expected_run is not None:
        if result.get("status") != "success" or key != expected_run["key"]:
            raise ValueError("Archive does not match published successful run")
        differences.update(compare(counts | rates, expected_run["observation"]["measurement"], "published."))
        if observation != expected_run["observation"]:
            differences["published.observationSnapshot"] = {"equal": False}
    sealed.verify_remaining()
    return {"version": VERSION, "archiveSha256": archive_sha, "status": result.get("status"),
            "failure": result.get("failure"), "identity": identity, "key": key, "attempt": result["attempt"],
            "sealedFilesVerified": len(sealed.verified), "eventCoverage": "complete",
            "recounted": counts, "rates": rates, "publishLatencyMs": ack,
            "endToEndFirstReceiptMs": quantiles(end_to_end), "latencyStatus": status,
            "endToEndDefinition": "first matching receiver event minus logged API-attempt timestamp; not ACK latency",
            "errorReasons": dict(error_reasons), "differences": differences,
            "anomalies": dict(anomalies), "unsettled": unsettled,
            "perPublisher": {str(k): dict(v) for k, v in per_publisher.items()},
            "completedByElapsedSecond": dict(sorted(completed_bins.items())),
            "deliveredByElapsedSecond": dict(sorted(delivery_bins.items())),
            "firstAttemptElapsedSeconds": None if not attempted else minimum_attempt,
            "lastOutcomeElapsedSeconds": maximum_outcome,
            "firstDeliveryElapsedSeconds": None if not all_delivery_times else first_delivery,
            "lastDeliveryElapsedSeconds": None if not all_delivery_times else last_delivery,
            "measurementClock": markers, "phaseCoverage": phase,
            "limits": ["Payload beyond ID and byte count not present in event logs",
                       "Receiver end-to-end latency starts at the logged attempt, before actual transport admission",
                       "Exact receiver measurement/drain boundary unavailable; only conservative bounds",
                       "External clock alignment is preserved evidence, not newly measured synchronization"]}
