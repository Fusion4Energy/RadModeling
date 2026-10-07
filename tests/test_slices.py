import re

import models
import numpy as np
import pytest
from typer.testing import CliRunner

from radmodeling.cli import app
from radmodeling.compare import (
    Plane,
    Spot,
    cluster_labels,
)
from radmodeling.plot import slice_figure
from radmodeling.registry import Key
from radmodeling.slices import (
    Atlas,
    instance_plane,
    load_scene,
    make_slice,
    overview_planes,
    parse_plane_counts,
    plane_shows,
    problem_pages,
    scene_comparison,
    spot_plane,
    thinnest_axis,
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
    pdf = tmp_path / "atlas.pdf"
    output = run(
        "atlas", mcnp, exported, "-o", pdf, "--planes", "z:2",
        "--points", "5000", "--resolution", "80", "--seed", "1",
    )  # fmt: skip
    assert "Written 3 slices" in output
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF")
    # One summary page, two overview slices and the ball
    assert len(re.findall(rb"/Type\s*/Page\b", data)) == 4

    result = runner.invoke(
        app, ["atlas", str(mcnp), str(cad_model), "-o", str(pdf),
              "--components", "3"],
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
    spot = Spot("overlap", Key(2), np.array([0.0, 0, 2]), np.array([2.0, 4, 3.5]), 1.0)
    plane = spot_plane(spot, context=1.0)
    # Normal to the thinnest side of the spot, through its middle
    assert plane.normal == pytest.approx([0, 0, 1])
    assert plane.origin == pytest.approx([1, 2, 2.75])
    assert (plane.width, plane.height) == pytest.approx((4, 6))
    assert plane_shows(plane, spot)
    elsewhere = Plane.from_basis([1, 2, 10], "z", 4, 6)
    assert not plane_shows(elsewhere, spot)


def test_thinnest_axis_breaks_near_ties_in_a_fixed_order():
    assert thinnest_axis(np.array([1.0, 4, 3])) == "x"
    # A round cluster: noise must not decide the axis
    assert thinnest_axis(np.array([5.16, 5.14, 5.17])) == "z"
    assert thinnest_axis(np.array([5.0, 5.1, 9])) == "y"
    assert thinnest_axis(np.array([0.0, 0, 2])) == "y"


def test_mismatch_shell_is_sliced_through_its_middle(tmp_path, cad_model):
    """A part slightly too large everywhere has a hollow shell of problem points:
    its slice must cut the middle of the part for any seed, not graze it."""
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(cad_model)]).exit_code == 0
    exported = tmp_path / "export.step"
    simplified = cad_model.with_name("machine_simplified.step")
    result = runner.invoke(app, ["export", str(simplified), "-o", str(exported)])
    assert result.exit_code == 0, result.output
    mcnp = tmp_path / "model.mcnp"
    mcnp.write_text(models.mcnp_text(ball_radius=2.6))  # ball: centre (5, 5, 10)
    scene = load_scene(mcnp, exported)
    for seed in range(6):
        work = Atlas(scene, scene_comparison(scene, [Key(3)], 20_000, seed), 80)
        pages, n_problems = problem_pages(scene, [work.search(Key(3))], 1.0, 20)
        assert n_problems == 1
        assert len(pages) == 1
        plane = pages[0][1]
        assert plane.origin == pytest.approx([5, 5, 10], abs=0.2)
        assert plane.normal == pytest.approx([0, 0, 1])
        assert not work.draw(pages[0]).zooms  # the whole ring is visible


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


def test_void_components_are_told_from_filler_void(clash):
    exported, mcnp = clash
    scene = load_scene(mcnp, exported)
    # Across the block (cell 1, tagged) and the void around it (cell 6, untagged)
    item = make_slice(scene, Plane.from_basis([5, 5, 1], "z", 20, 20), 100)
    result = item.result
    assert result.tagged is not None
    assert result.tagged[result.cells == 1].all()
    assert not result.tagged[result.cells == 6].any()
    fig = slice_figure(result, scene.slots)
    legend = fig.axes[0].get_legend()
    assert legend is not None
    labels = [t.get_text() for t in legend.get_texts()]
    assert {"Void component", "Void"} <= set(labels)


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


def test_tessellation_of_large_parts_is_not_a_mismatch():
    import migjorn

    from radmodeling.cad.occ import solids
    from radmodeling.mcnp.geometry import Geometry
    from radmodeling.slices import Scene
    from radmodeling.workflow import ExportedInstance

    # A 1 m radius disc: its mesh is up to about 0.28 mm inside the exact cylinder,
    # more than the fixed tolerance of small parts
    disc = solids(models.cylinder(0, 0, 0, 1000, 100))

    def mismatch_pixels(radius_cm: float) -> int:
        text = f"""disc
1 0 -1 2 -3 imp:n=1 $ [rm1.1] Disc

1 cz {radius_cm}
2 pz 0
3 pz 10

"""
        geometry = Geometry(migjorn.parse(text))
        instances = {Key(1): ExportedInstance("Disc", disc)}
        scene = Scene(geometry, [*disc], instances, {Key(1): [1]}, {}, {})
        # A thin window along the rim, with pixels much smaller than the deflection
        rim = Plane(
            np.array([100.0, 0, 5]), np.array([0.0, 1, 0]), np.eye(3)[0], 4, 0.1
        )
        return make_slice(scene, rim, 2000).result.mismatch_pixels or 0

    assert mismatch_pixels(100) == 0
    assert mismatch_pixels(100.1) > 1000  # a real 1 mm error is still a mismatch


def test_atlas_finds_overlaps(clash, tmp_path):
    exported, mcnp = clash
    runner = CliRunner()
    pdf = tmp_path / "atlas.pdf"
    args = ["atlas", mcnp, exported, "-o", pdf, "--planes", "z:1",
            "--points", "20000", "--seed", "1", "--resolution", "100"]  # fmt: skip
    result = runner.invoke(app, [str(a) for a in [*args, "--jobs", "2"]])
    assert result.exit_code == 0, result.output
    assert "Instances with problems: 1" in result.output
    data = pdf.read_bytes()
    # Summary, the overview slice and a slice through the overlap of the bolt
    assert len(re.findall(rb"/Type\s*/Page\b", data)) >= 3

    # --components limits the search. The overlap is measured from the block when the
    # bolt is not searched, and the other components have no problem
    for selection, problems in (("1", 1), ("2,3", 0)):
        command = [*args, "--components", selection]
        result = runner.invoke(app, [str(a) for a in command])
        assert result.exit_code == 0, result.output
        assert f"Instances with problems: {problems}" in result.output
