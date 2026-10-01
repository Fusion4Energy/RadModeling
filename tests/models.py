"""A small CAD assembly and the MCNP model equivalent to it, for tests."""

from __future__ import annotations

from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakeSphere,
)
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from radmodeling.cad.step import Part, compound, write_step


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
