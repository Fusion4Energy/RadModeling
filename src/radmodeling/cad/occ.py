"""Thin compatibility layer over OCP (OpenCascade) that works with OCP 7.8 and 8.x."""

from __future__ import annotations

from collections.abc import Iterator

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_ShapeEnum, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape, TopoDS_Solid

try:  # OCP >= 8
    from OCP.collections import Sequence_TDF_Label as LabelSequence
except ImportError:  # OCP 7.x
    from OCP.TDF import TDF_LabelSequence as LabelSequence  # type: ignore[no-redef]


def _caster(name: str):
    return getattr(TopoDS, f"{name}_s", None) or getattr(TopoDS, name)


to_solid = _caster("Solid")
to_face = _caster("Face")
to_edge = _caster("Edge")


def explore(shape: TopoDS_Shape, kind: TopAbs_ShapeEnum) -> Iterator[TopoDS_Shape]:
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        yield explorer.Current()
        explorer.Next()


def solids(shape: TopoDS_Shape) -> list[TopoDS_Solid]:
    return [to_solid(s) for s in explore(shape, TopAbs_SOLID)]


def faces(shape: TopoDS_Shape) -> list[TopoDS_Face]:
    return [to_face(f) for f in explore(shape, TopAbs_FACE)]


def volume_mm3(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def bounding_box(
    shape: TopoDS_Shape,
) -> tuple[float, float, float, float, float, float]:
    """Return (xmin, ymin, zmin, xmax, ymax, zmax) of a shape, in model units."""
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, True)
    lo, hi = box.CornerMin(), box.CornerMax()
    return lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()


__all__ = [
    "LabelSequence",
    "bounding_box",
    "explore",
    "faces",
    "solids",
    "to_edge",
    "to_face",
    "to_solid",
    "volume_mm3",
]
