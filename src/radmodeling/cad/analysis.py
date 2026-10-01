"""Geometric diagnostics that matter for CAD-to-CSG conversion."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.GeomAbs import GeomAbs_SurfaceType as ST
from OCP.TopoDS import TopoDS_Shape

from radmodeling.cad.occ import faces, solids, volume_mm3

SURFACE_NAMES = {
    ST.GeomAbs_Plane: "plane",
    ST.GeomAbs_Cylinder: "cylinder",
    ST.GeomAbs_Cone: "cone",
    ST.GeomAbs_Sphere: "sphere",
    ST.GeomAbs_Torus: "torus",
    ST.GeomAbs_BezierSurface: "bezier",
    ST.GeomAbs_BSplineSurface: "bspline",
    ST.GeomAbs_SurfaceOfRevolution: "revolution",
    ST.GeomAbs_SurfaceOfExtrusion: "extrusion",
    ST.GeomAbs_OffsetSurface: "offset",
    ST.GeomAbs_OtherSurface: "other",
}
# Surfaces that CSG converters (e.g. GEOUNED) can translate into MCNP surfaces
CONVERTIBLE = {"plane", "cylinder", "cone", "sphere", "torus"}


def surface_types(shape: TopoDS_Shape) -> Counter[str]:
    return Counter(
        SURFACE_NAMES[BRepAdaptor_Surface(f).GetType()] for f in faces(shape)
    )


@dataclass
class ShapeReport:
    n_solids: int
    volume_cm3: float
    surfaces: Counter[str]
    valid: bool
    negative_volume_solids: int

    @property
    def unconvertible_surfaces(self) -> list[str]:
        return sorted(set(self.surfaces) - CONVERTIBLE)

    @property
    def has_torus(self) -> bool:
        return "torus" in self.surfaces


def analyse(shape: TopoDS_Shape) -> ShapeReport:
    solid_list = solids(shape)
    volumes = [volume_mm3(s) for s in solid_list]
    return ShapeReport(
        n_solids=len(solid_list),
        volume_cm3=sum(volumes) / 1000.0,
        surfaces=surface_types(shape),
        valid=BRepCheck_Analyzer(shape).IsValid(),
        negative_volume_solids=sum(v <= 0 for v in volumes),
    )
