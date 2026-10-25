"""One bounded sequential fresh MqttBenchmark/MqttY series, never a restart of historical work."""
import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_twin_cohort import file_digest, events, recount
from run_fresh_twin_pilot import verify_events, write_new


def clock():
    return datetime.now(timezone.utc).isoformat()


def progress(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def terminate(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


def execute(command, log_path, affinity, seconds, env=None, cwd=None):
    started = time.monotonic()
    with log_path.open("x") as output:
        process = subprocess.Popen(["taskset", "-c", affinity] + command, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True, env=env, cwd=cwd)
        try:
            code = process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            terminate(process)
            raise RuntimeError(f"Owned job timeout after {seconds}s, pid={process.pid}; log={log_path}")
    if code:
        raise RuntimeError(f"Owned job exit={code}, pid={process.pid}; log={log_path}")
    return dict(pid=process.pid, exitCode=code, wallSeconds=time.monotonic() - started)


def broker_start(metadata, case, port, remaining):
    name = metadata["name"]
    executable = Path(metadata["executable"])
    if file_digest(executable) != metadata["executableSha256"]:
        raise RuntimeError("Broker executable changed: " + str(executable))
    environment = os.environ.copy()
    environment.update(metadata.get("environment", {}))
    cwd = None
    if name == "Mosquitto":
        source = Path(metadata["configFiles"][0])
        text = source.read_text()
        old = str(metadata["singleHostMqttPort"])
        text, count = re.subn(r"(?m)^(\s*listener\s+)" + old + r"\b",
                             lambda m: m.group(1) + str(port), text)
        if count != 1:
            raise RuntimeError("Expected one Mosquitto listener")
        configuration = case / "mosquitto.conf"
        configuration.write_text(text)
        command = [str(executable), "-c", str(configuration)]
    elif name == "Aedes":
        source = Path(metadata["args"][0]).parent
        home = case / "broker-home"
        shutil.copytree(source, home, symlinks=True)
        command = [str(executable), str(home / Path(metadata["args"][0]).name), str(port)]
        cwd = home
    elif name in ("ActiveMQ", "EMQX"):
        source = Path(metadata["workingDirectory"])
        home = case / "broker-home"
        shutil.copytree(source, home, symlinks=True, ignore=shutil.ignore_patterns("data", "log", "logs", "tmp"))
        launcher = home / Path(metadata["launcherExecutable"]).relative_to(source)
        if file_digest(launcher) != metadata["launcherSha256"]:
            raise RuntimeError("Broker launcher snapshot mismatch")
        if name == "ActiveMQ":
            configuration = home / "conf/activemq.xml"
            text = configuration.read_text().replace(str(source), str(home))
            text, count = re.subn(r"mqtt://[^\s\"<>:]+:\d+", "mqtt://127.0.0.1:" + str(port), text)
            if count < 1:
                raise RuntimeError("ActiveMQ MQTT connector not found")
            configuration.write_text(text)
            environment.update(ACTIVEMQ_HOME=str(home), ACTIVEMQ_BASE=str(home), ACTIVEMQ_DATA=str(home / "data"))
        else:
            environment.update(EMQX_LISTENERS__TCP__DEFAULT__BIND="127.0.0.1:" + str(port),
                               EMQX_NODE__NAME="article1_" + str(os.getpid()) + "@127.0.0.1")
            for configuration in (home / "etc").glob("*"):
                if configuration.is_file() and configuration.suffix in (".conf", ".hocon", ".args"):
                    text = configuration.read_text().replace(str(source), str(home))
                    configuration.write_text(text)
        command = [str(launcher)] + metadata["args"]
        cwd = home
    else:
        raise RuntimeError("Unsupported owned broker: " + name)
    log = (case / "broker.log").open("x")
    process = subprocess.Popen(["taskset", "-c", "0"] + command, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, env=environment, cwd=cwd)
    deadline = time.monotonic() + min(70, remaining)
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Owned {name} exited before ready: {process.returncode}; see {case / 'broker.log'}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    return process, log
            except OSError:
                time.sleep(.1)
        raise RuntimeError(f"Owned {name} readiness timeout; see {case / 'broker.log'}")
    except Exception:
        terminate(process)
        log.close()
        raise


def comparisons(output, rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["broker"], row["qos"], row["publishers"])].append(row)
    table = []
    for (broker, qos, publishers), group in sorted(groups.items()):
        native = [row["native"] for row in group if row.get("native")]
        for row in group:
            if not row.get("model"):
                continue
            for field, limit, relative in (("completedRpsInWindow", .15, True), ("p99PublishMs", .20, True),
                                           ("publishFailureRate", .05, False), ("deliveryLossRate", .05, False)):
                values = [item[field] for item in native if item[field] is not None]
                reference = sum(values) / len(values) if len(values) == 3 else None
                value = row["model"][field]
                error = None if reference is None or value is None or (relative and reference == 0) else abs(value - reference) / abs(reference) if relative else abs(value - reference)
                status = "notEvaluated" if error is None else "passed" if error <= limit else "failed"
                table.append(dict(broker=broker, qos=qos, publishers=publishers, seed=row["seed"],
                                  metric=field, nativeMean=reference, model=value, error=error, limit=limit,
                                  status=status, nativeRepeats=len(values)))
    with (output / "native-vs-model.csv").open("x", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["broker", "qos", "publishers", "seed", "metric", "nativeMean", "model", "error", "limit", "status", "nativeRepeats"])
        writer.writeheader()
        writer.writerows(table)
    return dict(metricRows=len(table), failedMetrics=sum(row["status"] == "failed" for row in table),
                passedMetrics=sum(row["status"] == "passed" for row in table),
                notEvaluated=sum(row["status"] == "notEvaluated" for row in table))


def reverify_events(directory):
    directory = Path(directory)
    phases = json.loads((directory / "phases.json").read_text())
    result = recount(events(directory / "publishes.ndjson"), events(directory / "deliveries.ndjson"), phases)
    result["rawSha256"] = {p.name: file_digest(p) for p in directory.iterdir()
        if p.name in ("publishes.ndjson", "deliveries.ndjson", "phases.json", "connections.ndjson")}
    return result


def recovered_rows(output, plan):
    """Reuse verified complete pairs; preserve every earlier attempt directory."""
    rows = []
    for job in plan["jobs"]:
        for result in sorted((output / "runs" / job["id"]).glob("attempt-*/result.json"), reverse=True):
            row = json.loads(result.read_text())
            if not row.get("native") or not row.get("model"):
                continue
            config=json.loads((result.parent / "config.json").read_text())
            if any(config.get(key)!=value for key,value in job.items()):
                raise ValueError("Completed configuration differs from program")
            native = reverify_events(result.parent / "native")
            model_root = Path(json.loads((result.parent / "model/trial.json").read_text())["runDirectory"])
            model = reverify_events(model_root)
            if native != row["native"] or model != row["model"]:
                raise ValueError("Completed pair evidence changed: " + str(result))
            rows.append(row)
            break
    return rows


def available_port(port):
    # Broker connections can leave TIME_WAIT after the owned listener exits.
    # SO_REUSEADDR permits that state, but does not displace a live listener.
    with socket.socket() as reservation:
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reservation.bind(("127.0.0.1", port))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--program", required=True)
    parser.add_argument("--program-sha256", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--continuation-budget-seconds", type=int)
    args = parser.parse_args()
    root, output, plan_path = Path(args.root), Path(args.output), Path(args.program)
    if file_digest(plan_path) != args.program_sha256:
        raise ValueError("Program changed before startup")
    plan = json.loads(plan_path.read_text())
    if args.continuation_budget_seconds is not None and (not args.resume or not 60 <= args.continuation_budget_seconds <= 21600):
        raise ValueError("Continuation requires resume and a bounded explicit allocation")
    frozen_path = root / "input-manifest.json"
    if file_digest(frozen_path) != plan["inputManifestSha256"]:
        raise ValueError("Runtime manifest changed")
    frozen = json.loads(frozen_path.read_text())
    for relative, sha in frozen["files"].items():
        if file_digest(root / relative) != sha:
            raise ValueError("Frozen input changed: " + relative)
    for path, sha in plan["launcherFiles"].items():
        if file_digest(path) != sha:
            raise ValueError("Scientific launcher changed: " + path)
    if args.resume:
        original = json.loads((output / "program-started.json").read_text())
        if original["programSha256"] != args.program_sha256:
            raise ValueError("Resume program identity differs")
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(original["startedUtc"])).total_seconds()
        rows = recovered_rows(output, plan)
        if args.continuation_budget_seconds is not None: elapsed = 0
        recovery = output / ("recovery-" + str(os.getpid()) + ".json")
        write_new(recovery, dict(observedAtUtc=clock(), previousFailure="OSError errno98 during port preflight; prior listener already exited",
            controllerSha256=file_digest(Path(__file__)), programSha256=args.program_sha256,
            reusedPairs=[r["id"] for r in rows], originalStartedUtc=original["startedUtc"], deadlineReset=args.continuation_budget_seconds is not None,
            continuationBudgetSeconds=args.continuation_budget_seconds, budgetAuthorizedBy="New explicit user request to launch Linux simulation" if args.continuation_budget_seconds else None))
    else:
        output.mkdir(parents=True, exist_ok=False)
        original = dict(programSha256=args.program_sha256, inputManifestSha256=plan["inputManifestSha256"],
                        startedUtc=clock(), controllerPid=os.getpid(), resources=plan["resources"], scientificCaveats=plan["scientificCaveats"])
        write_new(output / "program-started.json", original)
        elapsed = 0
        rows = []
    started = time.monotonic() - max(0, elapsed)
    deadline = started + (args.continuation_budget_seconds or plan["budgetSeconds"]) - 15
    state = dict(status="running", phase="initializing", controllerPid=os.getpid(), startedUtc=original["startedUtc"],
                 plannedNative=48, plannedModel=48, nativeCompleted=len(rows), modelCompleted=len(rows),
                 completedPairs=len(rows), technicalErrors=0, programSha256=args.program_sha256)
    state.update(continuationBudgetSeconds=args.continuation_budget_seconds, budgetStartedUtc=clock(), priorControllerFailure="Port reservation errno98" if args.resume else None)
    status_path = output / "progress.json"
    progress(status_path, state)
    metadata = {name: json.loads(Path(path).read_text())["brokers"][0] for name, path in plan["standMetadata"].items()}
    complete_ids = {row["id"] for row in rows}
    for index, job in enumerate(plan["jobs"]):
        if job["id"] in complete_ids:
            continue
        if time.monotonic() >= deadline:
            state.update(status="budgetExhausted", phase="stoppedBeforeNextPair")
            break
        if file_digest(plan_path) != args.program_sha256:
            raise RuntimeError("Frozen program changed during execution")
        if shutil.disk_usage(output).free < 10 * 1024**3:
            state.update(status="storageReserveReached", phase="stoppedBeforeNextPair")
            break
        parent = output / "runs" / job["id"]
        attempt = 1
        while (parent / f"attempt-{attempt:03d}").exists():
            attempt += 1
        case = parent / f"attempt-{attempt:03d}"
        case.mkdir(parents=True)
        port = 33083
        available_port(port)
        config = dict(job, endpoint=f"mqtt://127.0.0.1:{port}", profilePath=str(root / "profiles" / (job["broker"] + ".json")),
                      profileSha256=frozen["files"]["profiles/" + job["broker"] + ".json"],
                      runtimeIdentity="runtime-manifest-sha256:" + plan["inputManifestSha256"])
        config_path = case / "config.json"
        write_new(config_path, config)
        row = dict(broker=job["broker"], qos=job["qos"], publishers=job["publishers"], seed=job["seed"], repeat=job["repeat"], id=job["id"])
        for mode in ("native", "model"):
            broker = broker_log = None
            state.update(phase=mode, activeJob=job["id"], elapsedSeconds=time.monotonic() - started,
                         remainingBudgetSeconds=max(0, deadline - time.monotonic()))
            progress(status_path, state)
            if deadline - time.monotonic() < 10:
                row[mode + "Error"] = "global budget exhausted before job"
                continue
            try:
                if mode == "native":
                    broker, broker_log = broker_start(metadata[job["broker"]], case, port, deadline - time.monotonic())
                command = ["dotnet", str(root / "runtime/TwinValidationProbe.dll"), mode, str(config_path), str(case / mode)]
                receipt = execute(command, case / (mode + ".log"), "1,2" if mode == "native" else "1",
                                  max(1, min(plan["jobTimeoutSeconds"], deadline - time.monotonic() - 5)))
                raw = case / mode
                if mode == "model":
                    raw = Path(json.loads((raw / "trial.json").read_text())["runDirectory"])
                row[mode] = verify_events(raw)
                row[mode + "Process"] = receipt
                state[mode + "Completed"] += 1
            except Exception as error:
                row[mode + "Error"] = repr(error)
                state["technicalErrors"] += 1
                write_new(case / (mode + "-failure.json"), dict(error=repr(error), job=job, observedAtUtc=clock()))
            finally:
                if broker is not None:
                    terminate(broker)
                    broker_log.close()
        write_new(case / "result.json", row)
        rows.append(row)
        state.update(completedPairs=len(rows), elapsedSeconds=time.monotonic() - started,
                     remainingBudgetSeconds=max(0, deadline - time.monotonic()))
        progress(status_path, state)
    if state["status"] == "running":
        state["status"] = "completedWithTechnicalErrors" if state["technicalErrors"] else "completed"
    evaluation = comparisons(output, rows)
    state.update(phase="finished", activeJob=None, elapsedSeconds=time.monotonic() - started,
                 finishedUtc=clock(), comparison=evaluation)
    write_new(output / "summary.json", state | dict(rows=rows))
    progress(status_path, state)
    print(json.dumps(state))
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        if "--output" in sys.argv:
            directory = Path(sys.argv[sys.argv.index("--output") + 1])
            current = directory / "progress.json"
            if current.is_file():
                state = json.loads(current.read_text())
                state.update(status="failed", fatalError=repr(error), failedAtUtc=clock(), controllerPid=0)
                progress(current, state)
                write_new(directory / ("controller-failure-" + str(os.getpid()) + ".json"), state)
        raise
