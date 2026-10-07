"""Assignment of materials, densities and density correction factors to MCNP cells.

The material library is a TOML file with one table per material::

    [SS316L-IG]
    number = 2              # MCNP material number (optional, else auto-assigned)
    density = 7.93          # g/cm3, default for the components made of it
    aliases = ["316L", "AISI 316L"]

Material names in the registry are matched case-insensitively against the table names
and their aliases. Only the cells are edited: the material cards (compositions) are
defined elsewhere, and the material numbers used without a card are reported.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import migjorn

from radmodeling.mcnp.tags import cells_by_instance
from radmodeling.registry import Key, Registry

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class Material:
    name: str
    number: int | None = None
    density: float | None = None
    aliases: list[str] = field(default_factory=list)


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
            unknown = set(values) - {"number", "density", "aliases"}
            if unknown:
                raise ValueError(f"{path}: unknown keys {sorted(unknown)} in {name!r}")
            materials.append(Material(name=name, **values))
        return cls(materials)

    def get(self, name: str) -> Material | None:
        return self._index.get(_key(name))

    def names_by_number(self) -> dict[int, str]:
        """Names to show for the MCNP material numbers, such as 'SS316L (m10)'."""
        return {
            m.number: f"{m.name} (m{m.number})"
            for m in self.materials
            if m.number is not None
        }


def _key(name: str) -> str:
    return " ".join(name.lower().split())


@dataclass
class AssignmentReport:
    material_numbers: dict[str, int] = field(default_factory=dict)
    cells_per_material: dict[str, int] = field(default_factory=dict)
    mass_per_material_g: dict[str, float] = field(default_factory=dict)
    missing_cards: list[int] = field(default_factory=list)  # used, not in the input
    dcf_out_of_range: list[tuple[Key, float]] = field(default_factory=list)
    # Each instance with a material is in one of these, by its registry 'dcf':
    dcf_applied: list[Key] = field(default_factory=list)  # the volume ratio
    dcf_negligible: list[Key] = field(default_factory=list)  # 1
    dcf_manual: list[Key] = field(default_factory=list)  # edited by hand
    dcf_missing: list[Key] = field(default_factory=list)  # empty: no 'compare'
    void_components: int = 0


class AssignmentError(ValueError):
    pass


def assign_materials(
    model: migjorn.Model,
    registry: Registry,
    library: MaterialLibrary,
    use_dcf: bool = True,
    dcf_range: tuple[float, float] = (0.8, 1.25),
) -> AssignmentReport:
    """Write materials and densities into the tagged cells of a model.

    Each instance of a component gets the material and density of its own row. Its
    cells are found by the ``[rmID.instance]`` tags in their comments (see
    :mod:`radmodeling.mcnp.tags`).

    The density is multiplied by the registry's ``dcf``, as written by 'compare'
    (1 within the volume tolerance) or edited by hand.

    Every problem found (unknown components, missing materials or densities) is
    collected and raised together in a single :class:`AssignmentError`, so that the
    registry can be fixed in one pass.
    """
    report = AssignmentReport()
    errors: list[str] = []
    cells = cells_by_instance(model)
    if not cells:
        raise AssignmentError(
            "no cell comment has an '[rmID.instance]' tag: convert the STEP file "
            "written by 'radmod export' keeping the solid names as cell comments"
        )

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
    for key, numbers in sorted(cells.items()):
        if key not in registry:
            errors.append(f"Component {key} (cells {numbers}) is not in the registry")
            continue
        component = registry[key]

        if not component.material.strip():
            errors.append(f"Component {key} ({component.name}): no material")
            continue
        if component.is_void:
            report.void_components += 1
            for number in numbers:
                cell = model.cell(number)
                assert cell is not None
                cell.material = 0
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
        if use_dcf and component.dcf is None:
            report.dcf_missing.append(key)
        elif use_dcf and component.dcf is not None:
            dcf = component.dcf
            if not dcf_range[0] <= dcf <= dcf_range[1]:
                report.dcf_out_of_range.append((key, dcf))
            if component.manual_dcf:
                report.dcf_manual.append(key)
            elif dcf == 1.0:
                report.dcf_negligible.append(key)
            else:
                report.dcf_applied.append(key)

        number = material_number(material)
        used[material.name] = material
        for cell_number in numbers:
            cell = model.cell(cell_number)
            assert cell is not None
            cell.material = number
            cell.density = -float(f"{density * dcf:.6g}")
        report.cells_per_material[material.name] = report.cells_per_material.get(
            material.name, 0
        ) + len(numbers)
        if component.original_volume_cm3:
            report.mass_per_material_g[material.name] = (
                report.mass_per_material_g.get(material.name, 0.0)
                + density * component.original_volume_cm3
            )

    if errors:
        raise AssignmentError("the registry needs fixing:\n" + "\n".join(errors))

    defined = {m.id for m in model.materials() if m.id is not None}
    for material in sorted(used.values(), key=lambda m: m.number or 0):
        assert material.number is not None
        report.material_numbers[material.name] = material.number
        if material.number not in defined:
            report.missing_cards.append(material.number)
    return report
