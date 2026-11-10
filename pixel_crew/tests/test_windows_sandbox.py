"""Windows-бэкенд песочницы: Job Object реально ограничивает память и убивает
дерево процессов. Пропускается вне Windows."""
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only backend")

import ctypes  # noqa: E402

from sandbox import windows_runner  # noqa: E402

kernel32 = ctypes.windll.kernel32


def _spawn(code):
    return subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _close(job):
    kernel32.CloseHandle(job)


def test_job_memory_limit_kills_runaway():
    """Процесс, который выделяет больше SANDBOX_MEMORY_MB, должен быть убит job'ом."""
    job = windows_runner._create_job()
    try:
        p = _spawn("blocks=[]\n"
                   "for _ in range(200):\n"
                   "    blocks.append(bytearray(64 * 1024 * 1024))\n"
                   "print('survived')")
        windows_runner._assign(job, p.pid)
        try:
            out, _ = p.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            windows_runner._kill_job(job)
            p.communicate()
            pytest.fail("процесс не убит и не завершился — лимит памяти не сработал")
        assert p.returncode != 0 and "survived" not in out
    finally:
        _close(job)


def test_small_process_completes():
    job = windows_runner._create_job()
    try:
        p = _spawn("print('ok')")
        windows_runner._assign(job, p.pid)
        out, _ = p.communicate(timeout=30)
        assert p.returncode == 0 and "ok" in out
    finally:
        _close(job)


def test_render_screenshot_uses_windows_runner(monkeypatch):
    """Инструмент валидатора идёт в windows_runner, а не в обход песочницы."""
    import tools
    seen = {}

    def fake(html):
        seen["html"] = html
        return {"available": True, "status": "ok"}

    monkeypatch.setattr(windows_runner, "render", fake)
    res = tools.render_screenshot("<html></html>")
    assert res["available"] is True and seen["html"] == "<html></html>"
