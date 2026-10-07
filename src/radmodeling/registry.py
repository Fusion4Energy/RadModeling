"""The component registry: one row per body, kept as an Excel-friendly CSV.

Components are identified by an integer ID embedded in their name as ``[rmID]``. The
tag survives STEP round trips through any CAD program, so simplified models can always
be matched back to the original components, no matter how the user renames or moves
them in the assembly tree.

The tag belongs to the part definition, so all the instances of a repeated part (the
same bolt placed 20 times) share it and stay linked in the CAD program. Each instance
is a row of its own, keyed by ``(id, instance)`` and told apart by its centre of mass.
The assembly hierarchy is written as ``level_1``, ``level_2``... columns so that the
registry can be filtered by subassembly in Excel.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, fields
from typing import NamedTuple

# No colon ("[rm:12]"): SpaceClaim's STEP export drops colons from names. The STEP
# written by 'export' tags each solid with its instance too: "[rm12.3]"
TAG_RE = re.compile(r"\s*\[rm(\d+)(?:\.(\d+))?\]")


def tag_name(name: str, component_id: int) -> str:
    return f"{TAG_RE.sub('', name).strip()} [rm{component_id}]"


def key_name(name: str, key: Key) -> str:
    """Name tagged with a registry key, tag first: converters copy the names of the
    solids into the cell comments, and some cut or drop the end of long names."""
    return f"[rm{key}] {TAG_RE.sub('', name).strip()}"


def untag_name(name: str) -> str:
    return TAG_RE.sub("", name).strip()


def find_id(names: Iterable[str]) -> int | None:
    """Return the ID of the deepest name that carries a tag."""
    for name in reversed(tuple(names)):
        match = TAG_RE.search(name)
        if match:
            return int(match.group(1))
    return None


def find_key(name: str) -> Key | None:
    """Return the registry key of a name tagged with its instance (``[rm12.3]``)."""
    for match in TAG_RE.finditer(name):
        if match.group(2) is not None:
            return Key(int(match.group(1)), int(match.group(2)))
    return None


VOID = "void"

# Volume difference with the original accepted for a simplified component ('check'),
# and for its MCNP cells ('compare'): within it, or within DCF_SIGMAS times its
# uncertainty, the density correction factor is 1. The registry shows the factor that
# 'materials' applies, never a raw volume ratio
VOLUME_TOLERANCE_PCT = 1.0
DCF_SIGMAS = 3.0


class Key(NamedTuple):
    """Identifies a registry row: the component ID and the instance number."""

    id: int
    instance: int = 1

    def __str__(self) -> str:
        return f"{self.id}.{self.instance}"

    @classmethod
    def parse(cls, text: str) -> Key:
        """Read ``'3.7'`` (component 3, instance 7) or ``'3'`` (instance 1)."""
        component_id, _, instance = text.strip().partition(".")
        return cls(int(component_id), int(instance) if instance else 1)


@dataclass
class Component:
    id: int
    name: str
    instance: int = 1
    levels: tuple[str, ...] = ()  # names of the assemblies above, from the root
    material: str = ""
    density_g_cm3: float | None = None
    mass_g: float | None = None
    original_volume_cm3: float | None = None
    original_solids: int | None = None
    simplified_volume_cm3: float | None = None
    simplified_solids: int | None = None
    volume_diff_pct: float | None = None  # computed
    mcnp_volume_cm3: float | None = None
    mcnp_volume_std_cm3: float | None = None  # statistical uncertainty (1 sigma)
    dcf: float | None = None  # written by 'compare', can be edited afterwards
    status: str = ""
    comment: str = ""
    x_cm: float | None = None  # centre of mass of the original body
    y_cm: float | None = None
    z_cm: float | None = None

    COMPUTED = ("volume_diff_pct",)

    @property
    def key(self) -> Key:
        return Key(self.id, self.instance)

    @property
    def centre_cm(self) -> tuple[float, float, float] | None:
        if self.x_cm is None or self.y_cm is None or self.z_cm is None:
            return None
        return self.x_cm, self.y_cm, self.z_cm

    @property
    def is_void(self) -> bool:
        return self.material.strip().lower() == VOID

    @property
    def density(self) -> float | None:
        """Explicit density, or else the mass divided by the original volume."""
        if self.density_g_cm3 is not None:
            return self.density_g_cm3
        if self.mass_g is not None and self.original_volume_cm3:
            return self.mass_g / self.original_volume_cm3
        return None

    def update_computed(self) -> None:
        self.volume_diff_pct = None
        if self.original_volume_cm3 and self.simplified_volume_cm3 is not None:
            # 3 decimals: below that it is round-off (volumes are saved with .6g)
            diff = (
                self.simplified_volume_cm3 - self.original_volume_cm3
            ) / self.original_volume_cm3
            self.volume_diff_pct = round(diff * 100, 3) + 0.0  # + 0.0 avoids -0.0

    @property
    def volume_ratio(self) -> float | None:
        """Original volume / MCNP volume: the density correction factor, if needed."""
        if not (self.original_volume_cm3 and self.mcnp_volume_cm3):
            return None
        return self.original_volume_cm3 / self.mcnp_volume_cm3

    @property
    def manual_dcf(self) -> bool:
        """Whether the DCF was edited by hand: neither 1 nor the volume ratio."""
        if self.dcf is None or self.dcf == 1.0:
            return False
        ratio = self.volume_ratio
        # 1e-5: the volumes and the DCF are saved with 6 significant digits
        return ratio is None or not math.isclose(self.dcf, ratio, rel_tol=1e-5)

    def set_mcnp_volume(
        self,
        volume_cm3: float,
        std_cm3: float,
        tolerance_pct: float = VOLUME_TOLERANCE_PCT,
    ) -> None:
        """Store the MCNP volume from 'compare' and the DCF it gives.

        The DCF is 1 when the MCNP volume differs from the original one by less than
        the tolerance or than DCF_SIGMAS times its uncertainty. A DCF edited by hand
        is overwritten.
        """
        self.mcnp_volume_cm3, self.mcnp_volume_std_cm3 = volume_cm3, std_cm3
        ratio = self.volume_ratio
        if ratio is None:
            self.dcf = None
            return
        deviation = abs(ratio - 1)
        uncertainty = ratio * std_cm3 / volume_cm3
        if deviation <= tolerance_pct / 100 or deviation < DCF_SIGMAS * uncertainty:
            self.dcf = 1.0
        else:
            self.dcf = ratio


_FIELDS = [f.name for f in fields(Component) if f.name != "levels"]
_KEY = ("id", "instance")
_VALUES = [c for c in _FIELDS if c not in _KEY]  # after the level_N columns
_FLOATS = {f.name for f in fields(Component) if "float" in str(f.type)}
_INTS = {f.name for f in fields(Component) if f.type in ("int", "int | None")}
LEVEL_RE = re.compile(r"level_(\d+)$")


def columns(depth: int) -> list[str]:
    """CSV columns of a registry whose deepest component is ``depth`` levels down."""
    levels = [f"level_{i}" for i in range(1, depth + 1)]
    return [*_KEY, *levels, *_VALUES]


class Registry:
    def __init__(self, components: Iterable[Component] = ()):
        self._components: dict[Key, Component] = {}
        for component in components:
            self.add(component)

    def add(self, component: Component) -> None:
        if component.key in self._components:
            raise ValueError(f"Duplicated component {component.key}")
        self._components[component.key] = component

    def __getitem__(self, key: tuple[int, int]) -> Component:
        return self._components[Key(*key)]

    def __contains__(self, key: tuple[int, int]) -> bool:
        return Key(*key) in self._components

    def __iter__(self) -> Iterator[Component]:
        return iter(sorted(self._components.values(), key=lambda c: c.key))

    def __len__(self) -> int:
        return len(self._components)

    @property
    def ids(self) -> list[int]:
        return sorted({key.id for key in self._components})

    def instances(self, component_id: int) -> list[Component]:
        """The rows of a component, by instance number."""
        return [c for c in self if c.id == component_id]

    @property
    def next_id(self) -> int:
        return max((key.id for key in self._components), default=0) + 1

    # --- persistence ---------------------------------------------------------------

    def save(self, path: str | os.PathLike) -> None:
        for component in self:
            component.update_computed()
        depth = max((len(c.levels) for c in self), default=0)
        # utf-8-sig makes Excel recognise the encoding
        with open(path, "w", encoding="utf-8-sig", newline="") as outfile:
            writer = csv.writer(outfile)
            writer.writerow(columns(depth))
            for component in self:
                levels = [*component.levels, *[""] * (depth - len(component.levels))]
                writer.writerow(
                    [
                        *map(_format, component.key),
                        *levels,
                        *(_format(getattr(component, c)) for c in _VALUES),
                    ]
                )

    @classmethod
    def load(cls, path: str | os.PathLike) -> Registry:
        text = _read_text(path)
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        missing = {"id", "name"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        registry = cls()
        for line, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            try:
                component = _parse_row(row)
                registry.add(component)
            except ValueError as exc:
                raise ValueError(f"{path}, line {line}: {exc}") from exc
            component.update_computed()
        return registry


def _format(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _read_text(path: str | os.PathLike) -> str:
    """Read a CSV saved by Excel or any editor, whatever its encoding."""
    with open(path, "rb") as infile:
        data = infile.read()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AssertionError("latin-1 decodes any byte sequence")


def _parse_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    # Excel in many European locales writes decimal commas
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    value = float(text)
    if math.isnan(value):
        return None
    return value


def _parse_row(row: dict[str, str | None]) -> Component:
    values: dict[str, object] = {}
    for column in _FIELDS:
        raw = (row.get(column) or "").strip()
        if column in Component.COMPUTED:
            continue
        if column == "id":
            values["id"] = int(raw)
        elif column == "instance":
            values["instance"] = int(raw) if raw else 1
        elif column in _FLOATS:
            values[column] = _parse_float(raw)
        elif column in _INTS:
            values[column] = int(raw) if raw else None
        else:
            values[column] = raw
    levels = sorted(
        (int(match.group(1)), (value or "").strip())
        for column, value in row.items()
        if column and (match := LEVEL_RE.match(column))
    )
    names = [name for _, name in levels]
    while names and not names[-1]:
        names.pop()
    values["levels"] = tuple(names)
    return Component(**values)  # type: ignore[arg-type]
