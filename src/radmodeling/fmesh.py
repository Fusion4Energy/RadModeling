"""FMESH tally cards from boxes drawn in CAD.

Draw a box (any orientation) where the mesh tally should be and run the SpaceClaim
*Fmesh* tool on it; the box faces define the mesh. Rotated boxes get a TR card for the
mesh.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_SurfaceType as ST
from OCP.TopAbs import TopAbs_VERTEX
from OCP.TopoDS import TopoDS, TopoDS_Solid

from radmodeling.cad.occ import explore, faces, volume_mm3

MM_PER_CM = 10.0
MAX_TRANSFORM = 999
LINE_LENGTH = 80  # MCNP 6.1 and older; 6.2 allows 128
FIRST_TALLY = 14
FIRST_TRANSFORM = 900


@dataclass
class MeshBox:
    """Box in cm: corner + lengths along three orthonormal axes."""

    corner: np.ndarray
    axes: np.ndarray  # rows
    lengths: np.ndarray

    @property
    def axis_aligned(self) -> bool:
        return bool(np.allclose(np.abs(self.axes), np.eye(3), atol=1e-9))


def box_from_solid(solid: TopoDS_Solid, tolerance: float = 1e-6) -> MeshBox:
    """Recognise a box: planar faces in three orthogonal directions, filling the box.

    Faces may be split (imprints); the volume check rejects any other shape with the
    same face directions (L-shapes, steps).
    """
    normals = []
    for face in faces(solid):
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() != ST.GeomAbs_Plane:
            raise ValueError("The solid is not a box: it has non-planar faces")
        normal = np.array(surface.Plane().Axis().Direction().Coord())
        if not any(abs(abs(normal @ n) - 1) < tolerance for n in normals):
            normals.append(normal)
    if len(normals) != 3:
        raise ValueError("The solid is not a box: its faces need 3 directions")

    normals = np.array(normals)
    if not np.allclose(normals @ normals.T, np.eye(3), atol=1e-6):
        raise ValueError("The solid is not a box: its faces are not orthogonal")
    # Deterministic right-handed frame: k closest to z, i closest to x, j = k x i
    k_index = int(np.argmax(np.abs(normals[:, 2])))
    k_axis = normals[k_index] * np.sign(normals[k_index, 2] or 1.0)
    rest = [n for i, n in enumerate(normals) if i != k_index]
    i_axis = max(rest, key=lambda n: abs(n[0]))
    i_axis = i_axis * np.sign(i_axis[0] or 1.0)
    j_axis = np.cross(k_axis, i_axis)
    axes = np.array([i_axis, j_axis, k_axis])

    vertices = np.array([_vertex_point(v) for v in explore(solid, TopAbs_VERTEX)])
    vertices /= MM_PER_CM
    projected = vertices @ axes.T
    low, high = projected.min(axis=0), projected.max(axis=0)
    box = MeshBox(low @ axes, axes, high - low)
    box_volume = float(np.prod(box.lengths))
    solid_volume = volume_mm3(solid) / MM_PER_CM**3
    if abs(solid_volume - box_volume) > 1e-4 * box_volume:
        raise ValueError(
            f"The solid is not a box: {solid_volume:.6g} cm3 instead of "
            f"{box_volume:.6g} cm3"
        )
    return box


def _vertex_point(vertex) -> tuple[float, float, float]:
    from OCP.BRep import BRep_Tool

    cast = getattr(TopoDS, "Vertex_s", None) or TopoDS.Vertex
    return BRep_Tool.Pnt_s(cast(vertex)).Coord()


def fmesh_card(
    box: MeshBox,
    tally: int = 4,
    particle: str = "n",
    size: float | None = None,
    bins: Sequence[int] | None = None,
    transform: int = 999,
) -> str:
    """Write the FMESH card (and the TR card when the box is rotated).

    Args:
        size: target voxel size in cm; ``bins`` gives the number of voxels instead.
    """
    if tally % 10 != 4:
        raise ValueError("FMESH tally numbers must end in 4")
    if not box.axis_aligned and not 1 <= transform <= MAX_TRANSFORM:
        raise ValueError(f"TR numbers go from 1 to {MAX_TRANSFORM}, not {transform}")
    if bins is None:
        if size is None:
            raise ValueError("Give the voxel size or the number of bins")
        counts = voxel_counts(box, size)
    else:
        counts = list(bins)

    lines = [f"FMESH{tally}:{particle} GEOM=XYZ"]
    if box.axis_aligned:
        origin, ends = box.corner, box.corner + box.lengths
    else:
        origin, ends = np.zeros(3), box.lengths
    lines.append(f"     ORIGIN={origin[0]:.6g} {origin[1]:.6g} {origin[2]:.6g}")
    for name, end, count in zip("IJK", ends, counts, strict=True):
        lines.append(f"     {name}MESH={end:.6g} {name}INTS={count}")
    if not box.axis_aligned:
        lines.append(f"     TR={transform}")
        # Auxiliary origin and axes in main coordinates (M = 1)
        corner = " ".join(f"{v + 0.0:.9g}" for v in box.corner)
        lines.append(f"TR{transform} {corner}")
        for row in box.axes:
            lines.append("     " + " ".join(f"{v + 0.0:.9g}" for v in row))
    return "\n".join(lines) + "\n"


def voxel_counts(box: MeshBox, size: float) -> list[int]:
    """Voxels per axis so that none is larger than ``size`` cm."""
    return [max(1, math.ceil(length / size - 1e-9)) for length in box.lengths]


def comment_line(text: str) -> str:
    """MCNP comment card: ASCII only (accents dropped), within the line length."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return f"c {' '.join(ascii_text.split())}"[:LINE_LENGTH].rstrip()
