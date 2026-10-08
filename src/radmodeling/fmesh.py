"""FMESH tally cards from boxes drawn in CAD.

Draw a box (any orientation) where the mesh tally should be and run the SpaceClaim
*Fmesh* tool on it; the box faces define the mesh. Rotated boxes get a TR card for the
mesh.
"""

from __future__ import annotations

import math
import os
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_SurfaceType as ST
from OCP.TopAbs import TopAbs_VERTEX
from OCP.TopoDS import TopoDS, TopoDS_Solid

from radmodeling.cad.occ import explore, faces, volume_mm3
from radmodeling.cad.step import read_step

MM_PER_CM = 10.0
MAX_TRANSFORM = 999
LINE_LENGTH = 80  # MCNP 6.1 and older; 6.2 allows 128
FIRST_TALLY = 14
FIRST_TRANSFORM = 900
# Boxes within this of the main axes (direction cosines) are snapped to them: no TR
# for the round-off of a STEP export. Off by at most this times the box size.
ALIGNED_TOLERANCE = 1e-6
ZERO = 1e-9  # written values below this are round-off: written as 0


@dataclass
class MeshBox:
    """Box in cm: corner + lengths along three orthonormal axes."""

    corner: np.ndarray
    axes: np.ndarray  # rows
    lengths: np.ndarray

    @property
    def axis_aligned(self) -> bool:
        return bool(np.array_equal(self.axes, np.eye(3)))


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
    u, _, vt = np.linalg.svd(axes)  # nearest orthonormal frame: exact TR rows
    axes = u @ vt
    if np.allclose(axes, np.eye(3), atol=ALIGNED_TOLERANCE):
        axes = np.eye(3)

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
    lines.append(f"     ORIGIN={_numbers(origin)}")
    for name, end, count in zip("IJK", ends, counts, strict=True):
        lines.append(f"     {name}MESH={_numbers([end])} {name}INTS={count}")
    if not box.axis_aligned:
        lines.append(f"     TR={transform}")
        # Auxiliary origin and axes in main coordinates (M = 1)
        lines.append(f"TR{transform} {_numbers(box.corner)}")
        for row in box.axes:
            lines.append(f"     {_numbers(row)}")
    return "\n".join(lines) + "\n"


def _numbers(values) -> str:
    """Nine significant digits, round-off written as 0 (never -0 or 1e-17)."""
    return " ".join(f"{v if abs(v) >= ZERO else 0.0:.9g}" for v in values)


def voxel_counts(box: MeshBox, size: float) -> list[int]:
    """Voxels per axis so that none is larger than ``size`` cm."""
    return [max(1, math.ceil(length / size - 1e-9)) for length in box.lengths]


def comment_line(text: str) -> str:
    """MCNP comment card: ASCII only (accents dropped), within the line length."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return f"c {' '.join(ascii_text.split())}"[:LINE_LENGTH].rstrip()


@dataclass
class FmeshCards:
    text: str = ""  # the cards, each after a comment line with the box name
    tallies: list[tuple[int, str, list[int]]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # name, why


def fmesh_cards(step: str | os.PathLike, size: float) -> FmeshCards:
    """FMESH cards for every box of a STEP file, with voxels of about ``size`` cm.

    The tallies are numbered from :data:`FIRST_TALLY` in steps of 10, and rotated
    boxes get transforms numbered from :data:`FIRST_TRANSFORM`.
    """
    if size <= 0:
        raise ValueError("the voxel size must be positive")
    result = FmeshCards()
    cards = []
    number, tr = FIRST_TALLY, FIRST_TRANSFORM
    # SpaceClaim names the solids after the bodies: a few boxes, so reading is fast
    for part in read_step(step, body_names=lambda _: True):
        for k, solid in enumerate(part.solids):
            name = part.body_names[k] if k < len(part.body_names) else ""
            name = name or part.name
            try:
                box = box_from_solid(solid)
                card = fmesh_card(box, number, size=size, transform=tr)
            except ValueError as exc:
                result.skipped.append((name, str(exc)))
                continue
            cards.append(comment_line(name) + "\n" + card)
            result.tallies.append((number, name, voxel_counts(box, size)))
            number += 10
            tr += 0 if box.axis_aligned else 1
    result.text = "".join(cards)
    return result
