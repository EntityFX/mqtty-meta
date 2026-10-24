"""Audit the numeric tables and evidence links in the working twin article."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTICLE = ROOT / "docs/dissertation/articles/article-1-twin-validation.md"
AUDIT = ROOT / "reports/audits/dissertation/article-1-draft-v1"


def verify(text, accuracy, sensitivity):
    counts = {}
    for broker in accuracy["brokers"]:
        name = broker["broker"]
        failed = {r["key"] for r in accuracy["failures"] if r["broker"] == name}
        criteria = {key: sum(r["broker"] == name and r["name"] == key and r["status"] == "failed"
                             for r in accuracy["criteria"])
                    for key in ("rps", "latencyP99Ms", "publishFailureRate", "deliveryLossRate")}
        expected = [72-len(failed), len(failed), *criteria.values()]
        line = "| " + name + " | " + " | ".join(str(v) for v in expected) + " |"
        if line not in text:
            raise ValueError("Incorrect or missing accuracy row: " + name)
        counts[name] = expected
        native_points = {(p['bytes'], p['qos'], p['publishers']): p for p in accuracy['points'] if p['broker'] == name}
        contextual = sum(native_points[(r['bytes'], r['qos'], r['publishers'])]['nativeLossRepeatRange'] > .05
                         for r in accuracy['failures'] if r['broker'] == name)
        context_line = f"| {name} | {broker['nativePointsLossRangeAbove005']} | {broker['nativePointsRpsShiftAbove015']} | {contextual}/{len(failed)} |"
        if context_line not in text:
            raise ValueError('Incorrect native context row: ' + name)
        variants = ['baseline', 'halfTick', 'doubleWindow']
        passed = [18-sum(r['broker'] == name and r['kind'] == 'matrix' and r['variant'] == v
                         for r in sensitivity['scientificFailures']) for v in variants]
        if '| ' + name + ' | ' + ' | '.join(map(str, passed)) + ' |' not in text:
            raise ValueError('Incorrect sensitivity row: ' + name)
    phrases = ("203 из 288", "85 запусках", "576 натурных", "1152 решения", "96 неприменимых")
    if any(phrase not in text for phrase in phrases):
        raise ValueError("Core coverage statement missing")
    paired = sensitivity["pairedSummary"]
    for variant, metric in (("halfTick", "completedRps"), ("halfTick", "latencyP99Ms"),
                           ("doubleWindow", "completedRps"), ("doubleWindow", "latencyP99Ms")):
        value = 100*paired[variant]["metrics"][metric]["maxAbsoluteRelativeDelta"]
        if f"{value:.3f}".replace(".", ",") + "%" not in text:
            raise ValueError("Sensitivity maximum missing: " + variant + "/" + metric)
    required = ("не установила слепую независимость", "не является вероятностью",
                "не заменяют независимую проверку другим сотрудником", "notApplicable")
    if any(phrase not in text for phrase in required):
        raise ValueError("Required scientific limitation missing")
    return counts


def main():
    source_paths = {
        "article": ARTICLE,
        "accuracy": ROOT / "reports/audits/dissertation/accuracy-analysis-v1/analysis.json",
        "sensitivity": ROOT / "reports/audits/dissertation/sensitivity-final-v1/analysis/analysis.json",
        "method": ROOT / "docs/dissertation/broker-twin-method-map.md",
        "history": ROOT / "reports/audits/dissertation/control-history-v1/evidence.json",
    }
    raw = {k: p.read_bytes() for k, p in source_paths.items()}
    text = raw["article"].decode("utf-8")
    rows = verify(text, json.loads(raw["accuracy"]), json.loads(raw["sensitivity"]))
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    for name in images:
        if not (ARTICLE.parent / name).is_file():
            raise ValueError("Missing article figure: " + name)
    result = {
        "status": "technicalDraftReviewed", "scientificAuthorApproval": False,
        "submissionPerformed": False, "protectedDissertationChanged": False,
        "accuracyRowsVerified": rows, "sensitivityMaximaVerified": 4,
        "figureLinksVerified": len(images),
        "reviewerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sources": {p.relative_to(ROOT).as_posix(): hashlib.sha256(raw[k]).hexdigest()
                    for k, p in source_paths.items()},
        "primarySources": [
            {"url": "https://www.informs-sim.org/wsc11papers/016.pdf", "scope": "Verification, data validity and domain-specific validation"},
            {"url": "https://publicatio.bibl.u-szeged.hu/22708/1/MQTT_Perf_Mishra_MDPI_202109_revised_v2.pdf", "scope": "Author version: native broker performance comparison, not our quantitative benchmark"},
            {"url": "https://link.springer.com/article/10.1186/s43067-024-00181-x", "scope": "Conceptual MQTT model and OMNeT++/INET application evaluation"},
        ],
        "limitations": ["Not an exhaustive novelty search", "No journal-specific format selected", "Author and coauthor review pending"],
    }
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / "verification.json").write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print("PASS: four accuracy rows, four sensitivity maxima, coverage, limitations and figure links")


if __name__ == "__main__":
    main()
