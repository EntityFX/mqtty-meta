"""Immutable inputs and storage boundary for the network study runner."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def contained(root, relative):
    name = PurePosixPath(relative)
    if name.is_absolute() or not name.parts or any(p in ("..", ".") or ":" in p or "\\" in p for p in name.parts):
        raise ValueError("Unsafe relative evidence path: " + relative)
    path = Path(root) / relative
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Evidence path escapes root")
    return path


class StorageGuard:
    def __init__(self, mount, root, reserve_bytes):
        self.mount, self.root = Path(mount).resolve(), Path(root).resolve()
        if not self.mount.is_mount() or self.mount == Path(self.mount.anchor) or not self.root.is_relative_to(self.mount) or self.root == self.mount:
            raise ValueError("Use a dedicated mounted evidence disk and an output below it")
        self.device = self.mount.stat().st_dev
        self.reserve = reserve_bytes
        self.check()

    def identity(self):
        if not self.mount.is_mount() or self.mount.stat().st_dev != self.device:
            raise RuntimeError("Evidence mount missing or replaced; no new files may be written")
        existing = self.root
        while not existing.exists():
            existing = existing.parent
        if existing.stat().st_dev != self.device or not self.root.resolve().is_relative_to(self.mount):
            raise RuntimeError("Output crossed evidence filesystem boundary")

    def check(self):
        self.identity()
        if shutil.disk_usage(self.mount).free < self.reserve:
            raise RuntimeError("Evidence disk reserve exhausted")

    def save(self, path, value):
        self.identity()  # A small failure receipt is allowed below the free-space reserve.
        target = Path(path)
        if not target.resolve().is_relative_to(self.root):
            raise ValueError("Receipt must remain within study")
        write(target, value)


@contextmanager
def lock(path):
    with Path(path).open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.tell() == 0:
                stream.write(b"0"); stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def program_check(program):
    plan = read(program / "plan.json")
    counts = {"pilot": "pilotJobs", "matrix": "matrixJobs", "replay": "replayJobs", "diagnostic": "optionalDiagnosticJobs"}
    jobs = plan["jobs"]
    if plan["schemaVersion"] != 1 or not jobs or len({j["id"] for j in jobs}) != len(jobs):
        raise ValueError("Invalid program or duplicated job IDs")
    indexed = {j["id"]: j for j in jobs}
    for kind, field in counts.items():
        if plan[field] != sum(j["kind"] == kind for j in jobs):
            raise ValueError("Incomplete program: " + kind)
    for job in jobs:
        if job["kind"] not in counts or not re.fullmatch(r"[A-Za-z0-9._-]+", job["id"]):
            raise ValueError("Unsafe or unknown job")
        config = contained(program, job["config"])
        if config.is_symlink() or sha(config) != job["sha256"]:
            raise ValueError("Config hash differs: " + job["id"])
        o = read(config)
        profile = o.get("brokerProfilePath")
        if profile is not None:
            resolved = (config.parent / profile).resolve()
            if resolved != (program / "inputs/mosquitto-profile.json").resolve() or sha(resolved) != plan["profileSha256"]:
                raise ValueError("Profile is outside immutable snapshot or has changed")
        if job["kind"] == "replay":
            prior = indexed.get(job.get("replayOf"))
            if not prior or prior["kind"] != "matrix" or read(contained(program, prior["config"])) != o:
                raise ValueError("Replay must match an existing matrix configuration")
    if sha(program / "inputs/mosquitto-profile.json") != plan["profileSha256"]:
        raise ValueError("Profile snapshot changed")
    return plan


def verify_snapshot(root):
    manifest = read(root / "execution-manifest.json")
    expected = {name for name in manifest["files"] if name.startswith(("program/", "runtime/", "tools/"))}
    actual = {p.relative_to(root).as_posix() for folder in ("program", "runtime", "tools") for p in (root / folder).rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("Unexpected or missing snapshot files")
    for relative, expected in manifest["files"].items():
        path = contained(root, relative)
        if path.is_symlink() or sha(path) != expected:
            raise ValueError("Snapshot changed: " + relative)
    if sha(manifest["dotnet"]) != manifest["dotnetSha256"]:
        raise ValueError("Dotnet host changed")
    if sha(Path(__file__)) != manifest["files"]["tools/dissertation/network_study_inputs.py"]:
        raise ValueError("Execute the saved runner snapshot")
    for module, relative in {"network_study_process": "dissertation/network_study_process.py",
                             "network_study_verification": "dissertation/network_study_verification.py",
                             "network_review": "dissertation/network_review.py", "study": "federation/study.py",
                             "transit_study": "federation/transit_study.py"}.items():
        loaded = sys.modules.get(module)
        if loaded is not None and sha(loaded.__file__) != manifest["files"]["tools/" + relative]:
            raise ValueError("Loaded verifier differs from saved source: " + module)
    return manifest


def stage(program, runtime, output, mount, source_root, *, timeout=1800, rss_limit=3 * 1024**3,
          reserve=10 * 1024**3, disk_budget=40 * 1024**3, after_units=()):
    program, runtime, output, source_root = map(lambda p: Path(p).resolve(), (program, runtime, output, source_root))
    if output.exists():
        raise ValueError("Output already exists; choose a new study directory")
    if timeout <= 0 or rss_limit <= 0 or reserve < 5 * 1024**3 or disk_budget <= 0:
        raise ValueError("Invalid execution limits")
    guard = StorageGuard(mount, output, reserve)
    plan = program_check(program)
    revisions = {}
    for repo in (source_root, source_root / "mqtty"):
        if subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip():
            raise ValueError("Commit and clean source repositories before staging")
        revisions[str(repo)] = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    dotnet = Path(shutil.which("dotnet") or "").resolve()
    if not dotnet.is_file() or not (runtime / "EntityFX.MqttY.Federation.App.dll").is_file():
        raise ValueError("Dotnet or Federation runtime is missing")
    paths = [(program / "plan.json", "program/plan.json"),
             (program / "inputs/mosquitto-profile.json", "program/inputs/mosquitto-profile.json")]
    paths += [(program / j["config"], "program/" + j["config"]) for j in plan["jobs"]]
    code = ["dissertation/run_network_study.py", "dissertation/network_study_inputs.py", "dissertation/network_study_process.py", "dissertation/network_study_verification.py",
            "dissertation/network_review.py", "dissertation/run_network_parallel.py", "federation/study.py", "federation/transit_study.py"]
    paths += [(source_root / "tools" / p, "tools/" + p) for p in code]
    paths += [(p, "runtime/" + p.name) for p in runtime.iterdir() if p.is_file() and p.suffix in (".dll", ".json", ".so")]
    if any(p.is_symlink() for p, _ in paths):
        raise ValueError("Snapshot inputs must not contain symbolic links")
    output.mkdir(parents=True, exist_ok=False)
    try:
        for source, relative in paths:
            guard.check()
            target = contained(output, relative); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        info = subprocess.check_output([str(dotnet), "--info"], text=True)
        (output / "dotnet-info.txt").write_text(info, encoding="utf-8")
        files = {relative: sha(output / relative) for _, relative in paths}
        files["dotnet-info.txt"] = sha(output / "dotnet-info.txt")
        manifest = {"schemaVersion": 1, "files": files, "sourceRevisions": revisions, "dotnet": str(dotnet),
                    "dotnetSha256": sha(dotnet), "mountRoot": str(guard.mount), "mountDevice": guard.device,
                    "jobTimeoutSeconds": timeout, "jobRssLimitBytes": rss_limit, "reserveBytes": reserve,
                    "diskBudgetBytes": disk_budget, "workers": 1, "afterUnits": list(after_units),
                    "python": sys.version, "programSha256": files["program/plan.json"]}
        guard.save(output / "execution-manifest.json", manifest)
        program_check(output / "program")
        return manifest
    except BaseException as error:
        guard.save(output / "staging-failure.json", {"error": repr(error)})
        raise
