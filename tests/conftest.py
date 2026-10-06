from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import models  # noqa: E402


@pytest.fixture(autouse=True)
def single_job(monkeypatch):
    """Commands run in this process unless a test asks for workers (--jobs): starting
    processes takes longer than the small test models."""
    monkeypatch.setattr("radmodeling.parallel.default_jobs", lambda: 1)


@pytest.fixture
def cad_model(tmp_path: Path) -> Path:
    return models.write_cad(tmp_path / "machine.step")


@pytest.fixture
def library_file(tmp_path: Path) -> Path:
    path = tmp_path / "library.toml"
    path.write_text(
        """
[SS316L]
number = 10
density = 7.93
aliases = ["316L"]

[Tungsten]
density = 19.3

[Inconel]
number = 30
density = 8.19
""",
        encoding="utf-8",
    )
    return path
