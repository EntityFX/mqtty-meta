"""Run one owned process with observable resource limits and tree cleanup."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import signal
import subprocess
import time


def rss_bytes(pid, *, peak=False):
    if os.name != "nt":
        try:
            lines = Path(f"/proc/{pid}/status").read_text().splitlines()
            key = "VmHWM:" if peak else "VmRSS:"
            return int(next(line for line in lines if line.startswith(key)).split()[1]) * 1024
        except (FileNotFoundError, StopIteration):
            return 0
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [(s, ctypes.c_size_t) for s in
                    ("peakWorkingSet", "workingSet", "quotaPeakPaged", "quotaPaged", "quotaPeakNonPaged", "quotaNonPaged", "pagefile", "peakPagefile")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel.OpenProcess(0x410, False, pid)
    if not process:
        return 0
    try:
        c = Counters(); c.cb = ctypes.sizeof(c)
        api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not api(process, ctypes.byref(c), c.cb):
            raise OSError(ctypes.get_last_error(), "Cannot read process RSS")
        return c.peakWorkingSet if peak else c.workingSet
    finally:
        kernel.CloseHandle(process)


def tree_bytes(path):
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def kill_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def execute(command, attempt, guard, timeout, rss_limit, byte_budget, on_state, *, sample_seconds=1, memory_reader=rss_bytes):
    started = time.monotonic(); process = None; peak = 0; reason = None; code = None
    try:
        guard.check()
        if byte_budget <= 0:
            raise RuntimeError("studyDiskBudget")
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        with (attempt / "process.log").open("xb") as log:
            process = subprocess.Popen(command, cwd=attempt, stdout=log, stderr=subprocess.STDOUT, **kwargs)
            while process.poll() is None:
                guard.check()
                memory = memory_reader(process.pid)
                peak = max(peak, memory)
                elapsed = time.monotonic() - started
                if memory == 0 and elapsed > 2 and process.poll() is None:
                    raise RuntimeError("processRssUnavailable")
                if elapsed > timeout:
                    raise TimeoutError("jobTimeout")
                if peak > rss_limit:
                    raise RuntimeError("jobRssLimit")
                if tree_bytes(attempt) > byte_budget:
                    raise RuntimeError("studyDiskBudget")
                on_state({"pid": process.pid, "elapsedSeconds": elapsed, "rssBytes": memory, "peakRssBytes": peak})
                time.sleep(sample_seconds)
            code = process.wait()
        guard.check()
        if tree_bytes(attempt) > byte_budget:
            raise RuntimeError("studyDiskBudget")
    except BaseException as error:
        reason = type(error).__name__ + ": " + str(error)
    finally:
        if process is not None:
            kill_tree(process)
            code = process.returncode
    return {"returnCode": code, "technicalError": reason, "processWallSeconds": time.monotonic() - started,
            "peakRssBytes": peak, "pid": process.pid if process else None}
