"""Triangle meshes of solids and fast point-in-solid classification.

Points are classified by ray parity. To make it fast the points are organised on
parallel lines: every line is intersected once with all the triangles, and then all the
points on that line are classified by counting the crossings below them. Volume
sampling and slice plots both naturally produce points on lines.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Shape

from radmodeling.cad.occ import bounding_box, faces

# Memory budget (number of line-triangle pairs) per vectorised chunk
_CHUNK_PAIRS = 4_000_000


@dataclass
class TriMesh:
    vertices: np.ndarray  # (V, 3) float
    triangles: np.ndarray  # (T, 3) int, outward oriented

    @property
    def volume(self) -> float:
        a, b, c = (self.vertices[self.triangles[:, i]] for i in range(3))
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.vertices.min(axis=0), self.vertices.max(axis=0)


def triangulate(
    shape: TopoDS_Shape,
    linear_deflection: float | None = None,
    angular_deflection: float = 0.1,
) -> TriMesh:
    """Tessellate a shape. The default deflection is 1e-4 of the bounding diagonal."""
    if linear_deflection is None:
        box = np.array(bounding_box(shape))
        linear_deflection = max(float(np.linalg.norm(box[3:] - box[:3])) * 1e-4, 1e-6)
    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, linear_deflection, False, angular_deflection, True)

    vertices, triangles, offset = [], [], 0
    for face in faces(shape):
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            raise ValueError("A face could not be tessellated")
        transform = location.Transformation()
        nodes = np.array(
            [
                triangulation.Node(i).Transformed(transform).Coord()
                for i in range(1, triangulation.NbNodes() + 1)
            ]
        )
        tris = (
            np.array(
                [
                    triangulation.Triangle(i).Get()
                    for i in range(1, triangulation.NbTriangles() + 1)
                ]
            )
            - 1
        )
        if face.Orientation() == TopAbs_REVERSED:
            tris = tris[:, ::-1]
        vertices.append(nodes)
        triangles.append(tris + offset)
        offset += len(nodes)
    if not vertices:
        return TriMesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=int))
    return TriMesh(np.vstack(vertices), np.vstack(triangles))


@dataclass(frozen=True)
class LineFrame:
    """Orthonormal frame whose third axis is the direction of the lines."""

    origin: np.ndarray
    axes: np.ndarray  # (3, 3) rows are the unit vectors e1, e2, e3(line direction)

    def to_local(self, points: np.ndarray) -> np.ndarray:
        return (points - self.origin) @ self.axes.T

    def to_global(self, local: np.ndarray) -> np.ndarray:
        return local @ self.axes + self.origin


def inside_on_lines(
    mesh: TriMesh, frame: LineFrame, line_uv: np.ndarray, t: np.ndarray
) -> np.ndarray:
    """Classify points lying on lines parallel to the frame's third axis.

    Args:
        mesh: closed (watertight) mesh.
        frame: frame where the lines are parallel to ``frame.axes[2]``.
        line_uv: (L, 2) position of every line in the frame's first two axes.
        t: (L, K) coordinate of every point along its line.

    Returns:
        (L, K) boolean array, True for the points inside the mesh.
    """
    n_lines, n_points = t.shape
    inside = np.zeros((n_lines, n_points), dtype=bool)
    if len(mesh.triangles) == 0 or n_lines == 0:
        return inside

    local = frame.to_local(mesh.vertices)
    tri = local[mesh.triangles]  # (T, 3, 3)

    # Discard triangles whose projection cannot hit any line
    lo, hi = tri[:, :, :2].min(axis=1), tri[:, :, :2].max(axis=1)
    keep = np.all(hi >= line_uv.min(axis=0), axis=1) & np.all(
        lo <= line_uv.max(axis=0), axis=1
    )
    tri, lo, hi = tri[keep], lo[keep], hi[keep]

    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (
        c[:, 0] - a[:, 0]
    )
    valid = np.abs(area) > 1e-300  # triangles parallel to the lines are never hit
    a, b, c, area, lo, hi = (
        a[valid],
        b[valid],
        c[valid],
        area[valid],
        lo[valid],
        hi[valid],
    )

    hit_lines, hit_t = [], []
    for li, ti in _candidate_pairs(line_uv, lo, hi):
        px, py = line_uv[li, 0], line_uv[li, 1]
        ax, ay, bx, by, cx, cy = (
            a[ti, 0],
            a[ti, 1],
            b[ti, 0],
            b[ti, 1],
            c[ti, 0],
            c[ti, 1],
        )
        w_a = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area[ti]
        w_b = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area[ti]
        w_c = 1.0 - w_a - w_b
        hit = (w_a >= 0) & (w_b >= 0) & (w_c >= 0)
        li, ti = li[hit], ti[hit]
        hit_lines.append(li)
        hit_t.append(w_a[hit] * a[ti, 2] + w_b[hit] * b[ti, 2] + w_c[hit] * c[ti, 2])

    if not hit_lines:
        return inside
    hit_lines_arr = np.concatenate(hit_lines)
    hit_t_arr = np.concatenate(hit_t)

    # Count, for every point, the crossings of its line that lie below it. A single
    # sorted key (line index + normalised t) lets searchsorted do it for all lines.
    t_min = min(hit_t_arr.min(), t.min())
    span = max(hit_t_arr.max(), t.max()) - t_min
    span = span * (1 + 1e-9) + 1e-300

    def keys(lines: np.ndarray, values: np.ndarray) -> np.ndarray:
        return lines * 2.0 + (values - t_min) / span

    hit_keys = np.sort(keys(hit_lines_arr, hit_t_arr))
    line_index = np.repeat(np.arange(n_lines), n_points)
    point_keys = keys(line_index, t.ravel())
    below = np.searchsorted(hit_keys, point_keys)
    line_start = np.searchsorted(
        hit_keys, keys(np.arange(n_lines), np.full(n_lines, t_min))
    )
    crossings = below - line_start[line_index]
    return (crossings % 2 == 1).reshape(n_lines, n_points)


def _expand(counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For ranges of the given lengths return (range index, position in range)."""
    owner = np.repeat(np.arange(len(counts)), counts)
    position = np.arange(len(owner)) - np.repeat(np.cumsum(counts) - counts, counts)
    return owner, position


def _candidate_pairs(line_uv: np.ndarray, tri_lo: np.ndarray, tri_hi: np.ndarray):
    """Yield chunks of (line index, triangle index) pairs whose 2D boxes overlap.

    Lines are binned in a uniform 2D grid so that each triangle is only paired with the
    lines in the grid cells its bounding box touches.
    """
    n_lines = len(line_uv)
    grid = int(np.clip(np.sqrt(n_lines / 4), 1, 2048))
    origin = line_uv.min(axis=0)
    size = (line_uv.max(axis=0) - origin) / grid
    size[size <= 0] = 1.0

    def cell(values: np.ndarray) -> np.ndarray:
        return np.clip(((values - origin) / size).astype(np.int64), 0, grid - 1)

    line_cells = cell(line_uv)
    line_cell_id = line_cells[:, 0] * grid + line_cells[:, 1]
    order = np.argsort(line_cell_id, kind="stable")
    cell_start = np.searchsorted(line_cell_id[order], np.arange(grid * grid))
    cell_count = np.diff(np.append(cell_start, n_lines))

    c0, c1 = cell(tri_lo), cell(tri_hi)
    span = c1 - c0 + 1
    n_cells = span[:, 0] * span[:, 1]
    # Chunk the triangles so that the number of (triangle, cell) pairs stays bounded
    bounds = np.searchsorted(
        np.cumsum(n_cells),
        np.arange(_CHUNK_PAIRS // 8, n_cells.sum(), _CHUNK_PAIRS // 8),
    )
    for chunk in np.split(np.arange(len(tri_lo)), bounds):
        if len(chunk) == 0:
            continue
        owner, position = _expand(n_cells[chunk])
        tri = chunk[owner]
        cx = c0[tri, 0] + position // span[tri, 1]
        cy = c0[tri, 1] + position % span[tri, 1]
        cell_id = cx * grid + cy
        counts = cell_count[cell_id]
        owner, position = _expand(counts)
        lines = order[cell_start[cell_id[owner]] + position]
        tri = tri[owner]
        overlap = (
            (line_uv[lines, 0] >= tri_lo[tri, 0])
            & (line_uv[lines, 0] <= tri_hi[tri, 0])
            & (line_uv[lines, 1] >= tri_lo[tri, 1])
            & (line_uv[lines, 1] <= tri_hi[tri, 1])
        )
        yield lines[overlap], tri[overlap]
