import math
import re

import models
import numpy as np
import pytest
from typer.testing import CliRunner

from radmodeling.cli import app
from radmodeling.compare import Plane, Spot, cluster_labels
from radmodeling.registry import Key
from radmodeling.slices import (
    instance_plane,
    load_scene,
    make_slice,
    overview_planes,
    parse_plane_counts,
    plane_shows,
    read_problems,
    spot_plane,
    zoom_slices,
)


def test_overview_planes_are_evenly_spaced_inside_the_box():
    lo, hi = np.array([0.0, 0.0, 0.0]), np.array([10.0, 20.0, 30.0])
    planes = overview_planes(lo, hi, parse_plane_counts("x:1, z:2"))
    assert [title for title, _ in planes] == [
        "x = 5 cm (1 of 1)",
        "z = 10 cm (1 of 2)",
        "z = 20 cm (2 of 2)",
    ]
    _, plane = planes[1]
    assert plane.origin == pytest.approx([5, 10, 10])
    assert plane.normal == pytest.approx([0, 0, 1])
    # The window covers the box with a margin
    assert (plane.width, plane.height) == pytest.approx((11, 22))
    with pytest.raises(ValueError, match="x:3,y:3,z:6"):
        parse_plane_counts("w:3")


def test_instance_plane_shows_the_largest_section():
    # A bolt 1 cm across and 1.5 cm tall along z, at x = 15 cm
    plane = instance_plane([models.cylinder(150, 0, 0, 5, 15)])
    assert plane.origin == pytest.approx([15, 0, 0.75], abs=1e-6)
    assert abs(plane.normal[2]) < 1e-9  # cut along the bolt, not across it
    assert plane.height > 1.5


def test_read_problems_worst_first(tmp_path):
    report = tmp_path / "compare.csv"
    report.write_text(
        "component_id,instance,cells,discrepancy_pct,touches_sampling_box,overlaps,"
        "error\n"
        "1,1,1,0.2,False,,\n"
        "2,1,2,5.0,False,,\n"
        "3,1,3,25.0,False,,\n"
        "4,1,4,0.1,True,,\n"
        "5,1,,nan,False,,\n"
        "6,2,7,nan,False,,unknown surface 99\n"
        "7,1,8,0.05,False,8.1:2.5 7.1:0.1,\n"
    )
    problems = read_problems(report, threshold=1)
    assert [p.key for p in problems] == [
        (5, 1), (6, 2), (3, 1), (2, 1), (4, 1), (7, 1)
    ]  # fmt: skip
    assert problems[0].note == "no tagged cells"
    assert problems[1].note == "unknown surface 99"
    assert problems[4].note == "cells exceed the sampled box"
    assert problems[5].note == "overlaps 8.1, 7.1"
    assert math.isnan(problems[0].discrepancy_pct)
    assert Key(1, 1) not in [p.key for p in problems]


def test_atlas(tmp_path, cad_model):
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(app, [str(a) for a in args])
        assert result.exit_code == 0, result.output
        return result.output

    run("init", cad_model)
    exported = tmp_path / "export.step"
    run("export", cad_model.with_name("machine_simplified.step"), "-o", exported)
    mcnp = tmp_path / "model.mcnp"
    mcnp.write_text(models.mcnp_text(ball_radius=2.6))  # the ball is too big
    report = tmp_path / "compare.csv"
    run(
        "compare", exported, mcnp, "--no-registry", "--points", "5000",
        "--seed", "1", "--report", report,
    )  # fmt: skip

    pdf = tmp_path / "atlas.pdf"
    output = run(
        "atlas", mcnp, "--step", exported, "-o", pdf, "--planes", "z:2",
        "--report", report, "--resolution", "80",
    )  # fmt: skip
    assert "Written 3 slices" in output
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF")
    # One summary page, two overview slices and the ball
    assert len(re.findall(rb"/Type\s*/Page\b", data)) == 4

    result = runner.invoke(
        app, ["atlas", str(mcnp), "--step", str(cad_model), "-o", str(pdf),
              "--report", str(report)],
    )  # fmt: skip
    assert result.exit_code == 1
    assert "needs the STEP file written by 'export'" in result.output


def test_cluster_labels():
    points = np.array([[0, 0], [1, 0], [2, 1], [10, 10], [11, 10], [30, 0]], float)
    labels = cluster_labels(points, 1.0)
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4]
    assert len(set(labels.tolist())) == 3


def test_spot_planes():
    spot = Spot("overlap", Key(2), np.array([1.0, 2.0, 3.0]), np.array([0.0, 0, 2]),
                np.array([2.0, 4, 3.5]), 1.0)  # fmt: skip
    plane = spot_plane(spot, context=1.0)
    # Normal to the thinnest side of the spot, through the spot point
    assert plane.normal == pytest.approx([0, 0, 1])
    assert plane.origin == pytest.approx([1, 2, 3])
    assert (plane.width, plane.height) == pytest.approx((4, 6))
    assert plane_shows(plane, spot)
    elsewhere = Plane.from_basis([1, 2, 10], "z", 4, 6)
    assert not plane_shows(elsewhere, spot)


@pytest.fixture
def clash(tmp_path):
    """Exported model where the cells of the first bolt overlap the block's."""
    runner = CliRunner()
    step = models.write_clash_cad(tmp_path / "machine.step")
    exported = tmp_path / "export.step"
    for args in (
        ["init", step],
        ["export", step.with_name("machine_simplified.step"), "-o", exported],
    ):
        result = runner.invoke(app, [str(a) for a in args])
        assert result.exit_code == 0, result.output
    mcnp = tmp_path / "model.mcnp"
    mcnp.write_text(models.clash_mcnp_text())
    return exported, mcnp


def test_small_problems_are_circled_and_zoomed(clash):
    exported, mcnp = clash
    scene = load_scene(mcnp, exported)
    # Across the whole model, the bolt (1 cm across) is a few pixels
    plane = Plane.from_basis([12, 5, 1], "z", 30, 15)
    item = make_slice(scene, plane, 100)
    assert item.result.overlap_pixels
    zoom_slices(scene, item, 100)
    assert [m.label for m in item.markers] == ["A"]
    marker = item.markers[0]
    assert (marker.u, marker.v) == pytest.approx((-2.5, 0), abs=0.5)
    (zoom,) = item.zooms
    assert zoom.result.plane.width < plane.width / 4
    assert zoom.result.overlap_pixels > 10 * item.result.overlap_pixels
    assert zoom.title == "A: overlap of cells 1 [1.1], 4 [4.1]"


def test_tessellation_is_not_a_mismatch(clash):
    exported, mcnp = clash
    scene = load_scene(mcnp, exported)
    # Through the middle of the ball, exact in MCNP and tessellated in the CAD
    item = make_slice(scene, Plane.from_basis([5, 5, 10], "z", 6, 6), 300)
    assert item.result.mismatch_pixels == 0
    bigger = models.clash_mcnp_text().replace("11 s 5 5 10 2.5", "11 s 5 5 10 2.6")
    mcnp.write_text(bigger)
    item = make_slice(load_scene(mcnp, exported), item.result.plane, 300)
    assert (item.result.mismatch_pixels or 0) > 100  # a 1 mm ring around the ball


def test_atlas_finds_problems_without_a_report(clash, tmp_path):
    exported, mcnp = clash
    runner = CliRunner()
    pdf = tmp_path / "atlas.pdf"
    args = ["atlas", mcnp, "--step", exported, "-o", pdf, "--planes", "z:1",
            "--points", "20000", "--seed", "1", "--resolution", "100"]  # fmt: skip
    result = runner.invoke(app, [str(a) for a in [*args, "--jobs", "2"]])
    assert result.exit_code == 0, result.output
    assert "Instances with problems: 1" in result.output
    data = pdf.read_bytes()
    # Summary, the overview slice and a slice through the overlap of the bolt
    assert len(re.findall(rb"/Type\s*/Page\b", data)) >= 3

    assert "Using" not in result.output  # no report around: every instance searched

    # The report of 'compare' on model.mcnp is used for model_materials.mcnp too
    compare = ["compare", exported, mcnp, "--no-registry", "--points", "20000"]
    result = runner.invoke(app, [str(a) for a in compare])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "model_compare.csv").exists()
    materials = tmp_path / "model_materials.mcnp"
    materials.write_text(mcnp.read_text())
    args[1] = materials
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    output = result.output.replace("\n", "")
    assert "model_compare.csv: only the instances it reports" in output
    assert "Instances with problems: 1" in output
    result = runner.invoke(app, [str(a) for a in [*args, "--no-report"]])
    assert "Using" not in result.output


def test_find_report(tmp_path):
    from radmodeling.cli import _find_report

    for name in ("model_compare.csv", "model_v2_compare.csv", "other_compare.csv"):
        (tmp_path / name).write_text("")
    assert _find_report(tmp_path / "model.mcnp") == tmp_path / "model_compare.csv"
    found = _find_report(tmp_path / "model_materials.mcnp")
    assert found == tmp_path / "model_compare.csv"
    found = _find_report(tmp_path / "model_v2_materials.mcnp")
    assert found == tmp_path / "model_v2_compare.csv"
    assert _find_report(tmp_path / "modelx.mcnp") is None
