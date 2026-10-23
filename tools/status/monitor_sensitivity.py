#!/usr/bin/env python3
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta

PROGRESS = "/run/media/entityfx/hd250/mqtty-evidence/dissertation-sensitivity-v1/progress.json"
ROWS_DIR = "/run/media/entityfx/hd250/mqtty-evidence/dissertation-sensitivity-v1/rows"
INTERVAL = 5

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
            ["systemctl", "--user", "show", "mqtty-sensitivity-v1.service",
             "-p", "ActiveState,SubState,MainPID,Result"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [x.strip() for x in out.stdout.strip().splitlines() if x.strip()]
        return " | ".join(lines) if lines else "unknown"
    except Exception as e:
        return f"unavailable: {e}"


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


def group_stats(dim, active_ids):
    stats = {}
    try:
        for path in glob.glob(os.path.join(ROWS_DIR, "*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            rid = d.get("id", "")
            if rid in active_ids:
                continue
            parts = rid.split(".")
            if parts and parts[0] == "pilot":
                rest = parts[2:]
            else:
                rest = parts[1:]
            if len(rest) <= dim:
                continue
            key = rest[dim]
            st = stats.setdefault(key, {"runs": 0, "failed": 0, "wall": 0.0})
            st["runs"] += 1
            failed = bool(d.get("fidelityFailed"))
            for c in d.get("summary", {}).get("acceptanceCriteria", []):
                if c.get("status") == "failed":
                    failed = True
            if failed:
                st["failed"] += 1
            st["wall"] += float(d.get("wallSeconds", 0) or 0)
    except Exception:
        pass
    return stats


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


def broker_stats():
    stats = {}
    try:
        for path in glob.glob(os.path.join(ROWS_DIR, "*.json")):
            name = os.path.basename(path)
            parts = name.split(".")
            if parts and parts[0] == "pilot":
                broker = parts[1] if len(parts) > 1 else "?"
            else:
                broker = parts[0] if parts else "?"
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            st = stats.setdefault(broker, {"runs": 0, "failed": 0, "wall": 0.0})
            st["runs"] += 1
            failed = bool(d.get("fidelityFailed"))
            for c in d.get("summary", {}).get("acceptanceCriteria", []):
                if c.get("status") == "failed":
                    failed = True
            if failed:
                st["failed"] += 1
            st["wall"] += float(d.get("wallSeconds", 0) or 0)
    except Exception:
        pass
    return stats


def collect_runs(active_ids):
    runs = {}  # kind -> broker -> dict[(qos,pub,seed,var)] = status
    try:
        for path in glob.glob(os.path.join(ROWS_DIR, "*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            rid = d.get("id", "")
            kind = d.get("kind", "matrix")
            parts = rid.split(".")
            # broker may be after 'pilot.'
            if parts and parts[0] == "pilot":
                broker = parts[1] if len(parts) > 1 else "?"
                rest = parts[2:]
            else:
                broker = parts[0] if parts else "?"
                rest = parts[1:]
            # rest: m16, qX, pY, sZ, variant
            qos = rest[1] if len(rest) > 1 else "?"
            pub = rest[2] if len(rest) > 2 else "?"
            seed = rest[3] if len(rest) > 3 else "?"
            variant = rest[4] if len(rest) > 4 else "?"
            status = "passed"
            if d.get("technicalError"):
                status = "failed"
            elif d.get("fidelityFailed"):
                status = "failed"
            else:
                for c in d.get("summary", {}).get("acceptanceCriteria", []):
                    if c.get("status") == "failed":
                        status = "failed"
                        break
            if rid in active_ids:
                status = "running"
            runs.setdefault(kind, {}).setdefault(broker, {})[(qos, pub, seed, variant)] = status
    except Exception:
        pass
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
                        elif st == "running":
                            cells.append(f"{YELLOW}█{RESET}")
                        else:
                            cells.append(f"{DIM}░{RESET}")
                lines.append(f"{label} " + " ".join(cells))
    passed = failed = running = 0
    for bdata in data.values():
        for st in bdata.values():
            if st == "passed":
                passed += 1
            elif st == "failed":
                failed += 1
            elif st == "running":
                running += 1
    lines.append(f"{DIM}legend: {GREEN}█ok{RESET}{DIM} {RED}█fail{RESET}{DIM} {YELLOW}█run{RESET}{DIM} {DIM}░todo{RESET}{DIM}  "
                f"summary: ok={passed} fail={failed} run={running}{RESET}")
    lines.append("")


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
    TW = shutil.get_terminal_size(fallback=(120, 40)).columns
    if TW >= 110:
        lines.append(f"Pilots: {bar(pd, pt, width=18, color=BLUE)} {pd:>3}/{pt:<3} f={YELLOW}{pilots.get('failedCriteria', 0)}{RESET}   "
                     f"Matrix: {bar(md, mt, width=18, color=GREEN)} {md:>3}/{mt:<3} ({md / mt * 100:5.1f}%) f={YELLOW}{matrix.get('failedCriteria', 0)}{RESET}")
        lines.append(f"Replay: {bar(rd, rt, width=18, color=MAGENTA)} {rd:>3}/{rt:<3} f={YELLOW}{replay.get('failedCriteria', 0)}{RESET}   "
                     f"Overall: {bar(done, total, width=18, color=CYAN)} {done:>3}/{total:<3} ({done / total * 100:5.1f}%)")
    else:
        lines.append(f"Pilots:  {bar(pd, pt, width=18, color=BLUE)} {pd:>3}/{pt:<3}  failed={YELLOW}{pilots.get('failedCriteria', 0)}{RESET}")
        lines.append(f"Matrix:  {bar(md, mt, width=18, color=GREEN)} {md:>3}/{mt:<3} ({md / mt * 100:5.1f}%)  failed={YELLOW}{matrix.get('failedCriteria', 0)}{RESET}")
        lines.append(f"Replay:  {bar(rd, rt, width=18, color=MAGENTA)} {rd:>3}/{rt:<3}  failed={YELLOW}{replay.get('failedCriteria', 0)}{RESET}")
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
        lines.append(f"{DIM}Forecast: not enough completed units yet{RESET}")

    lines.append("")
    active_ids = set(data.get("active", {}).keys())
    stats = broker_stats()

    left = []
    right = []

    if stats:
        left.append(f"{BOLD}Brokers (rows/56):{RESET}")
        for broker in sorted(stats):
            st = stats[broker]
            avg = (st["wall"] / st["runs"]) if st["runs"] else 0
            left.append(f"  {broker:<10} {bar(st['runs'], 56, width=12, color=BLUE)} "
                        f"{st['runs']:>2}/56  f{st['failed']:<2}  avg {fmt(avg)}")
    else:
        left.append(f"{DIM}Brokers: no completed rows yet{RESET}")

    v_stats = group_stats(4, active_ids)
    q_stats = group_stats(1, active_ids)
    p_stats = group_stats(2, active_ids)
    s_stats = group_stats(3, active_ids)
    right.append(f"{BOLD}Stats by dimension:{RESET}")
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
        fail_rate = (total_failed / done * 100) if done else 0
        right.append(f"  Slowest: {slowest[0]} (avg {fmt(slowest[1]['wall']/slowest[1]['runs'])}, f{slowest[1]['failed']})   "
                     f"Fastest: {fastest[0]} (avg {fmt(fastest[1]['wall']/fastest[1]['runs'])})")
        right.append(f"  Failures: {total_failed}/{done} ({fail_rate:.1f}%)")

    TW = shutil.get_terminal_size(fallback=(120, 40)).columns
    if TW >= 120:
        colw = max(48, min(72, (TW - 6) // 2))
        lines.extend(side_by_side(left, right, width=colw))
    else:
        lines.extend(left)
        lines.append("")
        lines.extend(right)
    lines.append("")

    runs = collect_runs(active_ids)
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
