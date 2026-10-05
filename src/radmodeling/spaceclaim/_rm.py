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
from System.Diagnostics import Process, ProcessStartInfo  # noqa: E402
from System.Drawing import Color  # noqa: E402
from System.Text import Encoding  # noqa: E402
from System.Windows.Forms import DialogResult, OpenFileDialog  # noqa: E402

RM_TAG = re.compile(r"\[rm(\d+)\]")
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


def rm_parameter(name, default):
    """Number in the script parameter ``name`` of the document (Groups panel >
    Script Parameters), or default if there is none."""
    try:
        value = getattr(Parameters, name)
    except Exception:  # no such parameter, or no Parameters at all
        return default
    for getter in (lambda v: v, lambda v: v.Value):
        try:
            return float(getter(value))
        except Exception:
            pass
    return default


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
    """Read the registry CSV as a dict: component ID -> rows (dicts of strings).

    A component has one row per instance. In SpaceClaim the linked instances share
    their name and tag, so the tools work with all the rows of an ID at once.
    """
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
            rows.setdefault(int(row["id"]), []).append(row)
        except (KeyError, ValueError):
            continue
    return rows


def rm_instances(bodies):
    """Group the bodies of a component by instance: the bodies of each part."""
    groups = {}
    order = []
    for body in bodies:
        try:
            part = body.Parent
        except AttributeError:
            part = None
        if part is None:
            order.append([body])
            continue
        if part not in groups:
            groups[part] = []
            order.append(groups[part])
        groups[part].append(body)
    return order


def rm_instance_bodies(body):
    """The bodies of the instance the body belongs to: those of its part."""
    try:
        bodies = list(body.Parent.Bodies)
        if bodies:
            return bodies
    except AttributeError:
        pass
    return [body]


RM_SAME_VOLUME_PCT = 0.1


def rm_instance_volume(rows):
    """Original volume of one instance of a component, and an error message.

    Instances of a component share their geometry, so all its rows have the same
    original volume and every instance must keep it.
    """
    volumes = [rm_float(row.get("original_volume_cm3")) for row in rows or []]
    if not volumes or None in volumes:
        return None, "has no original volume in the registry"
    if (max(volumes) - min(volumes)) / max(volumes) * 100 > RM_SAME_VOLUME_PCT:
        return None, "has instances with different original volumes"
    return sum(volumes) / len(volumes), None


def rm_highlight(problems):
    """Colour in red the components listed per category, the rest grey transparent.

    Returns one line per category with its number of components.
    """
    by_id, _ = rm_bodies_by_id()
    flagged = set()
    lines = []
    for category in sorted(problems):
        ids = [i for i in problems[category] if i in by_id]
        flagged.update(ids)
        lines.append("%s: %d" % (category, len(problems[category])))
    every = [b for bodies in by_id.values() for b in bodies]
    rm_set_color(every, RM_GREY, transparent=True)
    rm_set_color([b for i in flagged for b in by_id[i]], RM_RED)
    return lines


def rm_run_radmod(arguments):
    """Run 'python -m radmod' with the Python that wrote the loaders.

    Returns the exit code and the output. SpaceClaim waits until it finishes.
    """
    python = globals().get("RADMODELING_PYTHON")
    if not python:
        raise ValueError(
            "This tool needs loaders written by a recent radmodeling: run "
            "'radmod spaceclaim-loaders' again and publish the tools again."
        )
    info = ProcessStartInfo(python)
    info.Arguments = " ".join('"%s"' % a for a in ["-m", "radmod"] + arguments)
    info.UseShellExecute = False
    info.CreateNoWindow = True
    info.RedirectStandardOutput = True
    info.RedirectStandardError = True
    info.StandardOutputEncoding = Encoding.UTF8
    info.StandardErrorEncoding = Encoding.UTF8
    info.EnvironmentVariables["PYTHONUTF8"] = "1"
    info.EnvironmentVariables["COLUMNS"] = "100"
    process = Process.Start(info)
    errors = process.StandardError.ReadToEndAsync()  # both pipes must be drained
    output = process.StandardOutput.ReadToEnd()
    process.WaitForExit()
    return process.ExitCode, output + errors.Result


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
