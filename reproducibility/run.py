"""Run the same virtual-time MQTT experiments on Windows and Linux (stdlib only)."""
import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MQTTY = ROOT / "mqtty"
PROJECT = MQTTY / "src/EntityFX.MqttSimulator/EntityFX.MqttY.MqttRelay.App"
PROFILE = "src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Mqtt/Data/broker-benchmark.v2.json"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def sha(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def command(args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True, encoding="utf-8").strip()


def clean_commit(repo):
    if command(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"]):
        raise RuntimeError(f"Tracked changes in {repo}; use a clean checkout.")
    return command(["git", "-C", str(repo), "rev-parse", "HEAD"])


def configuration(kind, qos, payload, seed):
    return {
        "experiment": {
            "topologyMode": "brokerFidelity",
            "profileClientCountMode": "connectedClients",
            "brokers": 1,
            "networkLength": 1,
            "clientsPerBroker": 1,
            "publishQos": ["atMostOnce", "atLeastOnce", "exactlyOnce"][qos],
            "subscribeQos": ["atMostOnce", "atLeastOnce", "exactlyOnce"][qos],
            "payloadBytes": payload,
            "brokerAssignmentMode": "ideal" if kind == "ideal-broker" else "Mosquitto",
            "warmupTicks": 1000,
            "measurementTicks": 10000,
            "drainTimeoutTicks": 20000,
            "offeredRps": 1000,
            "randomSeed": seed,
            "parallel": False,
            "refreshStrategy": 0,
            "enableCounters": True,
            "fidelityMode": True,
        },
        "ticks": {
            "tickPeriod": "00:00:00.0001000",
            "receiveWaitPeriod": "00:00:01",
            "outgoingWaitTicks": 1,
            "counterHistoryDepth": 100,
        },
        "network": {
            "capacityBytesPerSecond": 100000000,
            "throughputWindowTicks": 100,
            "queueCapacity": 100000,
            "transferTicks": 1,
            "networkType": "ethernet",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; never overwritten")
    parser.add_argument("--baseline", type=Path, help="Compare with a previous fingerprints.json")
    parser.add_argument("--full", action="store_true", help="Seeds 42/43/44 and payloads 16/256 (72 runs)")
    parser.add_argument("--dotnet", default="dotnet", help="Path to dotnet executable")
    args = parser.parse_args()
    baseline = read_json(args.baseline.resolve()) if args.baseline else None
    identity = {
        "mqttyCommit": clean_commit(MQTTY),
        "mqttBenchmarkCommit": clean_commit(ROOT / "MqttBenchmark"),
        "profileSha256": hashlib.sha256((MQTTY / PROFILE).read_bytes()).hexdigest(),
        "harnessSha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "suite": "full" if args.full else "smoke",
    }
    if baseline and identity != baseline["identity"]:
        raise RuntimeError("Baseline identity differs (sources, profile, harness or suite).")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "environment.json", {
        "os": platform.platform(), "architecture": platform.machine(),
        "python": platform.python_version(),
        "dotnetInfo": command([args.dotnet, "--info"]),
        "metaCommit": command(["git", "rev-parse", "HEAD"]), "identity": identity,
    })
    with (output / "build.log").open("x", encoding="utf-8") as log:
        subprocess.run([args.dotnet, "build", str(PROJECT), "-c", "Release", "--nologo"],
                       cwd=MQTTY, stdout=log, stderr=subprocess.STDOUT, check=True)
    dll = PROJECT / "bin/Release/net6.0/EntityFX.MqttY.MqttRelay.App.dll"
    results = {}
    for kind in ("ideal-broker", "legacy-mosquitto"):
        for payload in ([16, 256] if args.full else [16]):
            for qos in range(3):
                for seed in ([42, 43, 44] if args.full else [42]):
                    case = f"{kind}.m{payload}.q{qos}.s{seed}"
                    spec = configuration(kind, qos, payload, seed)
                    case_root = output / case
                    case_root.mkdir()
                    config = dict(spec, mqttYRepository=str(MQTTY),
                                  mqttBenchmarkRepository=str(ROOT / "MqttBenchmark"),
                                  brokerProfilePath=str(MQTTY / PROFILE))
                    config_path = case_root / "config.json"
                    write_json(config_path, config)
                    repeats = []
                    for repeat in (1, 2):
                        run_root = case_root / f"repeat-{repeat}"
                        with (case_root / f"repeat-{repeat}.log").open("x", encoding="utf-8") as log:
                            run = subprocess.run([args.dotnet, str(dll), "experiment", "--config",
                                                  str(config_path), "--output", str(run_root)],
                                                 cwd=MQTTY, stdout=log, stderr=subprocess.STDOUT,
                                                 timeout=180)
                        # Exit 3 means a reference-fidelity criterion failed, not a replay failure.
                        if run.returncode not in (0, 3):
                            raise RuntimeError(f"{case}, repeat {repeat}: exit {run.returncode}; see log.")
                        bundles = list(run_root.iterdir())
                        if len(bundles) != 1:
                            raise RuntimeError(f"Expected one result bundle: {run_root}")
                        bundle = bundles[0]
                        summary = read_json(bundle / "summary.json")
                        manifest = read_json(bundle / "experiment-manifest.json")
                        if summary["status"] != "completed" or summary["simulated"]["completed"] <= 0:
                            raise RuntimeError(f"{case}: incomplete or empty simulation.")
                        repeats.append({
                            "configSha256": sha(spec),
                            "runtime": manifest["runtime"],
                            "summarySha256": sha(summary),
                            "brokerCountersSha256": sha(read_json(bundle / "counters.json")["brokers"]),
                            "histogramSha256": sha(read_json(bundle / "latency-histogram.json")),
                            "phasesSha256": sha(manifest["phases"]),
                            "connectionsSha256": sha(manifest["brokerClientCounts"]),
                            "cliExitCode": run.returncode,
                        })
                    if repeats[0] != repeats[1]:
                        raise RuntimeError(f"{case}: repeat mismatch; retain artifacts for analysis.")
                    results[case] = repeats[0]
                    print(f"PASS replay {case} (reference exit {repeats[0]['cliExitCode']})", flush=True)
    # Detect tracked source changes that happened during the suite too.
    if clean_commit(MQTTY) != identity["mqttyCommit"] or clean_commit(ROOT / "MqttBenchmark") != identity["mqttBenchmarkCommit"]:
        raise RuntimeError("Source identity changed during the suite.")
    report = {"schemaVersion": 1, "identity": identity, "cases": results}
    write_json(output / "fingerprints.json", report)
    if baseline and report != baseline:
        different = sorted(key for key in set(results) | set(baseline["cases"])
                           if results.get(key) != baseline["cases"].get(key))
        raise RuntimeError("Cross-machine mismatch: " + ", ".join(different))
    print(f"PASS: {len(results)} cases, {len(results) * 2} runs; {output / 'fingerprints.json'}")
    if baseline:
        print("PASS: all fingerprints match the baseline.")


if __name__ == "__main__":
    main()
