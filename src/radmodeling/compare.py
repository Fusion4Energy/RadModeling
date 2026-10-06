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

Lengths: the CAD is in mm and MCNP in cm. Results are reported in cm and cm3.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from OCP.TopoDS import TopoDS_Shape

from radmodeling.cad.mesh import LineFrame, TriMesh, inside_on_lines, triangulate
from radmodeling.cad.occ import bounding_box
from radmodeling.mcnp.geometry import Geometry
from radmodeling.registry import Key

MM_PER_CM = 10.0


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

    @property
    def discrepancy_pct(self) -> float:
        """Volume where CAD and MCNP disagree, relative to the CAD volume."""
        if self.cad_volume_cm3 <= 0:
            return float("nan")
        return (self.cad_only_cm3 + self.mcnp_only_cm3) / self.cad_volume_cm3 * 100


class TaggedCells:
    """The tagged cells of a geometry with their bounding boxes, to find the cells
    of other instances near a component."""

    def __init__(self, geometry: Geometry, cells: Mapping[Key, Sequence[int]]):
        self.keys = [key for key, numbers in cells.items() for _ in numbers]
        self.numbers = [n for numbers in cells.values() for n in numbers]
        boxes = [geometry.cell_box(n) for n in self.numbers]
        self.lo = np.array([b[0] if b else [-np.inf] * 3 for b in boxes]).reshape(-1, 3)
        self.hi = np.array([b[1] if b else [np.inf] * 3 for b in boxes]).reshape(-1, 3)

    def near(self, lo: np.ndarray, hi: np.ndarray) -> list[tuple[Key, int]]:
        """Cells whose boxes intersect the box from lo to hi (cm)."""
        index = np.flatnonzero(np.all((self.lo <= hi) & (self.hi >= lo), axis=1))
        return [(self.keys[i], self.numbers[i]) for i in index]


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
) -> VolumeComparison:
    """Compare the CAD solids of an instance with its MCNP cells by point sampling.

    The points are spread over the bounding box of every solid (padded by
    ``margin``), in proportion to the box volumes, so that instances made of many
    small solids scattered far apart are sampled as densely as compact ones. Each
    point is weighted by one over the number of boxes that contain it, which keeps
    every estimate unbiased where boxes overlap. Every box gets at least two lines, so
    instances with many solids can get a few more points than ``n_points``.

    The cells in ``others`` (except those of ``key``) are checked for overlaps with
    the cells of the instance.
    """
    rng = rng or np.random.default_rng()
    try:
        meshes = [triangulate(s) for s in solids]
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
        inside = points.reshape(-1, 3)[in_cell] / MM_PER_CM
        for other, cell in others.near(inside.min(axis=0), inside.max(axis=0)):
            if other == key:
                continue
            try:
                mask = in_cell & evaluator.cell(cell)
            except ValueError:
                continue  # reported when that instance is compared
            if np.any(mask):
                shared[other] = shared[other] | mask if other in shared else mask
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
    )


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
            mesh: TriMesh = triangulate(solid)
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
