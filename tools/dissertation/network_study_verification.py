"""Raw arithmetic for loaded studies; historical lossless verifier is unchanged.

The recount below retains the study.py definitions of cohorts, histograms,
latency and drain. Relay exports are checked structurally, without assuming
that every source publication reaches the Relay listener under overload.
The runner additionally checks lossless ideal conservation when applicable.
"""
import base64
from collections import Counter
import hashlib
from pathlib import Path

from network_review import evidence

read, seal, close, quantile = evidence.read, evidence.seal, evidence.close, evidence.quantile


def verify_loaded_relay(options, publications, transmissions):
    pairs = set()
    for t in transmissions:
        if t["role"] != "forward":
            continue
        p = publications[t["publicationId"]]
        assert t["site"] == p["origin"] and t["target"] != p["origin"]
        assert 0 <= t["target"] < options["brokers"]
        assert t["topic"] == f"federation/routing/{p['origin']}"
        pair = (p["id"], t["target"])
        assert pair not in pairs, "Relay reexported the same ID to a peer"
        pairs.add(pair)


def verify_loaded_run(directory):
    directory = Path(directory)
    seal(directory)
    s = read(directory / "summary.json")
    o = read(directory / "run-config.json")
    publications = {p["id"]: p for p in read(directory / "publications.json")}
    hashes = {identity: hashlib.sha256(base64.b64decode(p["payload"])).hexdigest() for identity, p in publications.items()}
    assert len({(p["origin"], p["sequence"]) for p in publications.values()}) == len(publications)
    transmissions = read(directory / "transmissions.json")
    for t in transmissions:
        p = publications[t["publicationId"]]
        assert t["measurement"] == p["measurement"] and t["payloadSha256"] == hashes[p["id"]]
        assert t["payloadBytes"] == o["payloadBytes"]
        assert t["topicPayloadBytes"] == len(t["topic"].encode("utf-8")) + t["payloadBytes"]
    if o.get("mode") == "relay":
        verify_loaded_relay(o, publications, transmissions)
    source = [t for t in transmissions if t["measurement"] and t["role"] == "source"]
    forwards = [t for t in transmissions if t["measurement"] and t["role"] == "forward"]
    assert len(source) == s["apiAttempts"] == sum(p["measurement"] for p in publications.values())
    assert len({t["publicationId"] for t in source}) == len(source)
    assert sum(not t["accepted"] for t in source) == s["sendRejected"]
    assert s["scheduledPublishes"] - len(source) == s["backpressureUnsent"] >= 0
    assert len(forwards) == s["forwardAttempts"]
    assert sum(t["accepted"] for t in forwards) == s["forwardAccepted"]
    assert sum(not t["accepted"] for t in forwards) == s["forwardRejected"]
    assert sum(t["topicPayloadBytes"] for t in forwards) == s["forwardTopicPayloadBytes"]
    receivers = {r["site"]: r for r in s["receivers"]}
    expected = len(source) * len(receivers)
    assert expected == s["expectedDeliveries"]
    seen = set()
    per_receiver = {r: Counter() for r in receivers}
    duplicates = Counter()
    before = 0
    drain = next((p["startTick"] for p in s["phases"] if p["name"] == "drain"), None)
    for receipt in read(directory / "delivery-receipts.json"):
        p = publications[receipt["publicationId"]]
        assert receipt["payloadSha256"] == hashes[p["id"]]
        assert receipt["topic"] == f"federation/delivered/{p['origin']}"
        assert receipt["tick"] >= p["tick"]
        if not p["measurement"]:
            continue
        pair = (p["id"], receipt["receiver"])
        assert pair[1] in receivers
        if pair in seen:
            duplicates[pair[1]] += 1
        else:
            seen.add(pair)
            per_receiver[pair[1]][receipt["tick"] - p["tick"]] += 1
            before += drain is not None and receipt["tick"] < drain
    assert len(seen) == s["uniqueDeliveries"]
    if drain is not None:
        assert before == s["uniqueBeforeDrain"] and len(seen) - before == s["uniqueDuringDrain"]
    assert sum(duplicates.values()) == s["duplicateDeliveries"]
    rh = Counter()
    for site, receiver in receivers.items():
        hist = {str(t): n for t, n in per_receiver[site].items()}
        assert hist == receiver["latencyHistogramTicks"]
        assert sum(hist.values()) == receiver["uniqueDeliveries"]
        assert duplicates[site] == receiver["duplicates"]
        rh.update(per_receiver[site])
    assert {str(t): n for t, n in rh.items()} == s["receiverHistogramTicks"]
    fully = sum(p["measurement"] and all((p["id"], r) in seen for r in receivers) for p in publications.values()) if receivers else None
    assert fully == s["fullyDeliveredPublications"]
    ph = Counter()
    completed = 0
    for p in s["sources"]:
        m = p["metrics"]
        assert 0 <= m["completed"] <= m["admitted"] <= m["attempted"]
        assert m["completed"] + m["publishFailed"] + m["rateRejected"] <= m["attempted"]
        completed += m["completed"]
        ph.update({int(t): n for t, n in m["latencyHistogramTicks"].items()})
    assert completed == s["sourceCompleted"] == s["sourceCompletedBeforeDrain"] + s["sourceCompletedDuringDrain"]
    assert {str(t): n for t, n in ph.items()} == s["publishHistogramTicks"]
    if o["qos"]:
        assert sum(ph.values()) == completed
    for label, h in (("publish", ph), ("receiver", rh)):
        for percent in (50, 95, 99):
            expected_q = None if label == "publish" and o["qos"] == 0 else quantile(h, percent / 100, s["tickMilliseconds"])
            close(s[f"{label}P{percent}Ms"], expected_q)
    close(s["deliveryLoss"], 1 - len(seen) / expected if expected else None)
    if s["status"] == "completed":
        close(s["sourceCompletedRps"], completed / o["measurementSeconds"])
        close(s["uniqueGoodput"], len(seen) / o["measurementSeconds"])
    return s
