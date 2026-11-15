"""Separate Windows/offline duration sensitivity with frozen ActiveMQ profile and model."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("controller", "runtime", "dotnet", "output", "mqtty-repository", "benchmark-repository"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--keys", nargs="+", default=["m16.q1.p16.s42"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    broker = args.controller / "brokers/ActiveMQ"
    profile = broker / "broker-calibration.v3.json"
    observations = broker / "validation/broker-observations.v3.json"
    local_profile, local_reference = args.output / profile.name, args.output / "validation-observations.json"
    local_profile.write_bytes(profile.read_bytes())
    local_reference.write_bytes(observations.read_bytes())
    frozen = sha(local_profile)
    if sha(args.runtime) != "60c5a79fca5291b061af345f3fb98e758c62ae551763693d5f39f74d40e9cebd":
        raise ValueError("The diagnostic must use the same frozen runtime as the campaign")
    rows = []
    baseline = {run["key"]: run for run in json.loads((broker / "simulations/holdout-2s-overnight-v1/index.json").read_text())["runs"]}
    result = dict(excludedFromCampaignCoverage=True, notIndependentValidation=True,
                  purpose="Duration sensitivity only; no fitting to holdout and no change to thresholds",
                  profileSha256=frozen, referenceSha256=sha(local_reference),
                  runtimeFiles={p.name: sha(p) for p in sorted(args.runtime.parent.glob("*.dll"))}, rows=rows)
    for key in args.keys:
        original_bytes = (broker / f"simulations/holdout-2s-overnight-v1/{key}.json").read_bytes()
        original = json.loads(original_bytes)
        baseline_directory = Path(baseline[key]["directory"])
        # Convert the original evidence path to the caller's mounted evidence root.
        series_name = args.controller.parent.name
        suffix = baseline_directory.as_posix().split("/" + series_name + "/", 1)[1]
        reference_summary = json.loads((args.controller.parent / suffix / "summary.json").read_text())
        for seconds in (2, 10):
            config = copy.deepcopy(original)
            config["mqttYRepository"] = str(args.mqtty_repository.resolve())
            config["mqttBenchmarkRepository"] = str(args.benchmark_repository.resolve())
            config["brokerProfilePath"] = str(local_profile.resolve())
            config["referenceObservationsPath"] = str(local_reference.resolve())
            config["experiment"]["measurementTicks"] = seconds * 100000
            config_path = args.output / f"{key}-{seconds}s.json"
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            output = args.output / f"{key}-{seconds}s"
            print("START", key, seconds, flush=True)
            started = time.monotonic()
            with (args.output / f"{key}-{seconds}s.log").open("w", encoding="utf-8") as log:
                process = subprocess.run([str(args.dotnet), str(args.runtime), "experiment", "--config", str(config_path), "--output", str(output)],
                                         stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            if process.returncode not in (0, 3):
                raise ValueError(f"Diagnostic failed: {key} {seconds}s; inspect its log")
            directory = next(p for p in output.iterdir() if p.is_dir())
            summary = json.loads((directory / "summary.json").read_text())
            row = dict(key=key, measurementSeconds=seconds, wallSeconds=time.monotonic()-started,
                       summary=summary, directory=str(directory), originalSummary=reference_summary,
                       originalConfigSha256=hashlib.sha256(original_bytes).hexdigest())
            if seconds == 2:
                row["sameScientificSummaryAsLinux"] = all(summary[x] == reference_summary[x]
                    for x in ("status", "simulated", "reference", "acceptanceCriteria"))
            rows.append(row)
            result["profileUnchanged"] = sha(profile) == sha(local_profile) == frozen
            if not result["profileUnchanged"]:
                raise ValueError("Profile changed")
            (args.output / "results.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
            print("DONE", key, seconds, "wall", round(row["wallSeconds"],2), "metrics", summary["simulated"], flush=True)


if __name__ == "__main__":
    main()
