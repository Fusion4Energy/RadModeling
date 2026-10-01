# RadModeling: show only the components made of the chosen materials (from the
# registry). "(no material)" lists the components still waiting for one. Linked
# instances cannot be told apart here: a component is shown if any of its instances
# is made of a chosen material.
import clr

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
from System.Drawing import Point, Size  # noqa: E402
from System.Windows.Forms import (  # noqa: E402
    AnchorStyles,
    Button,
    CheckedListBox,
    DialogResult,
    Form,
    FormStartPosition,
)

NO_MATERIAL = "(no material)"


class RmMaterialsForm(Form):
    def __init__(self, materials):
        self.Text = "Show materials"
        self.ClientSize = Size(300, 380)
        self.StartPosition = FormStartPosition.CenterScreen
        self.checks = CheckedListBox()
        self.checks.CheckOnClick = True
        self.checks.Location = Point(12, 12)
        self.checks.Size = Size(276, 316)
        self.checks.Anchor = (
            AnchorStyles.Top
            | AnchorStyles.Bottom
            | AnchorStyles.Left
            | AnchorStyles.Right
        )
        for material in materials:
            self.checks.Items.Add(material)
        self.Controls.Add(self.checks)
        button = Button()
        button.Text = "Show"
        button.Location = Point(12, 340)
        button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        button.DialogResult = DialogResult.OK
        self.Controls.Add(button)
        self.AcceptButton = button

    def selected(self):
        return [str(item) for item in self.checks.CheckedItems]


def rm_show_by_material():
    registry = rm_read_registry()
    by_id, _ = rm_bodies_by_id()

    def materials_of(component_id):
        rows = registry.get(component_id) or [{}]
        return set((row.get("material") or "").strip() or NO_MATERIAL for row in rows)

    materials = set()
    for component_id in by_id:
        materials |= materials_of(component_id)
    materials = sorted(materials, key=lambda m: m.lower())
    form = RmMaterialsForm(materials)
    if form.ShowDialog() != DialogResult.OK:
        return
    chosen = set(form.selected())
    bodies = []
    for component_id, component_bodies in by_id.items():
        if materials_of(component_id) & chosen:
            bodies.extend(component_bodies)
    rm_show_only(bodies)
    ViewHelper.ZoomToEntity()


rm_show_by_material()
