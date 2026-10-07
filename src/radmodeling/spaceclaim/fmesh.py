# RadModeling: write FMESH tally cards for the selected boxes (any orientation).
#
# Select the boxes (bodies, or any of their faces) and run it. The boxes are exported
# to a temporary STEP file and 'radmod fmesh' writes the cards to <name>_fmesh.txt
# next to the document, replacing the previous file. Tally numbers start at 14,
# rotated boxes get TR cards from 900, and the voxel size is the FmeshVoxelSize
# script parameter in cm (5 by default): edit the cards as needed.
import os
import shutil
import tempfile

import clr
from System import Enum

VOXEL_SIZE_CM = 5.0  # default of FmeshVoxelSize


def rm_selected_bodies():
    bodies = []
    for item in Selection.GetActive().Items:
        body = item if isinstance(item, IDesignBody) else getattr(item, "Parent", None)
        if isinstance(body, IDesignBody) and body not in bodies:
            bodies.append(body)
    return bodies


def rm_export_bodies(bodies, path):
    """Write copies of the bodies, in world coordinates, to a STEP file.

    Part.Export writes no body names, but it names the solid of a single-body
    component after the component: each body goes into a component of its name.
    """
    document = Document.Create()
    try:
        for body in bodies:
            name = rm_name(body)
            template = Part.Create(document, name)
            Component.Create(document.MainPart, template)
            DesignBody.Create(template, name, body.Shape.Copy())
        part = document.MainPart
        part.Export(rm_step_format(), path, True, ExportOptions.Create())
    finally:
        rm_close_document(document)


def rm_close_document(document):
    """Close the tab of a scratch document.

    Document has no Close, and DocumentHelper.CloseDocument() takes no document (it
    would close the user's): close the windows showing this document only. Failures
    are ignored, so they never hide one of the export; the tab is then left open.
    """
    for name in ("AllWindows", "GetWindows"):
        try:
            found = getattr(Window, name)
            if name == "GetWindows":
                found = found(document)
            windows = [w for w in found if w.Document == document]
        except Exception:
            continue
        for window in windows:
            try:
                window.Close()
            except Exception:
                pass
        return


def rm_step_format():
    """PartExportFormat.Step. The enum is neither an injected global nor in the
    namespace of Part, so it is taken from the signature of Part.Export."""
    export = [m for m in clr.GetClrType(Part).GetMethods() if m.Name == "Export"][0]
    enum = export.GetParameters()[0].ParameterType
    names = [n for n in Enum.GetNames(enum) if n.lower() == "step"]
    if not names:
        raise ValueError("No STEP in %s: %s" % (enum, ", ".join(Enum.GetNames(enum))))
    return Enum.Parse(enum, names[0])


def rm_fmesh():
    size = rm_positive_parameter("FmeshVoxelSize", VOXEL_SIZE_CM)
    if size is None:
        return
    path = GetRootPart().Document.Path
    if not path:
        rm_message("Save the document first: the cards are written next to it.")
        return
    bodies = rm_selected_bodies()
    if not bodies:
        rm_message("Select the boxes first.")
        return
    cards = os.path.splitext(path)[0] + "_fmesh.txt"
    folder = tempfile.mkdtemp()
    try:
        step = os.path.join(folder, "boxes.stp")  # Part.Export forces .stp
        rm_export_bodies(bodies, step)
        code, output = rm_run_radmod(
            ["fmesh", step, "--output", cards, "--size", "%g" % size]
        )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if code != 0:
        rm_message("radmod fmesh failed:\n\n" + output)
        return
    rm_message(output)


rm_fmesh()
