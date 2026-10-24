"""Bounded local HTTP/worker check for a newly deployed MqttY.Web service."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import time
import urllib.error
import urllib.request


def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:5088" + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read())


def verify_seal(output, data_root):
    output = Path(output).resolve(strict=True)
    if Path(data_root).resolve(strict=True) not in output.parents:
        raise RuntimeError("Job output escapes the configured data root")
    seals = json.loads((output / "sha256.json").read_text())
    for relative, expected in seals.items():
        path = output / relative
        parsed = PurePosixPath(relative)
        if parsed.is_absolute() or ".." in parsed.parts or output not in path.resolve().parents:
            raise RuntimeError("Unsafe seal member")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError("Result seal mismatch: " + relative)
    required = {"summary.json", "manifest.json"}
    if not required.issubset(seals):
        raise RuntimeError("Scientific summary or manifest not covered by seals")
    return {"verifiedFiles": len(seals), "sha256FileSha256": hashlib.sha256(
        (output / "sha256.json").read_bytes()).hexdigest(),
        "summary": json.loads((output / "summary.json").read_text()),
        "manifest": json.loads((output / "manifest.json").read_text())}


def run(output, data_root, profile=None, qos=1):
    output = Path(output)
    output.mkdir()  # Never overwrite a previous smoke attempt.
    receipt = {"startedEpoch": time.time(), "profile": profile, "qos": qos, "jobs": [], "passed": False}
    def save():
        (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    save()
    try:
        receipt["healthBefore"] = request("/healthz")
        receipt["engines"] = request("/api/engines")
        for engine in ("relay", "federation", "loadBalancing"):
            body = {"engine": engine, "spec": {"kind": "line", "chainLength": 4 if profile else 3,
                    "brokerPresetId": (profile.lower() + "-default") if profile else "ideal-demo",
                    "clientsPerBroker": 16 if profile else 2, "qos": qos, "offeredRps": 100,
                    "warmupTicks": 100, "measurementTicks": 2000, "drainTimeoutTicks": 10000,
                    "payloadBytes": 16, "seed": 42},
                    "runSettings": {"executionMode": "OutOfProcess", "timeLimit": "00:01:00"}}
            job = {"engine": engine, "request": body, "submittedEpoch": time.time(), "observations": []}
            receipt["jobs"].append(job)
            status = request("/api/simulations", body)
            job["id"] = status["id"]
            save()
            deadline = time.monotonic() + 80
            while status["state"] in ("Queued", "Running", "Paused", "Starting"):
                if time.monotonic() > deadline:
                    # Cancel only this smoke helper's own job, retain all evidence.
                    job["stopResponse"] = request("/api/simulations/" + job["id"] + "/stop", {})
                    raise TimeoutError("Own smoke job exceeded 80-second wall limit")
                time.sleep(0.2)
                status = request("/api/simulations/" + job["id"])
                if len(job["observations"]) < 12:
                    job["observations"].append(status)
            job["status"] = status
            if status["state"] != "Completed":
                raise RuntimeError("Smoke job did not complete: " + json.dumps(status))
            job["monitor"] = request("/api/simulations/" + job["id"] + "/monitor")
            job["verification"] = verify_seal(status["outputDir"], data_root)
            job["httpSummary"] = request("/api/simulations/" + job["id"] + "/results/summary.json")
            if job["httpSummary"] != job["verification"]["summary"]:
                raise RuntimeError("HTTP summary differs from sealed disk summary")
            save()
        receipt["healthAfter"] = request("/healthz")
        receipt["passed"] = True
    except Exception as error:
        receipt["technicalError"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        receipt["finishedEpoch"] = time.time()
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--profile", choices=["Mosquitto"])
    parser.add_argument("--qos", type=int, choices=(0, 1, 2), default=1)
    args = parser.parse_args()
    run(args.output, args.data_root, args.profile, args.qos)
