"""The component registry: one row per component, kept as an Excel-friendly CSV.

Components are identified by an integer ID embedded in their name as ``[rm:ID]``. The
tag survives STEP round trips through any CAD program, so simplified models can always
be matched back to the original components, no matter how the user renames or moves
them in the assembly tree.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, fields

TAG_RE = re.compile(r"\s*\[rm:(\d+)\]")


def tag_name(name: str, component_id: int) -> str:
    return f"{TAG_RE.sub('', name).strip()} [rm:{component_id}]"


def untag_name(name: str) -> str:
    return TAG_RE.sub("", name).strip()


def find_id(names: Iterable[str]) -> int | None:
    """Return the ID of the deepest name that carries a tag."""
    for name in reversed(tuple(names)):
        match = TAG_RE.search(name)
        if match:
            return int(match.group(1))
    return None


VOID = "void"


@dataclass
class Component:
    id: int
    name: str
    path: str = ""
    material: str = ""
    density_g_cm3: float | None = None
    mass_g: float | None = None
    original_volume_cm3: float | None = None
    original_solids: int | None = None
    simplified_volume_cm3: float | None = None
    simplified_solids: int | None = None
    volume_diff_pct: float | None = None  # computed
    mcnp_volume_cm3: float | None = None
    dcf: float | None = None  # computed
    status: str = ""
    comment: str = ""

    COMPUTED = ("volume_diff_pct", "dcf")

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
            self.volume_diff_pct = (
                (self.simplified_volume_cm3 - self.original_volume_cm3)
                / self.original_volume_cm3
                * 100
            )
        self.dcf = None
        if self.original_volume_cm3 and self.mcnp_volume_cm3:
            self.dcf = self.original_volume_cm3 / self.mcnp_volume_cm3


COLUMNS = [f.name for f in fields(Component)]
_FLOATS = {f.name for f in fields(Component) if "float" in str(f.type)}
_INTS = {f.name for f in fields(Component) if "int" in str(f.type) and f.name != "id"}


class Registry:
    def __init__(self, components: Iterable[Component] = ()):
        self._components: dict[int, Component] = {}
        for component in components:
            self.add(component)

    def add(self, component: Component) -> None:
        if component.id in self._components:
            raise ValueError(f"Duplicated component id {component.id}")
        self._components[component.id] = component

    def __getitem__(self, component_id: int) -> Component:
        return self._components[component_id]

    def __contains__(self, component_id: int) -> bool:
        return component_id in self._components

    def __iter__(self) -> Iterator[Component]:
        return iter(sorted(self._components.values(), key=lambda c: c.id))

    def __len__(self) -> int:
        return len(self._components)

    @property
    def next_id(self) -> int:
        return max(self._components, default=0) + 1

    # --- persistence ---------------------------------------------------------------

    def save(self, path: str | os.PathLike) -> None:
        for component in self:
            component.update_computed()
        # utf-8-sig makes Excel recognise the encoding
        with open(path, "w", encoding="utf-8-sig", newline="") as outfile:
            writer = csv.writer(outfile)
            writer.writerow(COLUMNS)
            for component in self:
                writer.writerow(
                    _format(getattr(component, column)) for column in COLUMNS
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
    for column in COLUMNS:
        raw = (row.get(column) or "").strip()
        if column in Component.COMPUTED:
            continue
        if column == "id":
            values["id"] = int(raw)
        elif column in _FLOATS:
            values[column] = _parse_float(raw)
        elif column in _INTS:
            values[column] = int(raw) if raw else None
        else:
            values[column] = raw
    return Component(**values)  # type: ignore[arg-type]
