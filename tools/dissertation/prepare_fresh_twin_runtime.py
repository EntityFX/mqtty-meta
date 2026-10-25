"""Snapshot the ACTUAL research sources, portable build and historical profiles."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_twin_cohort import file_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mosquitto-config", required=True)
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[2]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(args.runtime, output / "runtime")
    (output / "profiles").mkdir()
    for broker in ("Aedes", "Mosquitto", "ActiveMQ", "EMQX"):
        shutil.copyfile(workspace / "reports/data" / broker / "profile/broker-calibration.v3.json",
                        output / "profiles" / (broker + ".json"))
    (output / "tools").mkdir()
    for name in ("audit_twin_cohort.py", "run_fresh_twin_pilot.py"):
        shutil.copyfile(Path(__file__).with_name(name), output / "tools" / name)
    roots = ["MqttBenchmark/src/EntityFX.MqttBenchmark", "MqttBenchmark/src/EntityFX.MqttBenchmark.Campaign",
             "mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttSimulator.Contracts",
             "mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Contracts.Mqtt",
             "mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Contracts.Scenarios",
             "mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttSimulator",
             "mqtty/src/EntityFX.MqttSimulator/EntityFX.MqttY.Plugin.Mqtt",
             "tools/dissertation/TwinValidationProbe"]
    for relative in roots:
        for path in (workspace / relative).rglob("*"):
            if not path.is_file() or any(part in ("bin", "obj", "node_modules") for part in path.parts):
                continue
            if path.suffix not in (".cs", ".csproj", ".json", ".props", ".targets"):
                continue
            target = output / "source" / path.relative_to(workspace)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    files = {str(path.relative_to(output)).replace("\\", "/"): file_digest(path)
             for path in sorted(output.rglob("*")) if path.is_file()}
    identity = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    repositories = {}
    for name, path in (("meta", workspace), ("mqtty", workspace / "mqtty"), ("MqttBenchmark", workspace / "MqttBenchmark")):
        repositories[name] = dict(head=subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip(),
                                  dirty=bool(subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True)))
    manifest = dict(scope="Dirty working-tree snapshot, not a clean-commit claim", repositories=repositories,
                    files=files, treeSha256=identity, sourceAndRuntimeFrozenBeforeNewPilot=True,
                    mosquittoConfigPath="/run/media/entityfx/hd250/mqtty-evidence/two-machine-20261014-1340-linux-local/brokers-native/mosquitto.conf",
                    mosquittoConfigSha256=file_digest(args.mosquitto_config), mosquittoOriginalPort=2883)
    with (output / "input-manifest.json").open("x", encoding="utf-8") as target:
        json.dump(manifest, target, indent=2)
    print(json.dumps(dict(files=len(files), treeSha256=identity,
                         inputManifestSha256=file_digest(output / "input-manifest.json"))))


if __name__ == "__main__":
    main()
