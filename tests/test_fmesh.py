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


@pytest.mark.parametrize(
    "direction",
    [
        ((0, 0, 1), (1, 1, 0)),  # about z only
        ((1, 2, 3), (3, -1, -1 / 3)),  # about no main axis: checked against MCNP
    ],
)
def test_rotated_box_gets_a_transformation(direction):
    placement = gp_Ax2(
        gp_Pnt(100, 200, 300), gp_Dir(*direction[0]), gp_Dir(*direction[1])
    )
    solid = solids(BRepPrimAPI_MakeBox(placement, 100, 50, 20).Shape())[0]
    box = box_from_solid(solid)
    assert not box.axis_aligned
    assert sorted(box.lengths) == pytest.approx([2, 5, 10])
    assert box.axes @ box.axes.T == pytest.approx(np.eye(3), abs=1e-12)
    assert np.linalg.det(box.axes) == pytest.approx(1)
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


def test_nearly_aligned_box_needs_no_transformation():
    # Off the axes by 1e-8 rad: round-off of a STEP export
    placement = gp_Ax2(gp_Pnt(12345.678, 0, 0), gp_Dir(0, 1e-8, 1), gp_Dir(1, 0, 0))
    box = box_from_solid(solids(BRepPrimAPI_MakeBox(placement, 100, 50, 20).Shape())[0])
    assert box.axis_aligned
    card = fmesh_card(box, size=5)
    assert "TR" not in card
    # Nine digits: not rounded to 1234.57. The tilt moves the bounds by 5e-8 cm.
    origin = [float(v) for v in card.split("ORIGIN=")[1].split("\n")[0].split()]
    assert origin == pytest.approx([1234.5678, 0, 0], abs=1e-6)
    assert "ORIGIN=1234.5678 " in card
    assert "IMESH=1244.5678 " in card


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


def test_cli_fmesh_writes_the_cards_of_the_boxes(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from typer.testing import CliRunner

    from radmodeling.cad.step import Part, write_step
    from radmodeling.cli import app

    path, cards = tmp_path / "boxes.step", tmp_path / "boxes_fmesh.txt"
    parts = [
        Part("Mesh", (), BRepPrimAPI_MakeBox(100, 100, 100).Shape()),
        Part("Pipe", (), BRepPrimAPI_MakeCylinder(10, 100).Shape()),
        Part("Fine", (), BRepPrimAPI_MakeBox(10, 20, 30).Shape()),
    ]
    write_step(parts, path)
    args = ["fmesh", str(path), "--output", str(cards), "--size", "5"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "Skipping Pipe" in result.output
    assert "FMESH14 Mesh: 2 x 2 x 2 = 8 voxels" in result.output
    text = cards.read_text()
    assert text.startswith("c Mesh\nFMESH14:n GEOM=XYZ\n")
    assert "c Fine\nFMESH24:n" in text
    assert "Pipe" not in text


def test_cli_fmesh_fails_without_boxes(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from typer.testing import CliRunner

    from radmodeling.cad.step import Part, write_step
    from radmodeling.cli import app

    path, cards = tmp_path / "boxes.step", tmp_path / "boxes_fmesh.txt"
    write_step([Part("Pipe", (), BRepPrimAPI_MakeCylinder(10, 100).Shape())], path)
    args = ["fmesh", str(path), "--output", str(cards), "--size", "5"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "no boxes found" in result.output
    assert not cards.exists()
