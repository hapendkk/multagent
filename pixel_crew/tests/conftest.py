import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DB", tmp_path / "memory.db")


@pytest.fixture(autouse=True)
def isolated_telemetry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TELEMETRY_DIR", tmp_path / "telemetry")
