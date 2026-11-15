"""Point-wise accuracy and measurement-window analysis from frozen evidence only."""
import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foundation import BROKERS, ROOT, read, save, sha, verify


def key(row):
    return row.get("messageBytes", row.get("bytes")), row["qos"], row["publishers"]


def native_point(rows):
    if sorted(r["key"]["repeat"] for r in rows) != [1, 2, 3]:
        raise ValueError("Each native point requires exactly repeats 1/2/3")
    ms = [r["observation"]["measurement"] for r in rows]
    loss = [m["deliveryLossRate"] for m in ms]
    return {"completedRps": statistics.mean(m["completedRps"] for m in ms),
            "publishFailureRate": statistics.mean(m["publishFailureRate"] for m in ms),
            "deliveryLossRate": statistics.mean(loss),
            "deliveryLossRange": max(loss) - min(loss),
            "attemptDeliveryFraction": statistics.mean(m["uniqueDeliveries"] / m["attemptedPublishes"] for m in ms),
            "durationMin": min(m["actualMeasurementSeconds"] for m in ms),
            "durationMax": max(m["actualMeasurementSeconds"] for m in ms)}


def aggregate(native, models, audit):
    selected = {r["archive"]["archivePath"]: r for r in native["runs"]}
    raw = {r["archivePath"]: r["scientific"] for r in audit["results"] if r["selectedPublishedRun"]}
    if selected.keys() != raw.keys():
        raise ValueError("Raw audit does not match the selected native set")
    point_rows, criteria_rows, failures, raw_rows, broker_rows = [], [], [], [], []
    for broker in BROKERS:
        points = {}
        for stage in ("calibration", "validation"):
            for k in sorted({key(r["key"]) for r in native["runs"] if r["broker"] == broker}):
                runs = [r for r in native["runs"] if r["broker"] == broker and r["stage"] == stage and key(r["key"]) == k]
                points[stage, k] = native_point(runs)
        selected_models = [r for r in models[broker] if r["stage"] == "holdout"]
        for k in sorted(k for stage, k in points if stage == "validation"):
            runs = [r for r in selected_models if key(r) == k]
            if sorted(r["seed"] for r in runs) != [42, 43, 44]:
                raise ValueError("Missing or duplicate model seed")
            cal, val = points["calibration", k], points["validation", k]
            row = {"broker": broker, "bytes": k[0], "qos": k[1], "publishers": k[2],
                   "failedSeeds": sum(any(c["status"] == "failed" for c in r["summary"]["acceptanceCriteria"]) for r in runs),
                   "nativeRpsCalibration": cal["completedRps"], "nativeRpsValidation": val["completedRps"],
                   "nativeLossCalibration": cal["deliveryLossRate"], "nativeLossValidation": val["deliveryLossRate"],
                   "nativeLossRepeatRange": val["deliveryLossRange"],
                   "nativeFailureValidation": val["publishFailureRate"],
                   "nativeAttemptDeliveryValidation": val["attemptDeliveryFraction"],
                   "nativeRpsShiftRelative": None if cal["completedRps"] == 0 else abs(val["completedRps"] - cal["completedRps"]) / cal["completedRps"]}
            point_rows.append(row)
        for run in selected_models:
            bad = []
            for criterion in run["summary"]["acceptanceCriteria"]:
                criteria_rows.append({"broker": broker, "key": run["key"], "bytes": run["bytes"],
                                      "qos": run["qos"], "publishers": run["publishers"], "seed": run["seed"], **criterion})
                if criterion["status"] == "failed": bad.append(criterion["name"])
            if bad:
                failures.append({"broker": broker, "key": run["key"], "failedCriteria": ";".join(sorted(bad)),
                                 "bytes": run["bytes"], "qos": run["qos"], "publishers": run["publishers"], "seed": run["seed"]})
        error_reasons = Counter()
        for path, run in selected.items():
            if run["broker"] != broker: continue
            s = raw[path]
            phases = s["phaseCoverage"]
            m = run["observation"]["measurement"]
            if run["stage"] == "validation": error_reasons.update(s["errorReasons"])
            delivered = s["recounted"]["uniqueDeliveries"]
            after, uncertain = phases["uniqueDeliveriesDefinitelyAfterEnd"], phases["uniqueDeliveriesBoundaryUncertain"]
            if phases["uniqueDeliveriesDefinitelyBeforeEnd"] + after + uncertain != delivered:
                raise ValueError("Receiver phase partitions disagree")
            raw_rows.append({"broker": broker, "stage": run["stage"], "key": run["key"]["key"],
                             "durationSeconds": m["actualMeasurementSeconds"], "uniqueDeliveries": delivered,
                             "definitelyAfterMeasurement": after, "boundaryUncertain": uncertain,
                             "fractionDefinitelyAfter": after / delivered if delivered else None,
                             "fractionPossiblyAfter": (after + uncertain) / delivered if delivered else None,
                             "originalOutcomesDuringDrain": phases["originalOutcomesDuringDrain"],
                             "endToEndP99Ms": (s["endToEndFirstReceiptMs"] or {}).get("p99Ms"),
                             "archivePath": path})
        bs = [r for r in raw_rows if r["broker"] == broker and r["stage"] == "validation"]
        bp = [r for r in point_rows if r["broker"] == broker]
        broker_rows.append({"broker": broker, "holdoutFailed": sum(r["broker"] == broker for r in failures),
                            "nativePointsLossRangeAbove005": sum(r["nativeLossRepeatRange"] > .05 for r in bp),
                            "nativePointsRpsShiftAbove015": sum(r["nativeRpsShiftRelative"] is not None and r["nativeRpsShiftRelative"] > .15 for r in bp),
                            "nativeMaxPossiblyAfterFraction": max(r["fractionPossiblyAfter"] or 0 for r in bs),
                            "errorReasons": dict(sorted(error_reasons.items()))})
    if len(point_rows) != 96 or len(criteria_rows) != 1152 or len(raw_rows) != 576:
        raise ValueError("Incorrect coverage")
    return {"points": point_rows, "criteria": criteria_rows, "failures": failures,
            "rawWindows": raw_rows, "brokers": broker_rows}


def csv_file(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def plot(result, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({"svg.hashsalt": "mqtty-accuracy-v1", "font.size": 10})
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharex=True, sharey=True, constrained_layout=True)
    labels = [(size, qos) for size in (16, 256) for qos in (0, 1, 2)]
    for broker, ax in zip(BROKERS, axes.flat):
        rows = [r for r in result["points"] if r["broker"] == broker]
        matrix = np.array([[next(r["failedSeeds"] for r in rows if (r["bytes"], r["qos"], r["publishers"]) == (size, qos, n))
                            for n in (1, 16, 64, 128)] for size, qos in labels])
        image = ax.imshow(matrix, cmap="YlOrRd", vmin=0, vmax=3, aspect="auto")
        ax.set_title(broker)
        ax.set_xticks(range(4), [1, 16, 64, 128]); ax.set_yticks(range(6), [f"{size} Б, QoS {q}" for size, q in labels])
        ax.set_xlabel("Publishers"); ax.set_ylabel("Точка нагрузки")
        for y in range(6):
            for x in range(4): ax.text(x, y, str(matrix[y, x]), ha="center", va="center", color="white" if matrix[y, x] >= 2 else "black")
    fig.colorbar(image, ax=axes, ticks=[0, 1, 2, 3], label="Seeds с хотя бы одним failed критерием, из трёх")
    fig.suptitle("Независимый контроль: все 96 точек, пороги не изменены")
    fig.savefig(output / "holdout-map.svg", metadata={"Date": None})
    fig.savefig(output / "holdout-map.png", dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(9, 4), constrained_layout=True)
    for i, broker in enumerate(BROKERS):
        values = sorted(r["nativeLossRepeatRange"] for r in result["points"] if r["broker"] == broker)
        ax.plot(range(1, 25), values, marker=".", label=broker)
    ax.axhline(.05, color="black", linestyle="--", label="0,05 — диагностический ориентир")
    ax.set(xlabel="Ранг точки по разбросу (у каждого брокера свой порядок)", ylabel="max(loss) − min(loss), три повтора", title="Разброс натурной недоставки в независимой серии")
    ax.grid(alpha=.2); ax.legend()
    fig.savefig(output / "native-loss-spread.svg", metadata={"Date": None})
    fig.savefig(output / "native-loss-spread.png", dpi=160); plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError("Output exists; use a new analysis directory")
    foundation = ROOT / "reports/audits/dissertation/foundation-v2/manifest.json"
    verify(foundation, ROOT, args.native_audit)
    sources = [ROOT / "reports/data/native-evidence.json", ROOT / "reports/data/model-evidence.json", args.native_audit]
    result = aggregate(*(read(p) for p in sources))
    args.output.mkdir(parents=True)
    for name in ("points", "criteria", "failures", "rawWindows"):
        csv_file(args.output / (name + ".csv"), result[name])
    save(args.output / "analysis.json", result)
    plot(result, args.output)
    save(args.output / "manifest.json", {"method": "Point-wise unchanged holdout criteria and native repeat/window context; no causal identification",
         "foundationManifestSha256": sha(foundation), "sourceSha256": {str(p): sha(p) for p in sources},
         "codeSha256": sha(Path(__file__)), "holdoutRuns": 288, "nativeRuns": 576,
         "notNewExperiments": True, "files": {p.name: sha(p) for p in sorted(args.output.iterdir()) if p.is_file()}})
    print(json.dumps({"failedHoldoutRuns": len(result["failures"]), "brokerContext": result["brokers"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
