import numpy as np
import pytest
from OCP.BRep import BRep_Tool
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopAbs import TopAbs_VERTEX
from OCP.TopoDS import TopoDS

from radmodeling.cad.occ import explore, faces, solids
from radmodeling.fmesh import box_from_solid, comment_line, fmesh_card
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


def _prism(*points, height=30.0):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    polygon = BRepBuilderAPI_MakePolygon()
    for x, y in points:
        polygon.Add(gp_Pnt(x, y, 0))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    return solids(BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, height)).Shape())[0]


def test_rejects_other_shapes():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    with pytest.raises(ValueError, match="not a box"):
        box_from_solid(solids(BRepPrimAPI_MakeCylinder(1, 1).Shape())[0])
    # Sheared box: two pairs of faces are orthogonal, the third is not
    with pytest.raises(ValueError, match="not orthogonal"):
        box_from_solid(_prism((0, 0), (100, 0), (150, 100), (50, 100)))
    # L-shape: faces in three orthogonal directions, but not a box
    with pytest.raises(ValueError, match="cm3"):
        box_from_solid(
            _prism((0, 0), (100, 0), (100, 50), (50, 50), (50, 100), (0, 100))
        )


def test_split_faces_are_still_a_box():
    # The bottom edge has an extra vertex: the side face is split in two
    solid = _prism((0, 0), (50, 0), (100, 0), (100, 100), (0, 100))
    assert len(faces(solid)) > 6
    assert box_from_solid(solid).lengths == pytest.approx([10, 10, 3])


def test_transform_numbers_are_limited():
    placement = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 1, 0))
    box = box_from_solid(solids(BRepPrimAPI_MakeBox(placement, 10, 10, 10).Shape())[0])
    with pytest.raises(ValueError, match="TR numbers"):
        fmesh_card(box, size=1, transform=1000)


def test_comment_line():
    assert comment_line("Cámara  de\tvacío") == "c Camara de vacio"
    assert len(comment_line("x" * 200)) == 80


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


def test_cli_fmesh_messages_stay_out_of_the_cards(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from typer.testing import CliRunner

    from radmodeling.cad.step import Part, write_step
    from radmodeling.cli import app

    path = tmp_path / "boxes.step"
    parts = [
        Part("Mesh", (), BRepPrimAPI_MakeBox(100, 100, 100).Shape()),
        Part("Pipe", (), BRepPrimAPI_MakeCylinder(10, 100).Shape()),
    ]
    write_step(parts, path)
    result = CliRunner().invoke(app, ["fmesh", str(path), "--size", "5"])
    assert result.exit_code == 0, result.output
    assert "Skipping Pipe" in result.stderr
    assert "Pipe" not in result.stdout
    assert result.stdout.startswith("c Mesh\nFMESH14:n GEOM=XYZ\n")
