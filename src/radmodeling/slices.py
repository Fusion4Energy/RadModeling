"""Slices of the MCNP geometry with the CAD on top, one at a time or as an atlas.

A :class:`Scene` holds what all the slices of a model share: the MCNP geometry, the
CAD solids and the cells of every instance. :func:`make_slice` classifies one plane
of it. The atlas is a series of planes: evenly spaced ones across the whole model
(:func:`overview_planes`) and one through each problem found by sampling every
instance like ``compare`` does (:func:`spot_plane`), or through the instance when
the problem has no location (:func:`instance_plane`). Small clusters of problem
pixels get a zoomed slice of their own (:func:`zoom_slices`).
"""

from __future__ import annotations

import math
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from OCP.TopoDS import TopoDS_Shape

from radmodeling.cad.mesh import mesh_of
from radmodeling.cad.occ import bounding_box
from radmodeling.cad.section import section_polylines
from radmodeling.cad.step import read_step
from radmodeling.compare import (
    MM_PER_CM,
    Comparison,
    Plane,
    SliceResult,
    Spot,
    VolumeComparison,
    classify_cad_on_plane,
    cluster_labels,
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
        return solids_bounds_cm(self.solids) if self.solids else None


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
        # Material cards often carry the material name as a comment
        first_line = card.text.splitlines()[0]
        if card.id is not None and "$" in first_line:
            label = first_line.split("$", 1)[1].strip()
            names[card.id] = f"{label} (m{card.id})" if label else f"m{card.id}"
    names.update(material_names or {})
    return Scene(geometry, solids, instances, cells, material_slots(counts), names)


@dataclass
class Marker:
    """A circle around a cluster of problem pixels (cm in the plane)."""

    u: float
    v: float
    radius: float
    label: str = ""


@dataclass
class Slice:
    title: str
    result: SliceResult
    outlines: list[np.ndarray] = field(default_factory=list)  # CAD, (u, v) in cm
    # Instance of every pixel by CAD and by MCNP (index in ``keys`` + 1, 0 = none)
    cad: np.ndarray | None = None
    mcnp: np.ndarray | None = None
    keys: list[Key] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    zooms: list[Slice] = field(default_factory=list)


# CAD/MCNP disagreements thinner than this are ignored [cm]. Near the surface of a
# large part it is larger: twice the deflection of its mesh (1e-4 of its size), as
# the tessellated CAD and the exact MCNP surface differ by up to the deflection and
# the points are moved along the axes of the slice, not normal to the surface.
SURFACE_TOLERANCE = 0.01


def make_slice(scene: Scene, plane: Plane, resolution: int, title: str = "") -> Slice:
    """Classify a plane: MCNP cells and materials, CAD outlines and mismatches.

    A pixel is a mismatch when the instance of its centre differs in the CAD and in
    MCNP, unless MCNP agrees with the CAD near it in any direction (within
    ``SURFACE_TOLERANCE``, more for large parts): a pixel centre that falls between
    the tessellated CAD and the exact MCNP surface is not a problem.
    """
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
    item = Slice(title, result, outlines)
    tagged = {n for numbers in scene.cells.values() for n in numbers}
    # Cell numbers can be huge (GEOUNED): look up only those in the image
    in_image, inverse = np.unique(result.cells, return_inverse=True)
    is_tagged = np.array([int(n) in tagged for n in in_image], dtype=bool)
    result.tagged = is_tagged[inverse].reshape(result.cells.shape)

    if scene.compares:
        # Pixels are labelled 1, 2, 3... by instance, 0 where there is none
        item.keys = sorted(scene.instances)
        label = {key: n for n, key in enumerate(item.keys, 1)}
        item.cad = classify_cad_on_plane(
            {label[k]: i.solids for k, i in scene.instances.items()}, plane, n_u, n_v
        )
        owner = {}
        for key, numbers in scene.cells.items():
            if key in label:
                owner.update(dict.fromkeys(numbers, label[key]))
        labels = np.array([owner.get(int(n), 0) for n in in_image], dtype=np.int64)
        item.mcnp = labels[inverse].reshape(item.cad.shape)
        mismatch = item.cad != item.mcnp
        if mismatch.any():
            cells_of = {label[k]: scene.cells.get(k, []) for k in item.keys}
            cad, mcnp = item.cad[mismatch], item.mcnp[mismatch]
            # Of the instances on both sides of each disagreement
            tolerance_of = np.full(len(item.keys) + 1, SURFACE_TOLERANCE)
            for n in (set(cad.tolist()) | set(mcnp.tolist())) - {0}:
                solids = scene.instances[item.keys[n - 1]].solids
                tolerance_of[n] = _surface_tolerance(solids, plane, reach)
            mismatch[mismatch] = ~_near_agreement(
                scene.geometry,
                plane,
                plane.points(n_u, n_v)[mismatch],
                cad,
                mcnp,
                cells_of,
                np.maximum(tolerance_of[cad], tolerance_of[mcnp]),
            )
        result.mismatch = mismatch
    return item


def _surface_tolerance(
    solids: Sequence[TopoDS_Shape], plane: Plane, reach: float
) -> float:
    """CAD/MCNP disagreements thinner than this are ignored near these solids [cm].
    Only the solids crossing the window count: those classified on the slice, so
    their meshes are made already."""
    deflections = [
        mesh_of(solid).deflection for solid in solids if near_plane(solid, plane, reach)
    ]
    return max([SURFACE_TOLERANCE, *(2 * d / MM_PER_CM for d in deflections)])


def _near_agreement(
    geometry: Geometry,
    plane: Plane,
    points: np.ndarray,
    cad: np.ndarray,
    mcnp: np.ndarray,
    cells_of: Mapping[int, Sequence[int]],
    tolerance: np.ndarray,
) -> np.ndarray:
    """Whether MCNP agrees with the CAD within the tolerance (cm) of each point: in
    the cells of the CAD instance, or out of the cells of the MCNP instance where the
    CAD has none."""
    steps = [
        sign * axis for axis in (plane.u, plane.v, plane.normal) for sign in (1, -1)
    ]
    shifted = np.concatenate([points + tolerance[:, None] * step for step in steps])
    evaluator = geometry.evaluator(shifted)
    cad, mcnp = np.tile(cad, len(steps)), np.tile(mcnp, len(steps))
    agree = np.zeros(len(shifted), dtype=bool)
    for instance in set(cad.tolist()) | set(mcnp.tolist()):
        if not instance:
            continue
        inside = np.zeros(len(shifted), dtype=bool)
        for number in cells_of[instance]:
            inside |= evaluator.cell(number)
        agree |= (cad == instance) & inside
        agree |= (cad == 0) & (mcnp == instance) & ~inside
    return agree.reshape(len(steps), -1).any(axis=0)


def problem_pixels(result: SliceResult) -> np.ndarray:
    problems = result.overlaps | (result.materials == -1)
    if result.mismatch is not None:
        problems = problems | result.mismatch
    return problems


def pixel_clusters(
    mask: np.ndarray, gap: int = 4
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Clusters of True pixels no further than about ``gap`` pixels apart, largest
    first, as the (row, column) indices of their pixels."""
    rows, columns = np.nonzero(mask)
    if not rows.size:
        return []
    labels = cluster_labels(np.column_stack([rows, columns]).astype(float), gap)
    order = np.argsort(-np.bincount(labels), kind="stable")
    return [(rows[labels == k], columns[labels == k]) for k in order]


def zoom_slices(
    scene: Scene,
    parent: Slice,
    resolution: int,
    max_zooms: int = 3,
    small: float = 0.125,
    max_zoom: float = 10.0,
) -> None:
    """Mark the clusters of problem pixels of a slice and, for those too small to be
    seen (under ``small`` times the window), add zoomed slices to ``parent.zooms``.
    """
    result = parent.result
    plane = result.plane
    n_v, n_u = result.cells.shape
    pixel = plane.width / n_u
    su, sv = plane.pixel_centres(n_u, n_v)
    sv = sv[::-1]  # top row first
    for rows, columns in pixel_clusters(problem_pixels(result)):
        u_lo, u_hi = su[columns].min(), su[columns].max()
        v_lo, v_hi = sv[rows].min(), sv[rows].max()
        size = max(u_hi - u_lo, v_hi - v_lo) + pixel
        centre = ((u_lo + u_hi) / 2, (v_lo + v_hi) / 2)
        if size >= small * max(plane.width, plane.height):
            continue  # visible as it is
        radius = size / 2 + 3 * pixel
        if len(parent.zooms) >= max_zooms:
            parent.markers.append(Marker(*centre, radius))
            continue
        letter = chr(ord("A") + len(parent.zooms))
        # A window a few times the cluster, at most max_zoom times smaller
        side = max(4 * size, plane.width / max_zoom)
        origin = plane.origin + centre[0] * plane.u + centre[1] * plane.v
        window = Plane(origin, plane.u, plane.v, side, side)
        item = make_slice(scene, window, resolution)
        item.title = f"{letter}: {describe_problems(scene, item)}"
        parent.zooms.append(item)
        parent.markers.append(Marker(*centre, radius, letter))


@dataclass
class Atlas:
    """The work of an atlas, run by its worker processes: searching instances for
    problems, then drawing the slices. Without comparison, only drawing."""

    scene: Scene
    comparison: Comparison | None
    resolution: int

    def search(self, key: Key) -> VolumeComparison:
        assert self.comparison is not None
        return self.comparison.compare(key)

    def draw(self, page: tuple[str, Plane]) -> Slice:
        title, plane = page
        item = make_slice(self.scene, plane, self.resolution, title)
        zoom_slices(self.scene, item, self.resolution)
        return item


def load_atlas(
    mcnp: str | os.PathLike,
    step: str | os.PathLike,
    material_names: Mapping[int, str],
    compared: Sequence[Key],
    n_points: int,
    entropy: int,
    resolution: int,
) -> Atlas:
    """The atlas of a model, as its worker processes load it."""
    scene = load_scene(mcnp, step, material_names)
    return Atlas(
        scene, scene_comparison(scene, compared, n_points, entropy), resolution
    )


def scene_comparison(
    scene: Scene, compared: Sequence[Key], n_points: int, entropy: int
) -> Comparison | None:
    """The comparison that locates the problems of some instances, if possible."""
    if not scene.compares:
        return None
    solids = {k: i.solids for k, i in scene.instances.items()}
    return Comparison(
        scene.geometry, solids, scene.cells, compared, n_points, entropy, locate=True
    )


def describe_problems(scene: Scene, item: Slice, max_points: int = 200) -> str:
    """What the problem pixels of a slice are: the cells that overlap, the
    instances CAD and MCNP disagree on, the regions in no cell."""
    result = item.result
    parts = []
    if result.overlap_pixels:
        points = result.plane.points(*result.cells.shape[::-1])[result.overlaps]
        points = points[:: max(len(points) // max_points, 1)]
        evaluator = scene.geometry.evaluator(points)
        cells = []
        for number in scene.geometry.level_zero_cells():
            try:
                if np.any(evaluator.cell(number)):
                    cells.append(number)
            except ValueError:
                continue
        tag = {n: key for key, numbers in scene.cells.items() for n in numbers}
        names = [f"{n} [{tag[n]}]" if n in tag else str(n) for n in cells]
        parts.append("overlap of cells " + ", ".join(names))
    if result.mismatch_pixels and item.cad is not None and item.mcnp is not None:
        names = ["none", *map(str, item.keys)]
        cad = item.cad[result.mismatch].tolist()
        mcnp = item.mcnp[result.mismatch].tolist()
        top = Counter(zip(cad, mcnp, strict=True)).most_common(2)
        parts.append(
            "mismatch "
            + ", ".join(f"CAD {names[c]} / MCNP {names[m]}" for (c, m), _ in top)
        )
    if result.lost_pixels:
        parts.append("no cell")
    return "; ".join(parts) or "no problem at this resolution"


def plane_shows(plane: Plane, spot: Spot) -> bool:
    """Whether a slice cuts through a spot, within its window."""
    centre, half = (spot.lo + spot.hi) / 2, (spot.hi - spot.lo) / 2
    offset = centre - plane.origin
    crosses = abs(offset @ plane.normal) <= np.abs(plane.normal) @ half
    inside = abs(offset @ plane.u) <= plane.width / 2
    inside &= abs(offset @ plane.v) <= plane.height / 2
    return bool(crosses and inside)


def spot_plane(spot: Spot, context: float) -> Plane:
    """Plane through a problem spot, normal to its thinnest direction, showing the
    spot with ``context`` cm around it."""
    size = spot.hi - spot.lo
    axis = AXES[int(np.argmin(size))]
    others = [i for i in range(3) if i != AXES.index(axis)]
    origin = (spot.lo + spot.hi) / 2
    origin[AXES.index(axis)] = spot.point[AXES.index(axis)]
    width, height = (size[i] + 2 * context for i in others)
    return Plane.from_basis(origin, axis, width, height)


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
    lo, hi = solids_bounds_cm(solids)
    axis = AXES[int(np.argmin(hi - lo))]
    size = hi - lo
    pad = np.maximum(size * margin, 0.05 * size.max())
    return axis_plane(
        lo - pad, hi + pad, axis, float((lo + hi)[AXES.index(axis)] / 2), 0
    )


def solids_bounds_cm(solids: Sequence[TopoDS_Shape]) -> tuple[np.ndarray, np.ndarray]:
    boxes = np.array([bounding_box(s) for s in solids]) / MM_PER_CM
    return boxes[:, :3].min(axis=0), boxes[:, 3:].max(axis=0)


def problem_pages(
    scene: Scene,
    results: Sequence[VolumeComparison],
    max_discrepancy: float,
    worst: int,
) -> tuple[list[tuple[str, Plane]], int]:
    """Slices through the problems of the ``worst`` instances, with their titles,
    and the number of instances with problems.

    Instances that could not be evaluated come first, then those beyond the maximum
    discrepancy (worst first), then those that only overlap (largest first).
    """
    problems = [r for r in results if r.is_problem(max_discrepancy)]
    problems.sort(
        key=lambda r: (
            not (r.error or math.isnan(r.discrepancy_pct)),
            not r.exceeds(max_discrepancy),
            -np.nan_to_num(r.discrepancy_pct),
            -sum(v for v, _ in r.overlaps.values()),
        )
    )
    pages = []
    for r in problems[:worst]:
        instance = scene.instances[r.key]
        name = f"{r.key} {instance.name}"
        lo, hi = solids_bounds_cm(instance.solids)
        size = float(np.max(hi - lo))
        spots = [
            spot
            for spot in r.spots
            if spot.kind == "overlap" or r.exceeds(max_discrepancy)
        ]
        shown: list[Plane] = []
        for spot in spots:
            if any(plane_shows(plane, spot) for plane in shown):
                continue
            context = max(0.15 * size, float(np.max(spot.hi - spot.lo)), 1.0)
            shown.append(spot_plane(spot, context))
            if spot.kind == "overlap":
                other = "itself" if spot.other == r.key else str(spot.other)
                title = f"{name}: overlap with {other} ({spot.volume_cm3:.3g} cm3)"
            else:
                title = f"{name}: mismatch, discrepancy {r.discrepancy_pct:.3g} %"
            pages.append((title, shown[-1]))
        if not spots:
            note = r.error or (
                "no tagged cells" if not r.cells else "no problem located"
            )
            title = f"{name}: discrepancy {r.discrepancy_pct:.3g} % ({note})"
            pages.append((title, instance_plane(instance.solids)))
    return pages, len(problems)
