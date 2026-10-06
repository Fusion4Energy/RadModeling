import math
import re

import models
import numpy as np
import pytest
from typer.testing import CliRunner

from radmodeling.cli import app
from radmodeling.registry import Key
from radmodeling.slices import (
    instance_plane,
    overview_planes,
    parse_plane_counts,
    read_problems,
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
