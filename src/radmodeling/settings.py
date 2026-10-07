"""Per-user settings, set once with ``radmod config`` and read by every command.

They are a JSON file in the user's application folder, which the SpaceClaim tools
read too (the loaders written by ``radmod spaceclaim-loaders`` carry its path).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import click

from radmodeling.registry import VOLUME_TOLERANCE_PCT

ENVIRONMENT = "RADMODELING_SETTINGS"  # another settings file, for tests

# name: (default, help)
SETTINGS = {
    "volume-tolerance": (
        VOLUME_TOLERANCE_PCT,
        "Volume difference allowed with the original [%]: in 'check', in the "
        "density correction factors of 'compare' and in the SpaceClaim tools.",
    ),
    "max-discrepancy": (
        1.0,
        "CAD/MCNP discrepancy that makes an instance a problem in 'compare' and "
        "'atlas' [%].",
    ),
}


@dataclass(frozen=True)
class Settings:
    volume_tolerance: float = SETTINGS["volume-tolerance"][0]
    max_discrepancy: float = SETTINGS["max-discrepancy"][0]


def path() -> Path:
    """The settings file (it may not exist yet)."""
    if given := os.environ.get(ENVIRONMENT):
        return Path(given)
    return Path(click.get_app_dir("radmodeling")) / "settings.json"


def stored() -> dict[str, float]:
    """The settings saved in the file, without the defaults."""
    try:
        values = json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read the settings file {path()}: {exc}") from exc
    return {k: float(v) for k, v in values.items() if k in SETTINGS}


def load() -> Settings:
    values = stored()
    return Settings(**{k.replace("-", "_"): v for k, v in values.items()})


def _check_name(name: str) -> None:
    if name not in SETTINGS:
        raise ValueError(f"unknown setting '{name}' (known: {', '.join(SETTINGS)})")


def set_value(name: str, text: str) -> float:
    """Save a setting; returns its value."""
    _check_name(name)
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a number") from None
    if not value > 0:
        raise ValueError(f"{name} must be more than 0")
    values = stored()
    values[name] = value
    _save(values)
    return value


def unset(name: str) -> None:
    """Restore the default of a setting."""
    _check_name(name)
    values = stored()
    if values.pop(name, None) is not None:
        _save(values)


def _save(values: dict[str, float]) -> None:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(values, indent=1) + "\n", encoding="utf-8")
