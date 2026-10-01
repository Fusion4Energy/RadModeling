"""Reading and writing STEP assemblies while keeping the component names intact.

An assembly is handled as a flat list of :class:`Part` objects. Each part keeps the
names of all its ancestors (``path``) and its shape with the assembly placement already
applied, so instancing is resolved: four instances of the same bolt become four parts.
That is what a Monte Carlo transport model needs, as each instance becomes its own set
of cells. The instances still share their geometry (``TShape``), so writing them back
keeps them linked: simplifying one bolt in a CAD program simplifies all four.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_Transform
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Interface import Interface_Static
from OCP.Message import Message, Message_Gravity
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_StepModelType
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape, TopoDS_Solid
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

from radmodeling.cad.occ import GeometryMap, LabelSequence, solids


@dataclass
class Part:
    """A leaf of the assembly tree: a named shape with its placement applied."""

    name: str
    path: tuple[str, ...]
    shape: TopoDS_Shape
    instance_name: str = ""
    # Position of each node of path + name among its siblings: tells apart the
    # instances of an assembly, which all have the same path. Empty if not read.
    occurrence: tuple[int, ...] = ()
    extra: dict = field(default_factory=dict)

    @property
    def solids(self) -> list[TopoDS_Solid]:
        return solids(self.shape)

    @property
    def names(self) -> tuple[str, ...]:
        """All the names that identify this part, from the root to the leaf."""
        names = [*self.path, self.name]
        if self.instance_name and self.instance_name != self.name:
            names.append(self.instance_name)
        return tuple(names)


def _quiet_occt() -> None:
    for printer in Message.DefaultMessenger_s().Printers():
        printer.SetTraceLevel(Message_Gravity.Message_Fail)


def _label_name(label: TDF_Label) -> str:
    attribute = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return attribute.Get().ToExtString().strip()
    return ""


def read_step(path: str | os.PathLike) -> list[Part]:
    """Read a STEP file and return its parts in document order."""
    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    _quiet_occt()

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ValueError(f"Could not read STEP file: {path}")
    reader.Transfer(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    parts: list[Part] = []

    def visit(
        labels: LabelSequence,
        path: tuple[str, ...],
        location: TopLoc_Location,
        occurrence: tuple[int, ...],
    ) -> None:
        for i in range(1, labels.Length() + 1):
            label = labels.Value(i)
            instance_name = ""
            node_location = location
            if XCAFDoc_ShapeTool.IsReference_s(label):
                instance_name = _label_name(label)
                node_location = location.Multiplied(
                    XCAFDoc_ShapeTool.GetLocation_s(label)
                )
                referred = TDF_Label()
                XCAFDoc_ShapeTool.GetReferredShape_s(label, referred)
                label = referred
            name = _label_name(label) or instance_name

            if XCAFDoc_ShapeTool.IsAssembly_s(label):
                children = LabelSequence()
                XCAFDoc_ShapeTool.GetComponents_s(label, children, False)
                visit(children, (*path, name), node_location, (*occurrence, i))
                continue

            shape = XCAFDoc_ShapeTool.GetShape_s(label).Moved(node_location)
            if not solids(shape):
                continue  # sketches, points, surfaces... are not relevant here
            parts.append(Part(name, path, shape, instance_name, (*occurrence, i)))

    free_shapes = LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    visit(free_shapes, (), TopLoc_Location(), ())
    return parts


def compound(shapes: Sequence[TopoDS_Shape]) -> TopoDS_Compound:
    result = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def write_step(
    parts: Sequence[Part], path: str | os.PathLike, instancing: bool = True
) -> None:
    """Write the parts as a STEP assembly that mirrors their ``path`` hierarchy.

    With ``instancing``, parts in an assembly that share their geometry and name (the
    instances of a CAD part, as :func:`read_step` returns them) become instances of a
    single product, so that editing one in a CAD program edits all of them.

    Without it, every part is written as its own product with its placement baked into
    the geometry, as CAD-to-MCNP converters expect. Parts are written in the given
    order, which is the order in which converters number the resulting cells.
    """
    _quiet_occt()
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    XCAFDoc_ShapeTool.SetAutoNaming_s(False)

    assemblies: dict[tuple[str, ...], TDF_Label] = {}
    identity = TopLoc_Location()

    def set_name(label: TDF_Label, name: str) -> None:
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name, True))

    def assembly_label(path: tuple[str, ...]) -> TDF_Label | None:
        if not path:
            return None
        if path not in assemblies:
            label = shape_tool.NewShape()
            set_name(label, path[-1])
            parent = assembly_label(path[:-1])
            if parent is not None:
                instance = shape_tool.AddComponent(parent, label, identity)
                set_name(instance, path[-1])
            assemblies[path] = label
        return assemblies[path]

    # Products written so far by geometry and name. XCAF would otherwise merge the
    # parts that share their geometry into a single product with a single name.
    products: GeometryMap[dict[str, TDF_Label]] = GeometryMap()
    for part in parts:
        parent = assembly_label(part.path)
        written = products.get(part.shape)
        seen = written is not None  # the geometry is already in another product
        if written is None:
            written = {}
            products[part.shape] = written
        if not instancing or parent is None:
            shape = part.shape
            if seen or not shape.Location().IsIdentity():
                shape = BRepBuilderAPI_Transform(
                    shape.Located(identity), shape.Location().Transformation(), True
                ).Shape()
            label = shape_tool.AddShape(shape, False, False)
            set_name(label, part.name)
            location = identity
        else:
            label = written.get(part.name)
            if label is None:
                shape = part.shape.Located(identity)
                if seen:  # under another name, or baked: it needs its own product
                    shape = BRepBuilderAPI_Copy(shape).Shape()
                written[part.name] = label = shape_tool.AddShape(shape, False, False)
                set_name(label, part.name)
            location = part.shape.Location()
        if parent is not None:
            instance = shape_tool.AddComponent(parent, label, location)
            set_name(instance, part.name)

    # Assemblies only become proper shapes once all their components are added
    shape_tool.UpdateAssemblies()
    write_document(doc, path)


def write_document(doc: TDocStd_Document, path: str | os.PathLike) -> None:
    """Write an XCAF document as STEP, with its names and in millimetres."""
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.SetColorMode(False)
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    if writer.Write(os.fspath(path)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"Failed to write STEP file: {path}")
    _escape_non_ascii(path)


def _escape_non_ascii(path: str | os.PathLike) -> None:
    """Replace raw UTF-8 by the ``\\X2\\...\\X0\\`` escapes required by ISO 10303-21.

    OCCT writes names as raw UTF-8, which other CAD programs may misread.
    """
    with open(path, encoding="utf-8") as infile:
        text = infile.read()
    if text.isascii():
        return

    def escape(char: str) -> str:
        code = ord(char)
        if code < 0x80:
            return char
        if code <= 0xFFFF:
            return f"\\X2\\{code:04X}\\X0\\"
        return f"\\X4\\{code:08X}\\X0\\"

    with open(path, "w", encoding="ascii", newline="") as outfile:
        outfile.write("".join(escape(c) for c in text))
