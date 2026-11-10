"""Песочница рендера на Windows: Job Object + песочница Chromium + блокировка сети в Playwright.

Слои защиты:
  - лимиты памяти, числа процессов и kill-on-close задаёт Windows Job Object;
  - Chromium запускается с собственной песочницей;
  - Playwright обрывает все запросы кроме file:/data:/about: и записывает попытки.

Если Job Object создать не удалось, рендер НЕ запускается: возвращается
available=False (fail-closed).
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path

import config

kernel32 = ctypes.windll.kernel32

_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD)]


class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t)]


WORKER = Path(__file__).resolve().parent / "render_worker.py"


def _create_job():
    """Job Object с лимитом памяти (на всю job и на процесс), лимитом процессов
    и KILL_ON_JOB_CLOSE: если наш процесс умрёт, песочница умрёт вместе с ним."""
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(f"CreateJobObjectW: {ctypes.get_last_error()}")
    info = _EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_PROCESS_MEMORY)
    info.BasicLimitInformation.ActiveProcessLimit = config.SANDBOX_PIDS
    mb = config.SANDBOX_MEMORY_MB * 1024 * 1024
    info.JobMemoryLimit = mb
    info.ProcessMemoryLimit = mb
    if not kernel32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(job)
        raise OSError(f"SetInformationJobObject: {ctypes.get_last_error()}")
    return job


def _assign(job, pid):
    h = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not h:
        raise OSError(f"OpenProcess({pid}): {ctypes.get_last_error()}")
    try:
        if not kernel32.AssignProcessToJobObject(job, h):
            raise OSError(f"AssignProcessToJobObject({pid}): {ctypes.get_last_error()}")
    finally:
        kernel32.CloseHandle(h)


def _kill_job(job):
    kernel32.TerminateJobObject(job, 1)


def render(html, job_root=None):
    cid = "win-" + uuid.uuid4().hex[:12]
    job_dir = Path(job_root or config.OUTPUT_DIR / "sandbox_jobs") / cid
    job_in, job_out = job_dir / "in", job_dir / "out"
    for d in (job_in, job_out):
        d.mkdir(parents=True, exist_ok=True)
    (job_in / "screen.html").write_text(html, encoding="utf-8")

    env = dict(os.environ)  # PLAYWRIGHT_BROWSERS_PATH не трогаем: playwright сам найдёт браузеры

    cmd = [sys.executable, str(WORKER), str(job_in / "screen.html"), str(job_out)]
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, cwd=str(job_out))
    try:
        job = _create_job()
    except OSError as e:
        proc.kill()
        return {"available": False, "reason": f"не удалось создать Job Object: {e}"}
    try:
        try:
            _assign(job, proc.pid)
        except OSError as e:
            _kill_job(job)
            return {"available": False, "reason": f"не удалось назначить процесс в Job Object: {e}"}
        try:
            _, err = proc.communicate(timeout=config.SANDBOX_TIMEOUT)
            status = "ok" if proc.returncode == 0 else "error"
            stderr = (err or "")[-800:]
        except subprocess.TimeoutExpired:
            _kill_job(job)
            proc.communicate()
            status, stderr = "timeout", f"превышен лимит {config.SANDBOX_TIMEOUT} с"
    finally:
        kernel32.CloseHandle(job)   # KILL_ON_JOB_CLOSE: добивает всё, что осталось

    out = {"available": True, "status": status, "sandbox": "Windows Job Object + Chromium sandbox",
           "job_dir": str(job_dir), "seconds": round(time.time() - t0, 2),
           "limits": {"network": "browser-level (Playwright route)", "memory_mb": config.SANDBOX_MEMORY_MB,
                      "processes": config.SANDBOX_PIDS, "timeout_s": config.SANDBOX_TIMEOUT}}
    result_file = job_out / "result.json"
    if status == "ok" and result_file.exists():
        out.update(json.loads(result_file.read_text(encoding="utf-8")))
        for v in out.get("viewports", {}).values():
            v["screenshot"] = str(job_out / v["screenshot"])
    else:
        out["error"] = stderr
    return out
