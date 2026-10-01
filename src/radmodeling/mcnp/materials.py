"""Assignment of materials, densities and density correction factors to MCNP cells.

The material library is a TOML file with one table per material::

    [SS316L-IG]
    number = 2              # MCNP material number (optional, else auto-assigned)
    density = 7.93          # g/cm3, default for the components made of it
    aliases = ["316L", "AISI 316L"]
    composition = '''
        26000.31c -0.6532
        24000.31c -0.1700
    '''

Material names in the registry are matched case-insensitively against the table names
and their aliases.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import migjorn

from radmodeling.cellmap import CellMap
from radmodeling.registry import Component, Key, Registry

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

MAX_LINE = 128  # MCNP6 line length limit
COMMENT_TAG = "rm:"


@dataclass
class Material:
    name: str
    number: int | None = None
    density: float | None = None
    aliases: list[str] = field(default_factory=list)
    composition: str = ""


class MaterialLibrary:
    def __init__(self, materials: list[Material] = ()):  # type: ignore[assignment]
        self.materials = list(materials)
        self._index: dict[str, Material] = {}
        for material in self.materials:
            for key in [material.name, *material.aliases]:
                key = _key(key)
                if key in self._index:
                    raise ValueError(f"Material name or alias {key!r} is duplicated")
                self._index[key] = material

    @classmethod
    def load(cls, path: str | os.PathLike) -> MaterialLibrary:
        with open(path, "rb") as infile:
            data = tomllib.load(infile)
        materials = []
        for name, values in data.items():
            if not isinstance(values, dict):
                raise ValueError(f"{path}: {name!r} must be a table")
            unknown = set(values) - {"number", "density", "aliases", "composition"}
            if unknown:
                raise ValueError(f"{path}: unknown keys {sorted(unknown)} in {name!r}")
            materials.append(Material(name=name, **values))
        return cls(materials)

    def get(self, name: str) -> Material | None:
        return self._index.get(_key(name))


def _key(name: str) -> str:
    return " ".join(name.lower().split())


@dataclass
class AssignmentReport:
    material_numbers: dict[str, int] = field(default_factory=dict)
    cells_per_material: dict[str, int] = field(default_factory=dict)
    mass_per_material_g: dict[str, float] = field(default_factory=dict)
    cards_added: list[int] = field(default_factory=list)
    cards_already_defined: list[int] = field(default_factory=list)
    without_composition: list[str] = field(default_factory=list)
    dcf_out_of_range: list[tuple[Key, float]] = field(default_factory=list)
    void_components: int = 0


class AssignmentError(ValueError):
    pass


def assign_materials(
    model: migjorn.Model,
    registry: Registry,
    cell_map: CellMap,
    library: MaterialLibrary,
    first_cell: int = 1,
    use_dcf: bool = True,
    dcf_range: tuple[float, float] = (0.8, 1.25),
) -> AssignmentReport:
    """Write materials, densities and component comments into the cells of a model.

    Each instance of a component gets the material and density of its own row.

    Every problem found (missing cells, materials or densities) is collected and
    raised together in a single :class:`AssignmentError`, so that the registry can be
    fixed in one pass.
    """
    report = AssignmentReport()
    errors: list[str] = []
    cells_by_instance = cell_map.cells_by_instance(first_cell)

    # Material numbers: explicit ones first, then auto-assign the rest
    used_numbers = {m.number for m in library.materials if m.number is not None}
    used_numbers |= {m.id for m in model.materials() if m.id is not None}
    next_number = max(used_numbers, default=0) + 1

    def material_number(material: Material) -> int:
        nonlocal next_number
        if material.number is None:
            material.number = next_number
            next_number += 1
        return material.number

    used: dict[str, Material] = {}
    for key, cells in sorted(cells_by_instance.items()):
        if key not in registry:
            errors.append(f"Component {key} is not in the registry")
            continue
        component = registry[key]
        missing_cells = [n for n in cells if model.cell(n) is None]
        if missing_cells:
            errors.append(
                f"Component {key}: cells {missing_cells} not found in the "
                "MCNP input (is the first cell number right?)"
            )
            continue

        if not component.material.strip():
            errors.append(f"Component {key} ({component.name}): no material")
            continue
        if component.is_void:
            report.void_components += 1
            for number in cells:
                cell = model.cell(number)
                assert cell is not None
                cell.material = 0
                _add_comment(cell, component, None)
            continue

        material = library.get(component.material)
        if material is None:
            errors.append(
                f"Component {key}: material {component.material!r} is not in "
                "the material library"
            )
            continue
        density = component.density or material.density
        if density is None:
            errors.append(f"Component {key} ({component.name}): no density")
            continue

        dcf = 1.0
        if use_dcf and component.mcnp_volume_cm3 and component.original_volume_cm3:
            dcf = component.original_volume_cm3 / component.mcnp_volume_cm3
            if not dcf_range[0] <= dcf <= dcf_range[1]:
                report.dcf_out_of_range.append((key, dcf))

        number = material_number(material)
        used[material.name] = material
        for cell_number in cells:
            cell = model.cell(cell_number)
            assert cell is not None
            cell.material = number
            cell.density = -float(f"{density * dcf:.6g}")
            _add_comment(cell, component, dcf)
        report.cells_per_material[material.name] = report.cells_per_material.get(
            material.name, 0
        ) + len(cells)
        if component.original_volume_cm3:
            report.mass_per_material_g[material.name] = (
                report.mass_per_material_g.get(material.name, 0.0)
                + density * component.original_volume_cm3
            )

    if errors:
        raise AssignmentError("\n".join(errors))

    defined = {m.id for m in model.materials() if m.id is not None}
    for material in sorted(used.values(), key=lambda m: m.number or 0):
        assert material.number is not None
        report.material_numbers[material.name] = material.number
        if material.number in defined:
            report.cards_already_defined.append(material.number)
        elif material.composition.strip():
            model.add_material(_material_card(material))
            report.cards_added.append(material.number)
        else:
            report.without_composition.append(material.name)
    return report


def _add_comment(cell: migjorn.Cell, component: Component, dcf: float | None) -> None:
    if f"$ {COMMENT_TAG}" in cell.text:
        return  # already processed
    comment = f"{COMMENT_TAG}{component.key} {component.name}"
    if dcf is not None and abs(dcf - 1.0) > 1e-4:
        comment += f" DCF={dcf:.4f}"
    last_line = cell.text.rstrip("\r\n").split("\n")[-1]
    room = MAX_LINE - len(last_line) - 3
    if room < len(COMMENT_TAG) + len(str(component.key)):
        return
    cell.append_comment(f"$ {comment[:room]}")


def _material_card(material: Material) -> str:
    assert material.number is not None
    lines = [f"m{material.number}  $ {material.name}"[:MAX_LINE]]
    for line in material.composition.strip().splitlines():
        line = line.strip()
        if line:
            lines.append(f"      {line}")
    return "\n".join(lines)
