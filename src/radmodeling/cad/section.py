"""Planar sections of CAD shapes as polylines, for plotting."""

from __future__ import annotations

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS_Shape

from radmodeling.cad.occ import explore, to_edge


def section_polylines(
    shape: TopoDS_Shape,
    origin: np.ndarray,
    normal: np.ndarray,
    deflection: float = 0.05,
) -> list[np.ndarray]:
    """Intersect a shape with a plane and return the edges as (k, 3) point arrays."""
    plane = gp_Pln(gp_Pnt(*map(float, origin)), gp_Dir(*map(float, normal)))
    section = BRepAlgoAPI_Section(shape, plane, False)
    section.Approximation(True)
    section.Build()
    if not section.IsDone():
        return []
    polylines = []
    for edge in explore(section.Shape(), TopAbs_EDGE):
        curve = BRepAdaptor_Curve(to_edge(edge))
        sampler = GCPnts_QuasiUniformDeflection(
            curve, deflection, curve.FirstParameter(), curve.LastParameter()
        )
        if not sampler.IsDone() or sampler.NbPoints() < 2:
            continue
        polylines.append(
            np.array(
                [sampler.Value(i).Coord() for i in range(1, sampler.NbPoints() + 1)]
            )
        )
    return polylines
