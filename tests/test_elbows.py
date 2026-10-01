import math

import numpy as np
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeTorus
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from radmodeling.cad.analysis import analyse
from radmodeling.cad.elbows import detect_elbow, replace_elbows
from radmodeling.cad.mesh import LineFrame, inside_on_lines, triangulate
from radmodeling.cad.occ import bounding_box, solids


def elbow(angle_degrees=90.0, hollow=True):
    axis = gp_Ax2(gp_Pnt(10, 20, 30), gp_Dir(0, 1, 1), gp_Dir(1, 0, 0))
    outer = BRepPrimAPI_MakeTorus(axis, 100, 20, math.radians(angle_degrees)).Shape()
    if not hollow:
        return outer
    inner = BRepPrimAPI_MakeTorus(axis, 100, 15, math.radians(angle_degrees)).Shape()
    return BRepAlgoAPI_Cut(outer, inner).Shape()


@pytest.mark.parametrize("angle", [45.0, 90.0, 200.0])
@pytest.mark.parametrize("hollow", [False, True])
def test_detect_elbow(angle, hollow):
    found = detect_elbow(solids(elbow(angle, hollow))[0])
    assert found is not None
    assert math.degrees(found.sweep) == pytest.approx(angle)
    assert found.minor_radii == ([20, 15] if hollow else [20])


def test_full_torus_is_not_an_elbow():
    assert detect_elbow(solids(BRepPrimAPI_MakeTorus(100, 20).Shape())[0]) is None


def sample(shapes, lo, hi, rng):
    """Inside masks of every shape on common random points (independent of OCCT
    Booleans, which are unreliable between nearly tangent tori and cylinders)."""
    lines, per_line = 50_000, 10
    uv = lo[:2] + rng.random((lines, 2)) * (hi - lo)[:2]
    t = lo[2] + rng.random((lines, per_line)) * (hi - lo)[2]
    frame = LineFrame(np.zeros(3), np.eye(3))
    return [inside_on_lines(triangulate(s), frame, uv, t) for s in shapes]


@pytest.mark.parametrize("preserve", [False, True])
def test_volume(preserve):
    segments, [record] = replace_elbows(elbow(), 4, preserve_volume=preserve)
    assert len(segments) == 5
    assert all(analyse(s).valid and not analyse(s).has_torus for s in segments)
    half = math.radians(90 / 4 / 2)
    growth = 1.0 if preserve else math.tan(half) / half
    assert record.new_volume_mm3 == pytest.approx(
        record.original_volume_mm3 * growth, rel=1e-6
    )


@pytest.mark.parametrize(
    ("hollow", "n", "coverage"), [(False, 8, 0.99), (True, 8, 0.97)]
)
def test_segments_follow_the_elbow_without_overlapping(hollow, n, coverage):
    original = elbow(hollow=hollow)
    segments, _ = replace_elbows(original, n)
    box = np.array(bounding_box(original))
    masks = sample(
        [original, *segments], box[:3] - 5, box[3:] + 5, np.random.default_rng(0)
    )
    inside_original, segment_masks = masks[0], masks[1:]
    count = np.sum(segment_masks, axis=0)
    assert count.max() == 1
    assert (inside_original & (count > 0)).sum() / inside_original.sum() > coverage


def test_ends_match_the_original_end_caps():
    original = elbow(hollow=False)
    segments, _ = replace_elbows(original, 3)

    # The first and last segments end exactly on the original end caps
    def caps(shape):
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepGProp import BRepGProp
        from OCP.GeomAbs import GeomAbs_SurfaceType as ST
        from OCP.GProp import GProp_GProps

        from radmodeling.cad.occ import faces

        centres = []
        for face in faces(shape):
            if BRepAdaptor_Surface(face).GetType() == ST.GeomAbs_Plane:
                props = GProp_GProps()
                BRepGProp.SurfaceProperties_s(face, props)
                centres.append((props.Mass(), props.CentreOfMass().Coord()))
        return centres

    original_caps = sorted(caps(original), key=lambda c: c[1])
    new_caps = [c for s in (segments[0], segments[-1]) for c in caps(s)]
    for area, centre in original_caps:
        match = [a for a, c in new_caps if np.allclose(c, centre, atol=1e-6)]
        assert match == [pytest.approx(area, rel=1e-6)]


def test_cli_elbows_keeps_tags(tmp_path):
    from typer.testing import CliRunner

    from radmodeling.cad.step import Part, read_step, write_step
    from radmodeling.cli import app

    source, target = tmp_path / "in.step", tmp_path / "out.step"
    write_step([Part("Elbow [rm7]", ("Pipes",), elbow())], source)
    result = CliRunner().invoke(app, ["elbows", str(source), "-o", str(target)])
    assert result.exit_code == 0, result.output
    assert "Elbow [rm7]: 90 deg elbow -> 5 solids" in result.output
    [part] = read_step(target)
    assert part.name == "Elbow [rm7]"
    assert len(part.solids) == 5
