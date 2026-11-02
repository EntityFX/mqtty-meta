"""Verify retrieved evidence without SSH, dotnet or rebuilding the simulator."""
import argparse
import hashlib
from pathlib import Path

import run as suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    root = args.report.resolve() / "evidence"
    registry = suite.read_json(root / "result-export-sha256.json")
    for name, expected in registry.items():
        path = (root / name).resolve()
        path.relative_to(root)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Retrieved file hash mismatch: {name}")
    if (root / "exit-code.txt").read_text().strip() != "0":
        raise RuntimeError("Remote suite did not exit successfully.")
    report = suite.read_json(root / "results/comparison.json")
    baseline = suite.read_json(root / "inputs/baseline-windows-smoke.json")
    environment = suite.read_json(root / "results/environment.json")
    package = suite.read_json(root / "package-manifest.json")
    assert report["identity"] == baseline["identity"] == environment["identity"]
    assert environment["buildOnThisMachine"] is False
    assert environment["appSha256"] == package["files"]["app/EntityFX.MqttY.MqttRelay.App.dll"]
    assert hashlib.sha256((root / "inputs/broker-benchmark.v2.json").read_bytes()).hexdigest() == report["identity"]["profileSha256"]
    assert report["cases"].keys() == baseline["cases"].keys()
    bundles_verified = 0
    for case, row in report["cases"].items():
        expected = baseline["cases"][case]
        config = suite.read_json(root / "results" / case / "config.json")
        spec = {key: config[key] for key in ("experiment", "ticks", "network")}
        fingerprints = []
        for repeat in (1, 2):
            bundles = list((root / "results" / case / f"repeat-{repeat}").iterdir())
            assert len(bundles) == 1
            bundle = bundles[0]
            manifest = suite.read_json(bundle / "experiment-manifest.json")
            summary = suite.read_json(bundle / "summary.json")
            assert summary["status"] == "completed" and summary["simulated"]["completed"] > 0
            provenance = manifest["provenance"]
            assert provenance["mqttYCommitSha"] == report["identity"]["mqttyCommit"]
            assert provenance["mqttBenchmarkCommitSha"] == report["identity"]["mqttBenchmarkCommit"]
            assert provenance["brokerProfileSha256"].lower() == report["identity"]["profileSha256"]
            fingerprint = {
                "configSha256": suite.sha(spec), "runtime": manifest["runtime"],
                "summarySha256": suite.sha(summary),
                "brokerCountersSha256": suite.sha(suite.read_json(bundle / "counters.json")["brokers"]),
                "histogramSha256": suite.sha(suite.read_json(bundle / "latency-histogram.json")),
                "phasesSha256": suite.sha(manifest["phases"]),
                "connectionsSha256": suite.sha(manifest["brokerClientCounts"]),
                "cliExitCode": 3 if any(item["status"] == "failed" for item in summary["acceptanceCriteria"]) else 0,
            }
            assert fingerprint == row["repeats"][repeat - 1], (case, repeat)
            fingerprints.append(fingerprint)
            bundles_verified += 1
        different = [key for key in expected if expected[key] != fingerprints[0][key]]
        assert fingerprints[0] == fingerprints[1] and row["localRepeatsMatch"]
        assert different == row["differentFields"] == ["runtime"], case
        assert row["scientificFingerprintsMatch"] and not row["runtimeMatches"]
    assert report["allLocalRepeatsMatch"] and report["allScientificFingerprintsMatch"]
    assert not report["allRuntimesMatch"]
    print(f"PASS: {len(registry)} retrieved file hashes, {bundles_verified} raw bundles, "
          f"{len(report['cases'])} baseline comparisons; only runtime differs.")


if __name__ == "__main__":
    main()
