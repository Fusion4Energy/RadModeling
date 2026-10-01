"""The CAD side of the CAD-to-MCNP workflow.

1. :func:`init` tags every component of the original model with a unique ID and
   records its original volume in the registry.
2. The tagged model is simplified in any CAD program (SpaceClaim, FreeCAD...).
3. :func:`check` matches the simplified model back to the registry and reports volume
   deviations and geometry that will not convert well.
4. :func:`export` writes the STEP file to convert, one solid per product in a known
   order, and the map between cell order and component IDs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from OCP.TopoDS import TopoDS_Solid

from radmodeling.cad.analysis import ShapeReport, analyse
from radmodeling.cad.occ import volume_mm3
from radmodeling.cad.step import Part, compound, read_step, write_step
from radmodeling.cellmap import CellMap
from radmodeling.registry import (
    TAG_RE,
    Component,
    Registry,
    find_id,
    tag_name,
    untag_name,
)

# --- init ------------------------------------------------------------------------


def init(
    original_step: str | os.PathLike,
    tagged_step: str | os.PathLike,
    registry_csv: str | os.PathLike,
    per_solid: bool = False,
) -> Registry:
    """Tag the components of a model and create its registry.

    Components that already carry a tag keep it, so running this on a model that was
    partially tagged before only adds the new components.
    """
    parts = read_step(original_step)
    if per_solid:
        parts = _split_solids(parts)

    used = {i for p in parts if (i := _own_id(p)) is not None}
    next_id = max(used, default=0) + 1
    registry = Registry()
    tagged_parts = []
    for part in parts:
        component_id = _own_id(part)
        if component_id is None or component_id in registry:
            component_id = next_id
            next_id += 1
        report = analyse(part.shape)
        registry.add(
            Component(
                id=component_id,
                name=untag_name(part.name),
                path=" / ".join(untag_name(n) for n in part.path),
                original_volume_cm3=report.volume_cm3,
                original_solids=report.n_solids,
            )
        )
        tagged_parts.append(
            Part(tag_name(part.name, component_id), part.path, part.shape)
        )

    write_step(tagged_parts, tagged_step)
    registry.save(registry_csv)
    return registry


def _own_id(part: Part) -> int | None:
    match = TAG_RE.search(part.name) or TAG_RE.search(part.instance_name)
    return int(match.group(1)) if match else None


def _split_solids(parts: list[Part]) -> list[Part]:
    result = []
    for part in parts:
        solids = part.solids
        if len(solids) == 1:
            result.append(part)
            continue
        for i, solid in enumerate(solids, start=1):
            result.append(Part(f"{untag_name(part.name)} #{i}", part.path, solid))
    return result


# --- check -----------------------------------------------------------------------


@dataclass
class ComponentState:
    """What the simplified model contains for one component."""

    id: int
    solids: list[TopoDS_Solid] = field(default_factory=list)
    tagged_nodes: set[tuple[str, ...]] = field(default_factory=set)
    report: ShapeReport | None = None


@dataclass
class CheckResult:
    states: dict[int, ComponentState]
    untagged: list[Part]
    unknown_ids: list[int]
    missing_ids: list[int]
    tolerance_pct: float
    registry: Registry

    def exceeding(self) -> list[Component]:
        return [
            c
            for c in self.registry
            if c.id in self.states
            and c.volume_diff_pct is not None
            and abs(c.volume_diff_pct) > self.tolerance_pct
        ]

    def duplicated(self) -> list[int]:
        return sorted(i for i, s in self.states.items() if len(s.tagged_nodes) > 1)

    def with_issue(self, predicate) -> list[int]:
        return sorted(
            i
            for i, s in self.states.items()
            if s.report is not None and predicate(s.report)
        )


def group_by_component(
    parts: list[Part],
) -> tuple[dict[int, ComponentState], list[Part]]:
    states: dict[int, ComponentState] = {}
    untagged = []
    for part in parts:
        component_id = find_id(part.names)
        if component_id is None:
            untagged.append(part)
            continue
        state = states.setdefault(component_id, ComponentState(component_id))
        state.solids.extend(part.solids)
        names = part.names
        depth = max(i for i, n in enumerate(names) if TAG_RE.search(n))
        state.tagged_nodes.add(tuple(untag_name(n) for n in names[: depth + 1]))
    return states, untagged


def check(
    simplified_step: str | os.PathLike,
    registry: Registry,
    tolerance_pct: float = 1.0,
    analyse_geometry: bool = True,
) -> CheckResult:
    """Match a simplified model with the registry and update the simplified volumes."""
    states, untagged = group_by_component(read_step(simplified_step))
    for component in registry:
        state = states.get(component.id)
        if state is None:
            component.simplified_volume_cm3 = None
            component.simplified_solids = None
            component.status = "deleted"
            continue
        if analyse_geometry:
            state.report = analyse(compound(state.solids))
            volume = state.report.volume_cm3
        else:
            volume = sum(volume_mm3(s) for s in state.solids) / 1000.0
        component.simplified_volume_cm3 = volume
        component.simplified_solids = len(state.solids)
        if component.status == "deleted":
            component.status = ""
        component.update_computed()

    return CheckResult(
        states=states,
        untagged=untagged,
        unknown_ids=sorted(i for i in states if i not in registry),
        missing_ids=sorted(c.id for c in registry if c.id not in states),
        tolerance_pct=tolerance_pct,
        registry=registry,
    )


# --- export ----------------------------------------------------------------------


def export(
    simplified_step: str | os.PathLike,
    registry: Registry,
    output_step: str | os.PathLike,
    cell_map_csv: str | os.PathLike,
) -> tuple[CellMap, list[int]]:
    """Write the model to convert with one solid per product, sorted by component ID.

    Untagged parts get new IDs and are added to the registry with status ``new``.

    Returns:
        The cell map and the IDs of the components that were added to the registry.
    """
    states, untagged = group_by_component(read_step(simplified_step))
    unknown = sorted(i for i in states if i not in registry)
    if unknown:
        raise ValueError(f"Component IDs not present in the registry: {unknown}")

    added = []
    for part in untagged:
        component_id = registry.next_id
        registry.add(
            Component(
                id=component_id,
                name=untag_name(part.name),
                path=" / ".join(untag_name(n) for n in part.path),
                status="new",
            )
        )
        states[component_id] = ComponentState(component_id, part.solids)
        added.append(component_id)

    parts, order = [], []
    root = (os.path.splitext(os.path.basename(os.fspath(output_step)))[0],)
    for component_id in sorted(states):
        component = registry[component_id]
        solids = states[component_id].solids
        for k, solid in enumerate(solids, start=1):
            suffix = f" #{k}" if len(solids) > 1 else ""
            parts.append(
                Part(tag_name(component.name + suffix, component_id), root, solid)
            )
            order.append(component_id)
        component.simplified_volume_cm3 = sum(volume_mm3(s) for s in solids) / 1000.0
        component.simplified_solids = len(solids)

    write_step(parts, output_step)
    cell_map = CellMap(order)
    cell_map.save(cell_map_csv)
    return cell_map, added
