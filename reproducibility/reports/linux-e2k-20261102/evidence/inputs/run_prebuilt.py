"""Run a locally published managed DLL on another machine, without building there."""
import argparse
import hashlib
import json
import platform
import re
import subprocess
from pathlib import Path

import run as suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, help="Exact profile bytes, independent of Git checkout line endings")
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    app = args.app.resolve()
    profile = args.profile.resolve() if args.profile else suite.MQTTY / suite.PROFILE
    baseline = suite.read_json(args.baseline.resolve())
    identity = {
        "mqttyCommit": suite.clean_commit(suite.MQTTY),
        "mqttBenchmarkCommit": suite.clean_commit(suite.ROOT / "MqttBenchmark"),
        "profileSha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        "harnessSha256": hashlib.sha256(Path(suite.__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "suite": baseline["identity"]["suite"],
    }
    if identity != baseline["identity"]:
        raise RuntimeError("Baseline identity differs from the packaged inputs.")
    if not app.is_file():
        raise FileNotFoundError(app)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    suite.write_json(output / "environment.json", {
        "os": platform.platform(), "architecture": platform.machine(),
        "python": platform.python_version(), "dotnetInfo": suite.command([args.dotnet, "--info"]),
        "identity": identity, "buildOnThisMachine": False,
        "appSha256": hashlib.sha256(app.read_bytes()).hexdigest(),
        "runnerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    results = {}
    for case, expected in baseline["cases"].items():
        match = re.fullmatch(r"(ideal-broker|legacy-mosquitto)\.m(\d+)\.q([012])\.s(\d+)", case)
        if not match:
            raise ValueError(f"Unknown baseline case {case}")
        kind, payload, qos, seed = match.groups()
        spec = suite.configuration(kind, int(qos), int(payload), int(seed))
        if suite.sha(spec) != expected["configSha256"]:
            raise RuntimeError(f"Configuration mismatch for {case}")
        case_root = output / case
        case_root.mkdir()
        config = dict(spec, mqttYRepository=str(suite.MQTTY),
                      mqttBenchmarkRepository=str(suite.ROOT / "MqttBenchmark"),
                      brokerProfilePath=str(profile))
        config_path = case_root / "config.json"
        suite.write_json(config_path, config)
        repeats = []
        for repeat in (1, 2):
            run_root = case_root / f"repeat-{repeat}"
            with (case_root / f"repeat-{repeat}.log").open("x", encoding="utf-8") as log:
                result = subprocess.run([args.dotnet, str(app), "experiment", "--config", str(config_path),
                                         "--output", str(run_root)], cwd=suite.MQTTY,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
            if result.returncode not in (0, 3):
                raise RuntimeError(f"{case} repeat {repeat}: exit {result.returncode}; inspect log.")
            bundles = list(run_root.iterdir())
            if len(bundles) != 1:
                raise RuntimeError(f"Unexpected result directories in {run_root}")
            bundle = bundles[0]
            summary = suite.read_json(bundle / "summary.json")
            manifest = suite.read_json(bundle / "experiment-manifest.json")
            if summary["status"] != "completed" or summary["simulated"]["completed"] <= 0:
                raise RuntimeError(f"Incomplete or empty simulation: {case}")
            repeats.append({
                "configSha256": suite.sha(spec), "runtime": manifest["runtime"],
                "summarySha256": suite.sha(summary),
                "brokerCountersSha256": suite.sha(suite.read_json(bundle / "counters.json")["brokers"]),
                "histogramSha256": suite.sha(suite.read_json(bundle / "latency-histogram.json")),
                "phasesSha256": suite.sha(manifest["phases"]),
                "connectionsSha256": suite.sha(manifest["brokerClientCounts"]),
                "cliExitCode": result.returncode,
            })
        local_match = repeats[0] == repeats[1]
        different = [key for key in expected if expected[key] != repeats[0].get(key)]
        results[case] = {
            "localRepeatsMatch": local_match,
            "scientificFingerprintsMatch": local_match and not [key for key in different if key != "runtime"],
            "runtimeMatches": repeats[0]["runtime"] == expected["runtime"],
            "baselineRuntime": expected["runtime"], "differentFields": different,
            "repeats": repeats,
        }
        print(f"{case}: localMatch={local_match}, differing baseline fields={different}", flush=True)
    if suite.clean_commit(suite.MQTTY) != identity["mqttyCommit"] or suite.clean_commit(suite.ROOT / "MqttBenchmark") != identity["mqttBenchmarkCommit"]:
        raise RuntimeError("Packaged source changed during execution.")
    report = {
        "schemaVersion": 1, "identity": identity, "buildOnThisMachine": False,
        "allLocalRepeatsMatch": all(row["localRepeatsMatch"] for row in results.values()),
        "allScientificFingerprintsMatch": all(row["scientificFingerprintsMatch"] for row in results.values()),
        "allRuntimesMatch": all(row["runtimeMatches"] for row in results.values()),
        "comparisonPolicy": "Exact baseline comparison of every fingerprint; runtime differences reported separately.",
        "cases": results,
    }
    suite.write_json(output / "comparison.json", report)
    if not report["allScientificFingerprintsMatch"]:
        raise RuntimeError("Scientific fingerprint or local-repeat mismatch; see comparison.json.")
    print(f"PASS scientific replay: {len(results)} cases / {len(results) * 2} runs; runtimeMatch={report['allRuntimesMatch']}", flush=True)


if __name__ == "__main__":
    main()
