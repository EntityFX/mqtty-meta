#!/usr/bin/env python3
"""
Sensitivity v1 monitoring script with extended system checks.
Monitors:
- Sensitivity service status
- hd250 mount status
- QoS1 diagnostics service status
- Web service health endpoint
- System resources (CPU, memory, power)
"""
import glob
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

PROGRESS = "/run/media/entityfx/hd250/mqtty-evidence/dissertation-sensitivity-v1/progress.json"
ROWS_DIR = "/run/media/entityfx/hd250/mqtty-evidence/dissertation-sensitivity-v1/rows"
QOS1_PROGRESS = "/run/media/entityfx/hd250/mqtty-evidence/qos1-linux-diagnostics-v1/progress.json"
INTERVAL = 5
SERVICE = "mqtty-sensitivity-v1.service"
WEB_HEALTH_ENDPOINT = "http://127.0.0.1:5088/healthz"

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"

FILLED = "█"
EMPTY = "░"


def move_home():
    sys.stdout.write("\033[H")


def clear_to_end():
    sys.stdout.write("\033[J")


def bar(done, total, width=30, color=GREEN):
    if total <= 0:
        return "[" + " " * width + "]"
    filled = int(width * done / total)
    if filled > width:
        filled = width
    return f"[{color}{''.join(FILLED for _ in range(filled))}{RESET}{DIM}{''.join(EMPTY for _ in range(width - filled))}{RESET}]"


def fmt(seconds):
    seconds = int(seconds or 0)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def parse_active(key):
    parts = key.split(".")
    broker = parts[0] if len(parts) > 0 else "?"
    model = parts[1] if len(parts) > 1 else "?"
    qos = parts[2] if len(parts) > 2 else "?"
    publishers = parts[3] if len(parts) > 3 else "?"
    seed = parts[4] if len(parts) > 4 else "?"
    variant = parts[5] if len(parts) > 5 else ""
    return f"{broker} | {model} | {qos} | {publishers} | seed {seed} | {variant}"


def service_status():
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", SERVICE,
             "-p", "ActiveState,SubState,MainPID,Result"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [x.strip() for x in out.stdout.strip().splitlines() if x.strip()]
        return " | ".join(lines) if lines else "unknown"
    except Exception as e:
        return f"unavailable: {e}"


def qos1_service_status():
    """Check status of mqtty-qos1-diagnostics-v1.service."""
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", "mqtty-qos1-diagnostics-v1.service",
             "-p", "MainPID,ActiveState,SubState,Result,ExecMainStatus"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [x.strip() for x in out.stdout.strip().splitlines() if x.strip()]
        return " | ".join(lines) if lines else "unknown"
    except Exception as e:
        return f"unavailable: {e}"


def web_health_check():
    """Check Web service health endpoint."""
    try:
        out = subprocess.run(
            ["curl", "-fsS", WEB_HEALTH_ENDPOINT],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(out.stdout)
        return data
    except Exception as e:
        return {"status": "error", "error": str(e)}


def check_hd250_mount():
    """Check if hd250 is properly mounted."""
    try:
        out = subprocess.run(
            ["mount"],
            capture_output=True, text=True, timeout=10,
        )
        for line in out.stdout.splitlines():
            if "hd250" in line:
                parts = line.split()
                if len(parts) >= 3:
                    return {
                        "mounted": True,
                        "device": parts[0],
                        "mountPoint": parts[2],
                        "type": parts[4] if len(parts) > 4 else "unknown",
                        "line": line
                    }
        return {"mounted": False, "error": "hd250 not found in mount output"}
    except Exception as e:
        return {"mounted": False, "error": str(e)}


def qos1_progress_status():
    """Read QoS1 diagnostic progress status."""
    try:
        with open(QOS1_PROGRESS, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "status": data.get("status", "unknown"),
            "matrixAdmitted": data.get("matrixAdmitted", "unknown"),
            "jobId": data.get("jobId", "unknown")
        }
    except FileNotFoundError:
        return {"status": "not_started", "error": "progress file not found"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def forecast(done, total, elapsed):
    if done <= 0 or total <= done or elapsed <= 0:
        return None, None
    rate = done / elapsed
    remaining = total - done
    eta_seconds = remaining / rate
    finish = datetime.now() + timedelta(seconds=eta_seconds)
    return eta_seconds, finish


_CPU_PREV = None
_RAPL_PREV = None


def _read_cpu_times():
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            line = f.readline()
        parts = line.split()
        # user, nice, system, idle, iowait, irq, softirq, steal, guest, guest_nice
        vals = [int(x) for x in parts[1:11]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        return total, idle
    except Exception:
        return None


def _read_mem():
    try:
        info = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, rest = line.split(":", 1)
                info[key] = int(rest.strip().split()[0])  # kB
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used = max(total - avail, 0)
        return used / 1024.0 / 1024.0, total / 1024.0 / 1024.0  # GiB
    except Exception:
        return None


def _read_cpu_temp():
    try:
        with open("/sys/class/hwmon/hwmon0/temp1_input", "r", encoding="utf-8") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None


def _read_rapl_watts():
    global _RAPL_PREV
    path = "/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"
    try:
        with open(path, "r", encoding="utf-8") as f:
            energy = int(f.read().strip())
    except Exception:
        return None
    now = time.time()
    if _RAPL_PREV is None:
        _RAPL_PREV = (energy, now)
        return None
    prev_energy, prev_time = _RAPL_PREV
    dt = now - prev_time
    if dt <= 0:
        _RAPL_PREV = (energy, now)
        return None
    # handle counter wrap
    max_range = 262143328850
    delta = energy - prev_energy
    if delta < 0:
        delta += max_range
    watts = delta / 1e6 / dt
    _RAPL_PREV = (energy, now)
    return watts


def _read_gpu_power():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        vals = []
        for line in out.stdout.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    vals.append(float(line))
                except ValueError:
                    pass
        if vals:
            return sum(vals)
    except Exception:
        pass
    return None


def system_stats():
    global _CPU_PREV
    cpu_percent = None
    cur = _read_cpu_times()
    if cur is not None:
        if _CPU_PREV is not None:
            prev_total, prev_idle = _CPU_PREV
            dt_total = cur[0] - prev_total
            dt_idle = cur[1] - prev_idle
            if dt_total > 0:
                cpu_percent = (1.0 - dt_idle / dt_total) * 100.0
        _CPU_PREV = cur

    mem = _read_mem()
    mem_str = None
    if mem is not None:
        used, total = mem
        mem_str = (used, total)

    return {
        "cpu_percent": cpu_percent,
        "mem": mem_str,
        "cpu_temp": _read_cpu_temp(),
        "cpu_energy_w": _read_rapl_watts(),
        "gpu_power_w": _read_gpu_power(),
    }


def read_rows():
    rows = []
    for path in glob.glob(os.path.join(ROWS_DIR, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                rows.append(json.load(f))
        except (OSError, ValueError):
            continue  # A row being atomically published may not yet be readable.
    return rows


def row_identity(row):
    parts = row.get("id", "").split(".")
    kind = row.get("kind") or (parts[0] if parts[0] in ("pilot", "replay") else "matrix")
    if parts[0] in ("pilot", "replay"):
        parts = parts[1:]
    return kind, parts[0], parts[1:]


def fidelity_failed(row):
    return bool(row.get("fidelityFailed")) or any(
        c.get("status") == "failed"
        for c in row.get("summary", {}).get("acceptanceCriteria", []))


def accumulate(stats, key, row):
    st = stats.setdefault(key, {"runs": 0, "failed": 0, "technical": 0, "wall": 0.0})
    st["runs"] += 1
    st["failed"] += int(fidelity_failed(row))
    st["technical"] += int(bool(row.get("technicalError")))
    st["wall"] += float(row.get("wallSeconds", 0) or 0)


def group_stats(dim, active_ids, rows=None, kind="matrix"):
    stats = {}
    for row in read_rows() if rows is None else rows:
        cohort, _, rest = row_identity(row)
        if cohort != kind or row.get("id") in active_ids or len(rest) <= dim:
            continue
        accumulate(stats, rest[dim], row)
    return stats


def replay_stats(rows):
    result = {"equal": 0, "mismatched": 0, "unchecked": 0, "fidelityFailed": 0}
    for row in rows:
        if row_identity(row)[0] != "replay":
            continue
        value = row.get("replayEqual")
        result["equal" if value is True else "mismatched" if value is False else "unchecked"] += 1
        result["fidelityFailed"] += int(fidelity_failed(row))
    return result


def replay_detail(rows, total):
    st = replay_stats(rows)
    return (f"Replay identity: equal={st['equal']}/{total} "
            f"mismatch={st['mismatched']} unchecked={st['unchecked']}; "
            f"fidelity failed={st['fidelityFailed']} (separate test)")


def fmt_stats_line(stats, order=None):
    if not stats:
        return ""
    keys = order if order else sorted(stats.keys())
    parts = []
    for k in keys:
        st = stats.get(k)
        if not st:
            continue
        avg = (st["wall"] / st["runs"]) if st["runs"] else 0
        parts.append(f"{k}: {st['runs']} runs, fail={st['failed']}, avg {fmt(avg)}")
    return " | ".join(parts)
def visible_len(s):
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def side_by_side(left, right, gap=4, width=78):
    n = max(len(left), len(right))
    left = list(left) + [""] * (n - len(left))
    right = list(right) + [""] * (n - len(right))
    out = []
    for l, r in zip(left, right):
        pad = max(0, width - visible_len(l))
        out.append(l + " " * pad + " " * gap + r)
    return out


def broker_stats(rows=None, kind="matrix", active_ids=()):
    stats = {}
    for row in read_rows() if rows is None else rows:
        cohort, broker, _ = row_identity(row)
        if cohort == kind and row.get("id") not in active_ids:
            accumulate(stats, broker, row)
    return stats


def broker_lines(rows, counts, active_ids=()):
    matrix = broker_stats(rows, "matrix", active_ids)
    pilots = broker_stats(rows, "pilot", active_ids)
    brokers = sorted(set(matrix) | set(pilots))
    matrix_total = counts.get("matrix", {}).get("total", 216) // 4
    pilot_total = counts.get("pilot", {}).get("total", 8) // 4
    lines = [f"{BOLD}Brokers (matrix / pilot separately; f=criteria failures):{RESET}"]
    for broker in brokers:
        st = matrix.get(broker, {"runs": 0, "failed": 0, "wall": 0})
        pilot = pilots.get(broker, {"runs": 0, "failed": 0})
        avg = st["wall"] / st["runs"] if st["runs"] else 0
        lines.append(f"  {broker:<10} {bar(st['runs'], matrix_total, width=12, color=BLUE)} "
                     f"matrix {st['runs']}/{matrix_total} f{st['failed']} "
                     f"pilot {pilot['runs']}/{pilot_total} f{pilot['failed']} "
                     f"matrix avg {fmt(avg)}")
    return lines


def collect_runs(active_ids, rows=None):
    runs = {}
    for row in read_rows() if rows is None else rows:
        kind, broker, rest = row_identity(row)
        if len(rest) < 5:
            continue
        status = "passed"
        if row.get("technicalError"):
            status = "technical"
        elif fidelity_failed(row):
            status = "failed"
        if row.get("id") in active_ids:
            status = "running"
        runs.setdefault(kind, {}).setdefault(broker, {})[tuple(rest[1:5])] = status
    return runs


def render_matrix_block(lines, title, kind, runs, active_ids):
    data = runs.get(kind, {})
    if not data:
        return
    brokers = sorted(data.keys())
    qos_list = ["q0", "q1", "q2"]
    pub_list = ["p1", "p128"]
    seed_list = ["s42", "s43", "s44"]
    var_list = ["baseline", "doubleWindow", "halfTick"]
    lines.append(f"{BOLD}{title}{RESET}")
    # header
    header = " " * 16 + "".join(f"{v[:3]:>3}" for v in var_list for _ in seed_list)
    # simplify: group by variant then seed
    lines.append(f"{DIM}{'broker':<9} {'qos':<3} {'pub':<4} " +
                " ".join(f"{v[0].upper()}{s[-2:]}" for v in var_list for s in seed_list) + RESET)
    for broker in brokers:
        bdata = data.get(broker, {})
        for qos in qos_list:
            for pub in pub_list:
                row_has_data = any(
                    bdata.get((qos, pub, seed, var), "missing") != "missing"
                    for var in var_list for seed in seed_list
                )
                if not row_has_data:
                    continue
                label = f"{broker:<9} {qos:<3} {pub:<4}"
                cells = []
                for var in var_list:
                    for seed in seed_list:
                        st = bdata.get((qos, pub, seed, var), "missing")
                        if st == "passed":
                            cells.append(f"{GREEN}█{RESET}")
                        elif st == "failed":
                            cells.append(f"{RED}█{RESET}")
                        elif st == "technical":
                            cells.append(f"{MAGENTA}!{RESET}")
                        elif st == "running":
                            cells.append(f"{YELLOW}█{RESET}")
                        else:
                            cells.append(f"{DIM}░{RESET}")
                lines.append(f"{label} " + " ".join(cells))
    passed = failed = running = technical = 0
    for bdata in data.values():
        for st in bdata.values():
            if st == "passed":
                passed += 1
            elif st == "failed":
                failed += 1
            elif st == "technical":
                technical += 1
            elif st == "running":
                running += 1
    lines.append(f"{DIM}legend: {GREEN}█ok{RESET}{DIM} {RED}█criteria{RESET}{DIM} {MAGENTA}!technical{RESET}{DIM} {YELLOW}█run{RESET}{DIM} {DIM}░todo{RESET}{DIM}  "
                f"summary: ok={passed} criteriaFail={failed} technical={technical} run={running}{RESET}")
    lines.append("")


def sensitivity_main():
    while True:
        move_home()
        clear_to_end()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = []
        lines.append(f"{BOLD}{CYAN}Sensitivity v1{RESET}  {DIM}{now} MSK{RESET}")
        lines.append(f"{DIM}File: {PROGRESS}{RESET}")
        lines.append(f"Service: {service_status()}")
        lines.append("")

        try:
            with open(PROGRESS, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            lines.append(f"{RED}Cannot read progress.json: {e}{RESET}")
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
            time.sleep(INTERVAL)
            continue

        counts = data.get("counts", {})
        pilots = counts.get("pilot", {})
        matrix = counts.get("matrix", {})
        replay = counts.get("replay", {})

        pd = pilots.get("done", 0)
        pt = pilots.get("total", 8)
        md = matrix.get("done", 0)
        mt = matrix.get("total", 216)
        rd = replay.get("done", 0)
        rt = replay.get("total", 6)

        total = pt + mt + rt
        done = pd + md + rd
        elapsed = data.get("elapsedSeconds", 0)

        lines.append(f"Technical errors: {GREEN}{data.get('technicalErrors', 0)}{RESET}    "
                     f"Workers: {data.get('workers', '?')}    "
                     f"Elapsed: {BOLD}{fmt(elapsed)}{RESET}")
        lines.append("")
        ss = system_stats()
        cpu = f"{ss['cpu_percent']:.0f}%" if ss['cpu_percent'] is not None else "n/a"
        mem = ss['mem']
        mem_str = f"{mem[0]:.1f}/{mem[1]:.1f} GiB" if mem else "n/a"
        temp = f"{ss['cpu_temp']:.0f}°C" if ss['cpu_temp'] is not None else "n/a"
        cpu_e = f"{ss['cpu_energy_w']:.1f} W" if ss['cpu_energy_w'] is not None else "n/a (no perm)"
        gpu_e = f"{ss['gpu_power_w']:.1f} W" if ss['gpu_power_w'] is not None else "n/a"
        lines.append(f"System: CPU {cpu} ({temp})   Mem {mem_str}   CPU power {cpu_e}   GPU power {gpu_e}")
        lines.append("")

        # Check hd250 mount
        mount_info = check_hd250_mount()
        if mount_info.get("mounted"):
            lines.append(f"{GREEN}hd250 mounted:{RESET} {mount_info['device']} on {mount_info['mountPoint']} ({mount_info['type']})")
        else:
            lines.append(f"{RED}hd250 mount issue:{RESET} {mount_info.get('error', 'unknown')}")

        # Check QoS1 diagnostics service
        lines.append(f"QoS1 diagnostics service: {qos1_service_status()}")
        qos1_status = qos1_progress_status()
        lines.append(f"QoS1 progress: {qos1_status.get('status', 'unknown')} | Matrix admitted: {qos1_status.get('matrixAdmitted', 'unknown')}")

        # Check Web service health
        web_health = web_health_check()
        if web_health.get("status") == "ok":
            lines.append(f"{GREEN}Web service: OK{RESET} | PID: {web_health.get('processId', '?')} | RSS: {web_health.get('processWorkingSetBytes', 0) // (1024**2)} MB | Running: {web_health.get('runningJobs', 0)} | Queued: {web_health.get('queuedJobs', 0)}")
        else:
            lines.append(f"{RED}Web service: ERROR{RESET} | {web_health.get('error', 'unknown')}")

        lines.append("")
        TW = shutil.get_terminal_size(fallback=(120, 40)).columns
        if TW >= 110:
            lines.append(f"Pilots: {bar(pd, pt, width=18, color=BLUE)} {pd:>3}/{pt:<3} criteriaFail={YELLOW}{pilots.get('failedCriteria', 0)}{RESET}   "
                         f"Matrix: {bar(md, mt, width=18, color=GREEN)} {md:>3}/{mt:<3} ({md / mt * 100:5.1f}%) criteriaFail={YELLOW}{matrix.get('failedCriteria', 0)}{RESET}")
            lines.append(f"Replay: {bar(rd, rt, width=18, color=MAGENTA)} {rd:>3}/{rt:<3} criteriaFail={YELLOW}{replay.get('failedCriteria', 0)}{RESET}   "
                         f"Overall: {bar(done, total, width=18, color=CYAN)} {done:>3}/{total:<3} ({done / total * 100:5.1f}%)")
        else:
            lines.append(f"Pilots:  {bar(pd, pt, width=18, color=BLUE)} {pd:>3}/{pt:<3}  criteriaFail={YELLOW}{pilots.get('failedCriteria', 0)}{RESET}")
            lines.append(f"Matrix:  {bar(md, mt, width=18, color=GREEN)} {md:>3}/{mt:<3} ({md / mt * 100:5.1f}%)  criteriaFail={YELLOW}{matrix.get('failedCriteria', 0)}{RESET}")
            lines.append(f"Replay:  {bar(rd, rt, width=18, color=MAGENTA)} {rd:>3}/{rt:<3}  criteriaFail={YELLOW}{replay.get('failedCriteria', 0)}{RESET}")
            lines.append(f"Overall: {bar(done, total, width=18, color=CYAN)} {done:>3}/{total:<3} ({done / total * 100:5.1f}%)")
        lines.append("")

        eta_seconds, finish_time = forecast(done, total, elapsed)
        if eta_seconds is not None:
            plan = ""
            est = data.get("estimatedFullMatrixWallHoursAfterPilot")
            if isinstance(est, dict):
                lower = est.get("lower")
                upper = est.get("upper")
                if lower is not None and upper is not None:
                    plan = f"  (plan {lower:.1f}-{upper:.1f}h)"
            lines.append(f"{BOLD}Forecast:{RESET} ETA {BOLD}{fmt(eta_seconds)}{RESET}  "
                         f"finish ~ {finish_time.strftime('%Y-%m-%d %H:%M')} MSK{plan}")
        else:
            lines.append(f"{DIM}Forecast: complete{RESET}" if done >= total else f"{DIM}Forecast: not enough completed units yet{RESET}")

        lines.append("")
        active_ids = set(data.get("active", {}).keys())
        rows = read_rows()
        stats = broker_stats(rows, active_ids=active_ids)

        left = []
        right = []

        left.extend(broker_lines(rows, counts, active_ids))
        left.append(replay_detail(rows, rt))

        v_stats = group_stats(4, active_ids, rows)
        q_stats = group_stats(1, active_ids, rows)
        p_stats = group_stats(2, active_ids, rows)
        s_stats = group_stats(3, active_ids, rows)
        right.append(f"{BOLD}Stats by dimension (matrix only):{RESET}")
        right.append(f"  Variants: {fmt_stats_line(v_stats, ['baseline','doubleWindow','halfTick'])}")
        right.append(f"  QoS:      {fmt_stats_line(q_stats, ['q0','q1','q2'])}")
        right.append(f"  Pub:      {fmt_stats_line(p_stats, ['p1','p128'])}")
        right.append(f"  Seeds:    {fmt_stats_line(s_stats, ['s42','s43','s44'])}")

        right.append("")
        right.append(f"{BOLD}Heuristics:{RESET}")
        if elapsed and elapsed > 0:
            thr = done / (elapsed / 3600.0)
            right.append(f"  Throughput: {thr:.2f} units/h   Remaining matrix: {mt-md} units")
            m_eta, m_finish = forecast(md, mt, elapsed)
            if m_eta is not None:
                right.append(f"  Matrix ETA (linear): {fmt(m_eta)}  finish ~ {m_finish.strftime('%H:%M')} MSK")
        if stats:
            slowest = max(stats.items(), key=lambda kv: (kv[1]['wall'] / kv[1]['runs']) if kv[1]['runs'] else 0)
            fastest = min(stats.items(), key=lambda kv: (kv[1]['wall'] / kv[1]['runs']) if kv[1]['runs'] else 0)
            total_failed = sum(st['failed'] for st in stats.values())
            matrix_rows = sum(st["runs"] for st in stats.values())
            fail_rate = (total_failed / matrix_rows * 100) if matrix_rows else 0
            right.append(f"  Slowest: {slowest[0]} (avg {fmt(slowest[1]['wall']/slowest[1]['runs'])}, f{slowest[1]['failed']})   "
                         f"Fastest: {fastest[0]} (avg {fmt(fastest[1]['wall']/fastest[1]['runs'])})")
            right.append(f"  Matrix criteria failures: {total_failed}/{matrix_rows} ({fail_rate:.1f}%)")

        TW = shutil.get_terminal_size(fallback=(120, 40)).columns
        if TW >= 120:
            colw = max(48, min(72, (TW - 6) // 2))
            lines.extend(side_by_side(left, right, width=colw))
        else:
            lines.extend(left)
            lines.append("")
            lines.extend(right)
        lines.append("")

        runs = collect_runs(active_ids, rows)
        pilot_tmp = []
        render_matrix_block(pilot_tmp, "Pilot matrix (B/D/H variants, seeds 42-44):", "pilot", runs, active_ids)
        matrix_tmp = []
        render_matrix_block(matrix_tmp, "Experiment matrix (B/D/H variants, seeds 42-44):", "matrix", runs, active_ids)
        TW = shutil.get_terminal_size(fallback=(120, 40)).columns
        if TW >= 130:
            colw = max(50, (TW - 6) // 2)
            lines.extend(side_by_side(pilot_tmp, matrix_tmp, width=colw))
        else:
            lines.extend(pilot_tmp)
            lines.append("")
            lines.extend(matrix_tmp)
        lines.append("")
        active = data.get("active", {})
        if active:
            lines.append(f"{BOLD}Active:{RESET}")
            for key, info in active.items():
                rss = info.get("rssBytes", 0) / (1024 ** 3)
                peak = info.get("peakRssBytes", 0) / (1024 ** 3)
                lines.append(f"  {GREEN}●{RESET} {parse_active(key)}  "
                             f"elapsed {fmt(info.get('elapsedSeconds', 0))}  "
                             f"RSS {rss:.2f}/{peak:.2f} GiB")
        else:
            lines.append(f"{DIM}Active: none{RESET}")

        lines.append("")
        lines.append(f"{DIM}Press Ctrl+C to exit. Refresh every {INTERVAL}s.{RESET}")

        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        time.sleep(INTERVAL)



def network_lines(data, rows, budget, now_epoch=None):
    now_epoch = time.time() if now_epoch is None else now_epoch
    counts = data.get("counts", {})
    active = data.get("active") or {}
    jobs = active.get("jobs", {})
    lines = [f"State: {data.get('status', '?')} | Phase: {data.get('phase', '?')} | Workers: {active.get('workers', '?')}"]
    done = total = 0
    for kind, default_total in (("matrix", 162), ("replay", 6)):
        group = counts.get(kind, {})
        n, target = group.get("done", 0), group.get("total", default_total)
        done += n; total += target
        lines.append(f"{kind.capitalize():8} {bar(n,target,24)}  ✅ {n}/{target} / ❌{group.get('unresolvedErrors',0)} / ⚠{group.get('warnings',0)}")
    lines.append(f"Overall  {bar(done,total,24,CYAN)}  {done}/{total} ({100*done/total if total else 0:.1f}%)")
    lines.append("Preserved pilots: separate composite basis; not new runs. Optional 54 diagnostics excluded.")
    remaining = max(0, budget.get("deadlineEpoch", now_epoch)-now_epoch)
    elapsed = max(0, now_epoch-budget.get("startedEpoch", now_epoch))
    finish = datetime.fromtimestamp(budget["deadlineEpoch"],timezone(timedelta(hours=3))) if budget.get("deadlineEpoch") else None
    lines.append(f"Elapsed: {fmt(elapsed)} | Budget remaining: {fmt(remaining)}" + (f" | Hard stop ~ {finish:%d.%m %H:%M:%S} MSK" if finish else ""))
    if done and done < total and elapsed:
        eta = elapsed*(total-done)/done
        lines.append(f"Linear ETA: {fmt(eta)} (load-dependent estimate; " + ("exceeds budget" if eta>remaining else "within current budget") + ")")
    latest = {}
    for row in rows:
        key = row.get("id", "")
        if row.get("attemptNumber",0) >= latest.get(key,{}).get("attemptNumber",-1): latest[key]=row
    lines += ["", "Mode             ✅done / ❌technical / ⚠warnings (matrix only)"]
    for mode in ("single", "relay", "federation"):
        selected = [r for key,r in latest.items() if key.startswith("matrix.") and len(key.split('.'))>2 and key.split('.')[2]==mode]
        ok = sum(not r.get("technicalError") for r in selected)
        failed = sum(bool(r.get("technicalError")) for r in selected)
        warnings = sum(bool(r.get("warnings")) for r in selected)
        lines.append(f"{mode:15} ✅{ok:3}/54 / ❌{failed} / ⚠{warnings}")
    lines += ["", "Active model processes:"]
    if not jobs: lines.append("  none")
    for key, state in jobs.items():
        lines.append(f"  ● {key} | PID {state.get('pid','?')} | {fmt(state.get('elapsedSeconds',0))} | RSS {state.get('rssBytes',0)/1024**3:.2f} GiB")
    if data.get("reason"): lines += ["", "Stop reason: " + str(data['reason'])]
    lines += ["", "Warning = model delivery/queue result; technical error = execution/verification failure."]
    return lines


def network_main(once=False):
    while True:
        move_home(); clear_to_end()
        lines = [f"{BOLD}{CYAN}Network campaign: 162 + 6{RESET}", f"File: {PROGRESS}", f"Service: {service_status()}"]
        try:
            with open(PROGRESS, encoding="utf-8") as stream: data=json.load(stream)
            try:
                with open(os.path.join(os.path.dirname(PROGRESS),'wall-budget.json'), encoding="utf-8") as stream: budget=json.load(stream)
            except (OSError,ValueError): budget={}
            lines += network_lines(data,read_rows(),budget)
        except (OSError,ValueError) as error: lines.append(f"Cannot read campaign state: {error}")
        ss=system_stats();mem=ss['mem']
        cpu=f"{ss['cpu_percent']:.0f}%" if ss['cpu_percent'] is not None else 'n/a'
        temperature=f"{ss['cpu_temp']:.0f}°C" if ss['cpu_temp'] is not None else 'n/a'
        memory=f"{mem[0]:.1f}/{mem[1]:.1f} GiB" if mem else 'n/a'
        power=f"{ss['cpu_energy_w']:.1f} W" if ss['cpu_energy_w'] is not None else 'n/a (no perm)'
        gpu=f"{ss['gpu_power_w']:.1f} W" if ss['gpu_power_w'] is not None else 'n/a'
        lines += ["", f"System: CPU {cpu} ({temperature})   Mem {memory}   CPU power {power}   GPU power {gpu}"]
        mount=check_hd250_mount();lines.append(f"hd250: {mount}")
        web=web_health_check();lines.append(f"Web: {web.get('status','error')} | Running {web.get('runningJobs','?')} | Queued {web.get('queuedJobs','?')}")
        lines.append("This campaign is an external CLI controller; Web lists its own job registry.")
        lines.append(f"Press Ctrl+C to exit. Refresh every {INTERVAL}s.")
        sys.stdout.write('\n'.join(lines)+'\n');sys.stdout.flush()
        if once: return
        time.sleep(INTERVAL)


def main(argv=None):
    global PROGRESS, ROWS_DIR, SERVICE, INTERVAL
    # Programmatic callers retain the original sensitivity dashboard.
    if argv is None: return sensitivity_main()
    parser=argparse.ArgumentParser()
    parser.add_argument('--campaign',choices=('sensitivity','network'),default='sensitivity')
    parser.add_argument('--study')
    parser.add_argument('--service')
    parser.add_argument('--interval',type=float,default=5)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args(argv)
    if args.interval<1:parser.error('Refresh interval must be at least one second')
    INTERVAL=args.interval
    if args.study:
        PROGRESS=os.path.join(args.study,'progress.json');ROWS_DIR=os.path.join(args.study,'rows')
    elif args.campaign=='network':
        study='/run/media/entityfx/hd250/mqtty-evidence/network-full-3h-v1'
        PROGRESS=os.path.join(study,'progress.json');ROWS_DIR=os.path.join(study,'rows')
    if args.service:SERVICE=args.service
    elif args.campaign=='network':SERVICE='mqtty-network-full-3h-v1.service'
    if args.campaign=='network':return network_main(args.once)
    return sensitivity_main()

if __name__ == "__main__":
    main(sys.argv[1:])
