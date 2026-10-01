# Shared helpers of the RadModeling SpaceClaim tools.
#
# These scripts run in SpaceClaim's IronPython 2.7: no f-strings, no type hints, and
# the SpaceClaim API names (GetRootPart, Selection, ColorHelper...) are globals
# injected by SpaceClaim. Names are prefixed with rm_ to never shadow them.
#
# All the real logic lives in the radmodeling CPython package; the tools here only
# read the registry written by 'radmod' and colour, hide or extrude things.
import csv
import io
import os
import re

import clr

clr.AddReference("System.Drawing")
clr.AddReference("System.Windows.Forms")
from System.Drawing import Color  # noqa: E402
from System.Windows.Forms import DialogResult, OpenFileDialog  # noqa: E402

RM_TAG = re.compile(r"\[rm:(\d+)\]")
RM_RED = Color.FromArgb(208, 59, 59)
RM_BLUE = Color.FromArgb(42, 120, 214)
RM_GREY = Color.FromArgb(185, 184, 179)


def rm_message(text):
    MessageBox.Show(text)


def rm_name(obj):
    for getter in (lambda o: o.GetName(), lambda o: o.Name):
        try:
            name = getter(obj)
            if name:
                return name
        except Exception:
            pass
    return ""


def rm_tag(name):
    match = RM_TAG.search(name or "")
    return int(match.group(1)) if match else None


def rm_component_id(component):
    """Tag of the component or of its closest tagged ancestor."""
    while component is not None:
        tag = rm_tag(rm_name(component))
        if tag is not None:
            return tag
        try:
            component = component.Parent.Parent  # Component -> Part -> Component
        except AttributeError:
            return None
    return None


def rm_body_id(body):
    tag = rm_tag(rm_name(body))
    if tag is not None:
        return tag
    try:
        return rm_component_id(body.Parent.Parent)
    except AttributeError:
        return None


def rm_bodies_by_id():
    """Map component ID -> bodies, and the list of untagged bodies."""
    by_id, untagged = {}, []
    for body in GetRootPart().GetAllBodies():
        component_id = rm_body_id(body)
        if component_id is None:
            untagged.append(body)
        else:
            by_id.setdefault(component_id, []).append(body)
    return by_id, untagged


def rm_volume_cm3(bodies):
    return sum(body.MassProperties.Mass for body in bodies) * 1.0e6


def rm_ask_file(title, pattern):
    dialog = OpenFileDialog()
    dialog.Title = title
    dialog.Filter = pattern
    if dialog.ShowDialog() == DialogResult.OK:
        return dialog.FileName
    return None


def rm_find_file(suffix, title, pattern):
    """A file next to the document ending in suffix, or else ask for it."""
    try:
        folder = os.path.dirname(GetRootPart().Document.Path)
        found = [f for f in os.listdir(folder) if f.lower().endswith(suffix)]
    except Exception:
        found = []
    if len(found) == 1:
        return os.path.join(folder, found[0])
    return rm_ask_file(title, pattern)


def rm_float(text):
    text = (text or "").strip()
    if not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def rm_read_registry(path=None):
    """Read the registry CSV as a dict: component ID -> row (dict of strings)."""
    path = path or rm_find_file(
        "_registry.csv", "Select the RadModeling registry", "Registry (*.csv)|*.csv"
    )
    if not path:
        raise ValueError("No registry selected")
    text = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with io.open(path, encoding=encoding) as infile:
                text = infile.read()
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    delimiter = ";" if lines[0].count(";") > lines[0].count(",") else ","
    rows = {}
    for row in csv.DictReader(lines, delimiter=delimiter):
        try:
            rows[int(row["id"])] = row
        except (KeyError, ValueError):
            continue
    return rows


def rm_set_color(bodies, color, transparent=False):
    if not bodies:
        return
    selection = Selection.Create(bodies)
    options = SetColorOptions()
    options.FaceColorTarget = FaceColorTarget.Body
    ColorHelper.SetColor(selection, options, color)
    ColorHelper.SetFillStyle(
        selection, FillStyle.Transparent if transparent else FillStyle.Opaque
    )


def rm_show_only(bodies):
    everything = Selection.Create(GetRootPart().GetAllBodies())
    ViewHelper.SetObjectVisibility(everything, VisibilityType.Hide, False)
    if bodies:
        ViewHelper.SetObjectVisibility(
            Selection.Create(bodies), VisibilityType.Show, False
        )
