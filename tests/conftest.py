from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import models  # noqa: E402


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
composition = '''
26000.31c -0.7
24000.31c -0.3
'''

[Tungsten]
density = 19.3
composition = "74000.31c 1"

[Inconel]
number = 30
density = 8.19
""",
        encoding="utf-8",
    )
    return path
