"""The mitred bend geometry of the 'Replace elbows' SpaceClaim tool.

The SpaceClaim script cannot run here, so the tests repeat its construction with OCP:
find the cross sections of the bend, remove it with a wedge and fuse the mitred
segments. The expected volume change is exact, which the script also checks.
"""

import math

import numpy as np
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeTorus,
)
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_CurveType as CT
from OCP.GeomAbs import GeomAbs_SurfaceType as ST
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.TopAbs import TopAbs_EDGE

from radmodeling.cad.analysis import analyse
from radmodeling.cad.occ import explore, faces, solids, to_edge, to_face, volume_mm3
from radmodeling.spaceclaim._mitre import (
    rm_bend_cutter,
    rm_bend_ends,
    rm_bend_volumes,
    rm_cross_section_angle,
    rm_mitre_segments,
    rm_pipe_area,
    rm_segment_count,
)

CENTRE = np.array([10.0, 20.0, 30.0])
AXIS = np.array([0.0, 1.0, 1.0]) / math.sqrt(2)
X_DIR = np.array([1.0, 0.0, 0.0])
Y_DIR = np.cross(AXIS, X_DIR)
MAJOR, OUTER, INNER, LEG = 100.0, 20.0, 15.0, 150.0


def pnt(v):
    return gp_Pnt(*map(float, v))


def direction(v):
    return gp_Dir(*map(float, v))


def at(angle, radius=MAJOR):
    return CENTRE + radius * (math.cos(angle) * X_DIR + math.sin(angle) * Y_DIR)


def tangent(angle):
    return -math.sin(angle) * X_DIR + math.cos(angle) * Y_DIR


def pipe(sweep_degrees=90.0, hollow=True, legs=True):
    """A bend from angle 0 with a straight leg before and after it (or none)."""
    sweep = math.radians(sweep_degrees)

    def solid(radius, overshoot):
        axes = gp_Ax2(pnt(CENTRE), direction(AXIS), direction(X_DIR))
        shape = BRepPrimAPI_MakeTorus(axes, MAJOR, radius, sweep).Shape()
        if legs:
            length = LEG + overshoot
            for start, d in (
                (at(0) - length * tangent(0), tangent(0)),
                (at(sweep), tangent(sweep)),
            ):
                axes = gp_Ax2(pnt(start), direction(d))
                cylinder = BRepPrimAPI_MakeCylinder(axes, radius, length).Shape()
                shape = BRepAlgoAPI_Fuse(shape, cylinder).Shape()
        return shape

    shape = solid(OUTER, 0.0)
    if hollow:
        shape = BRepAlgoAPI_Cut(shape, solid(INNER, 1.0)).Shape()
    return solids(shape)[0]


def find_bend(shape):
    """Start and sweep of the bend, as the script finds them."""
    radii, angles, inside = [OUTER, INNER], [], []

    def local(p):
        d = np.array(p.Coord()) - CENTRE
        return d @ X_DIR, d @ Y_DIR, d @ AXIS

    for face in faces(shape):
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() != ST.GeomAbs_Torus:
            continue
        for edge in explore(face, TopAbs_EDGE):
            curve = BRepAdaptor_Curve(to_edge(edge))
            if curve.GetType() == CT.GeomAbs_Circle:
                circle = curve.Circle()
                angle = rm_cross_section_angle(
                    local(circle.Location()), circle.Radius(), MAJOR, radii, 1e-6
                )
                if angle is not None:
                    angles.append(angle)
        u0, u1, v0, v1 = BRepTools.UVBounds_s(to_face(face))
        x, y, _ = local(surface.Value((u0 + u1) / 2, (v0 + v1) / 2))
        inside.append(math.atan2(y, x))
    return rm_bend_ends(angles, inside)


def prism(polygon, half_height):
    maker = BRepBuilderAPI_MakePolygon()
    for x, y in polygon:
        maker.Add(pnt(CENTRE + x * X_DIR + y * Y_DIR - half_height * AXIS))
    maker.Close()
    face = BRepBuilderAPI_MakeFace(maker.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(*(2 * half_height * AXIS))).Shape()


def tube(piece, radii):
    (x, y), (dx, dy) = piece["start"], piece["direction"]
    axes = gp_Ax2(
        pnt(CENTRE + x * X_DIR + y * Y_DIR), direction(dx * X_DIR + dy * Y_DIR)
    )
    shape = BRepPrimAPI_MakeCylinder(axes, radii[0], piece["length"]).Shape()
    if len(radii) > 1:
        hole = BRepPrimAPI_MakeCylinder(axes, radii[1], piece["length"]).Shape()
        shape = BRepAlgoAPI_Cut(shape, hole).Shape()
    return shape


def replace_bend(shape, radii, count):
    """The construction of rm_replace in replace_elbows.py."""
    start, sweep = find_bend(shape)
    rest = BRepAlgoAPI_Cut(
        shape, prism(*rm_bend_cutter(start, sweep, MAJOR, radii[0]))
    ).Shape()
    pieces = solids(rest)
    for piece in rm_mitre_segments(start, sweep, MAJOR, radii[0], count):
        trimmed = BRepAlgoAPI_Common(
            tube(piece, radii), prism(piece["wedge"], piece["half_height"])
        ).Shape()
        # The volume the script reports for each piece when the check fails
        assert volume_mm3(trimmed) == pytest.approx(
            rm_pipe_area(radii) * piece["side"], rel=1e-6
        )
        pieces.extend(solids(trimmed))
    result = pieces[0]
    for piece in pieces[1:]:
        result = BRepAlgoAPI_Fuse(result, piece).Shape()
    return result


@pytest.mark.parametrize(
    ("sweep", "hollow", "legs", "count"),
    [
        (90.0, True, True, 4),
        (45.0, False, True, 2),
        (200.0, True, True, 9),
        (90.0, True, False, 4),  # a bare elbow: nothing left besides the segments
    ],
)
def test_replaced_bend_is_merged_with_the_expected_volume(sweep, hollow, legs, count):
    radii = [OUTER, INNER] if hollow else [OUTER]
    original = pipe(sweep, hollow, legs)
    result = replace_bend(original, radii, count)

    [solid] = solids(result)  # merged into a single body
    assert analyse(solid).valid and not analyse(solid).has_torus
    bend, mitred = rm_bend_volumes(math.radians(sweep), MAJOR, radii, count)
    assert volume_mm3(solid) - volume_mm3(original) == pytest.approx(
        mitred - bend, abs=1e-6 * bend
    )


def test_bend_found_in_pipe():
    start, sweep = find_bend(pipe(120.0))
    assert start == pytest.approx(0.0, abs=1e-9) or start == pytest.approx(2 * math.pi)
    assert math.degrees(sweep) == pytest.approx(120.0)


def test_bend_ends_skip_seams_and_wrap_around():
    seams = [math.radians(a) for a in (350, 10, 30, 30, 50)]  # 30 on both tori
    start, sweep = rm_bend_ends(seams)
    assert math.degrees(start) == pytest.approx(350)
    assert math.degrees(sweep) == pytest.approx(60)


def test_bend_ends_beyond_half_a_turn_need_a_point_inside():
    ends = [0.0, math.radians(270)]
    start, sweep = rm_bend_ends(ends)
    assert (math.degrees(start), math.degrees(sweep)) == pytest.approx((270, 90))
    start, sweep = rm_bend_ends(ends, inside=[math.radians(100)])
    assert (math.degrees(start), math.degrees(sweep)) == pytest.approx((0, 270))


@pytest.mark.parametrize("angles", [[], [1.0], [1.0, 1.0 + 1e-9]])
def test_bend_ends_need_two_ends(angles):
    with pytest.raises(ValueError):
        rm_bend_ends(angles)


def test_cross_sections_are_minor_circles_on_the_centre_line():
    radii = [OUTER, INNER]
    assert rm_cross_section_angle((0, MAJOR, 0), INNER, MAJOR, radii, 1e-6) == (
        pytest.approx(math.pi / 2)
    )
    assert rm_cross_section_angle((0, 0, 0), MAJOR + OUTER, MAJOR, radii, 1e-6) is None
    assert rm_cross_section_angle((0, MAJOR, 1), OUTER, MAJOR, radii, 1e-6) is None


def test_segment_count_and_volume_growth():
    assert rm_segment_count(math.radians(90), 22.5, 2) == 4
    assert rm_segment_count(math.radians(10), 22.5, 2) == 2
    bend, mitred = rm_bend_volumes(math.radians(90), MAJOR, [OUTER], 4)
    assert mitred / bend == pytest.approx(1.0130, abs=1e-4)
