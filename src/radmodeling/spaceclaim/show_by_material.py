# RadModeling: show only the components made of the chosen materials (from the
# registry). "(no material)" lists the components still waiting for one.
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

    def material_of(component_id):
        row = registry.get(component_id)
        material = (row.get("material") or "").strip() if row else ""
        return material or NO_MATERIAL

    materials = sorted(set(material_of(i) for i in by_id), key=lambda m: m.lower())
    form = RmMaterialsForm(materials)
    if form.ShowDialog() != DialogResult.OK:
        return
    chosen = set(form.selected())
    bodies = []
    for component_id, component_bodies in by_id.items():
        if material_of(component_id) in chosen:
            bodies.extend(component_bodies)
    rm_show_only(bodies)
    ViewHelper.ZoomToEntity()


rm_show_by_material()
