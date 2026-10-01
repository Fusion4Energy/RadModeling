import numpy as np
import pytest
from OCP.BRep import BRep_Tool
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopAbs import TopAbs_VERTEX
from OCP.TopoDS import TopoDS

from radmodeling.cad.occ import explore, solids
from radmodeling.fmesh import box_from_solid, fmesh_card
from radmodeling.mcnp.geometry import Transform

to_vertex = getattr(TopoDS, "Vertex_s", None) or TopoDS.Vertex


def test_axis_aligned_box():
    solid = solids(BRepPrimAPI_MakeBox(gp_Pnt(-100, 0, 50), 200, 100, 30).Shape())[0]
    box = box_from_solid(solid)
    assert box.axis_aligned
    card = fmesh_card(box, tally=14, size=4)
    assert card == (
        "FMESH14:n GEOM=XYZ\n"
        "     ORIGIN=-10 0 5\n"
        "     IMESH=10 IINTS=5\n"
        "     JMESH=10 JINTS=3\n"
        "     KMESH=8 KINTS=1\n"
    )


def test_rotated_box_gets_a_transformation():
    placement = gp_Ax2(gp_Pnt(10, 20, 30), gp_Dir(0, 0, 1), gp_Dir(1, 1, 0))
    solid = solids(BRepPrimAPI_MakeBox(placement, 100, 50, 20).Shape())[0]
    box = box_from_solid(solid)
    assert not box.axis_aligned
    assert sorted(box.lengths) == pytest.approx([2, 5, 10])
    card = fmesh_card(box, bins=(10, 5, 2), transform=7)
    assert "TR=7" in card
    tr_line = card[card.index("TR7") :].split()[1:]
    transform = Transform.from_values([float(v) for v in tr_line], degrees=False)
    # Every vertex of the box lies inside the mesh, and the extremes are reached
    vertices = np.array(
        [BRep_Tool.Pnt_s(to_vertex(v)).Coord() for v in explore(solid, TopAbs_VERTEX)]
    )
    local = transform.to_auxiliary(vertices / 10)
    assert local.min(axis=0) == pytest.approx([0, 0, 0], abs=1e-6)
    assert local.max(axis=0) == pytest.approx(box.lengths, abs=1e-6)


def test_rejects_other_shapes():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    with pytest.raises(ValueError, match="not a box"):
        box_from_solid(solids(BRepPrimAPI_MakeCylinder(1, 1).Shape())[0])


def test_cli_fmesh(tmp_path):
    from typer.testing import CliRunner

    from radmodeling.cad.step import Part, write_step
    from radmodeling.cli import app

    path = tmp_path / "boxes.step"
    write_step([Part("Mesh", (), BRepPrimAPI_MakeBox(100, 100, 100).Shape())], path)
    result = CliRunner().invoke(app, ["fmesh", str(path), "--bins", "2,2,2"])
    assert result.exit_code == 0, result.output
    assert "FMESH14:n GEOM=XYZ" in result.output
    assert "IMESH=10 IINTS=2" in result.output
