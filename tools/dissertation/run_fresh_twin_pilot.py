"""Owned, bounded first native/profiled-model pilot; no historical inputs altered."""
import argparse
import json
import os
import re
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_twin_cohort import events, file_digest, recount


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")


def run_bounded(command, output, seconds=180, env=None, cwd=None):
    with Path(output).open("x", encoding="utf-8") as log:
        started = time.monotonic()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, env=env, cwd=cwd)
        try:
            result = process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
            raise RuntimeError(f"Owned trial exceeded {seconds}s; pid={process.pid}")
        if result:
            raise RuntimeError(f"Trial exit code {result}; see {output}")
        return dict(pid=process.pid, exitCode=result, elapsedWallSeconds=time.monotonic() - started)


def ready(process, port):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"Owned broker exited {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                return
        except OSError:
            time.sleep(.05)
    raise RuntimeError("Owned broker readiness timed out")


def verify_events(directory):
    directory = Path(directory)
    phases = json.loads((directory / "phases.json").read_text())
    result = recount(events(directory / "publishes.ndjson"), events(directory / "deliveries.ndjson"), phases)
    result["rawSha256"] = {p.name: file_digest(p) for p in directory.iterdir()
                          if p.name in ("publishes.ndjson", "deliveries.ndjson", "phases.json", "connections.ndjson")}
    write_new(directory / "cohort-recount.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root, output = Path(args.root), Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    frozen = json.loads((root / "input-manifest.json").read_text())
    for relative, sha in frozen["files"].items():
        if file_digest(root / relative) != sha:
            raise ValueError("Input changed: " + relative)
    port = 33083
    # Abort if an existing listener owns our proposed port. Never stop it.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", port))
    original = Path(frozen["mosquittoConfigPath"])
    original_bytes = original.read_bytes()
    if file_digest(original) != frozen["mosquittoConfigSha256"]:
        raise ValueError("Historical Mosquitto config changed")
    config_text = original_bytes.decode()
    old_port = frozen["mosquittoOriginalPort"]
    config_text, count = re.subn(r"(?m)^(\s*listener\s+)" + str(old_port) + r"\b",
                                 lambda match: match.group(1) + str(port), config_text)
    if count != 1:
        raise ValueError("Expected exactly one historical MQTT listener")
    broker_config = output / "mosquitto.conf"
    broker_config.write_text(config_text, encoding="utf-8")
    results = []
    started = time.monotonic()
    try:
        for window in (.2, .4):
            case = output / ("window-" + str(window))
            case.mkdir()
            config = dict(id="pilot-Mosquitto-q1-p16-w" + str(window), broker="Mosquitto", qos=1,
                          publishers=16, payloadBytes=16, seed=42, repeat=1, warmupSeconds=.01,
                          measurementSeconds=window, drainSeconds=1, tickSeconds=.00001,
                          endpoint=f"mqtt://127.0.0.1:{port}", profilePath=str(root / "profiles/Mosquitto.json"),
                          profileSha256=frozen["files"]["profiles/Mosquitto.json"],
                          runtimeIdentity="runtime-manifest-sha256:" + file_digest(root / "input-manifest.json"))
            config_path = case / "config.json"
            write_new(config_path, config)
            with (case / "broker.log").open("x") as log:
                broker = subprocess.Popen(["/usr/sbin/mosquitto", "-c", str(broker_config)],
                                          stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    ready(broker, port)
                    native_process = run_bounded(["dotnet", str(root / "runtime/TwinValidationProbe.dll"),
                        "native", str(config_path), str(case / "native")], case / "native.log", 30)
                finally:
                    os.killpg(broker.pid, signal.SIGTERM)
                    broker.wait(timeout=5)
            native = verify_events(case / "native")
            model_process = run_bounded(["dotnet", str(root / "runtime/TwinValidationProbe.dll"),
                "model", str(config_path), str(case / "model")], case / "model.log", 180)
            model_trial = json.loads((case / "model/trial.json").read_text())
            model = verify_events(Path(model_trial["runDirectory"]))
            result = dict(windowSeconds=window, native=native, model=model,
                          nativeProcess=native_process, modelProcess=model_process)
            results.append(result)
            write_new(case / "comparison.json", result)
        write_new(output / "pilot-summary.json", dict(status="completed", scope="Fresh MqttBenchmark/profiled MqttY pilot; not main validation",
                  elapsedWallSeconds=time.monotonic() - started, cases=results,
                  originalBrokerConfigUnchanged=file_digest(original) == frozen["mosquittoConfigSha256"]))
        print(json.dumps(dict(status="completed", output=str(output), elapsedWallSeconds=time.monotonic() - started)))
        return 0
    except Exception as error:
        write_new(output / "failure.json", dict(error=repr(error), completedCases=results))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
