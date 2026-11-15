"""Immutable loopback-only MqttY.Web deployment and owned-process monitoring.

Run on Linux, with the published payload already copied onto the evidence disk.
This helper never restarts an existing service or deletes a release/result.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.request

DLL = "EntityFX.MqttY.Web.App.dll"
RESERVE_BYTES = 10 * 1024**3
DATA_BUDGET_BYTES = 5 * 1024**3


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def check_storage(mount, root, *, reserve=RESERVE_BYTES):
    mount = Path(mount).resolve(strict=True)
    root = Path(root).resolve()
    if not os.path.ismount(mount):
        raise RuntimeError("Evidence mount is absent; refusing system-disk fallback")
    if root == mount or mount not in root.parents:
        raise RuntimeError("Deployment root must be strictly inside evidence mount")
    if shutil.disk_usage(mount).free < reserve:
        raise RuntimeError("Evidence disk reserve is exhausted")
    return mount, root


def payload_files(directory):
    directory = Path(directory).resolve(strict=True)
    files = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Symlinks are forbidden in published payload")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = sha256(path)
    if DLL not in files:
        raise RuntimeError("Published Web DLL is absent")
    return dict(sorted(files.items()))


def stage(payload, manifest_path, mount, root):
    _, root = check_storage(mount, root)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("mqttyCommit", "")):
        raise ValueError("A full committed mqtty source identity is required")
    expected = manifest["files"]
    if payload_files(payload) != expected:
        raise RuntimeError("Payload files differ from signed-off manifest")
    identity = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    release = root / "releases" / identity
    if root not in release.resolve().parents:
        raise RuntimeError("Release directory escapes deployment root")
    release.parent.mkdir(parents=True, exist_ok=True)
    release.mkdir()  # A collision must never overwrite a previous release.
    try:
        shutil.copytree(payload, release / "payload")
        if payload_files(release / "payload") != expected:
            raise RuntimeError("Copied payload hashes differ")
        shutil.copyfile(__file__, release / "linux_web_host.py")
        manifest["releaseId"] = identity
        manifest["helperSha256"] = sha256(release / "linux_web_host.py")
        atomic_json(release / "release.json", manifest)
        (release / "ready").write_text(identity + "\n", encoding="ascii")
        for path in (release / "payload").rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        (release / "payload").chmod(0o555)
        for path in release.iterdir():
            if path.is_file():
                path.chmod(0o444)
        release.chmod(0o555)
    except BaseException:
        # Preserve even a partially copied release for investigation.
        atomic_json(release / "stage-failed.json", {"failed": True})
        raise
    return release


class BoundedLog:
    """One process's output; bounded independently of system journal policy."""
    def __init__(self, path, *, max_bytes=5 * 1024**2, backups=3):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backups = backups
        self.lock = threading.Lock()

    def write(self, chunk):
        with self.lock:
            for offset in range(0, len(chunk), self.max_bytes):
                part = chunk[offset:offset + self.max_bytes]
                if self.path.exists() and self.path.stat().st_size + len(part) > self.max_bytes:
                    for index in range(self.backups, 0, -1):
                        old = self.path if index == 1 else Path(str(self.path) + f".{index-1}")
                        new = Path(str(self.path) + f".{index}")
                        if old.exists():
                            old.replace(new)
                with self.path.open("ab") as stream:
                    stream.write(part)


def directory_bytes(path):
    return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())


def http_health(url="http://127.0.0.1:5088/healthz"):
    if not url.startswith("http://127.0.0.1:"):
        raise ValueError("Health requests must target loopback")
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return {"ok": response.status == 200, "statusCode": response.status,
                    "body": json.loads(response.read(1024 * 1024))}
    except Exception as error:
        return {"ok": False, "error": type(error).__name__ + ": " + str(error)}


def process_rss(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except FileNotFoundError:
        pass
    return None


def stop_owned(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def serve(release, mount, root, port=5088):
    _, root = check_storage(mount, root)
    release = Path(release).resolve(strict=True)
    if release.parent != root / "releases" or not (release / "ready").is_file():
        raise RuntimeError("Release is not a ready version under this deployment root")
    manifest = json.loads((release / "release.json").read_text())
    if payload_files(release / "payload") != manifest["files"]:
        raise RuntimeError("Immutable release was modified")
    if sha256(release / "linux_web_host.py") != manifest["helperSha256"]:
        raise RuntimeError("Release monitoring helper was modified")
    data = root / "data"
    if data.is_symlink() or (data.exists() and any(p.is_symlink() for p in data.rglob("*"))):
        raise RuntimeError("Web data must not escape the evidence disk through symlinks")
    if root not in data.resolve().parents:
        raise RuntimeError("Data directory escapes deployment root")
    for relative in ("logs", "tmp", "runtime", "monitoring"):
        (data / relative).mkdir(parents=True, exist_ok=True)
    if directory_bytes(data) > DATA_BUDGET_BYTES:
        raise RuntimeError("Existing Web data already exceeds disk budget")
    environment = dict(os.environ, ASPNETCORE_URLS=f"http://127.0.0.1:{port}",
                       ASPNETCORE_ENVIRONMENT="Production", MqttYWeb__DataDirectory=str(data),
                       MqttYWeb__MaxParallelJobs="1", MqttYWeb__DefaultExecutionMode="OutOfProcess",
                       MqttYWeb__MaxParallelJobLimit="1", MqttYWeb__RequiredMountPoint=str(Path(mount).resolve()),
                       MqttYWeb__MinimumFreeDiskBytes=str(RESERVE_BYTES),
                       MqttYWeb__DefaultTimeLimit="00:02:00", MqttYWeb__DefaultParallel="false",
                       TMPDIR=str(data / "tmp"), DOTNET_BUNDLE_EXTRACT_BASE_DIR=str(data / "runtime"),
                       DOTNET_CLI_HOME=str(data / "runtime"))
    process = subprocess.Popen(["/bin/dotnet", str(release / "payload" / DLL)],
                               cwd=release / "payload", env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    logger = BoundedLog(data / "logs" / "web.log")
    def copy_output():
        while chunk := process.stdout.read(4096):
            logger.write(chunk)
    output_thread = threading.Thread(target=copy_output, daemon=True)
    output_thread.start()
    stop = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stop.set())
    started = time.time()
    reason = None
    http_failures = 0
    snapshot = {"releaseId": manifest["releaseId"], "mqttyCommit": manifest["mqttyCommit"],
                "pid": process.pid, "hostPid": os.getpid(), "startedEpoch": started}
    try:
        while process.poll() is None and not stop.is_set():
            check_storage(mount, root)
            data_bytes = directory_bytes(data)
            if data_bytes > DATA_BUDGET_BYTES:
                raise RuntimeError("Web data disk budget exceeded; evidence retained")
            snapshot.update(observedEpoch=time.time(), state="running", rssBytes=process_rss(process.pid),
                            dataBytes=data_bytes, diskFreeBytes=shutil.disk_usage(mount).free,
                            http=http_health(f"http://127.0.0.1:{port}/healthz"))
            http_failures = 0 if snapshot["http"]["ok"] else http_failures + 1
            snapshot["httpConsecutiveFailures"] = http_failures
            atomic_json(data / "monitoring" / "latest.json", snapshot)
            if time.time() - started > 60 and http_failures >= 3:
                raise RuntimeError("Web HTTP health unavailable for three samples after startup allowance")
            stop.wait(10)
    except Exception as error:
        reason = type(error).__name__ + ": " + str(error)
    finally:
        stop_owned(process)
        output_thread.join(timeout=3)
        snapshot.update(observedEpoch=time.time(), state="stopped", exitCode=process.returncode,
                        reason=reason or ("service stop requested" if stop.is_set() else "Web process exited"))
        # Never write a fallback status onto the system disk after unmount.
        if os.path.ismount(mount):
            atomic_json(data / "monitoring" / "latest.json", snapshot)
    return 1 if reason or (process.returncode and not stop.is_set()) else 0


def unit_text(release, mount, root):
    for value in (release, mount, root):
        if any(c in str(value) for c in ('"', "\n", "\r", "%", "\\")):
            raise ValueError("Unsupported systemd path characters")
    return f'''[Unit]
Description=MqttY.Web local simulation host with evidence-disk monitoring
After=network.target

[Service]
Type=simple
ExecStartPre=/usr/bin/mountpoint -q "{mount}"
ExecStart=/usr/bin/python3 "{release}/linux_web_host.py" serve --release "{release}" --mount "{mount}" --root "{root}"
Restart=no
KillMode=control-group
TimeoutStopSec=20
MemoryMax=2G
MemorySwapMax=0
CPUQuota=150%
TasksMax=256
UMask=0077
NoNewPrivileges=yes
StandardOutput=null
StandardError=journal
LogRateLimitIntervalSec=30s
LogRateLimitBurst=20

[Install]
WantedBy=default.target
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    package = commands.add_parser("manifest")
    package.add_argument("--payload", required=True)
    package.add_argument("--mqtty-commit", required=True)
    package.add_argument("--output", required=True)
    for name in ("stage", "serve", "unit"):
        command = commands.add_parser(name)
        command.add_argument("--mount", required=True)
        command.add_argument("--root", required=True)
        if name == "stage":
            command.add_argument("--payload", required=True)
            command.add_argument("--manifest", required=True)
        else:
            command.add_argument("--release", required=True)
    commands.add_parser("health")
    args = parser.parse_args()
    if args.command == "manifest":
        atomic_json(args.output, {"schemaVersion": 1, "mqttyCommit": args.mqtty_commit,
                                 "files": payload_files(args.payload)})
    elif args.command == "stage":
        print(stage(args.payload, args.manifest, args.mount, args.root))
    elif args.command == "serve":
        raise SystemExit(serve(args.release, args.mount, args.root))
    elif args.command == "unit":
        check_storage(args.mount, args.root)
        print(unit_text(args.release, args.mount, args.root))
    else:
        value = http_health()
        print(json.dumps(value, ensure_ascii=False, indent=2))
        raise SystemExit(0 if value["ok"] else 1)


if __name__ == "__main__":
    main()
