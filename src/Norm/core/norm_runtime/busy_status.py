from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
_CPU_ACTIVE_CORE_PCT = 7.0
_GPU_ACTIVE_PCT = 20.0
_SAMPLE_INTERVAL_SECONDS = 0.25

_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


def _process_table() -> dict[int, tuple[int, str]]:
    if os.name != "nt":
        return {}
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        return {}
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    rows: dict[int, tuple[int, str]] = {}
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            rows[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID), str(entry.szExeFile).lower()
            )
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def _related_pids(table: dict[int, tuple[int, str]]) -> tuple[set[int], list[dict[str, Any]]]:
    root_pid = os.getpid()
    related = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _) in table.items():
            if ppid in related and pid not in related:
                related.add(pid)
                changed = True
    model_processes: list[dict[str, Any]] = []
    for pid, (_, name) in table.items():
        if name in {"ollama.exe", "llama-server.exe"}:
            related.add(pid)
            model_processes.append({"pid": pid, "name": name})
    return related, model_processes


def _filetime_value(value: _FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _cpu_times(pids: set[int]) -> dict[int, int]:
    if os.name != "nt":
        return {}
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    out: dict[int, int] = {}
    for pid in pids:
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        creation, exit_time, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
        try:
            if kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)
            ):
                out[pid] = _filetime_value(kernel) + _filetime_value(user)
        finally:
            kernel32.CloseHandle(handle)
    return out


def _nvidia_smi_path() -> str | None:
    candidates = [
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
        Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _gpu_utilization() -> float | None:
    exe = _nvidia_smi_path()
    if not exe:
        return None
    try:
        completed = subprocess.run(
            [exe, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return None
        values = [float(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
        return max(values) if values else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _resource_snapshot() -> dict[str, Any]:
    table = _process_table()
    related, model_processes = _related_pids(table)
    return {
        "monotonic": time.monotonic(),
        "cpu_times": _cpu_times(related),
        "gpu_pct": _gpu_utilization(),
        "related_process_count": len(related),
        "model_processes": model_processes,
    }


def _interval_vote(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    dt = max(0.001, float(current["monotonic"]) - float(previous["monotonic"]))
    prior_times = previous["cpu_times"]
    current_times = current["cpu_times"]
    cpu_ticks = sum(
        max(0, int(current_times[pid]) - int(prior_times[pid]))
        for pid in set(prior_times).intersection(current_times)
    )
    cpu_core_pct = (cpu_ticks / 10_000_000.0) / dt * 100.0
    gpu_pct = current.get("gpu_pct")
    model_present = bool(current.get("model_processes"))
    cpu_active = cpu_core_pct >= _CPU_ACTIVE_CORE_PCT
    gpu_active = gpu_pct is not None and float(gpu_pct) >= _GPU_ACTIVE_PCT and model_present
    return {
        "active": bool(cpu_active or gpu_active),
        "cpu_core_pct": round(cpu_core_pct, 2),
        "gpu_pct": None if gpu_pct is None else round(float(gpu_pct), 2),
        "cpu_active": cpu_active,
        "gpu_active": gpu_active,
        "model_processes": current.get("model_processes", []),
        "interval_ms": round(dt * 1000.0, 1),
    }


def sample_resource_activity(interval_seconds: float = _SAMPLE_INTERVAL_SECONDS) -> dict[str, Any]:
    previous = _resource_snapshot()
    votes: list[dict[str, Any]] = []
    for _ in range(2):
        time.sleep(interval_seconds)
        current = _resource_snapshot()
        votes.append(_interval_vote(previous, current))
        previous = current
    if votes[0]["active"] != votes[1]["active"]:
        time.sleep(interval_seconds)
        current = _resource_snapshot()
        votes.append(_interval_vote(previous, current))
    active_votes = sum(1 for vote in votes if vote["active"])
    return {
        "busy": active_votes > len(votes) // 2,
        "active_votes": active_votes,
        "sample_count": len(votes),
        "sample_interval_ms": int(round(interval_seconds * 1000)),
        "samples": votes,
    }


def collect_busy_status(
    *,
    chat_server=None,
    worker=None,
    prompt_queue=None,
    durable=None,
    ollama_active_calls: int = 0,
    resource_sampler: Callable[[], dict[str, Any]] = sample_resource_activity,
) -> dict[str, Any]:
    active_requests = int(getattr(chat_server, "active_request_count", 0) or 0)
    worker_idle = True if worker is None else bool(worker.is_idle())
    queue = {"stream_length": 0, "pending_count": 0, "retry_count": 0, "escalation_count": 0}
    if prompt_queue is not None:
        stats = prompt_queue.stats()
        queue["stream_length"] = int(stats.get("stream_length", 0) or 0)
        queue["pending_count"] = int(stats.get("pending_count", 0) or 0)
        queue["retry_count"] = int(prompt_queue.r.xlen(prompt_queue.retry_stream))
        queue["escalation_count"] = int(prompt_queue.r.xlen(prompt_queue.escalation_stream))

    running_tasks: list[dict[str, Any]] = []
    if durable is not None and hasattr(durable, "running_tasks"):
        running_tasks = durable.running_tasks(limit=20)

    resources = resource_sampler()
    queue_busy = any(int(queue[key]) > 0 for key in queue)
    tracked_busy = bool(active_requests or ollama_active_calls or not worker_idle or queue_busy)
    busy = bool(tracked_busy or resources.get("busy"))

    if active_requests and not running_tasks and not queue_busy:
        phase = "planning_or_prequeue"
    elif ollama_active_calls:
        phase = "ollama_generation"
    elif not worker_idle:
        phase = "worker_execution"
    elif queue_busy:
        phase = "queued_or_pending"
    elif active_requests:
        phase = "request_wait"
    elif resources.get("busy"):
        phase = "resource_activity"
    elif running_tasks:
        phase = "open_task_idle"
    else:
        phase = "idle"

    if tracked_busy:
        confidence = 1.0
    elif resources.get("busy"):
        confidence = 0.8
    elif running_tasks:
        confidence = 0.75
    else:
        confidence = 0.95

    return {
        "status": "ok",
        "busy": busy,
        "phase": phase,
        "confidence": confidence,
        "timestamp": datetime.now(NY_TZ).isoformat(timespec="milliseconds"),
        "signals": {
            "active_chat_requests": active_requests,
            "ollama_active_calls": int(ollama_active_calls),
            "worker_idle": worker_idle,
            "queue": queue,
            "running_tasks": running_tasks,
        },
        "resources": resources,
    }
