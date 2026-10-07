import csv
import json
import math
import os
from pathlib import Path

import migjorn
import models
import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeSphere
from OCP.gp import gp_Pnt

from radmodeling import workflow
from radmodeling.cad.occ import GeometryMap, bounding_box, volume_and_centroid
from radmodeling.cad.step import Part, read_step, write_step
from radmodeling.compare import TaggedCells, compare_component
from radmodeling.mcnp.geometry import Geometry
from radmodeling.mcnp.materials import (
    AssignmentError,
    MaterialLibrary,
    assign_materials,
)
from radmodeling.mcnp.tags import cell_key, cells_by_instance
from radmodeling.registry import (
    Component,
    Key,
    Registry,
    find_id,
    find_key,
    key_name,
    tag_name,
)


def test_tags():
    assert tag_name("Pump [rm3]", 7) == "Pump [rm7]"
    assert find_id(["Root [rm1]", "Body"]) == 1
    assert find_id(["Root [rm1]", "Child [rm2]", "Solid1"]) == 2
    assert find_id(["Root", "x"]) is None
    # The STEP written by 'export' tags each solid with its instance too
    assert key_name("Pump [rm3]", Key(7, 2)) == "[rm7.2] Pump"
    assert find_id(["[rm7.2] Pump #1"]) == 7
    assert find_key("/export/[rm7.2] Pump #11") == (7, 2)
    assert find_key("Pump [rm7]") is None


def test_registry_reads_excel_european_csv(tmp_path):
    path = tmp_path / "registry.csv"
    path.write_bytes(
        "id;name;material;density_g_cm3;mass_g;original_volume_cm3\n"
        "1;Válvula;SS316L;7,93;;10,5\n"
        "2;Bomba;Tungsten;;193;10\n".encode("cp1252")
    )
    registry = Registry.load(path)
    assert registry[1, 1].name == "Válvula"
    assert registry[1, 1].density == pytest.approx(7.93)
    assert registry[2, 1].density == pytest.approx(19.3)

    registry.save(path)
    again = Registry.load(path)
    assert again[1, 1].name == "Válvula"
    assert again[1, 1].original_volume_cm3 == pytest.approx(10.5)


def test_registry_hierarchy_columns(tmp_path):
    path = tmp_path / "registry.csv"
    Registry(
        [
            Component(1, "Plate", levels=("Machine",)),
            Component(2, "Bolt", 2, ("Machine", "Left"), x_cm=1.5, y_cm=0, z_cm=-2),
        ]
    ).save(path)
    header = path.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert header[:5] == ["id", "instance", "level_1", "level_2", "name"]
    assert header[-3:] == ["x_cm", "y_cm", "z_cm"]

    registry = Registry.load(path)
    assert registry[1, 1].levels == ("Machine",)
    assert registry[2, 2].levels == ("Machine", "Left")
    assert registry[2, 2].centre_cm == (1.5, 0, -2)
    assert registry.ids == [1, 2]
    assert Key.parse("2.10") == (2, 10)
    assert Key.parse("7") == (7, 1)
    assert str(Key(2, 10)) == "2.10"


def simplify(tagged, output):
    """Mimic a CAD session: move and rename things, shrink the ball, add a part."""
    parts = []
    for part in read_step(tagged):
        if "Ball" in part.name:
            ball = BRepPrimAPI_MakeSphere(gp_Pnt(50, 50, 100), 24).Shape()
            parts.append(Part("Simplified sphere [rm3]", ("New", "Misc"), ball))
        else:
            parts.append(Part(part.name, ("New",), part.shape))
    parts.append(Part("Shield", ("New",), models.cylinder(300, 0, 0, 10, 10)))
    write_step(parts, output)


def test_init_check_export(tmp_path, cad_model):
    tagged = tmp_path / "tagged.step"
    registry_csv = tmp_path / "registry.csv"
    registry = workflow.init(cad_model, tagged)
    registry.save(registry_csv)
    assert [c.name for c in registry] == ["Block", "Pipe", "Ball", "Bolts"]
    assert registry[4, 1].original_solids == 2
    assert registry[2, 1].levels == ("Machine", "Piping")
    assert registry[3, 1].original_volume_cm3 == pytest.approx(4 / 3 * math.pi * 2.5**3)
    assert registry[3, 1].centre_cm == pytest.approx((5, 5, 10))

    simplified = tmp_path / "simplified.step"
    simplify(tagged, simplified)
    result = workflow.check(simplified, Registry.load(registry_csv), tolerance_pct=1)
    assert [c.key for c in result.exceeding()] == [(3, 1)]
    assert result.registry[3, 1].volume_diff_pct == pytest.approx(
        (24**3 / 25**3 - 1) * 100, rel=1e-4
    )
    assert [i.name for i in result.matching.untagged] == ["Shield"]
    assert result.matching.missing == []

    registry = Registry.load(registry_csv)
    output = tmp_path / "export.step"
    result = workflow.export(simplified, registry, output)
    assert result.added == result.untagged == [(5, 1)]
    assert registry[5, 1].status == "new"
    assert [k.id for k in result.keys] == [1, 2, 3, 4, 4, 5]
    # The exported file is flat and in cell order
    assert [p.name for p in read_step(output)] == [
        "[rm1.1] Block",
        "[rm2.1] Pipe",
        "[rm3.1] Ball",
        "[rm4.1] Bolts #1",
        "[rm4.1] Bolts #2",
        "[rm5.1] Shield",
    ]


def simplify_bolts(tagged, output):
    """Mimic a CAD session on the instanced model.

    The linked bolt is simplified once for all its instances, the bolt at x = 50 on
    the left side is deleted and one bolt is copied to x = 500. The parts are written
    in reverse order: matching must not depend on it.
    """
    parts, thinner = [], None
    for part in read_step(tagged):
        if "Bolt" in part.name:
            if part.path[-1] == "Left" and bounding_box(part.shape)[0] == pytest.approx(
                45
            ):
                continue
            if thinner is None:
                thinner = models.cylinder(0, 0, 0, 4.9, 20)
            part = Part(part.name, part.path, thinner.Moved(part.shape.Location()))
        parts.append(part)
    copy = parts[-1]
    parts.append(Part(copy.name, copy.path, models.placed(copy.shape, 400, 0)))
    write_step(parts[::-1], output)


def test_instances_are_simplified_once_and_tracked_one_by_one(tmp_path):
    original = models.write_instanced_cad(tmp_path / "bolts.step")
    tagged, registry_csv = tmp_path / "tagged.step", tmp_path / "registry.csv"
    registry = workflow.init(original, tagged)
    registry.save(registry_csv)
    assert [str(c.key) for c in registry] == [
        "1.1", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6"
    ]  # fmt: skip
    assert registry[2, 1].levels == ("Machine", "Left")
    assert registry[2, 4].levels == ("Machine", "Right")
    assert registry[2, 2].centre_cm == pytest.approx((5, 0, 1))
    assert registry[2, 6].centre_cm == pytest.approx((8, 20, 1))
    # The tagged bolts are still instances of one product
    bolts = [p for p in read_step(tagged) if "Bolt" in p.name]
    assert {p.name for p in bolts} == {"Bolt [rm2]"}
    definition: GeometryMap[bool] = GeometryMap()
    definition[bolts[0].shape] = True
    assert all(p.shape in definition for p in bolts)

    for component in registry.instances(2):
        component.material = "SS316L"
    registry.save(registry_csv)
    simplified = tmp_path / "simplified.step"
    simplify_bolts(tagged, simplified)

    result = workflow.check(simplified, Registry.load(registry_csv))
    matching = result.matching
    assert matching.missing == [(2, 2)]
    assert [i.centre_cm for i in matching.extra] == [pytest.approx((48, 20, 1))]
    assert matching.moved == []
    assert [str(c.key) for c in result.exceeding()] == [
        "2.1", "2.3", "2.4", "2.5", "2.6"
    ]  # fmt: skip
    assert result.registry[2, 2].status == "deleted"
    assert result.registry[2, 6].volume_diff_pct == pytest.approx(
        (0.98**2 - 1) * 100, rel=1e-4
    )

    registry = Registry.load(registry_csv)
    output = tmp_path / "export.step"
    result = workflow.export(simplified, registry, output)
    assert result.added == [(2, 7)]
    assert result.untagged == []
    assert registry[2, 7].status == "new"
    assert registry[2, 7].material == "SS316L"
    assert [str(k) for k in result.keys] == [
        "1.1", "2.1", "2.3", "2.4", "2.5", "2.6", "2.7"
    ]  # fmt: skip
    # Each exported solid is the instance its registry row describes
    for key, solids in workflow.exported_solids(output).items():
        centre = np.array(volume_and_centroid(solids[0])[1]) / 10
        assert centre == pytest.approx(registry[key].centre_cm, abs=1e-6)


def test_tagged_assemblies_are_instances(tmp_path):
    """Every occurrence of a tagged assembly is one instance with all its bodies."""
    path = models.write_instanced_assembly(tmp_path / "screws.step")
    instances = workflow.find_instances(read_step(path), workflow.Measurer())
    assert [(i.id, i.name, len(i.parts)) for i in instances] == [(3, "Screw", 2)] * 3
    assert [i.centre_cm[0] for i in instances if i.centre_cm] == pytest.approx(
        [0, 10, 20], abs=1e-6
    )
    registry = Registry(
        Component(3, "Screw", n, x_cm=x, y_cm=0, z_cm=1.2)
        for n, x in ((1, 20), (2, 0), (3, 10))
    )
    matching = workflow.match_instances(instances, registry)
    assert {k: i.centre_cm[0] for k, i in matching.found.items() if i.centre_cm} == (
        pytest.approx({(3, 1): 20, (3, 2): 0, (3, 3): 10}, abs=1e-6)
    )


def test_new_tags_become_new_components(tmp_path, cad_model):
    """A tag written in the CAD after init adds a component, only once: on check, or
    on export for a model that was not checked."""
    tagged, registry_csv = tmp_path / "tagged.step", tmp_path / "registry.csv"
    workflow.init(cad_model, tagged).save(registry_csv)
    shield = models.cylinder(300, 0, 0, 10, 10)
    parts = read_step(tagged)
    parts += [
        Part("Shield [rm9]", ("New",), models.placed(shield, 0, y)) for y in (0, 50)
    ]
    simplified = tmp_path / "simplified.step"
    write_step(parts, simplified)

    registry = Registry.load(registry_csv)
    result = workflow.check(simplified, registry)
    assert [i.id for i in result.matching.new] == [9, 9]
    assert result.matching.missing == []
    assert [c.status for c in registry.instances(9)] == ["new", "new"]
    assert registry[9, 1].simplified_volume_cm3 == pytest.approx(
        result.matching.found[Key(9, 1)].volume_cm3
    )
    assert workflow.check(simplified, registry).matching.new == []

    for run in range(2):
        registry = Registry.load(registry_csv)
        output = tmp_path / "export.step"
        result = workflow.export(simplified, registry, output)
        registry.save(registry_csv)
        assert result.added == ([(9, 1), (9, 2)] if run == 0 else [])
        assert result.untagged == []
    registry = Registry.load(registry_csv)
    assert [c.status for c in registry.instances(9)] == ["new", "new"]
    assert registry[9, 2].centre_cm == pytest.approx((30, 5, 0.5))
    assert registry[9, 1].levels == ("New",)


def test_tags_left_only_on_bodies_are_found(tmp_path):
    """SpaceClaim may write a component as a bare solid that alone keeps the tag."""
    original = tmp_path / "pipe.step"
    write_step([Part("Pipe", ("Machine",), models.pipe())], original)
    tagged, registry_csv = tmp_path / "tagged.step", tmp_path / "registry.csv"
    registry = workflow.init(original, tagged)
    registry.save(registry_csv)
    text = tagged.read_text().replace("'Pipe [rm1]'", "'SOLID'")
    text = text.replace("MANIFOLD_SOLID_BREP(''", "MANIFOLD_SOLID_BREP('Pipe [rm1]'")
    assert text.count("Pipe [rm1]") == 1
    simplified = tmp_path / "simplified.step"
    simplified.write_text(text)
    assert [p.name for p in read_step(simplified)] == ["SOLID"]

    matching = workflow.check(simplified, registry).matching
    assert list(matching.found) == [(1, 1)]
    assert matching.untagged == []
    assert matching.found[Key(1)].name == "Pipe"


def test_far_instances_are_not_matched_unless_unambiguous():
    def instance(component_id, x):
        return workflow.Instance(component_id, "Part", (), [], 1.0, (x, 0.0, 0.0))

    registry = Registry(
        [
            Component(1, "Alone", original_volume_cm3=1, x_cm=0, y_cm=0, z_cm=0),
            *(
                Component(2, "Bolt", n, original_volume_cm3=1, x_cm=x, y_cm=0, z_cm=0)
                for n, x in ((1, 0), (2, 10))
            ),
        ]
    )
    # Component 1 moved; for component 2, instance 2 was deleted and one copied
    found = [instance(1, 50), instance(2, 0.1), instance(2, 30)]
    matching = workflow.match_instances(found, registry)
    assert sorted(matching.found) == [(1, 1), (2, 1)]
    assert matching.moved == [(1, 1)]
    assert matching.missing == [(2, 2)]
    assert [i.centre_cm for i in matching.extra] == [(30, 0, 0)]


def cell(model: migjorn.Model, number: int) -> migjorn.Cell:
    result = model.cell(number)
    assert result is not None
    return result


def test_cells_are_found_by_the_tags_in_their_comments():
    model = migjorn.parse(models.mcnp_text())
    assert cells_by_instance(model) == {
        (1, 1): [1], (2, 1): [2], (3, 1): [3], (4, 1): [4, 5]
    }  # fmt: skip
    # The tags survive renumbering, unlike the cell order
    model.renumber_cells({1: 105, 2: 104, 3: 103, 4: 102, 5: 101})
    assert cells_by_instance(model) == {
        (1, 1): [105], (2, 1): [104], (3, 1): [103], (4, 1): [102, 101]
    }  # fmt: skip


def test_cell_tags_are_read_from_dollar_comments_only():
    text = models.mcnp_text().replace("2 0  -8", "c [rm7.1] a comment line\n2 0  -8")
    model = migjorn.parse(text)
    cell2 = cell(model, 2)
    assert cell2.text.startswith("c [rm7.1]")  # the comment belongs to the card
    assert cell_key(cell2) == (2, 1)
    # GEOUNED writes the material first; a component tag without instance is no key
    text = models.mcnp_text().replace(
        "$/export/[rm3.1]", "$SS316\n" + " " * 11 + "$/x/[rm3]/[rm9.2]"
    )
    assert cell_key(cell(migjorn.parse(text), 3)) == (9, 2)


def make_registry():
    return Registry(
        [
            Component(1, "Block", material="316L", original_volume_cm3=437.17),
            Component(
                2,
                "Pipe",
                material="SS316L",
                mcnp_volume_cm3=150,
                original_volume_cm3=157.08,
                dcf=157.08 / 150,
            ),
            Component(
                3, "Ball", material="tungsten", mass_g=1000, original_volume_cm3=50
            ),
            Component(4, "Bolts", material="Void"),
        ]
    )


def test_assign_materials(library_file):
    # The input defines the steel card: only the tungsten one is missing
    model = migjorn.parse(models.mcnp_text() + "m10 26000.31c 1\n")
    report = assign_materials(
        model, make_registry(), MaterialLibrary.load(library_file)
    )
    assert cell(model, 1).material == 10
    assert cell(model, 1).density == pytest.approx(-7.93)
    assert cell(model, 2).density == pytest.approx(-7.93 * 157.08 / 150, rel=1e-5)
    assert cell(model, 3).material == 31  # auto-assigned after the highest number
    assert cell(model, 3).density == pytest.approx(-20)
    assert cell(model, 4).material == 0
    assert report.material_numbers == {"SS316L": 10, "Tungsten": 31}
    assert report.missing_cards == [31]
    assert model.material(31) is None  # material cards are never written
    assert cell(model, 6).material == 0  # untagged cells are left alone


def test_assign_materials_void(library_file):
    # Void ignores case and spaces, needs no library entry or density, and
    # replaces any material the cell already had
    registry = make_registry()
    registry[4, 1].material = "  VOID "
    registry[4, 1].density_g_cm3 = None
    model = migjorn.parse(models.mcnp_text())
    cell(model, 4).material = 5
    cell(model, 4).density = -3.0
    report = assign_materials(model, registry, MaterialLibrary.load(library_file))
    assert cell(model, 4).material == 0
    assert cell(model, 4).density is None
    assert report.void_components == 1
    assert "void" not in {name.lower() for name in report.cells_per_material}
    assert "void" not in {name.lower() for name in report.mass_per_material_g}


@pytest.mark.parametrize(
    ("mcnp_volume", "std", "tolerance", "applied"),
    [
        (49.0, None, 1, True),  # 2 %
        (49.6, None, 1, False),  # 0.8 %, within the volume tolerance
        (49.6, None, 0.5, True),  # beyond a tighter tolerance
        (49.48, None, 1, True),  # 1.05 %, just beyond the volume tolerance
        (49.48, 0.05, 1, False),  # but within it given its uncertainty (3σ: 0.3 %)
        (49.0, 0.4, 1, False),  # 2 %, within 1 % + three standard deviations (2.4 %)
        (49.0, 0.1, 1, True),  # 2 % and well beyond 1 % + 3σ (0.6 %)
    ],
)
def test_negligible_dcf_are_not_applied(
    library_file, mcnp_volume, std, tolerance, applied
):
    registry = make_registry()
    ball = registry[3, 1]
    # The registry shows the factor that is applied, not the raw volume ratio
    ball.set_mcnp_volume(mcnp_volume, std or 0.0, tolerance)
    assert ball.dcf == pytest.approx(50 / mcnp_volume if applied else 1)
    model = migjorn.parse(models.mcnp_text())
    report = assign_materials(model, registry, MaterialLibrary.load(library_file))
    dcf = 50 / mcnp_volume if applied else 1
    assert cell(model, 3).density == pytest.approx(-20 * dcf, rel=1e-5)
    assert ((3, 1) in report.dcf_applied) == applied
    assert ((3, 1) in report.dcf_negligible) != applied
    assert report.dcf_applied[:1] == [(2, 1)]  # the pipe: 4.7 %
    assert report.dcf_manual == []
    assert report.dcf_missing == [(1, 1)]  # the only one without MCNP volume


def test_manual_dcf_is_applied_until_compare(library_file, tmp_path):
    registry = make_registry()
    ball = registry[3, 1]
    ball.set_mcnp_volume(49.0, 0.1)
    ball.dcf = 1.01  # by hand

    # Saving and loading do not recompute it, 'materials' applies and lists it
    path = tmp_path / "registry.csv"
    registry.save(path)
    registry = Registry.load(path)
    ball = registry[3, 1]
    assert ball.dcf == 1.01 and ball.manual_dcf

    model = migjorn.parse(models.mcnp_text())
    report = assign_materials(model, registry, MaterialLibrary.load(library_file))
    assert cell(model, 3).density == pytest.approx(-20.2)
    assert report.dcf_manual == [(3, 1)]

    # A new 'compare' overwrites it
    ball.set_mcnp_volume(48.0, 0.1)
    assert ball.dcf == pytest.approx(50 / 48) and not ball.manual_dcf


@pytest.mark.parametrize("mcnp_volume", [70.37037, 69.3])  # left at 1, applied
def test_dcf_from_compare_is_not_manual_after_saving(tmp_path, mcnp_volume):
    # Whatever the tolerance, and with the volumes rounded to the 6 digits saved
    registry = make_registry()
    registry[3, 1].original_volume_cm3 = 70.3
    registry[3, 1].set_mcnp_volume(mcnp_volume, 0.0, tolerance_pct=5)
    path = tmp_path / "registry.csv"
    registry.save(path)
    assert not Registry.load(path)[3, 1].manual_dcf


def test_dcf_without_mcnp_volume_is_manual():
    ball = make_registry()[3, 1]
    ball.dcf = 1.1
    assert ball.manual_dcf


def test_material_library_rejects_unknown_keys(tmp_path):
    path = tmp_path / "library.toml"
    path.write_text("[SS316L]\ndensty = 7.93\n")  # typo
    with pytest.raises(ValueError, match="unknown keys \\['densty'\\]"):
        MaterialLibrary.load(path)


def test_assign_materials_needs_tags(library_file):
    with pytest.raises(AssignmentError, match="keeping the solid names"):
        assign_materials(
            migjorn.parse("t\n1 0 -1 imp:n=1 $/export/Block1\n\n1 so 1\n\nmode n\n"),
            make_registry(),
            MaterialLibrary.load(library_file),
        )


def test_assign_materials_reports_all_problems(library_file):
    registry = make_registry()
    registry[1, 1].material = ""
    registry[2, 1].material = "Unobtainium"
    with pytest.raises(AssignmentError) as error:
        assign_materials(
            migjorn.parse(  # 9 is not in the registry
                models.mcnp_text(tags=["1.1", "2.1", "3.1", "4.1", "9.1"])
            ),
            registry,
            MaterialLibrary.load(library_file),
        )
    message = str(error.value)
    assert "Component 1.1 (Block): no material" in message
    assert "Unobtainium" in message
    assert "Component 9.1 (cells [5]) is not in the registry" in message


@pytest.mark.parametrize(("radius", "matches"), [(2.5, True), (2.6, False)])
def test_compare_component(radius, matches):
    geometry = Geometry(migjorn.parse(models.mcnp_text(ball_radius=radius)))
    result = compare_component(
        Key(3),
        [models.ball()],
        [3],
        geometry,
        n_points=60_000,
        rng=np.random.default_rng(1),
    )
    exact = 4 / 3 * math.pi * radius**3
    assert result.mcnp_volume_cm3 == pytest.approx(
        exact, abs=4 * result.mcnp_volume_std_cm3 + 0.05
    )
    assert (result.discrepancy_pct < 1) == matches
    assert not result.touches_sampling_box


def test_compare_uncertainty_is_not_below_one_line():
    """A box converts exactly: no point disagrees, yet the uncertainty is not 0."""
    text = models.mcnp_text().replace("1 -2 3 -4 5 -6 7 ", "1 -2 3 -4 5 -6   ")
    box = BRepPrimAPI_MakeBox(100, 100, 50).Shape()
    result = compare_component(
        Key(1), [box], [1], Geometry(migjorn.parse(text)), n_points=10_000,
        rng=np.random.default_rng(5),
    )  # fmt: skip
    assert result.cad_only_cm3 == result.mcnp_only_cm3 == 0
    assert result.mcnp_volume_cm3 == pytest.approx(500)
    # One line of 20 points: the sampling box (padded, a bit larger than the box) /
    # the 500 lines
    assert 500 / 500 < result.mcnp_volume_std_cm3 < 2 * 500 / 500


TWO_RODS = """Two rods 0.5 mm apart, so that their sampling boxes overlap
1 0  -1 2 -3   imp:n=1
2 0  -4 2 -3   imp:n=1
3 0  -9 #1 #2  imp:n=1
4 0  9         imp:n=0

1 c/z 0 0 0.5
2 pz 0
3 pz 2
4 c/z 1.05 0 0.5
9 so 50

mode n
"""


@pytest.mark.parametrize(("cells", "fraction"), [([1, 2], 1.0), ([1], 0.5)])
def test_compare_is_unbiased_where_sampling_boxes_overlap(cells, fraction):
    rods = [models.cylinder(0, 0, 0, 5, 20), models.cylinder(10.5, 0, 0, 5, 20)]
    geometry = Geometry(migjorn.parse(TWO_RODS))
    result = compare_component(
        Key(1), rods, cells, geometry, n_points=40_000, rng=np.random.default_rng(3)
    )
    rod = math.pi * 0.5**2 * 2
    assert result.cad_volume_cm3 == pytest.approx(2 * rod, rel=1e-3)
    # The tessellated CAD is 0.03 % smaller than the exact MCNP cylinders
    tolerance = 4 * result.mcnp_volume_std_cm3 + 2e-3
    assert result.mcnp_volume_cm3 == pytest.approx(fraction * 2 * rod, abs=tolerance)
    assert result.cad_only_cm3 == pytest.approx((1 - fraction) * 2 * rod, abs=tolerance)
    assert result.mcnp_only_cm3 < 5e-3


def test_compare_samples_scattered_solids_densely():
    """Two small rods 3 m apart: the points go next to them, not in between."""
    far = TWO_RODS.replace("4 c/z 1.05 0 0.5", "4 c/z 300 0 0.5")
    rods = [models.cylinder(0, 0, 0, 5, 20), models.cylinder(3000, 0, 0, 5, 20)]
    result = compare_component(
        Key(1), rods, [1], Geometry(migjorn.parse(far)), n_points=20_000,
        rng=np.random.default_rng(4),
    )  # fmt: skip
    rod = math.pi * 0.5**2 * 2
    # One rod is missing in MCNP: measured precisely although the rods fill only
    # 0.5 % of their common bounding box
    assert result.mcnp_volume_std_cm3 < 0.05 * rod
    tolerance = 4 * result.mcnp_volume_std_cm3
    assert result.mcnp_volume_cm3 == pytest.approx(rod, abs=tolerance)


def test_compare_reports_cad_that_cannot_be_meshed(monkeypatch):
    import radmodeling.compare

    def fail(shape):
        raise ValueError("a face could not be tessellated")

    monkeypatch.setattr(radmodeling.compare, "mesh_of", fail)
    geometry = Geometry(migjorn.parse(models.mcnp_text()))
    result = compare_component(Key(3), [models.ball()], [3], geometry, n_points=100)
    assert result.error == "CAD: a face could not be tessellated"
    assert math.isnan(result.mcnp_volume_cm3)


def test_compare_flags_cells_beyond_the_cad():
    text = models.mcnp_text().replace("11 s 5 5 10 2.5", "11 s 5 5 10 6")
    result = compare_component(
        Key(3), [models.ball()], [3], Geometry(migjorn.parse(text)), n_points=20_000
    )
    assert result.touches_sampling_box


def test_compare_measures_overlaps_with_other_instances():
    """Rods 0.8 cm apart: their cells share a lens 2 cm tall."""
    text = TWO_RODS.replace("4 c/z 1.05 0 0.5", "4 c/z 0.8 0 0.5")
    geometry = Geometry(migjorn.parse(text))
    tagged = TaggedCells(geometry, {Key(1): [1], Key(2): [2]})
    lens = 2 * (2 * 0.5**2 * math.acos(0.8) - 0.4 * math.sqrt(1 - 0.8**2))
    result = compare_component(
        Key(1), [models.cylinder(0, 0, 0, 5, 20)], [1], geometry, tagged,
        n_points=40_000, rng=np.random.default_rng(5),
    )  # fmt: skip
    volume, std = result.overlaps[Key(2)]
    assert 0 < std < 0.15 * lens
    assert volume == pytest.approx(lens, abs=4 * std)
    assert list(result.overlaps) == [Key(2)]  # its own cells are not in the others

    # Cells of the same instance overlapping each other
    both = [models.cylinder(0, 0, 0, 5, 20), models.cylinder(8, 0, 0, 5, 20)]
    result = compare_component(
        Key(1), both, [1, 2], geometry, n_points=40_000, rng=np.random.default_rng(6)
    )
    volume, std = result.overlaps[Key(1)]
    assert volume == pytest.approx(lens, abs=4 * std)


def test_compare_locates_the_overlap():
    text = TWO_RODS.replace("4 c/z 1.05 0 0.5", "4 c/z 0.8 0 0.5")
    geometry = Geometry(migjorn.parse(text))
    tagged = TaggedCells(geometry, {Key(1): [1], Key(2): [2]})
    result = compare_component(
        Key(1), [models.cylinder(0, 0, 0, 5, 20)], [1], geometry, tagged,
        n_points=20_000, rng=np.random.default_rng(7), locate=True,
    )  # fmt: skip
    spots = [s for s in result.spots if s.kind == "overlap"]
    assert len(spots) == 1  # one lens, between x = 0.3 and 0.5 cm, 2 cm tall
    spot = spots[0]
    assert spot.other == Key(2)
    assert spot.lo[0] >= 0.3 - 1e-9 and spot.hi[0] <= 0.5 + 1e-9
    assert spot.hi[2] - spot.lo[2] > 1.5
    assert spot.volume_cm3 == pytest.approx(result.overlaps[Key(2)][0])
    # The CAD and the cells of rod 1 agree: no mismatch worth showing
    assert not [s for s in result.spots if s.kind == "mismatch"]


def test_compare_ignores_cells_that_only_touch():
    geometry = Geometry(migjorn.parse(models.mcnp_text()))
    tagged = TaggedCells(geometry, {Key(1): [1], Key(2): [2], Key(3): [3]})
    result = compare_component(
        Key(1), [models.block()], [1], geometry, tagged, n_points=20_000
    )
    assert result.overlaps == {}


def test_cli_compare_reports_overlapping_pairs_once(tmp_path):
    from typer.testing import CliRunner

    from radmodeling.cli import app

    runner = CliRunner()

    def run(*args):
        result = runner.invoke(app, [str(a) for a in args])
        assert result.exit_code == 0, result.output
        return result.output

    # The first bolt was left inside the block: the CAD and the cells clash
    step = models.write_clash_cad(tmp_path / "machine.step")
    run("init", step)
    exported = tmp_path / "export.step"
    run("export", step.with_name("machine_simplified.step"), "-o", exported)
    mcnp = tmp_path / "model.mcnp"
    mcnp.write_text(models.clash_mcnp_text())
    report = tmp_path / "compare.csv"
    output = run(
        "compare", mcnp, exported, "--no-registry", "--points", "20000",
        "--seed", "2", "-o", report,
    )  # fmt: skip
    assert "2 instances agree" in output
    assert "1 overlapping pairs" in output
    rows = list(csv.DictReader(report.open()))
    overlaps = {row["component_id"]: row["overlaps"] for row in rows}
    # Measured once, from the bolts: sampled more densely than the block
    assert overlaps["4"].startswith("1.1:")
    assert overlaps["1"] == overlaps["2"] == overlaps["3"] == ""
    volume = float(overlaps["4"].partition(":")[2])
    assert volume == pytest.approx(math.pi * 0.5**2 * 2, rel=0.3)


def test_cli_compare_with_workers_gives_the_same_results(tmp_path):
    from typer.testing import CliRunner

    from radmodeling.cli import app

    step = models.write_clash_cad(tmp_path / "machine.step")
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(step)]).exit_code == 0
    exported = tmp_path / "export.step"
    simplified = step.with_name("machine_simplified.step")
    result = runner.invoke(app, ["export", str(simplified), "-o", str(exported)])
    assert result.exit_code == 0, result.output
    mcnp = tmp_path / "model.mcnp"
    mcnp.write_text(models.clash_mcnp_text())
    reports = []
    for jobs in (1, 2):
        report = tmp_path / f"compare_{jobs}.csv"
        args = ["compare", mcnp, exported, "--no-registry", "--points", "5000",
                "--seed", "3", "-o", report, "--jobs", jobs]  # fmt: skip
        result = runner.invoke(app, [str(a) for a in args])
        assert result.exit_code == 0, result.output
        reports.append(report.read_text())
    # Every instance has its own random points: the same in any worker
    assert reports[0] == reports[1]


def test_cli_pipeline(tmp_path, cad_model, library_file):
    from typer.testing import CliRunner

    from radmodeling.cli import app

    runner = CliRunner()

    def run(*args):
        result = runner.invoke(app, [str(a) for a in args])
        assert result.exit_code == 0, result.output
        return result.output

    run("init", cad_model)
    registry = cad_model.with_name("machine_registry.csv")
    reg = Registry.load(registry)
    for component, material in zip(
        reg, ["SS316L", "SS316L", "Tungsten", "void"], strict=True
    ):
        component.material = material
    reg.save(registry)

    simplified = cad_model.with_name("machine_simplified.step")
    # The registry is found next to the model: no -r needed
    problems = tmp_path / "h.json"
    output = run("check", simplified, "--highlight", problems)
    assert "Problems written" not in output
    # The interface of the SpaceClaim 'Check model' tool: IDs per category
    issues = json.loads(problems.read_text())
    assert issues["problems"]["volume"] == []
    assert set(issues["warnings"]) == {"torus", "moved", "deleted", "new"}
    assert f"Registry: {registry}" in output.replace("\n", "")
    assert "Instances beyond the volume tolerance (1 %): 0" in output

    run("export", simplified)
    exported = cad_model.with_name("machine_export.step")
    mcnp = tmp_path / "model.mcnp"
    # Renumbering the cells does not lose track of the components
    model = migjorn.parse(models.mcnp_text())
    model.offset_cells(100)
    model.save(str(mcnp))

    output = run("compare", mcnp, exported, "--seed", "1", "--points", "20000")
    assert "4 instances agree" in output
    ball = Registry.load(registry)[3, 1]
    assert ball.dcf == pytest.approx(1, abs=0.01)
    assert ball.mcnp_volume_std_cm3 is not None

    run("materials", mcnp, "-r", registry, "-l", library_file)
    result = migjorn.parse(mcnp.with_name("model_materials.mcnp").read_text())
    assert cell(result, 103).material == 31

    png = tmp_path / "slice.png"
    output = run(
        "slice",
        mcnp.with_name("model_materials.mcnp"),
        exported,
        "-o",
        png,
        "--resolution",
        "200",
    )
    assert png.stat().st_size > 10_000
    assert "no cell" not in output
    assert "CAD/MCNP mismatching pixels" in output


def test_registry_is_found_only_when_unambiguous(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from radmodeling.cli import app

    runner = CliRunner()

    def run(*args):
        return runner.invoke(app, [str(a) for a in args])

    model = tmp_path / "model"
    model.mkdir()
    step = models.write_cad(model / "machine.step")
    monkeypatch.chdir(tmp_path)  # the current folder is searched too
    result = run("check", step)
    assert result.exit_code == 1
    assert "no *_registry.csv found" in result.output

    assert run("init", step).exit_code == 0
    registry = model / "machine_registry.csv"
    # init does not overwrite the model being simplified
    result = run("init", step, "-r", tmp_path / "new_registry.csv")
    assert result.exit_code == 1
    assert "machine_simplified.step already exists" in result.output.replace("\n", "")
    assert run("check", step).exit_code == 0

    (model / "other_registry.csv").write_bytes(registry.read_bytes())
    result = run("check", step)
    assert result.exit_code == 1
    assert "several registries found" in result.output
    assert run("check", step, "-r", registry).exit_code == 0

    # --no-registry leaves the registry untouched
    exported = tmp_path / "export.step"
    assert run("export", step, "-r", registry, "-o", exported).exit_code == 0
    mcnp = tmp_path / "model.mcnp"
    mcnp.write_text(models.mcnp_text())
    before = registry.read_bytes()
    args = ("compare", mcnp, exported, "--points", "2000")
    result = run(*args, "--no-registry")
    assert result.exit_code == 0, result.output
    assert registry.read_bytes() == before

    # A registry that cannot be written (open in Excel) stops before any work
    registry.chmod(0o444)
    try:
        result = run("export", step, "-r", registry, "-o", tmp_path / "again.step")
    finally:
        registry.chmod(0o644)
    assert result.exit_code == 1
    assert "close it in Excel" in result.output
    assert not (tmp_path / "again.step").exists()


def test_spaceclaim_loaders(tmp_path, user_settings):
    import sys

    from typer.testing import CliRunner

    from radmodeling.cli import app

    result = CliRunner().invoke(app, ["spaceclaim-loaders", str(tmp_path)])
    assert result.exit_code == 0, result.output
    loaders = sorted(p.name for p in tmp_path.iterdir())
    assert "RadModeling - Check volumes.py" in loaders
    assert "RadModeling - Check model.py" in loaders
    text = (tmp_path / "RadModeling - Check volumes.py").read_text()
    assert text.startswith("# Python Script, API Version = V241")
    # 'Check model' runs radmod with the Python that wrote the loaders
    assert f'RADMODELING_PYTHON = r"{sys.executable}"' in text
    # The volume tools read the volume tolerance set with 'radmod config'
    assert f'RADMODELING_SETTINGS = r"{user_settings}"' in text
    folder = text.split('RADMODELING_SPACECLAIM = r"')[1].split('"')[0]
    for script in ("_rm.py", "check_volumes.py"):
        assert (Path(folder) / script).exists()


@pytest.mark.parametrize("module", ["radmod", "radmodeling"])
def test_python_dash_m_entry_point(module):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert f"python -m {module}" in result.stdout
    assert "spaceclaim-loaders" in result.stdout


def test_check_warns_when_the_document_is_newer(tmp_path):
    from typer.testing import CliRunner

    from radmodeling.cli import app

    runner = CliRunner()

    def run(*args):
        result = runner.invoke(app, [str(a) for a in args])
        assert result.exit_code == 0, result.output
        return result.output.replace("\n", " ")

    step = models.write_cad(tmp_path / "machine.step")
    run("init", step)
    simplified = tmp_path / "machine_simplified.step"
    assert "Warning" not in run("check", simplified)

    document = simplified.with_suffix(".scdocx")
    document.write_text("SpaceClaim document")
    saved = simplified.stat().st_mtime
    os.utime(document, (saved + 10, saved + 10))
    assert "was saved after machine_simplified.step" in run("check", simplified)

    os.utime(simplified, (saved + 20, saved + 20))
    assert "Warning" not in run("check", simplified)
