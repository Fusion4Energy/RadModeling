"""The CAD side of the CAD-to-MCNP workflow.

1. :func:`init` tags every part definition of the original model with a unique ID and
   records the original volume and centre of mass of each instance in the registry.
2. The tagged model is simplified in any CAD program (SpaceClaim, FreeCAD...). The
   instances of a part stay linked, so each part is simplified only once.
3. :func:`check` matches the simplified model back to the registry and reports volume
   deviations and geometry that will not convert well.
4. :func:`export` writes the STEP file to convert, one solid per product in a known
   order, and the map between cell order and registry rows.

Instances are matched to their registry rows by their centre of mass: simplifying a
part barely moves it compared with the distance between instances. An instance further
from a row than its own size is not that row (a deleted instance and a copy elsewhere
look alike), except for components with a single row and a single body.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
from OCP.gp import gp_Pnt
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Shape, TopoDS_Solid

from radmodeling.cad.analysis import ShapeReport, analyse
from radmodeling.cad.occ import GeometryMap, volume_and_centroid
from radmodeling.cad.step import Part, read_step, write_step
from radmodeling.cellmap import CellMap
from radmodeling.compare import MM_PER_CM
from radmodeling.registry import (
    TAG_RE,
    Component,
    Key,
    Registry,
    find_id,
    tag_name,
    untag_name,
)

# --- instances -------------------------------------------------------------------


@dataclass
class Instance:
    """One occurrence of a component in a model and the parts it is made of."""

    id: int | None  # None if untagged
    name: str  # untagged name of the tagged node
    levels: tuple[str, ...]  # untagged names of the assemblies above it
    parts: list[Part]
    volume_cm3: float = 0.0
    centre_cm: tuple[float, float, float] | None = None
    report: ShapeReport | None = None

    @property
    def solids(self) -> list[TopoDS_Solid]:
        return [s for p in self.parts for s in p.solids]


class Measurer:
    """Measures instances, analysing every shared geometry (part definition) once."""

    def __init__(self, analyse_geometry: bool = True):
        self.analyse_geometry = analyse_geometry
        self._cache: GeometryMap[
            tuple[float, tuple[float, ...], ShapeReport | None]
        ] = GeometryMap()

    def _measure(
        self, shape: TopoDS_Shape
    ) -> tuple[float, tuple[float, ...], ShapeReport | None]:
        cached = self._cache.get(shape)
        if cached is None:
            bare = shape.Located(TopLoc_Location())
            volume, centre = volume_and_centroid(bare)
            report = analyse(bare) if self.analyse_geometry else None
            self._cache[shape] = cached = (volume, centre, report)
        return cached

    def __call__(self, instance: Instance) -> None:
        volume, moment, reports = 0.0, np.zeros(3), []
        for part in instance.parts:
            part_volume, centre, report = self._measure(part.shape)
            placed = gp_Pnt(*centre).Transformed(part.shape.Location().Transformation())
            volume += part_volume
            moment += part_volume * np.array([placed.X(), placed.Y(), placed.Z()])
            reports.append(report)
        instance.volume_cm3 = volume / 1000.0
        if volume > 0:
            x, y, z = (moment / volume / MM_PER_CM).tolist()
            instance.centre_cm = (x, y, z)
        if self.analyse_geometry:
            instance.report = _combine([r for r in reports if r is not None])


def _combine(reports: list[ShapeReport]) -> ShapeReport:
    return ShapeReport(
        n_solids=sum(r.n_solids for r in reports),
        volume_cm3=sum(r.volume_cm3 for r in reports),
        surfaces=sum((r.surfaces for r in reports), Counter()),
        valid=all(r.valid for r in reports),
        negative_volume_solids=sum(r.negative_volume_solids for r in reports),
    )


def find_instances(parts: Sequence[Part], measure: Measurer) -> list[Instance]:
    """Group the parts of a model into the instances of the components.

    The instance is the deepest tagged node: a tagged part, or every occurrence of a
    tagged assembly with all the parts below it. Untagged parts are instances of their
    own with no ID.
    """
    groups: dict[object, Instance] = {}
    for index, part in enumerate(parts):
        names = part.names
        tagged = [i for i, n in enumerate(names) if TAG_RE.search(n)]
        component_id = find_id(names)
        if not tagged or tagged[-1] >= len(part.path):
            group: object = index
            name, levels = part.name, part.path
        else:
            depth = tagged[-1]
            node = part.occurrence[: depth + 1] or part.path[: depth + 1]
            group = (component_id, node)
            name, levels = part.path[depth], part.path[:depth]
        if group not in groups:
            groups[group] = Instance(
                component_id, untag_name(name), tuple(map(untag_name, levels)), []
            )
        groups[group].parts.append(part)
    instances = list(groups.values())
    for instance in instances:
        measure(instance)
    return instances


@dataclass
class Matching:
    """The instances of a model matched with the rows of the registry."""

    found: dict[Key, Instance]
    missing: list[Key]  # registry rows not in the model
    extra: list[Instance]  # more instances of a component than registry rows
    unknown: list[Instance]  # tags that are not in the registry
    untagged: list[Instance]
    moved: list[Key]  # single-body components far from their original position


def match_instances(instances: Sequence[Instance], registry: Registry) -> Matching:
    by_id: dict[int, list[Instance]] = defaultdict(list)
    untagged = []
    for instance in instances:
        if instance.id is None:
            untagged.append(instance)
        else:
            by_id[instance.id].append(instance)

    found: dict[Key, Instance] = {}
    extra, unknown, moved = [], [], []
    for component_id, group in sorted(by_id.items()):
        rows = registry.instances(component_id)
        if not rows:
            unknown.extend(group)
            continue
        paired = set()
        for row, instance in _pair(rows, group):
            far = _far(row, instance)
            if far and not len(rows) == len(group) == 1:
                continue
            found[row.key] = instance
            paired.add(id(instance))
            if far:
                moved.append(row.key)
        extra.extend(i for i in group if id(i) not in paired)
    missing = [c.key for c in registry if c.key not in found]
    return Matching(found, missing, extra, unknown, untagged, sorted(moved))


def _pair(
    rows: list[Component], group: list[Instance]
) -> list[tuple[Component, Instance]]:
    """Pair rows and instances, closest centres first (in order if none known)."""
    original = [r.centre_cm for r in rows]
    current = [i.centre_cm for i in group]
    if None in original or None in current:
        return list(zip(rows, group, strict=False))
    a, b = np.array(original), np.array(current)
    # Squared distances without the (rows, instances, 3) intermediate array
    distance = (a**2).sum(1)[:, None] + (b**2).sum(1)[None] - 2 * a @ b.T
    pairs, used_rows, used_instances = [], set(), set()
    for flat in np.argsort(distance, axis=None, kind="stable"):
        r, i = divmod(int(flat), len(group))
        if r in used_rows or i in used_instances:
            continue
        used_rows.add(r)
        used_instances.add(i)
        pairs.append((rows[r], group[i]))
        if len(pairs) == min(len(rows), len(group)):
            break
    return pairs


def _far(row: Component, instance: Instance) -> bool:
    """Whether the instance is further from the row than its own size."""
    original = row.centre_cm
    if original is None or instance.centre_cm is None or not row.original_volume_cm3:
        return False
    distance = np.linalg.norm(np.subtract(instance.centre_cm, original))
    return bool(distance > row.original_volume_cm3 ** (1 / 3))


def _new_component(key: Key, instance: Instance, **values) -> Component:
    x, y, z = instance.centre_cm or (None, None, None)
    return Component(
        key.id,
        instance.name,
        key.instance,
        instance.levels,
        x_cm=x,
        y_cm=y,
        z_cm=z,
        **values,
    )


# --- init ------------------------------------------------------------------------


def init(
    original_step: str | os.PathLike,
    tagged_step: str | os.PathLike,
    registry_csv: str | os.PathLike,
    per_solid: bool = False,
) -> Registry:
    """Tag the part definitions of a model and create its registry.

    All the instances of a part get the same tag and stay linked in the tagged model.
    Each instance gets its own row in the registry. Components that already carry a
    tag keep it, so running this on a model that was partially tagged before only
    adds the new components.
    """
    parts = read_step(original_step)
    if per_solid:
        parts = _split_solids(parts)

    used = {i for p in parts if (i := _own_id(p)) is not None}
    next_id = max(used, default=0) + 1
    ids: GeometryMap[dict[str, int]] = GeometryMap()  # definition -> ID
    instances: Counter[int] = Counter()
    measure = Measurer(analyse_geometry=False)
    registry = Registry()
    tagged_parts = []
    for part in parts:
        component_id = _own_id(part)
        if component_id is None:
            names = ids.get(part.shape)
            if names is None:
                ids[part.shape] = names = {}
            if part.name not in names:
                names[part.name] = next_id
                next_id += 1
            component_id = names[part.name]
        instances[component_id] += 1
        instance = Instance(
            component_id,
            untag_name(part.name),
            tuple(map(untag_name, part.path)),
            [part],
        )
        measure(instance)
        registry.add(
            _new_component(
                Key(component_id, instances[component_id]),
                instance,
                original_volume_cm3=instance.volume_cm3,
                original_solids=len(part.solids),
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
            name = f"{untag_name(part.name)} #{i}"
            result.append(Part(name, part.path, solid, occurrence=part.occurrence))
    return result


# --- check -----------------------------------------------------------------------


@dataclass
class CheckResult:
    matching: Matching
    tolerance_pct: float
    registry: Registry
    tagged: list[Instance] = field(default_factory=list)

    def exceeding(self) -> list[Component]:
        return [
            c
            for c in self.registry
            if c.key in self.matching.found
            and c.volume_diff_pct is not None
            and abs(c.volume_diff_pct) > self.tolerance_pct
        ]

    def with_issue(self, predicate: Callable[[ShapeReport], object]) -> list[int]:
        """IDs of the components with an instance whose geometry has the issue."""
        return sorted(
            {
                i.id
                for i in self.tagged
                if i.id is not None and i.report is not None and predicate(i.report)
            }
        )


def check(
    simplified_step: str | os.PathLike,
    registry: Registry,
    tolerance_pct: float = 1.0,
    analyse_geometry: bool = True,
) -> CheckResult:
    """Match a simplified model with the registry and update the simplified volumes."""
    instances = find_instances(read_step(simplified_step), Measurer(analyse_geometry))
    matching = match_instances(instances, registry)
    for component in registry:
        instance = matching.found.get(component.key)
        if instance is None:
            component.simplified_volume_cm3 = None
            component.simplified_solids = None
            component.status = "deleted"
            continue
        component.simplified_volume_cm3 = instance.volume_cm3
        component.simplified_solids = len(instance.solids)
        if component.status == "deleted":
            component.status = ""
        component.update_computed()

    return CheckResult(
        matching=matching,
        tolerance_pct=tolerance_pct,
        registry=registry,
        tagged=[i for i in instances if i.id is not None],
    )


# --- export ----------------------------------------------------------------------


def export(
    simplified_step: str | os.PathLike,
    registry: Registry,
    output_step: str | os.PathLike,
    cell_map_csv: str | os.PathLike,
) -> tuple[CellMap, list[Key]]:
    """Write the model to convert with one solid per product, sorted by registry key.

    New bodies are added to the registry with status ``new``: copies of a component
    (more instances than rows) as new instances of it, with its material, and untagged
    parts as new components.

    Returns:
        The cell map and the keys of the rows that were added to the registry.
    """
    matching = match_instances(
        find_instances(read_step(simplified_step), Measurer(analyse_geometry=False)),
        registry,
    )
    if matching.unknown:
        unknown = sorted({i.id for i in matching.unknown if i.id is not None})
        raise ValueError(f"Component IDs not present in the registry: {unknown}")

    found = dict(matching.found)
    added = []
    for instance in matching.extra:
        assert instance.id is not None
        rows = registry.instances(instance.id)
        key = Key(instance.id, max(c.instance for c in rows) + 1)
        registry.add(
            _new_component(
                key,
                instance,
                material=rows[0].material,
                density_g_cm3=rows[0].density_g_cm3,
                status="new",
            )
        )
        found[key] = instance
        added.append(key)

    ids: GeometryMap[dict[str, int]] = GeometryMap()  # untagged definition -> ID
    for instance in matching.untagged:
        part = instance.parts[0]
        names = ids.get(part.shape)
        if names is None:
            ids[part.shape] = names = {}
        component_id = names.setdefault(part.name, registry.next_id)
        key = Key(component_id, len(registry.instances(component_id)) + 1)
        registry.add(_new_component(key, instance, status="new"))
        found[key] = instance
        added.append(key)

    parts, keys = [], []
    root = (os.path.splitext(os.path.basename(os.fspath(output_step)))[0],)
    for key in sorted(found):
        component = registry[key]
        solids = found[key].solids
        for k, solid in enumerate(solids, start=1):
            suffix = f" #{k}" if len(solids) > 1 else ""
            parts.append(Part(tag_name(component.name + suffix, key.id), root, solid))
            keys.append(key)
        component.simplified_volume_cm3 = found[key].volume_cm3
        component.simplified_solids = len(solids)

    write_step(parts, output_step, instancing=False)
    cell_map = CellMap(keys)
    cell_map.save(cell_map_csv)
    return cell_map, added


def exported_solids(
    step: str | os.PathLike, cell_map: CellMap
) -> dict[Key, list[TopoDS_Solid]]:
    """The solids of a STEP file written by :func:`export`, by registry key."""
    solids = [s for p in read_step(step) for s in p.solids]
    if len(solids) != len(cell_map.keys):
        raise ValueError(
            f"{os.fspath(step)} has {len(solids)} solids but the cell map "
            f"{len(cell_map.keys)}: use the STEP file written with the cell map"
        )
    result: dict[Key, list[TopoDS_Solid]] = defaultdict(list)
    for key, solid in zip(cell_map.keys, solids, strict=True):
        result[key].append(solid)
    return dict(result)
