#!/usr/bin/env python3
"""Read-only, one-shot health check. No SSH, retries, signals, or input edits."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def service_fields(value):
    if isinstance(value, dict):
        return value
    return dict(line.split("=", 1) for line in (value or "").splitlines() if "=" in line)


def analyze(snapshot, now=None, stale_seconds=30):
    """Judge only observed evidence; missing observations stay unknown."""
    now = time.time() if now is None else now
    findings = []

    def add(level, code, message, **evidence):
        findings.append(dict(level=level, code=code, message=message, evidence=evidence))

    observed = snapshot.get("observedAtUtc")
    fresh = False
    if observed is None:
        add("warning", "observationTimeUnknown", "Время наблюдения неизвестно: актуальность состояния не подтверждена.")
    else:
        age = now - timestamp(observed)
        fresh = -5 <= age <= stale_seconds
        if age > stale_seconds:
            add("warning", "snapshotStale", "Снимок устарел; он не показывает состояние процесса сейчас.", ageSeconds=age)
        elif age < -5:
            add("warning", "clockMismatch", "Время снимка находится в будущем; проверьте часы.", ageSeconds=age)

    progress = snapshot.get("progress", {})
    mtime = snapshot.get("progressMtimeUtc")
    if mtime is None:
        add("info", "heartbeatUnknown", "Нет времени изменения progress.json: остановку обновлений оценить нельзя.")
    elif observed is not None:
        lag = timestamp(observed) - timestamp(mtime)
        if lag > stale_seconds and progress.get("status") == "running":
            add("warning", "heartbeatStale", "На момент снимка progress.json давно не обновлялся; проверьте службу и журнал.", lagSeconds=lag)

    counts = progress.get("counts", {})
    done = sum(v.get("done", 0) for v in counts.values())
    total = sum(v.get("total", 0) for v in counts.values())
    fidelity = sum(v.get("failedCriteria", 0) for v in counts.values())
    technical = progress.get("technicalErrors")
    if technical:
        add("critical", "technicalErrors", "Есть технические ошибки; сохраняйте попытки и проверьте причину перед повтором.", count=technical, reason=progress.get("reason"))
    if fidelity:
        add("info", "fidelityFailures", "Нарушены критерии точности модели. Это отдельный результат, не доказательство падения процесса.", count=fidelity)
    if progress.get("status") == "failed":
        add("critical", "campaignFailed", "Управляющий процесс сообщил остановку с ошибкой.", reason=progress.get("reason"))
    service = service_fields(snapshot.get("service"))
    if service.get("ActiveState") in ("failed", "inactive") and total > done and progress.get("status") != "completed":
        add("critical", "serviceStoppedIncomplete", "На момент снимка служба остановлена, а программа не завершена.", state=service.get("ActiveState"), result=service.get("Result"))

    limits = snapshot.get("limits", {})
    processes = snapshot.get("processes", {})
    for identifier, active in progress.get("active", {}).items():
        pid = str(active.get("pid"))
        process = processes.get(pid, {})
        if process.get("alive") is False:
            add("warning", "activeProcessMissing", "Указанный в progress.json PID отсутствовал при проверке. Возможна смена задания между чтениями; подтвердите новым снимком.", job=identifier, pid=pid)
        elapsed = active.get("elapsedSeconds")
        timeout = limits.get("timeoutSeconds")
        if timeout and elapsed is not None and elapsed >= .8 * timeout:
            add("critical" if elapsed >= timeout else "warning", "timeoutApproaching", "Попытка приблизилась к техническому лимиту времени; не менять научные входы и не считать её зависшей только по длительности.", job=identifier, elapsedSeconds=elapsed, timeoutSeconds=timeout)
        rss = active.get("peakRssBytes", active.get("rssBytes"))
        limit = limits.get("maxRssBytes")
        if limit and rss is not None and rss >= .8 * limit:
            add("critical" if rss >= limit else "warning", "rssApproaching", "Память попытки близка к лимиту. Оцените число параллельных заданий до новых запусков.", job=identifier, bytes=rss, limitBytes=limit)

    mount = snapshot.get("mountAvailable")
    if mount is False:
        add("critical", "mountMissing", "Диск доказательств не смонтирован. Новые записи и очистка запрещены.")
    disk = snapshot.get("disk", {})
    free = disk.get("free", disk.get("freeBytes"))
    reserve = limits.get("minFreeBytes")
    if free is not None and reserve:
        if free < reserve:
            add("critical", "diskReserveViolated", "Свободное место ниже резерва; не запускать новые попытки и сохранить исходные данные.", freeBytes=free, reserveBytes=reserve)
        elif free < 2 * reserve:
            add("warning", "diskReserveApproaching", "Запас свободного места меньше двух резервов; оцените объём оставшейся программы.", freeBytes=free, reserveBytes=reserve)
    if mount is None or free is None or reserve is None:
        add("info", "storageEvidenceIncomplete", "Неполные сведения о mount, свободном месте или резерве; безопасность записи не подтверждена.")
    if snapshot.get("collectionErrors"):
        add("warning", "collectionIncomplete", "Часть проверок не выполнена; сведения об ошибках сохранены.", errors=snapshot["collectionErrors"])
    severity = "critical" if any(f["level"] == "critical" for f in findings) else "warning" if any(f["level"] == "warning" for f in findings) else "ok"
    return dict(schemaVersion=1, observedAtUtc=observed, observationFresh=fresh, status=severity,
                completed=done, total=total, scientificFailedCriteria=fidelity,
                technicalErrors=technical, findings=findings,
                policy="Read-only; no automatic restart or deletion. Freshness is required before operational action.")


def collect(root, mount, service):
    """Run locally on Linux; caller may periodically export its JSON using an existing monitor."""
    if not sys.platform.startswith("linux"):
        raise ValueError("Collection is Linux-only; use --snapshot to analyze exported observations on this host")
    root, mount = Path(root).resolve(), Path(mount).resolve()
    root.relative_to(mount)
    errors = []
    path = root / "progress.json"
    progress = json.loads(path.read_text(encoding="utf-8"))
    result = dict(observedAtUtc=datetime.now(timezone.utc).isoformat(), root=str(root), progress=progress,
                  progressMtimeUtc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                  mountAvailable=os.path.ismount(mount), processes={})
    try:
        plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
        result["limits"] = {"timeoutSeconds": plan.get("jobTimeoutSeconds"), "maxRssBytes": plan.get("jobRssLimitBytes"), "minFreeBytes": 5 * 1024**3}
        result["limitsSource"] = "Frozen plan limits; v1 storage reserve is 5 GiB. Operational amendments must be supplied separately."
    except (OSError, ValueError) as error:
        errors.append("plan: " + str(error))
    try:
        result["disk"] = shutil.disk_usage(mount)._asdict()
    except OSError as error:
        errors.append("disk: " + str(error))
    for active in progress.get("active", {}).values():
        pid = int(active["pid"])
        process = Path(f"/proc/{pid}/stat")
        try:
            stat = process.read_text()
            fields = stat[stat.rfind(")") + 2:].split()
            result["processes"][str(pid)] = {"alive": fields[0] != "Z", "state": fields[0], "startTicks": int(fields[19])}
        except FileNotFoundError:
            result["processes"][str(pid)] = {"alive": False}
        except (OSError, ValueError, IndexError) as error:
            errors.append(f"PID {pid}: {error}")
    try:
        probe = subprocess.run(["systemctl", "--user", "show", service, "-p", "ActiveState,SubState,MainPID,Result"], capture_output=True, text=True, timeout=10, check=True)
        result["service"] = service_fields(probe.stdout)
    except (OSError, subprocess.SubprocessError) as error:
        errors.append("systemctl: " + str(error))
    result["collectionErrors"] = errors
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path)
    source.add_argument("--root", type=Path)
    parser.add_argument("--mount", type=Path)
    parser.add_argument("--service", default="mqtty-sensitivity-v1.service")
    parser.add_argument("--save-snapshot", type=Path)
    parser.add_argument("--stale-seconds", type=float, default=30)
    args = parser.parse_args()
    if args.stale_seconds <= 0:
        parser.error("--stale-seconds must be positive")
    if args.root and args.mount is None:
        parser.error("--root requires --mount")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot else collect(args.root, args.mount, args.service)
    if args.save_snapshot:
        # Exclusive creation preserves the old observation instead of overwriting it.
        with args.save_snapshot.open("x", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(analyze(snapshot, stale_seconds=args.stale_seconds), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
