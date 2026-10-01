"""A small CAD assembly and the MCNP model equivalent to it, for tests."""

from __future__ import annotations

from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakeSphere,
)
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFDoc import XCAFDoc_DocumentTool

from radmodeling.cad.step import Part, compound, write_document, write_step


def cylinder(x: float, y: float, z: float, radius: float, height: float):
    axis = gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1))
    return BRepPrimAPI_MakeCylinder(axis, radius, height).Shape()


def block():
    """100 x 100 x 50 mm block with a 20 mm radius hole through its centre."""
    box = BRepPrimAPI_MakeBox(100, 100, 50).Shape()
    return BRepAlgoAPI_Cut(box, cylinder(50, 50, 0, 20, 50)).Shape()


def pipe():
    """Pipe at (200, 50), radii 30 and 20 mm, 100 mm long."""
    return BRepAlgoAPI_Cut(
        cylinder(200, 50, 0, 30, 100), cylinder(200, 50, 0, 20, 100)
    ).Shape()


def ball():
    return BRepPrimAPI_MakeSphere(gp_Pnt(50, 50, 100), 25).Shape()


def bolts():
    """Two solids in one part."""
    return compound([cylinder(150, 0, 0, 5, 20), cylinder(150, 100, 0, 5, 20)])


def write_cad(path: Path) -> Path:
    parts = [
        Part("Block", ("Machine",), block()),
        Part("Pipe", ("Machine", "Piping"), pipe()),
        Part("Ball", ("Machine",), ball()),
        Part("Bolts", ("Machine", "Fasteners"), bolts()),
    ]
    write_step(parts, path)
    return path


def translation(x: float, y: float, z: float = 0.0) -> TopLoc_Location:
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(x, y, z))
    return TopLoc_Location(move)


def placed(shape, x: float, y: float, z: float = 0.0):
    """The shape moved by (x, y, z): an instance sharing its geometry."""
    return shape.Moved(translation(x, y, z))


BOLT_X = (20, 50, 80)


def write_instanced_cad(path: Path) -> Path:
    """A plate and six instances of one bolt, three in each of two subassemblies."""
    bolt = cylinder(0, 0, 0, 5, 20)
    parts = [Part("Plate", ("Machine",), BRepPrimAPI_MakeBox(100, 300, 10).Shape())]
    for side, y in (("Left", 0), ("Right", 200)):
        parts += [Part("Bolt", ("Machine", side), placed(bolt, x, y)) for x in BOLT_X]
    write_step(parts, path)
    return path


def write_instanced_assembly(path: Path) -> Path:
    """A tagged two-body screw assembly placed three times along x, 100 mm apart.

    This is how SpaceClaim writes a component with several bodies.
    """
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def name(label, text):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(text, True))

    screw = tool.NewShape()
    name(screw, "Screw [rm:3]")
    bodies = (("Head", cylinder(0, 0, 20, 8, 5)), ("Shank", cylinder(0, 0, 0, 4, 20)))
    for body, shape in bodies:
        label = tool.AddShape(shape, False, False)
        name(label, body)
        name(tool.AddComponent(screw, label, TopLoc_Location()), body)
    root = tool.NewShape()
    name(root, "Machine")
    for x in (0, 100, 200):
        instance = tool.AddComponent(root, screw, translation(x, 0))
        name(instance, "Screw [rm:3]")
    tool.UpdateAssemblies()
    write_document(doc, path)
    return path


def mcnp_text(ball_radius: float = 2.5) -> str:
    """MCNP model (cm) with the cells in the order 'export' writes the solids."""
    return f"""Test model
1 0  1 -2 3 -4 5 -6 7     imp:n=1
2 0  -8 9 5 -10           imp:n=1
3 0  -11                  imp:n=1
4 0  -12 5 -13            imp:n=1
5 0  -14 5 -13            imp:n=1
6 0  -99 #1 #2 #3 #4 #5   imp:n=1
7 0  99                   imp:n=0

1 px 0
2 px 10
3 py 0
4 py 10
5 pz 0
6 pz 5
7 c/z 5 5 2
8 c/z 20 5 3
9 c/z 20 5 2
10 pz 10
11 s 5 5 10 {ball_radius}
12 c/z 15 0 0.5
13 pz 2
14 c/z 15 10 0.5
99 so 100

mode n
"""
