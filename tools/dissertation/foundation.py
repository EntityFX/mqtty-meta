"""Freeze and verify the evidence foundation; never rerun or modify a campaign."""
import argparse
import csv
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_audit import compare
from run_native_audit import counts, validate_published_matrix


ROOT = Path(__file__).resolve().parents[2]
BROKERS = ("Aedes", "Mosquitto", "ActiveMQ", "EMQX")
VERSION = 1


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    with Path(path).open("rb") as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
        return value.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def inside(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Path escapes evidence root: " + relative)
    return target


def observation_identities(data):
    return {(broker, stage): read(data / broker / stage / "observations.json")["provenance"]["identity"]
            for broker in BROKERS for stage in ("calibration", "validation")}


def check_raw_audit(audit, native, registry, identities):
    """Bind a completed event recount to every selected archive, not just totals."""
    rows = audit["results"]
    by_path = {r["archivePath"]: r for r in rows}
    if len(by_path) != len(rows):
        raise ValueError("Duplicate audit archive")
    registered = {r.get("linuxArchivePath", r["archivePath"]): r["sha256"] for r in registry}
    if len(registered) != len(registry) or set(by_path) != set(registered):
        raise ValueError("Audit and registry archive sets differ")
    selected = {r["archive"]["archivePath"]: r for r in native["runs"]}
    if len(selected) != len(native["runs"]):
        raise ValueError("Duplicate selected archive")
    if not set(selected) <= set(by_path):
        raise ValueError("Selected archive missing from audit")
    for path, row in by_path.items():
        if row["error"] or row["expectedSha256"] != registered[path]:
            raise ValueError("Audit failed or wrong archive hash: " + path)
        s = row["scientific"]
        if s["archiveSha256"] != registered[path] or row["selectedPublishedRun"] != (path in selected):
            raise ValueError("Wrong selected archive provenance: " + path)
        if path in selected:
            expected = selected[path]
            if expected["archive"]["sha256"] != registered[path]:
                raise ValueError("Published archive hash differs: " + path)
            if s["key"] != expected["key"] or s["identity"] != identities[expected["broker"], expected["stage"]]:
                raise ValueError("Published run identity differs: " + path)
            if s["status"] != "success" or s["eventCoverage"] != "complete":
                raise ValueError("Selected run lacks successful event coverage: " + path)
            if s["differences"] or s["anomalies"] or s["unsettled"]:
                raise ValueError("Selected raw recount differs: " + path)
            measurement = expected["observation"]["measurement"]
            if compare(s["recounted"] | s["rates"], measurement):
                raise ValueError("Recounted counters/rates disagree with publication: " + path)
            if s["publishLatencyMs"] != measurement["publishLatencyMs"]:
                raise ValueError("Recounted ACK quantiles disagree with publication: " + path)
    actual = counts(rows, len(registry))
    if actual != audit["counts"] or actual["selectedPassed"] != len(selected):
        raise ValueError("Audit summary counters differ")
    manifest = audit["manifest"]
    if manifest["pilotLimit"] is not None or manifest["archives"] != len(registry) or manifest["publishedRuns"] != len(selected):
        raise ValueError("Audit is a pilot or incomplete matrix")
    return actual


def profile_registry(root, data):
    result = []
    state = read(data / "state.json")
    for broker in BROKERS:
        profile = data / broker / "profile/broker-calibration.v3.json"
        observed = data / broker / "calibration/observations.json"
        held = data / broker / "validation/observations.json"
        p, a, b = read(profile), read(observed), read(held)
        if sha(profile) != state["profiles"][broker] or p["provenance"]["observationsSha256"] != sha(observed):
            raise ValueError("Profile or calibration observations changed: " + broker)
        original = p["provenance"]["observations"]["identity"]
        if original != a["provenance"]["identity"]:
            raise ValueError("Profile calibration identity differs: " + broker)
        if original["campaignId"] == b["provenance"]["identity"]["campaignId"]:
            raise ValueError("Calibration and validation identity merged: " + broker)
        result.append({"broker": broker, "path": profile.relative_to(root).as_posix(),
                       "sha256": sha(profile), "method": p["provenance"]["method"],
                       "calibrationIdentity": original,
                       "validationIdentity": b["provenance"]["identity"],
                       "calibrationObservationsSha256": sha(observed),
                       "validationObservationsSha256": sha(held),
                       "points": len(p["calibration"]["points"]),
                       "interpolationAllowed": False, "holdoutRetuningAllowed": False})
    return result


def collect_files(root, paths):
    result = {}
    for path in sorted(set(Path(p).resolve() for p in paths)):
        relative = path.relative_to(root.resolve()).as_posix()
        if path.suffix.lower() in (".zip", ".tar", ".gz", ".7z"):
            raise ValueError("Archives must stay outside the Git evidence package")
        result[relative] = {"sha256": sha(path), "bytes": path.stat().st_size}
    return result


def verify_files(root, entries):
    for name, entry in entries.items():
        path = inside(root, name)
        if not path.is_file() or sha(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("Frozen input changed: " + name)
    return len(entries)


def arithmetic(root):
    path = root / "tools/campaign/assess-evidence.py"
    spec = importlib.util.spec_from_file_location("foundation_assessor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.assess(root / "reports", None)


def equivalent(actual, expected):
    """Allow scalar roundoff across Python runtimes, never different decisions/counts."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(
            equivalent(actual[k], value) for k, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            equivalent(a, e) for a, e in zip(actual, expected))
    if isinstance(expected, float):
        return (not isinstance(actual, bool) and isinstance(actual, (int, float)) and
                math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10))
    return type(actual) is type(expected) and actual == expected


def coverage_csv(audit, selected):
    stream = io.StringIO(newline="")
    fields = ["broker", "stage", "messageBytes", "qos", "publishers", "repeat", "selected",
              "status", "eventCoverage", "sealedFilesVerified", "failure", "archivePath", "sha256"]
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in sorted(audit["results"], key=lambda r: r["archivePath"]):
        s, path = row["scientific"], row["archivePath"]
        key = s["key"]
        writer.writerow({**{k: key[k] for k in ("broker", "messageBytes", "qos", "publishers", "repeat")},
                         "stage": selected[path]["stage"] if path in selected else "outside-selected-matrix",
                         "selected": row["selectedPublishedRun"], "status": s["status"],
                         "eventCoverage": s["eventCoverage"], "sealedFilesVerified": s["sealedFilesVerified"],
                         "failure": s.get("failure"), "archivePath": path, "sha256": row["expectedSha256"]})
    return stream.getvalue()


def freeze(root, target, raw_path):
    root, target = Path(root).resolve(), Path(target).resolve()
    if target.exists():
        raise ValueError("Output already exists; verify it or choose a new evidence version")
    data = root / "reports/data"
    native, registry, audit = read(data / "native-evidence.json"), read(data / "archive-registry.json"), read(raw_path)
    validate_published_matrix(native)
    checked = check_raw_audit(audit, native, registry, observation_identities(data))
    manifest = audit["manifest"]
    if manifest["nativeEvidenceSha256"] != sha(data / "native-evidence.json") or manifest["registrySha256"] != sha(data / "archive-registry.json"):
        raise ValueError("Audit input snapshot changed")
    for name, expected in manifest["sourceSha256"].items():
        if sha(root / "tools/dissertation" / name) != expected:
            raise ValueError("Raw auditor source changed; preserve its original version: " + name)
    remote = read(root / "reports/status/linux-native-audit-summary.json")
    if remote["status"] != "completed" or remote["remoteSummary"]["sha256"] != sha(raw_path) or remote["counts"] != checked:
        raise ValueError("Downloaded summary differs from completed Linux audit")
    calculated, profiles = arithmetic(root), profile_registry(root, data)
    # Historical publication hashes are independently checked by the assessor.
    paths = [data / name for name in read(data / "final-evidence-sha256.json")["files"]]
    # The original publication manifest deliberately predates derived EST/replay
    # outputs. Bind the assessor's complete declared dependency set as well.
    paths += [root / "reports" / name for name in calculated["sourceSha256"]]
    paths += [data / "final-evidence-sha256.json", data / "archive-registry.json",
              root / "reports/status/linux-native-audit-summary.json",
              root / "docs/overview/measurement-contract.md",
              root / "docs/dissertation/foundation-reproduction.md"]
    paths += list((root / "tools/dissertation").glob("*.py"))
    paths += [root / "tools/campaign/assess-evidence.py", root / "tools/campaign/render-final-reports.py"]
    for folder in ("federation-full-mesh-v1", "federation-transit-10-v1", "load-balancing-study-v1"):
        paths += [p for p in (data / folder).rglob("*") if p.is_file() and p.name != "publication-sha256.json" and p.suffix.lower() not in (".md", ".html")]
    source_names = [
        "MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Workload/DeliveryLedger.cs",
        "MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Workload/MeasurementSummary.cs",
        "MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/Workload/CampaignWorkloadRunner.cs",
        "MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign/CampaignAggregator.cs",
        "MqttBenchmark/src/EntityFX.MqttBenchmark/Calibration/MetricStatistics.cs",
        "MqttBenchmark/src/EntityFX.MqttBenchmark/Calibration/LatencyQuantiles.cs",
        "mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Mqtt/Experiments/MqttRelayExperimentRunner.cs",
        "mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/Results/FederationSummary.cs",
    ]
    paths += [root / p for p in source_names]
    files = collect_files(root, paths)
    roles = {"native": "576 selected runs; calibrated and independent observations remain separate",
             "brokerModels": "576 BrokerFidelity simulations with unchanged profile hashes",
             "relay": "360 model runs; no native network accuracy established",
             "additionalNetworks": "short federation/load-balancing mechanism studies, not steady-state throughput proof"}
    registry_out = {"version": VERSION, "profiles": profiles, "roles": roles,
                    "criteria": {"rpsRelative": .15, "p99Relative": .20, "failureAbsolute": .05, "lossAbsolute": .05},
                    "humanScientificAcceptance": "pending", "interpolateMissingPoints": False,
                    "rawArchiveRegistry": "reports/data/archive-registry.json",
                    "rawAuditSha256": sha(raw_path), "rawAuditRemotePath": remote["remoteSummary"]["path"],
                    "limits": remote["limits"], "files": files}
    selected = {r["archive"]["archivePath"]: r for r in native["runs"]}
    verification = {"raw": checked, "arithmetic": calculated,
                    "scope": "Source-linked event recount plus independent published arithmetic; no new physical measurements",
                    "fullRawRecountProvidedSeparately": True,
                    "scientificAcceptanceByAuthor": False}
    target.mkdir(parents=True)
    save(target / "input-registry.json", registry_out)
    save(target / "metrics-verification.json", verification)
    (target / "native-coverage.csv").write_text(coverage_csv(audit, selected), encoding="utf-8", newline="\n")
    generated = collect_files(target, [p for p in target.iterdir() if p.is_file()])
    revisions = {name: subprocess.check_output(["git", "-C", str(root / name), "rev-parse", "HEAD"], text=True).strip()
                 for name in (".", "mqtty", "MqttBenchmark", "diss")}
    save(target / "manifest.json", {"version": VERSION, "status": "technicalFoundationVerified",
                                    "repositoriesAtFreeze": revisions, "packageFiles": generated,
                                    "rawAuditSha256": sha(raw_path), "humanScientificAcceptance": "pending"})
    return verify(target / "manifest.json", root, raw_path)


def verify(manifest_path, root=ROOT, raw_path=None):
    manifest_path, root = Path(manifest_path).resolve(), Path(root).resolve()
    manifest = read(manifest_path)
    if manifest["version"] != VERSION or manifest["status"] != "technicalFoundationVerified":
        raise ValueError("Unknown evidence foundation version/status")
    package_count = verify_files(manifest_path.parent, manifest["packageFiles"])
    registry = read(manifest_path.parent / "input-registry.json")
    frozen_count = verify_files(root, registry["files"])
    if registry["rawAuditSha256"] != manifest["rawAuditSha256"]:
        raise ValueError("Raw summary identity differs inside package")
    if raw_path is not None:
        if sha(raw_path) != manifest["rawAuditSha256"]:
            raise ValueError("Full raw audit summary changed")
        check_raw_audit(read(raw_path), read(root / "reports/data/native-evidence.json"),
                        read(root / "reports/data/archive-registry.json"), observation_identities(root / "reports/data"))
    if not equivalent(arithmetic(root), read(manifest_path.parent / "metrics-verification.json")["arithmetic"]):
        raise ValueError("Repeated arithmetic verification differs")
    return {"status": "verified", "packageFiles": package_count, "frozenFiles": frozen_count,
            "fullRawAuditVerified": raw_path is not None, "humanScientificAcceptance": "pending"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("freeze")
    new.add_argument("--output", type=Path, required=True)
    new.add_argument("--native-audit", type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--native-audit", type=Path)
    args = parser.parse_args()
    value = freeze(ROOT, args.output, args.native_audit) if args.command == "freeze" else verify(args.manifest, ROOT, args.native_audit)
    print(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    main()
