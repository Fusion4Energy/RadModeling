"""Replacement of toroidal pipe elbows by mitred cylinder segments.

Tori are expensive for MCNP (quartic surfaces) and many converters handle them
poorly. A pipe elbow is replaced by the classic mitred (gored) bend: straight segments
following the tangent polygon of the bend centre line. The two end segments are half
length and start perpendicular to the elbow ends, so the result joins the adjacent
straight pipes seamlessly.

The polygon is slightly longer than the arc, so the volume grows a little (1.3% for a
90 degree elbow in 4 segments). Either keep the radii (default, exact joints, the
density correction factor absorbs the difference) or scale them to keep the volume.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeHalfSpace
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_SurfaceType as ST
from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt
from OCP.GProp import GProp_GProps
from OCP.TopoDS import TopoDS_Shape, TopoDS_Solid

from radmodeling.cad.occ import faces, solids, to_face, volume_mm3


@dataclass
class Elbow:
    centre: np.ndarray
    axis: np.ndarray  # unit vector, the sweep is counterclockwise around it
    x_dir: np.ndarray  # reference direction of angle 0
    major_radius: float
    minor_radii: list[float]  # outer, and inner for a hollow pipe
    start_angle: float
    sweep: float  # radians, positive

    def point(self, angle: float, radius: float | None = None) -> np.ndarray:
        radius = self.major_radius if radius is None else radius
        y_dir = np.cross(self.axis, self.x_dir)
        return self.centre + radius * (
            math.cos(angle) * self.x_dir + math.sin(angle) * y_dir
        )

    def tangent(self, angle: float) -> np.ndarray:
        y_dir = np.cross(self.axis, self.x_dir)
        return -math.sin(angle) * self.x_dir + math.cos(angle) * y_dir


def _vec(xyz) -> np.ndarray:
    return np.array(xyz.Coord() if hasattr(xyz, "Coord") else xyz, dtype=float)


def detect_elbow(solid: TopoDS_Solid, tolerance: float = 1e-6) -> Elbow | None:
    """Return the elbow parameters if the solid is a plain torus segment.

    A plain elbow only has toroidal faces sharing centre, axis and major radius, plus
    two planar end caps.
    """
    tori, planes = [], []
    for face in faces(solid):
        surface = BRepAdaptor_Surface(face)
        kind = surface.GetType()
        if kind == ST.GeomAbs_Torus:
            tori.append((face, surface.Torus()))
        elif kind == ST.GeomAbs_Plane:
            planes.append(face)
        else:
            return None
    if not tori or len(planes) != 2:
        return None

    first = tori[0][1]
    centre = _vec(first.Location())
    axis = _vec(first.Axis().Direction())
    major = first.MajorRadius()
    scale = max(major, 1.0)
    minor = set()
    for _, torus in tori:
        same_axis = abs(abs(_vec(torus.Axis().Direction()) @ axis) - 1) < tolerance
        if (
            not same_axis
            or np.linalg.norm(_vec(torus.Location()) - centre) > tolerance * scale
            or abs(torus.MajorRadius() - major) > tolerance * scale
        ):
            return None
        minor.add(round(torus.MinorRadius(), 9))
    if len(minor) > 2:
        return None

    x_dir = _vec(first.Position().XDirection())
    y_dir = np.cross(axis, x_dir)

    def angle_of(point: np.ndarray) -> float:
        d = point - centre
        return math.atan2(d @ y_dir, d @ x_dir)

    end_angles = []
    for plane in planes:
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(plane, props)
        centroid = _vec(props.CentreOfMass())
        d = centroid - centre
        radial = d - (d @ axis) * axis
        if (
            abs(d @ axis) > 1e-4 * scale
            or abs(np.linalg.norm(radial) - major) > 1e-4 * scale
        ):
            return None  # caps not centred on the bend centre line
        end_angles.append(angle_of(centroid))

    # A point inside the elbow tells which of the two arcs it occupies
    face = tori[0][0]
    u0, u1, v0, v1 = BRepTools.UVBounds_s(to_face(face))
    middle = _vec(BRepAdaptor_Surface(face).Value((u0 + u1) / 2, (v0 + v1) / 2))
    a, b = end_angles
    to_b = (b - a) % (2 * math.pi)
    to_middle = (angle_of(middle) - a) % (2 * math.pi)
    if to_middle < to_b:
        start, sweep = a, to_b
    else:
        start, sweep = b, 2 * math.pi - to_b
    return Elbow(centre, axis, x_dir, major, sorted(minor, reverse=True), start, sweep)


def _half_space(point: np.ndarray, normal: np.ndarray) -> TopoDS_Solid:
    """Solid half space on the side of ``normal``."""
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*point), gp_Dir(*normal))).Face()
    return BRepPrimAPI_MakeHalfSpace(face, gp_Pnt(*(point + normal))).Solid()


def _tube(start: np.ndarray, direction: np.ndarray, length: float, radii: list[float]):
    def cylinder(radius):
        axis = gp_Ax2(gp_Pnt(*start), gp_Dir(*direction))
        return BRepPrimAPI_MakeCylinder(axis, radius, length).Shape()

    shape = cylinder(radii[0])
    if len(radii) > 1:
        shape = BRepAlgoAPI_Cut(shape, cylinder(radii[1])).Shape()
    return shape


def mitred_segments(
    elbow: Elbow, n_segments: int, preserve_volume: bool = False
) -> list[TopoDS_Shape]:
    """Build the mitred bend: two half segments at the ends and n-1 full ones."""
    delta = elbow.sweep / n_segments
    radii = list(elbow.minor_radii)
    if preserve_volume:
        ratio = (delta / 2) / math.tan(delta / 2)  # arc length / polygon length
        radii = [r * math.sqrt(ratio) for r in radii]

    angles = [elbow.start_angle + i * delta for i in range(n_segments + 1)]
    corner_radius = elbow.major_radius / math.cos(delta / 2)
    corners = [elbow.point(a + delta / 2, corner_radius) for a in angles[:-1]]
    vertices = [elbow.point(angles[0]), *corners, elbow.point(angles[-1])]
    # Cutting planes: the elbow ends, and the bisectors at every corner
    cut_normals = [
        elbow.tangent(angles[0]),
        *(elbow.tangent(a + delta / 2) for a in angles[:-1]),
        elbow.tangent(angles[-1]),
    ]

    segments = []
    margin = 2 * radii[0] * math.tan(delta) + radii[0]
    for i in range(len(vertices) - 1):
        start, end = vertices[i], vertices[i + 1]
        direction = (end - start) / np.linalg.norm(end - start)
        length = float(np.linalg.norm(end - start)) + 2 * margin
        tube = _tube(start - margin * direction, direction, length, radii)
        tube = BRepAlgoAPI_Common(tube, _half_space(start, cut_normals[i])).Shape()
        tube = BRepAlgoAPI_Common(tube, _half_space(end, -cut_normals[i + 1])).Shape()
        segments.extend(solids(tube))
    return segments


@dataclass
class ElbowReplacement:
    original_volume_mm3: float
    new_volume_mm3: float
    sweep_degrees: float
    segments: int


def replace_elbows(
    shape: TopoDS_Shape,
    n_segments: int = 4,
    max_segment_angle: float | None = None,
    preserve_volume: bool = False,
) -> tuple[list[TopoDS_Shape], list[ElbowReplacement]]:
    """Replace the plain elbows among the solids of a shape.

    Args:
        n_segments: number of mitre segments per elbow.
        max_segment_angle: if given (degrees), use as many segments as needed so that
            no segment turns more than this, overriding ``n_segments`` when larger.

    Returns:
        The resulting solids and a record of every replacement.
    """
    result: list[TopoDS_Shape] = []
    replacements = []
    for solid in solids(shape):
        elbow = detect_elbow(solid)
        if elbow is None:
            result.append(solid)
            continue
        n = n_segments
        if max_segment_angle:
            n = max(n, math.ceil(math.degrees(elbow.sweep) / max_segment_angle))
        segments = mitred_segments(elbow, n, preserve_volume)
        result.extend(segments)
        replacements.append(
            ElbowReplacement(
                volume_mm3(solid),
                sum(volume_mm3(s) for s in segments),
                math.degrees(elbow.sweep),
                len(segments),
            )
        )
    return result, replacements
