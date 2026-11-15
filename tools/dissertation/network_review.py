"""Read-only check of saved federation journals and comparison conditions."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/federation"))
import study as evidence
import transit_study


def physical_edges(o):
    n = o["brokers"]
    if o["topology"] == "star":
        return {tuple(sorted((f"site{i}", "transit"))) for i in range(n)}
    pairs = {(i, i + 1) for i in range(n - 1)}
    if o["topology"] == "ring" and n > 2:
        pairs.add((0, n - 1))
    if o["topology"] == "custom":
        pairs = {(e["a"], e["b"]) for e in o["links"]}
    return {tuple(sorted((f"site{a}", f"site{b}"))) for a, b in pairs}


def check_graph(path, o):
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    doc = ET.parse(path)
    nodes = {e.attrib["id"]: {d.attrib["key"]: d.text for d in e.findall("g:data", ns)}
             for e in doc.findall(".//g:node", ns)}
    networks = {k: v["label"] for k, v in nodes.items() if v["type"] == "Network"}
    edges = [(e.attrib["source"], e.attrib["target"]) for e in doc.findall(".//g:edge", ns)]
    names = {f"site{i}" for i in range(o["brokers"])}
    if o["topology"] == "star":
        names.add("transit")
    assert set(networks.values()) == names
    actual = {tuple(sorted((networks[a], networks[b]))) for a, b in edges if a in networks and b in networks}
    assert actual == physical_edges(o), "Physical graph differs from configuration"
    receivers = o["receiverSites"] if o["receiverSites"] is not None else list(range(o["brokers"]))
    clients = [(f"publisher{i:04d}", i % o["brokers"]) for i in range(o["publishers"])]
    clients += [(f"receiver{i:04d}", i) for i in receivers]
    for label, site in clients:
        ids = [k for k, v in nodes.items() if v["label"] == label]
        assert len(ids) == 1
        attached = {networks[b if a == ids[0] else a] for a, b in edges
                    if (a == ids[0] and b in networks) or (b == ids[0] and a in networks)}
        assert attached == {f"site{site}"}, "Client moved to a different site"
    brokers = [v for v in nodes.values() if v["type"] == "Server"]
    assert len(brokers) == (1 if o["mode"] == "single" else o["brokers"])
    if o["topology"] == "star":
        transit = next(k for k, v in networks.items() if v == "transit")
        assert all((a in networks and b in networks) for a, b in edges if transit in (a, b))


def signature(o):
    # Same logical task and physical resources; backend count deliberately differs.
    fields = ("brokers", "topology", "links", "publishers", "receiverSites", "payloadBytes", "qos",
              "offeredRps", "seed", "warmupSeconds", "measurementSeconds", "drainTimeoutSeconds",
              "tickPeriod", "queueCapacity", "cacheCapacity", "hopLimit", "ideal", "brokerType", "network")
    return {key: o[key] for key in fields}


def ideal_counts(o, s):
    if not o["ideal"]:
        return None
    n, p = o["brokers"], s["apiAttempts"]
    receivers = o["receiverSites"] if o["receiverSites"] is not None else list(range(n))
    assert s["status"] == "completed" and s["sourceCompleted"] == p
    assert s["uniqueDeliveries"] == s["expectedDeliveries"] == p * len(receivers)
    assert not any(s[k] for k in ("duplicateDeliveries", "queueDrops", "hopLimitDrops", "forwardRejected"))
    if o["mode"] == "single":
        forwards, suppressed = 0, 0
    elif o["mode"] == "relay":
        forwards, suppressed = p * (n - 1), 0
    else:
        edges = n * (n - 1) // 2 if o["topology"] == "star" else len(physical_edges(o))
        # With sufficient TTL and no losses: every first reception except the origin
        # omits precisely the arrival edge. All other directed edges are sent once.
        forwards = p * (2 * edges - (n - 1))
        suppressed = forwards - p * (n - 1)
    assert s["forwardAttempts"] == forwards, "Forwarding conservation failed"
    assert s["suppressedDuplicates"] == suppressed, "Duplicate suppression count differs"
    return {"sourcePublications": p, "expectedPairs": p * len(receivers),
            "forwardAttempts": forwards, "suppressedDuplicates": suppressed}


def check_comparison(options):
    assert {o["mode"] for o in options} == {"single", "relay", "federation"}
    assert len(options) == 3
    first = signature(options[0])
    assert all(signature(o) == first for o in options), "Comparison changes physical conditions or useful task"


def review(work, transit=False):
    plan = evidence.read(work / "study-plan.json")
    expected = {f"{family}-{mode}-q{qos}-s{seed}" for family in ("ideal", "mosquitto")
                for mode in (("relay",) if transit else ("single", "relay", "federation"))
                for qos in range(3) for seed in (42, 43, 44)}
    identities = [case["id"] for case in plan["cases"]]
    assert len(identities) == len(expected) and set(identities) == expected, "Incomplete or duplicated matrix"
    publication = "federation-transit-10-v1" if transit else "federation-full-mesh-v1"
    published = {r["id"]: r for r in evidence.read(ROOT / "reports/data" / publication / "runs.json")}
    assert set(published) == expected
    rows, groups = [], defaultdict(list)
    for case in plan["cases"]:
        directory = work / "runs" / case["id"]
        s = evidence.verify_run(directory)
        o = evidence.read(directory / "run-config.json")
        assert s == published[case["id"]]["summary"], "Raw result differs from published summary"
        family = "ideal" if o["ideal"] else "mosquitto"
        assert case["id"] == f"{family}-{o['mode']}-q{o['qos']}-s{o['seed']}"
        check_graph(directory / "topology.graphml", o)
        predicted = ideal_counts(o, s)
        if transit:
            transit_study.verify_network(directory)
        else:
            groups[(case["family"], case["qos"], case["seed"])].append(o)
        rows.append({"id": case["id"], "ideal": o["ideal"], "status": s["status"],
                     "summarySha256": evidence.sha(directory / "summary.json"), "counts": predicted})
    for options in groups.values():
        check_comparison(options)
    repeats = []
    for directory in sorted((work / "replays").iterdir()):
        evidence.verify_run(directory)
        original = work / "runs" / directory.name
        assert (directory / "summary.json").read_bytes() == (original / "summary.json").read_bytes()
        repeats.append(directory.name)
    assert len(repeats) == 6 and len(set(repeats)) == 6
    return {"mainRunsVerified": len(rows), "idealRunsVerified": sum(r["ideal"] for r in rows),
            "comparisonGroupsVerified": len(groups), "exactSummaryReplaysVerified": repeats,
            "studyPlanSha256": evidence.sha(work / "study-plan.json"),
            "publishedRunsSha256": evidence.sha(ROOT / "reports/data" / publication / "runs.json"),
            "historicalMqttYCommit": plan["sources"]["mqttYCommit"], "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh-work", type=Path, required=True)
    parser.add_argument("--transit-work", type=Path, required=True)
    parser.add_argument("--trx", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output directory")
    trx = ET.parse(args.trx)
    counters = next(e for e in trx.iter() if e.tag.endswith("}Counters"))
    assert int(counters.attrib["total"]) > 0
    assert counters.attrib["failed"] == "0" and counters.attrib["passed"] == counters.attrib["total"]
    result = {"status": "verified", "scope": "Technical mechanism review; not steady-state performance or physical network fidelity",
              "currentMqttYCommit": subprocess.check_output(["git", "-C", str(ROOT / "mqtty"), "rev-parse", "HEAD"], text=True).strip(),
              "targetedDotnetTests": dict(counters.attrib), "trxSha256": evidence.sha(args.trx),
              "mesh": review(args.mesh_work), "transit": review(args.transit_work, True)}
    result["sourceSha256"] = {str(p.relative_to(ROOT)): evidence.sha(p) for p in
                              [Path(__file__), ROOT / "tools/federation/study.py", ROOT / "tools/federation/transit_study.py",
                               ROOT / "mqtty/src/EntityFX.MqttSimulator/EntityFX.Tests.Integration/FederationTests.cs"]}
    args.output.mkdir(parents=True, exist_ok=False)
    evidence.write(args.output / "verification.json", result)
    print(json.dumps({"status": result["status"], "dotnetTests": counters.attrib["total"],
                      "mainRuns": result["mesh"]["mainRunsVerified"] + result["transit"]["mainRunsVerified"],
                      "comparisonGroups": result["mesh"]["comparisonGroupsVerified"],
                      "exactReplays": len(result["mesh"]["exactSummaryReplaysVerified"]) + len(result["transit"]["exactSummaryReplaysVerified"])}))


if __name__ == "__main__":
    main()
