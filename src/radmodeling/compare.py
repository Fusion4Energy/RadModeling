"""Comparison of the CAD model with the MCNP geometry produced from it.

For every component the same random points are classified against the CAD solids and
against the MCNP cells of that component. This gives, without running MCNP:

* the volume of the MCNP cells, with an unbiased estimator whose uncertainty only
  depends on the points where CAD and MCNP disagree (hence very small when the
  conversion is good). It is the volume needed for the density correction factor.
* the volume where CAD and MCNP disagree, a direct measure of the conversion quality.

Lengths: the CAD is in mm and MCNP in cm. Results are reported in cm and cm3.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from OCP.TopoDS import TopoDS_Solid

from radmodeling.cad.mesh import LineFrame, TriMesh, inside_on_lines, triangulate
from radmodeling.cad.occ import bounding_box
from radmodeling.mcnp.geometry import Geometry

MM_PER_CM = 10.0


@dataclass
class VolumeComparison:
    component_id: int
    cells: list[int]
    cad_volume_cm3: float  # of the tessellated CAD, consistent with the sampling
    mcnp_volume_cm3: float
    mcnp_volume_std_cm3: float
    cad_only_cm3: float  # inside the CAD but outside the cells
    mcnp_only_cm3: float  # inside the cells but outside the CAD
    touches_sampling_box: bool  # the cells may extend beyond the sampled region
    error: str = ""

    @property
    def discrepancy_pct(self) -> float:
        """Volume where CAD and MCNP disagree, relative to the CAD volume."""
        if self.cad_volume_cm3 <= 0:
            return float("nan")
        return (self.cad_only_cm3 + self.mcnp_only_cm3) / self.cad_volume_cm3 * 100


def compare_component(
    component_id: int,
    solids: list[TopoDS_Solid],
    cells: list[int],
    geometry: Geometry,
    n_points: int = 100_000,
    points_per_line: int = 20,
    margin: float = 0.05,
    rng: np.random.Generator | None = None,
) -> VolumeComparison:
    rng = rng or np.random.default_rng()
    meshes = [triangulate(s) for s in solids]
    cad_volume = sum(m.volume for m in meshes) / 1000.0

    lo, hi = _union_bounds(solids)
    size = hi - lo
    pad = margin * size + 1e-3 * max(size.max(), 1.0)
    lo, hi = lo - pad, hi + pad
    box_volume_cm3 = float(np.prod(hi - lo)) / 1000.0

    n_lines = max(n_points // points_per_line, 1)
    line_uv = lo[:2] + rng.random((n_lines, 2)) * (hi - lo)[:2]
    t = lo[2] + rng.random((n_lines, points_per_line)) * (hi - lo)[2]
    frame = LineFrame(np.zeros(3), np.eye(3))

    in_cad = np.zeros(t.shape, dtype=bool)
    for mesh in meshes:
        in_cad |= inside_on_lines(mesh, frame, line_uv, t)

    points_mm = np.column_stack(
        [np.repeat(line_uv, points_per_line, axis=0), t.ravel()]
    )
    evaluator = geometry.evaluator(points_mm / MM_PER_CM)
    in_cell = np.zeros(len(points_mm), dtype=bool)
    try:
        for cell in cells:
            in_cell |= evaluator.cell(cell)
    except ValueError as exc:
        return VolumeComparison(
            component_id, cells, cad_volume, *([float("nan")] * 4), False, str(exc)
        )
    in_cell = in_cell.reshape(t.shape)

    cad_only = in_cad & ~in_cell
    mcnp_only = in_cell & ~in_cad
    # Batch means per line account for the correlation of points on the same line
    per_line = (mcnp_only.astype(float) - cad_only).mean(axis=1)
    mcnp_volume = cad_volume + per_line.mean() * box_volume_cm3
    std = per_line.std(ddof=1) / np.sqrt(n_lines) * box_volume_cm3 if n_lines > 1 else 0

    # Cells reaching the faces of the sampling box probably continue beyond it
    near = 0.01 * (hi - lo)
    points = points_mm.reshape(*t.shape, 3)
    on_faces = np.any((points < lo + near) | (points > hi - near), axis=-1)

    return VolumeComparison(
        component_id=component_id,
        cells=cells,
        cad_volume_cm3=cad_volume,
        mcnp_volume_cm3=float(mcnp_volume),
        mcnp_volume_std_cm3=float(std),
        cad_only_cm3=float(cad_only.mean() * box_volume_cm3),
        mcnp_only_cm3=float(mcnp_only.mean() * box_volume_cm3),
        touches_sampling_box=bool(np.any(in_cell & on_faces)),
    )


def _union_bounds(solids: list[TopoDS_Solid]) -> tuple[np.ndarray, np.ndarray]:
    boxes = np.array([bounding_box(s) for s in solids])
    return boxes[:, :3].min(axis=0), boxes[:, 3:].max(axis=0)


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
    cad_component: np.ndarray | None = None  # component id per pixel, 0 = none
    mcnp_component: np.ndarray | None = None


def slice_mcnp(geometry: Geometry, plane: Plane, n_u: int, n_v: int) -> SliceResult:
    evaluator = geometry.evaluator(plane.points(n_u, n_v).reshape(-1, 3))
    owner, count = evaluator.cell_index()
    material_of = {n: c.material for n, c in geometry.cells.items()}
    materials = np.array([material_of.get(int(n), -1) for n in owner])
    materials[owner == 0] = -1
    shape = (n_v, n_u)
    return SliceResult(
        plane,
        owner.reshape(shape),
        (count > 1).reshape(shape),
        materials.reshape(shape),
    )


def classify_cad_on_plane(
    components: dict[int, list[TopoDS_Solid]], plane: Plane, n_u: int, n_v: int
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


def near_plane(solid: TopoDS_Solid, plane: Plane, half_diagonal: float) -> bool:
    """Whether the solid's box crosses the plane near the window (lengths in mm)."""
    box = np.array(bounding_box(solid))
    centre, half = (box[:3] + box[3:]) / 2, (box[3:] - box[:3]) / 2
    offset = centre - plane.origin * MM_PER_CM
    # The box intersects the plane and is near the window
    crosses_plane = abs(offset @ plane.normal) <= np.abs(plane.normal) @ half
    near_window = np.linalg.norm(offset) <= half_diagonal + np.linalg.norm(half)
    return bool(crosses_plane and near_window)
