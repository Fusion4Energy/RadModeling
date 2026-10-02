"""Slices of the MCNP geometry with the CAD on top, one at a time or as an atlas.

A :class:`Scene` holds what all the slices of a model share: the MCNP geometry, the
CAD solids and the cells of every instance. :func:`make_slice` classifies one plane
of it. The atlas is a series of planes: evenly spaced ones across the whole model
(:func:`overview_planes`) and one through each of the worst instances of a
``compare`` report (:func:`read_problems`, :func:`instance_plane`).
"""

from __future__ import annotations

import csv
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from OCP.TopoDS import TopoDS_Shape

from radmodeling.cad.occ import bounding_box
from radmodeling.cad.section import section_polylines
from radmodeling.cad.step import read_step
from radmodeling.compare import (
    MM_PER_CM,
    Plane,
    SliceResult,
    classify_cad_on_plane,
    near_plane,
    slice_mcnp,
)
from radmodeling.mcnp.geometry import Geometry
from radmodeling.mcnp.tags import cells_by_instance
from radmodeling.plot import material_slots
from radmodeling.registry import Key
from radmodeling.workflow import ExportedInstance, read_exported

AXES = "xyz"


@dataclass
class Scene:
    geometry: Geometry
    solids: list[TopoDS_Shape]  # CAD to overlay
    # Instances of the STEP file written by 'export': empty for any other STEP
    instances: dict[Key, ExportedInstance]
    cells: dict[Key, list[int]]  # tagged cells of every instance
    slots: dict[int, int]  # colour slot of the most used materials
    material_names: dict[int, str]

    @property
    def compares(self) -> bool:
        """Whether CAD/MCNP mismatches can be found."""
        return bool(self.instances and self.cells)

    def bounds_cm(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Bounding box of the CAD, or None without CAD."""
        return _bounds_cm(self.solids) if self.solids else None


def load_scene(
    mcnp: str | os.PathLike,
    step: str | os.PathLike | None = None,
    material_names: Mapping[int, str] | None = None,
) -> Scene:
    """Read the MCNP input and, if given, the CAD to overlay on it."""
    geometry = Geometry.from_file(mcnp)
    cells = cells_by_instance(geometry.model)
    instances: dict[Key, ExportedInstance] = {}
    solids: list[TopoDS_Shape] = []
    if step is not None:
        try:
            instances = read_exported(step)
            solids = [s for i in instances.values() for s in i.solids]
        except ValueError:  # not from 'export': overlay only
            solids = [s for part in read_step(step) for s in part.solids]

    counts: dict[int, int] = {}
    for cell in geometry.cells.values():
        counts[cell.material] = counts.get(cell.material, 0) + 1
    names = {}
    for card in geometry.model.materials():
        # 'materials' writes the material name as a comment on the card
        first_line = card.text.splitlines()[0]
        if card.id is not None and "$" in first_line:
            label = first_line.split("$", 1)[1].strip()
            names[card.id] = f"{label} (m{card.id})" if label else f"m{card.id}"
    names.update(material_names or {})
    return Scene(geometry, solids, instances, cells, material_slots(counts), names)


@dataclass
class Slice:
    title: str
    result: SliceResult
    outlines: list[np.ndarray] = field(default_factory=list)  # CAD, (u, v) in cm


def make_slice(scene: Scene, plane: Plane, resolution: int, title: str = "") -> Slice:
    """Classify a plane: MCNP cells and materials, CAD outlines and mismatches."""
    n_u = resolution
    n_v = max(int(round(resolution * plane.height / plane.width)), 1)
    result = slice_mcnp(scene.geometry, plane, n_u, n_v)

    reach = np.hypot(plane.width, plane.height) / 2 * MM_PER_CM
    outlines = []
    for solid in scene.solids:
        if not near_plane(solid, plane, reach):
            continue
        for line in section_polylines(solid, plane.origin * MM_PER_CM, plane.normal):
            local = line / MM_PER_CM - plane.origin
            outlines.append(np.column_stack([local @ plane.u, local @ plane.v]))

    if scene.compares:
        # Pixels are labelled 1, 2, 3... by instance, 0 where there is none
        label = {key: n for n, key in enumerate(sorted(scene.instances), 1)}
        cad = classify_cad_on_plane(
            {label[k]: i.solids for k, i in scene.instances.items()}, plane, n_u, n_v
        )
        owner = {}
        for key, numbers in scene.cells.items():
            if key in label:
                owner.update(dict.fromkeys(numbers, label[key]))
        # Cell numbers can be huge (GEOUNED): look up only those in the image
        numbers, inverse = np.unique(result.cells, return_inverse=True)
        labels = np.array([owner.get(int(n), 0) for n in numbers], dtype=np.int64)
        result.mismatch = cad != labels[inverse].reshape(cad.shape)
    return Slice(title, result, outlines)


def axis_plane(
    lo: np.ndarray, hi: np.ndarray, axis: str, position: float, margin: float = 0.05
) -> Plane:
    """Plane normal to an axis at a position, covering the box (cm)."""
    index = AXES.index(axis)
    others = [i for i in range(3) if i != index]
    origin = (lo + hi) / 2
    origin[index] = position
    size = (hi - lo) * (1 + 2 * margin)
    return Plane.from_basis(origin, axis, size[others[0]], size[others[1]])


def parse_plane_counts(text: str) -> dict[str, int]:
    """Read 'x:3,y:3,z:6' (number of planes normal to each axis)."""
    counts = {}
    for item in text.replace(" ", "").split(","):
        if not item:
            continue
        axis, _, count = item.partition(":")
        if axis.lower() not in AXES or not count.isdigit():
            raise ValueError(f"bad plane count {item!r}: write them like 'x:3,y:3,z:6'")
        counts[axis.lower()] = int(count)
    return counts


def overview_planes(
    lo: np.ndarray, hi: np.ndarray, counts: Mapping[str, int]
) -> list[tuple[str, Plane]]:
    """Evenly spaced planes across a box (cm), never on its faces, with titles."""
    planes = []
    for axis in AXES:
        n = counts.get(axis, 0)
        index = AXES.index(axis)
        for k in range(n):
            position = lo[index] + (k + 1) / (n + 1) * (hi[index] - lo[index])
            title = f"{axis} = {position:.6g} cm ({k + 1} of {n})"
            planes.append((title, axis_plane(lo, hi, axis, position)))
    return planes


def instance_plane(solids: Sequence[TopoDS_Shape], margin: float = 0.25) -> Plane:
    """Plane through the centre of some solids, normal to their thinnest direction,
    which shows their largest section, with some surroundings around them."""
    lo, hi = _bounds_cm(solids)
    axis = AXES[int(np.argmin(hi - lo))]
    size = hi - lo
    pad = np.maximum(size * margin, 0.05 * size.max())
    return axis_plane(
        lo - pad, hi + pad, axis, float((lo + hi)[AXES.index(axis)] / 2), 0
    )


@dataclass
class Problem:
    key: Key
    discrepancy_pct: float
    note: str = ""


def read_problems(report: str | os.PathLike, threshold: float = 1.0) -> list[Problem]:
    """The instances of a 'compare' report beyond the threshold, worst first.

    Instances that could not be evaluated (no discrepancy) come first.
    """
    problems = []
    with open(report, encoding="utf-8-sig", newline="") as infile:
        for row in csv.DictReader(infile):
            key = Key(int(row["component_id"]), int(row["instance"]))
            discrepancy = float(row["discrepancy_pct"] or "nan")
            notes = []
            if row.get("error"):
                notes.append(row["error"])
            if not row.get("cells", "").strip():
                notes.append("no tagged cells")
            if row.get("touches_sampling_box") == "True":
                notes.append("cells exceed the sampled box")
            if math.isnan(discrepancy) or discrepancy > threshold or notes:
                problems.append(Problem(key, discrepancy, "; ".join(notes)))
    problems.sort(
        key=lambda p: -math.inf if math.isnan(p.discrepancy_pct) else -p.discrepancy_pct
    )
    return problems


def _bounds_cm(solids: Sequence[TopoDS_Shape]) -> tuple[np.ndarray, np.ndarray]:
    boxes = np.array([bounding_box(s) for s in solids]) / MM_PER_CM
    return boxes[:, :3].min(axis=0), boxes[:, 3:].max(axis=0)
