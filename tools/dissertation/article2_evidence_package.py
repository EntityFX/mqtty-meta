"""Recount compact article-2 evidence; never run or change experiments."""
import argparse
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
SHORT = Path("reports/data/federation-full-mesh-v1")
METRICS = ("sourceCompletedRps", "uniqueGoodput", "deliveryLoss", "receiverP99Ms",
           "publishP99Ms", "forwardAttempts", "suppressedDuplicates")


def close(a, b):
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a == b
    return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)


def check_run(row):
    s = row["summary"]
    errors = []
    def same(name, actual, expected):
        if not close(actual, expected):
            errors.append(f'{row["id"]}:{name}: {actual!r} != {expected!r}')
    same("status", s["status"], "completed")
    same("sourceCompleted", s["sourceCompleted"], sum(x["metrics"]["completed"] for x in s["sources"]))
    same("apiAttempts", s["apiAttempts"], sum(x["metrics"]["attempted"] for x in s["sources"]))
    same("expectedDeliveries", s["expectedDeliveries"], s["apiAttempts"] * len(s["receivers"]))
    same("uniqueDeliveries", s["uniqueDeliveries"], sum(x["uniqueDeliveries"] for x in s["receivers"]))
    same("sourceDrain", s["sourceCompleted"], s["sourceCompletedBeforeDrain"] + s["sourceCompletedDuringDrain"])
    same("uniqueDrain", s["uniqueDeliveries"], s["uniqueBeforeDrain"] + s["uniqueDuringDrain"])
    same("sourceCompletedRps", s["sourceCompletedRps"], s["sourceCompleted"] / s["measurementSeconds"])
    same("uniqueGoodput", s["uniqueGoodput"], s["uniqueDeliveries"] / s["measurementSeconds"])
    same("deliveryLoss", s["deliveryLoss"], 1-s["uniqueDeliveries"]/s["expectedDeliveries"])
    same("forwardAttempts", s["forwardAttempts"], s["forwardAccepted"] + s["forwardRejected"])
    if row["qos"] == 0:
        same("qos0Ack", s["publishP99Ms"], None)
    return errors


def check_campaign(p):
    m, r, a = p["counts"]["matrix"], p["counts"]["replay"], p["attempts"]["matrix"]
    assert p["verified"] == m["done"]
    assert a["total"] - a["failed"] == m["done"]
    assert 0 <= m["unresolvedErrors"] <= m["total"]-m["done"]
    return {"status": p["status"], "verifiedCases": m["done"], "plannedCases": m["total"],
            "coveragePercent": 100*m["done"]/m["total"],
            "notVerifiedCases": m["total"]-m["done"],
            "unresolvedErrorCases": m["unresolvedErrors"],
            "otherNotVerifiedCases": m["total"]-m["done"]-m["unresolvedErrors"],
            "attempts": a["total"], "failedAttempts": a["failed"],
            "replaysDone": r["done"], "replaysPlanned": r["total"],
            "acceptedAsComplete": m["done"] == m["total"] and r["done"] == r["total"] and not m["unresolvedErrors"],
            "perCaseCoverageAvailableLocally": False}


def build(output):
    inputs = [SHORT / x for x in ("runs.json", "aggregates.json", "study-plan.json", "verification.json")]
    inputs += [Path("reports/status/network-full-3h-progress.json")]
    runs, aggregates, plan, old_check, progress = [json.loads((ROOT/p).read_text(encoding="utf-8")) for p in inputs]
    expected = set(itertools.product(("ideal", "mosquitto"), ("single", "relay", "federation"), range(3), (42,43,44)))
    actual = [(r["family"], r["mode"], r["qos"], r["seed"]) for r in runs]
    assert len(actual) == len(set(actual)) == 54 and set(actual) == expected
    assert {r["id"] for r in runs} == {r["id"] for r in plan["cases"]}
    groups = [(a["family"], a["mode"], a["qos"]) for a in aggregates]
    assert len(groups) == len(set(groups)) == 18
    assert set(groups) == {x[:3] for x in expected}
    errors = [error for row in runs for error in check_run(row)]
    recalculated = []
    aggregate_values = 0
    for a in aggregates:
        rows = [r for r in runs if all(r[k] == a[k] for k in ("family", "mode", "qos"))]
        assert len(rows) == 3 and a["successful"] == 3 and a["failed"] == 0
        out = {k:a[k] for k in ("family", "mode", "qos")}
        for metric in METRICS:
            values = [r["summary"][metric] for r in rows if r["summary"][metric] is not None]
            mean = statistics.mean(values) if values else None
            ci = 4.302652729911275 * statistics.stdev(values) / math.sqrt(3) if len(values) == 3 else None
            for field,value in (("n",len(values)),("mean",mean),("ci95",ci)):
                aggregate_values += 1
                if not close(value,a[metric][field]):
                    errors.append(f'{out}:{metric}.{field}')
            out[metric] = mean
        for metric in ("forwardTopicPayloadBytes", "uniqueDuringDrain", "sourceCompletedDuringDrain", "queueDrops", "duplicateDeliveries"):
            out[metric] = statistics.mean(r["summary"][metric] for r in rows)
        out["receiverP99ApplicableRuns"] = sum(r["summary"]["receiverP99Ms"] is not None for r in rows)
        recalculated.append(out)
    assert not errors, errors
    campaign = check_campaign(progress)
    assert len(old_check["replays"]) == 6 and all(x["identical"] for x in old_check["replays"])
    result = {"status":"compactArithmeticVerified", "scope":"Stored summaries only; no new raw-ID or replay execution",
              "arithmeticTolerance":{"relative":1e-10,"absolute":1e-12,"scientificThresholdsChanged":False},
              "shortCases":54,"shortAggregateGroups":18,"aggregateValuesChecked":aggregate_values,
              "shortHistoricalReplayReceiptCount":6,"longCampaign":campaign,
              "step23Accepted":False,"step24Accepted":False,"humanScientificReviewPerformed":False,
              "errors":errors}
    output.mkdir(parents=True, exist_ok=False)
    def write(name, text):
        (output/name).write_bytes(text.encode("utf-8"))
    write("verification.json", json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    with (output/"comparison.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(recalculated[0]))
        writer.writeheader(); writer.writerows(recalculated)
    lines=["# Проверенные значения короткой серии", "", "Средние трёх seed, кроме p99: усредняются только запуски с получениями, их число указано отдельно. Это среднее отдельных p99, а не p99 объединённой выборки. Недоставка — доля; p99 — мс; объём — topic+payload, не полный сетевой трафик.", "", "| Модель | Режим | QoS | Завершения/с | Недоставка | p99 доставки | n p99 /3 | Пересылки | Байт пересылки | Доставки в ожидании |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for x in recalculated:
        lines.append(f'| {x["family"]} | {x["mode"]} | {x["qos"]} | {x["sourceCompletedRps"]:.3f} | {x["deliveryLoss"]:.6f} | {x["receiverP99Ms"]:.3f} | {x["receiverP99ApplicableRuns"]} | {x["forwardAttempts"]:.3f} | {x["forwardTopicPayloadBytes"]:.3f} | {x["uniqueDuringDrain"]:.3f} |')
    write("Comparison.md","\n".join(lines)+"\n")
    sources={p.as_posix():hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in inputs}
    for p in (Path("docs/dissertation/articles/article-2-relay-federation.md"),
              Path("reports/analysis/Article-2-evidence-package.md"),
              Path("tools/dissertation/test_article2_evidence_package.py")):
        sources[p.as_posix()] = hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
    sources[Path(__file__).relative_to(ROOT).as_posix()]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write("manifest.json",json.dumps({"inputs":sources,"outputs":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}},indent=2)+"\n")
    print(json.dumps(result,ensure_ascii=False))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    build(parser.parse_args().output)
