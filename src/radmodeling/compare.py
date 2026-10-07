"""Comparison of the CAD model with the MCNP geometry produced from it.

For every component the same random points are classified against the CAD solids and
against the MCNP cells of that component. This gives, without running MCNP:

* the volume of the MCNP cells, with an unbiased estimator whose uncertainty only
  depends on the points where CAD and MCNP disagree (hence very small when the
  conversion is good). It is the volume needed for the density correction factor.
* the volume where CAD and MCNP disagree, a direct measure of the conversion quality.
* the volume the cells of the component share with the cells of other components (or
  with each other). Clashes in the CAD, such as a bolt left inside a plate, convert
  faithfully into overlapping cells that agree with their own CAD.
* optionally, where those problems are (:class:`Spot`), to slice through them.

Lengths: the CAD is in mm and MCNP in cm. Results are reported in cm and cm3.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from OCP.TopoDS import TopoDS_Shape

from radmodeling.cad.mesh import LineFrame, inside_on_lines, mesh_of
from radmodeling.cad.occ import bounding_box
from radmodeling.mcnp.geometry import Geometry
from radmodeling.registry import Key

MM_PER_CM = 10.0


@dataclass
class Spot:
    """A cluster of sample points where an instance has a problem (cm)."""

    kind: str  # "overlap" or "mismatch" (CAD and MCNP disagree)
    other: Key | None  # the overlapping instance (the instance itself: its own cells)
    point: np.ndarray  # a sample point of the problem, near the centre of the cluster
    lo: np.ndarray  # box of the cluster
    hi: np.ndarray
    volume_cm3: float


@dataclass
class VolumeComparison:
    key: Key
    cells: list[int]
    cad_volume_cm3: float  # of the tessellated CAD, consistent with the sampling
    mcnp_volume_cm3: float
    mcnp_volume_std_cm3: float
    cad_only_cm3: float  # inside the CAD but outside the cells
    mcnp_only_cm3: float  # inside the cells but outside the CAD
    touches_sampling_box: bool  # the cells may extend beyond the sampled region
    error: str = ""
    # Volume and its standard deviation shared with the cells of other instances
    # (with its own key: cells of the instance overlapping each other)
    overlaps: dict[Key, tuple[float, float]] = field(default_factory=dict)
    spots: list[Spot] = field(default_factory=list)  # only when asked to locate

    @property
    def discrepancy_pct(self) -> float:
        """Volume where CAD and MCNP disagree, relative to the CAD volume."""
        if self.cad_volume_cm3 <= 0:
            return float("nan")
        return (self.cad_only_cm3 + self.mcnp_only_cm3) / self.cad_volume_cm3 * 100

    def exceeds(self, threshold: float) -> bool:
        """Whether CAD and MCNP disagree beyond the threshold [%] (or could not be
        compared)."""
        return (
            bool(self.error)
            or self.touches_sampling_box
            or not self.discrepancy_pct <= threshold
        )

    def is_problem(self, threshold: float) -> bool:
        return self.exceeds(threshold) or bool(self.overlaps)


class TaggedCells:
    """The cells of every instance and a box around them (cm), to find the cells of
    other instances near a component.

    Cells made of oblique planes often cannot be bounded from their surfaces. With
    the CAD solids of the instance, its cells are bounded by the box sampled when
    it is compared: cells beyond it are reported then (``touches_sampling_box``).

    Each pair of the ``compared`` instances is measured once, from the instance whose
    boxes are smaller, hence sampled more densely: a bolt in a plate is checked from
    the bolt. Every overlap is inside both sampled regions.
    """

    def __init__(
        self,
        geometry: Geometry,
        cells: Mapping[Key, Sequence[int]],
        solids: Mapping[Key, Sequence[TopoDS_Shape]] | None = None,
        compared: Collection[Key] = (),
        margin: float = 0.05,
    ):
        self.cells = {key: list(numbers) for key, numbers in cells.items() if numbers}
        self.keys = list(self.cells)
        self._index = {key: i for i, key in enumerate(self.keys)}
        self.lo = np.full((len(self.keys), 3), -np.inf)
        self.hi = np.full((len(self.keys), 3), np.inf)
        self._sampled_volume: dict[Key, float] = {}
        self._sampled: dict[Key, np.ndarray] = {}  # (B, 6) boxes of the solids
        for i, key in enumerate(self.keys):
            boxes = [geometry.cell_box(n) for n in self.cells[key]]
            if all(b is not None for b in boxes):
                self.lo[i] = np.min([b[0] for b in boxes if b is not None], axis=0)
                self.hi[i] = np.max([b[1] for b in boxes if b is not None], axis=0)
            if solids and solids.get(key):
                sampled = _padded_boxes(solids[key], margin) / MM_PER_CM
                self.lo[i] = np.maximum(self.lo[i], sampled[:, :3].min(axis=0))
                self.hi[i] = np.minimum(self.hi[i], sampled[:, 3:].max(axis=0))
                self._sampled[key] = sampled
                volumes = np.prod(sampled[:, 3:] - sampled[:, :3], axis=1)
                self._sampled_volume[key] = float(volumes.sum())
        self._compared = set(compared) & set(self._sampled_volume)

    def measures(self, key: Key, other: Key) -> bool:
        """Whether the comparison of ``key`` measures its overlap with ``other``."""
        if other == key:
            return False
        if key not in self._compared or other not in self._compared:
            return True
        volume = self._sampled_volume
        return (volume[key], key) < (volume[other], other)

    def near(self, lo: np.ndarray, hi: np.ndarray) -> list[Key]:
        """Instances whose boxes intersect the box from lo to hi (cm)."""
        index = np.flatnonzero(np.all((self.lo <= hi) & (self.hi >= lo), axis=1))
        return [self.keys[i] for i in index]

    def around(self, key: Key, points: np.ndarray) -> np.ndarray:
        """Mask of the points (cm) that can be in the cells of an instance."""
        i = self._index[key]
        inside = np.all((points >= self.lo[i]) & (points <= self.hi[i]), axis=1)
        boxes = self._sampled.get(key)
        if boxes is not None and len(boxes) > 1:
            in_any = np.zeros(len(points), dtype=bool)
            for box in boxes:
                in_any |= np.all((points >= box[:3]) & (points <= box[3:]), axis=1)
            inside &= in_any
        return inside


def compare_component(
    key: Key,
    solids: Sequence[TopoDS_Shape],
    cells: list[int],
    geometry: Geometry,
    others: TaggedCells | None = None,
    n_points: int = 100_000,
    points_per_line: int = 20,
    margin: float = 0.05,
    rng: np.random.Generator | None = None,
    locate: bool = False,
) -> VolumeComparison:
    """Compare the CAD solids of an instance with its MCNP cells by point sampling.

    The points are spread over the bounding box of every solid (padded by
    ``margin``), in proportion to the box volumes, so that instances made of many
    small solids scattered far apart are sampled as densely as compact ones. Each
    point is weighted by one over the number of boxes that contain it, which keeps
    every estimate unbiased where boxes overlap. Every box gets at least two lines, so
    instances with many solids can get a few more points than ``n_points``.

    The cells in ``others`` (except those of ``key``) are checked for overlaps with
    the cells of the instance. With ``locate``, the points of every overlap and of
    the CAD/MCNP disagreements are grouped into :class:`Spot` clusters.
    """
    rng = rng or np.random.default_rng()
    try:
        meshes = [mesh_of(s) for s in solids]
    except ValueError as exc:
        return _failed(key, cells, float("nan"), f"CAD: {exc}")
    cad_volume = sum(m.volume for m in meshes) / 1000.0

    boxes = _padded_boxes(solids, margin)  # (B, 6) mm
    box_volumes = np.prod(boxes[:, 3:] - boxes[:, :3], axis=1) / 1000.0  # cm3
    lines_per_box = _allocate_lines(box_volumes, max(n_points // points_per_line, 1))
    box_of_line = np.repeat(np.arange(len(boxes)), lines_per_box)
    lo, hi = boxes[box_of_line, :3], boxes[box_of_line, 3:]
    n_lines = len(box_of_line)
    line_uv = lo[:, :2] + rng.random((n_lines, 2)) * (hi - lo)[:, :2]
    t = lo[:, 2:] + rng.random((n_lines, points_per_line)) * (hi - lo)[:, 2:]
    points = np.concatenate(
        [np.broadcast_to(line_uv[:, None, :], (*t.shape, 2)), t[..., None]], axis=-1
    )  # (L, K, 3) mm
    weight = 1.0 / _box_multiplicity(points, boxes, lines_per_box)

    frame = LineFrame(np.zeros(3), np.eye(3))
    in_cad = np.zeros(t.shape, dtype=bool)
    for mesh in meshes:
        mesh_lo, mesh_hi = mesh.bounds
        crossing = np.flatnonzero(
            np.all((line_uv >= mesh_lo[:2]) & (line_uv <= mesh_hi[:2]), axis=1)
        )
        if crossing.size:
            in_cad[crossing] |= inside_on_lines(
                mesh, frame, line_uv[crossing], t[crossing]
            )

    evaluator = geometry.evaluator(points.reshape(-1, 3) / MM_PER_CM)
    cell_count = np.zeros(points.shape[0] * points.shape[1], dtype=np.int32)
    try:
        for cell in cells:
            cell_count += evaluator.cell(cell)
    except ValueError as exc:
        return _failed(key, cells, cad_volume, str(exc))
    in_cell = cell_count > 0
    shared = {key: cell_count > 1} if np.any(cell_count > 1) else {}
    if others is not None and np.any(in_cell):
        # Other cells only matter inside the cells of the instance
        index = np.flatnonzero(in_cell)
        inside = points.reshape(-1, 3)[index] / MM_PER_CM
        for other in others.near(inside.min(axis=0), inside.max(axis=0)):
            if not others.measures(key, other):
                continue
            in_box = others.around(other, inside)
            candidates = index[in_box]
            if not candidates.size:
                continue
            candidate_evaluator = geometry.evaluator(inside[in_box])
            hits = np.zeros(candidates.size, dtype=bool)
            try:
                for cell in others.cells[other]:
                    hits |= candidate_evaluator.cell(cell)
            except ValueError:
                continue  # reported when that instance is compared
            if np.any(hits):
                shared[other] = np.zeros_like(in_cell)
                shared[other][candidates[hits]] = True
    in_cell = in_cell.reshape(t.shape)

    cad_only = in_cad & ~in_cell
    mcnp_only = in_cell & ~in_cad

    def integral(values: np.ndarray) -> tuple[float, float]:
        """Integral over the union of the boxes and its variance. Batch means per
        line account for the correlation of points on the same line."""
        line_means = (values * weight).mean(axis=1)
        n = lines_per_box
        mean = np.bincount(box_of_line, line_means, len(boxes)) / n
        squares = np.bincount(box_of_line, line_means**2, len(boxes))
        variance = np.maximum(squares - n * mean**2, 0) / (n - 1)
        estimate = float(box_volumes @ mean)
        return estimate, float(box_volumes**2 @ (variance / n))

    difference, variance = integral(mcnp_only.astype(float) - cad_only)
    overlaps = {}
    for other, mask in sorted(shared.items()):
        volume, overlap_variance = integral(mask.reshape(t.shape).astype(float))
        overlaps[other] = (volume, float(np.sqrt(overlap_variance)))

    spots = []
    if locate:
        # Volume represented by each point, and the spacing of the lines of points
        point_volume = (
            (box_volumes[box_of_line] / lines_per_box[box_of_line])[:, None]
            * weight
            / points_per_line
        )
        widest = int(np.argmax(lines_per_box))
        area = np.prod(boxes[widest, 3:5] - boxes[widest, :2]) / MM_PER_CM**2
        spacing = math.sqrt(area / lines_per_box[widest])
        flat = points.reshape(-1, 3) / MM_PER_CM
        problems = [("mismatch", None, (cad_only | mcnp_only).reshape(-1))]
        problems += [("overlap", k, mask) for k, mask in sorted(shared.items())]
        for kind, other, mask in problems:
            spots += _spots(
                kind, other, flat[mask], point_volume.reshape(-1)[mask], 2 * spacing
            )

    # Cells reaching the faces of the sampling boxes, away from any other box,
    # probably continue beyond them
    near = 0.01 * (hi - lo)[:, None, :]
    on_faces = np.any(
        (points < lo[:, None, :] + near) | (points > hi[:, None, :] - near), axis=-1
    )
    return VolumeComparison(
        key=key,
        cells=cells,
        cad_volume_cm3=cad_volume,
        mcnp_volume_cm3=cad_volume + difference,
        mcnp_volume_std_cm3=float(np.sqrt(variance)),
        cad_only_cm3=integral(cad_only.astype(float))[0],
        mcnp_only_cm3=integral(mcnp_only.astype(float))[0],
        touches_sampling_box=bool(np.any(in_cell & on_faces & (weight == 1))),
        overlaps=overlaps,
        spots=spots,
    )


class Comparison:
    """Compares instances with their cells, each with its own random points.

    The points of an instance only depend on ``entropy`` and its key, not on the
    other instances compared or on their order: the worker processes compare any
    instance and get the same result.
    """

    def __init__(
        self,
        geometry: Geometry,
        solids: Mapping[Key, Sequence[TopoDS_Shape]],
        cells: Mapping[Key, Sequence[int]],
        compared: Collection[Key],
        n_points: int,
        entropy: int,
        locate: bool = False,
    ):
        self.geometry = geometry
        self.solids = solids
        self.cells = cells
        self.tagged = TaggedCells(geometry, cells, solids, compared)
        self.n_points = n_points
        self.entropy = entropy
        self.locate = locate

    def compare(self, key: Key) -> VolumeComparison:
        return compare_component(
            key,
            self.solids[key],
            list(self.cells.get(key, [])),
            self.geometry,
            self.tagged,
            n_points=self.n_points,
            rng=np.random.default_rng([self.entropy, key.id, key.instance]),
            locate=self.locate,
        )


def _spots(
    kind: str,
    other: Key | None,
    points: np.ndarray,
    volumes: np.ndarray,
    size: float,
    max_spots: int = 3,
    min_fraction: float = 0.1,
) -> list[Spot]:
    """The largest clusters of some problem points (cm), skipping those much smaller
    than the largest one (scattered points along surfaces where the tessellated CAD
    and the exact MCNP surfaces differ)."""
    if not len(points):
        return []
    labels = cluster_labels(points, size)
    totals = np.bincount(labels, volumes)
    spots = []
    for label in np.argsort(-totals)[:max_spots]:
        if totals[label] < min_fraction * totals.max():
            break
        members = points[labels == label]
        weights = volumes[labels == label]
        centre = weights @ members / weights.sum()
        point = members[np.argmin(np.linalg.norm(members - centre, axis=1))]
        spots.append(
            Spot(
                kind,
                other,
                point,
                members.min(axis=0),
                members.max(axis=0),
                float(totals[label]),
            )
        )
    return spots


def cluster_labels(points: np.ndarray, size: float) -> np.ndarray:
    """Group points (N, d) into clusters: points in neighbouring cubes of a grid of
    the given size are in the same cluster. Returns a label from 0 per point."""
    cubes = np.floor(points / size).astype(np.int64)
    unique, inverse = np.unique(cubes, axis=0, return_inverse=True)
    index = {tuple(c): i for i, c in enumerate(unique.tolist())}
    offsets = list(itertools.product((-1, 0, 1), repeat=points.shape[1]))
    cube_label = np.full(len(unique), -1)
    n_labels = 0
    for start in range(len(unique)):
        if cube_label[start] >= 0:
            continue
        cube_label[start] = n_labels
        stack = [start]
        while stack:
            cube = unique[stack.pop()]
            for offset in offsets:
                neighbour = index.get(tuple((cube + offset).tolist()))
                if neighbour is not None and cube_label[neighbour] < 0:
                    cube_label[neighbour] = n_labels
                    stack.append(neighbour)
        n_labels += 1
    return cube_label[inverse.reshape(-1)]


def _padded_boxes(solids: Sequence[TopoDS_Shape], margin: float) -> np.ndarray:
    """(B, 6) bounding boxes of the solids, enlarged by a margin on every side."""
    boxes = np.array([bounding_box(s) for s in solids], dtype=float)
    size = boxes[:, 3:] - boxes[:, :3]
    pad = margin * size + 1e-3 * np.maximum(size.max(axis=1, keepdims=True), 1.0)
    return np.hstack([boxes[:, :3] - pad, boxes[:, 3:] + pad])


def _allocate_lines(box_volumes: np.ndarray, n_lines: int) -> np.ndarray:
    """Lines per box, in proportion to the box volume, and at least two (needed for
    the variance)."""
    share = box_volumes / box_volumes.sum() * n_lines
    return np.maximum(np.round(share).astype(np.int64), 2)


def _box_multiplicity(
    points: np.ndarray, boxes: np.ndarray, lines_per_box: np.ndarray
) -> np.ndarray:
    """Number of boxes containing every point. The points of each box are stored
    contiguously, and only boxes overlapping theirs are checked."""
    count = np.ones(points.shape[:2], dtype=np.int64)
    lo, hi = boxes[:, :3], boxes[:, 3:]
    ends = np.cumsum(lines_per_box)
    for j in range(len(boxes)):
        overlapping = np.flatnonzero(np.all((lo <= hi[j]) & (hi >= lo[j]), axis=1))
        rows = slice(ends[j] - lines_per_box[j], ends[j])
        for k in overlapping:
            if k != j:
                inside = (points[rows] >= lo[k]) & (points[rows] <= hi[k])
                count[rows] += np.all(inside, axis=-1)
    return count


def _failed(
    key: Key, cells: list[int], cad_volume: float, error: str
) -> VolumeComparison:
    nan = float("nan")
    return VolumeComparison(
        key=key,
        cells=cells,
        cad_volume_cm3=cad_volume,
        mcnp_volume_cm3=nan,
        mcnp_volume_std_cm3=nan,
        cad_only_cm3=nan,
        mcnp_only_cm3=nan,
        touches_sampling_box=False,
        error=error,
    )


# --- slices ------------------------------------------------------------------------


@dataclass
class Plane:
    """A rectangular window on a plane, in cm."""

    origin: np.ndarray  # centre of the window
    u: np.ndarray  # horizontal axis of the image
    v: np.ndarray  # vertical axis of the image
    width: float
    height: float

    @property
    def normal(self) -> np.ndarray:
        return np.cross(self.u, self.v)

    @classmethod
    def from_basis(
        cls, origin, basis: str, width: float, height: float | None = None
    ) -> Plane:
        """Plane normal to an axis: 'x' (image axes y, z), 'y' (x, z), 'z' (x, y)."""
        axes = {"x": (1, 2), "y": (0, 2), "z": (0, 1)}[basis.lower()]
        eye = np.eye(3)
        return cls(
            np.asarray(origin, dtype=float),
            eye[axes[0]],
            eye[axes[1]],
            width,
            height if height is not None else width,
        )

    @classmethod
    def from_normal(
        cls, origin, normal, width: float, height: float | None = None, up=None
    ) -> Plane:
        n = np.asarray(normal, dtype=float)
        n /= np.linalg.norm(n)
        up = np.array([0.0, 0.0, 1.0]) if up is None else np.asarray(up, dtype=float)
        if abs(up @ n) > 0.99:
            up = np.array([0.0, 1.0, 0.0])
        v = up - (up @ n) * n
        v /= np.linalg.norm(v)
        u = np.cross(v, n)
        return cls(
            np.asarray(origin, dtype=float),
            u,
            v,
            width,
            height if height is not None else width,
        )

    def pixel_centres(self, n_u: int, n_v: int) -> tuple[np.ndarray, np.ndarray]:
        """Coordinates along u and v of the pixel centres."""
        su = (np.arange(n_u) + 0.5) / n_u * self.width - self.width / 2
        sv = (np.arange(n_v) + 0.5) / n_v * self.height - self.height / 2
        return su, sv

    def points(self, n_u: int, n_v: int) -> np.ndarray:
        """(n_v, n_u, 3) pixel centres in global coordinates, top row first."""
        su, sv = self.pixel_centres(n_u, n_v)
        grid_u, grid_v = np.meshgrid(su, sv[::-1])
        return self.origin + grid_u[..., None] * self.u + grid_v[..., None] * self.v


@dataclass
class SliceResult:
    plane: Plane
    cells: np.ndarray  # (n_v, n_u) cell number per pixel, 0 = no cell
    overlaps: np.ndarray  # (n_v, n_u) True where several cells contain the pixel
    materials: np.ndarray  # (n_v, n_u) material number, -1 = no cell
    # (n_v, n_u) True where the cell is tagged: a component, not filler void
    tagged: np.ndarray | None = None
    # (n_v, n_u) True where the CAD instance and the instance of the cell differ
    mismatch: np.ndarray | None = None

    @property
    def lost_pixels(self) -> int:
        return int((self.materials == -1).sum())

    @property
    def overlap_pixels(self) -> int:
        return int(self.overlaps.sum())

    @property
    def mismatch_pixels(self) -> int | None:
        return None if self.mismatch is None else int(self.mismatch.sum())


def slice_mcnp(geometry: Geometry, plane: Plane, n_u: int, n_v: int) -> SliceResult:
    evaluator = geometry.evaluator(plane.points(n_u, n_v).reshape(-1, 3))
    owner, count = evaluator.cell_index()
    material_of = {n: c.material for n, c in geometry.cells.items()}
    materials = np.array([material_of.get(int(n), -1) for n in owner])
    materials[owner == 0] = -1
    shape = (n_v, n_u)
    return SliceResult(
        plane,
        owner.reshape(shape).astype(np.int32),
        (count > 1).reshape(shape),
        materials.reshape(shape).astype(np.int32),
    )


def classify_cad_on_plane(
    components: Mapping[int, Sequence[TopoDS_Shape]], plane: Plane, n_u: int, n_v: int
) -> np.ndarray:
    """Component id of every pixel according to the CAD (0 where there is none)."""
    result = np.zeros((n_v, n_u), dtype=np.int64)
    su, sv = plane.pixel_centres(n_u, n_v)
    # Image rows are lines along u. Frame axes: e1 = v, e2 = normal, e3 = u (in mm).
    frame = LineFrame(
        plane.origin * MM_PER_CM, np.array([plane.v, plane.normal, plane.u])
    )
    # A tiny offset keeps the lines off mesh edges laid on "round" coordinates
    jitter = 1e-7 * max(plane.width, plane.height) * MM_PER_CM
    line_uv = np.column_stack([sv[::-1] * MM_PER_CM + jitter, np.full(n_v, jitter)])
    t = np.tile(su * MM_PER_CM, (n_v, 1))
    half_diagonal = np.hypot(plane.width, plane.height) / 2 * MM_PER_CM
    for component_id, solids in components.items():
        for solid in solids:
            if not near_plane(solid, plane, half_diagonal):
                continue
            mesh = mesh_of(solid)
            result[inside_on_lines(mesh, frame, line_uv, t)] = component_id
    return result


def near_plane(solid: TopoDS_Shape, plane: Plane, half_diagonal: float) -> bool:
    """Whether the solid's box crosses the plane near the window (lengths in mm)."""
    box = np.array(bounding_box(solid))
    centre, half = (box[:3] + box[3:]) / 2, (box[3:] - box[:3]) / 2
    offset = centre - plane.origin * MM_PER_CM
    # The box intersects the plane and is near the window
    crosses_plane = abs(offset @ plane.normal) <= np.abs(plane.normal) @ half
    near_window = np.linalg.norm(offset) <= half_diagonal + np.linalg.norm(half)
    return bool(crosses_plane and near_window)
