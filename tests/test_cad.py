import math

import models
import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeTorus

from radmodeling.cad.analysis import analyse
from radmodeling.cad.mesh import LineFrame, inside_on_lines, triangulate
from radmodeling.cad.occ import bounding_box, volume_mm3
from radmodeling.cad.section import section_polylines
from radmodeling.cad.step import Part, read_step, write_step


def test_step_round_trip_keeps_names_hierarchy_and_order(tmp_path):
    path = tmp_path / "a.step"
    parts = [
        Part("Ñandú [rm:3]", ("Raíz", "Sub"), models.block()),
        Part("Pipe", ("Raíz",), models.pipe()),
        Part("Bolts", ("Raíz", "Sub"), models.bolts()),
    ]
    write_step(parts, path)
    assert path.read_bytes().isascii()

    read = read_step(path)
    # Parts come grouped by assembly, in the order the assemblies were created
    assert [(p.path, p.name) for p in read] == [
        (("Raíz", "Sub"), "Ñandú [rm:3]"),
        (("Raíz", "Sub"), "Bolts"),
        (("Raíz",), "Pipe"),
    ]
    volumes = {p.name: volume_mm3(p.shape) for p in read}
    assert volumes["Pipe"] == pytest.approx(math.pi * (30**2 - 20**2) * 100)
    assert len(next(p for p in read if p.name == "Bolts").solids) == 2


def test_analysis_reports_tori():
    report = analyse(BRepPrimAPI_MakeTorus(50, 10).Shape())
    assert report.has_torus
    assert report.valid
    assert report.unconvertible_surfaces == []
    assert report.volume_cm3 == pytest.approx(2 * math.pi**2 * 50 * 10**2 / 1000)


@pytest.mark.parametrize(
    "shape",
    [
        BRepPrimAPI_MakeTorus(50, 10).Shape(),
        models.block(),
        models.pipe(),
    ],
)
def test_point_classification_matches_mesh_volume(shape):
    mesh = triangulate(shape)
    assert mesh.volume == pytest.approx(volume_mm3(shape), rel=2e-3)

    rng = np.random.default_rng(0)
    box = np.array(bounding_box(shape))
    lo, hi = box[:3], box[3:]
    lines = 60_000
    uv = lo[:2] + rng.random((lines, 2)) * (hi - lo)[:2]
    t = lo[2] + rng.random((lines, 3)) * (hi - lo)[2]
    inside = inside_on_lines(mesh, LineFrame(np.zeros(3), np.eye(3)), uv, t)
    p = inside.mean()
    sigma = np.sqrt(p * (1 - p) / lines)
    assert p == pytest.approx(mesh.volume / np.prod(hi - lo), abs=4 * sigma)


def test_classification_in_rotated_frame_is_exact_for_a_box():
    mesh = triangulate(BRepPrimAPI_MakeBox(10, 20, 30).Shape())
    axes = np.array([[0.0, 1, 0], [0, 0, 1], [1, 0, 0]])  # lines along x
    frame = LineFrame(np.zeros(3), axes)
    uv = np.array([[5.0, 15.0], [25.0, 15.0]])  # (y, z) of the lines
    t = np.array([[-1.0, 0.5, 9.5, 11.0], [5.0, 5.0, 5.0, 5.0]])
    inside = inside_on_lines(mesh, frame, uv, t)
    assert inside.tolist() == [[False, True, True, False], [False] * 4]


def test_section_of_block():
    lines = section_polylines(models.block(), np.array([0, 0, 25]), np.array([0, 0, 1]))
    points = np.vstack(lines)
    assert np.allclose(points[:, 2], 25)
    radius = np.hypot(points[:, 0] - 50, points[:, 1] - 50)
    assert radius.min() == pytest.approx(20, abs=0.1)
