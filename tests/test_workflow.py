import math
from pathlib import Path

import migjorn
import models
import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCP.gp import gp_Pnt

from radmodeling import workflow
from radmodeling.cad.occ import GeometryMap, bounding_box, volume_and_centroid
from radmodeling.cad.step import Part, read_step, write_step
from radmodeling.cellmap import CellMap
from radmodeling.compare import compare_component
from radmodeling.mcnp.geometry import Geometry
from radmodeling.mcnp.materials import (
    AssignmentError,
    MaterialLibrary,
    assign_materials,
)
from radmodeling.registry import Component, Key, Registry, find_id, tag_name


def test_tags():
    assert tag_name("Pump [rm3]", 7) == "Pump [rm7]"
    assert find_id(["Root [rm1]", "Body"]) == 1
    assert find_id(["Root [rm1]", "Child [rm2]", "Solid1"]) == 2
    assert find_id(["Root", "x"]) is None


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
    registry = workflow.init(cad_model, tagged, registry_csv)
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
    cell_map, added = workflow.export(simplified, registry, output, tmp_path / "c.csv")
    assert added == [(5, 1)]
    assert registry[5, 1].status == "new"
    assert [k.id for k in cell_map.keys] == [1, 2, 3, 4, 4, 5]
    assert CellMap.load(tmp_path / "c.csv").keys == cell_map.keys
    # The exported file is flat and in cell order
    assert [p.name for p in read_step(output)] == [
        "Block [rm1]",
        "Pipe [rm2]",
        "Ball [rm3]",
        "Bolts #1 [rm4]",
        "Bolts #2 [rm4]",
        "Shield [rm5]",
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
    registry = workflow.init(original, tagged, registry_csv)
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
    cell_map, added = workflow.export(simplified, registry, output, tmp_path / "c.csv")
    assert added == [(2, 7)]
    assert registry[2, 7].status == "new"
    assert registry[2, 7].material == "SS316L"
    assert [str(k) for k in cell_map.keys] == [
        "1.1", "2.1", "2.3", "2.4", "2.5", "2.6", "2.7"
    ]  # fmt: skip
    # Each exported solid is the instance its registry row describes
    for key, solids in workflow.exported_solids(output, cell_map).items():
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


def test_tags_left_only_on_bodies_are_found(tmp_path):
    """SpaceClaim may write a component as a bare solid that alone keeps the tag."""
    original = tmp_path / "pipe.step"
    write_step([Part("Pipe", ("Machine",), models.pipe())], original)
    tagged, registry_csv = tmp_path / "tagged.step", tmp_path / "registry.csv"
    registry = workflow.init(original, tagged, registry_csv)
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


def cell_map(ids: list[int]) -> CellMap:
    return CellMap([Key(i) for i in ids])


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
            ),
            Component(
                3, "Ball", material="tungsten", mass_g=1000, original_volume_cm3=50
            ),
            Component(4, "Bolts", material="Void"),
        ]
    )


def test_assign_materials(library_file):
    model = migjorn.parse(models.mcnp_text())
    report = assign_materials(
        model,
        make_registry(),
        cell_map([1, 2, 3, 4, 4]),
        MaterialLibrary.load(library_file),
    )
    assert cell(model, 1).material == 10
    assert cell(model, 1).density == pytest.approx(-7.93)
    assert cell(model, 2).density == pytest.approx(-7.93 * 157.08 / 150, rel=1e-5)
    assert cell(model, 3).material == 31  # auto-assigned after the highest number
    assert cell(model, 3).density == pytest.approx(-20)
    assert cell(model, 4).material == 0
    assert report.material_numbers == {"SS316L": 10, "Tungsten": 31}
    assert report.cards_added == [10, 31]
    assert "$ rm:2.1 Pipe DCF=1.0472" in cell(model, 2).text
    assert model.material(31) is not None

    # Running it again does not duplicate the comments
    assign_materials(
        model,
        make_registry(),
        cell_map([1, 2, 3, 4, 4]),
        MaterialLibrary.load(library_file),
    )
    assert cell(model, 2).text.count("rm:2") == 1


def test_assign_materials_reports_all_problems(library_file):
    registry = make_registry()
    registry[1, 1].material = ""
    registry[2, 1].material = "Unobtainium"
    with pytest.raises(AssignmentError) as error:
        assign_materials(
            migjorn.parse(models.mcnp_text()),
            registry,
            cell_map([1, 2, 3, 4, 4, 4, 4, 4, 4, 4]),  # more solids than cells
            MaterialLibrary.load(library_file),
        )
    message = str(error.value)
    assert "Component 1.1 (Block): no material" in message
    assert "Unobtainium" in message
    assert "not found in the MCNP input" in message


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


def test_compare_flags_cells_beyond_the_cad():
    text = models.mcnp_text().replace("11 s 5 5 10 2.5", "11 s 5 5 10 6")
    result = compare_component(
        Key(3), [models.ball()], [3], Geometry(migjorn.parse(text)), n_points=20_000
    )
    assert result.touches_sampling_box


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

    tagged = cad_model.with_name("machine_tagged.step")
    # The registry is found next to the model: no -r needed
    output = run("check", tagged, "--highlight", tmp_path / "h.json")
    assert f"Registry: {registry}" in output.replace("\n", "")
    assert "Instances beyond volume tolerance: 0" in output

    exported = tmp_path / "export.step"
    run("export", tagged, "-o", exported)
    cells = tmp_path / "export_cells.csv"
    mcnp = tmp_path / "model.mcnp"
    mcnp.write_text(models.mcnp_text())

    output = run(
        "compare",
        exported,
        mcnp,
        "-m",
        cells,
        "--seed",
        "1",
        "--points",
        "20000",
    )
    assert "4 instances agree" in output
    assert Registry.load(registry)[3, 1].dcf == pytest.approx(1, abs=0.01)

    run("materials", mcnp, "-r", registry, "-m", cells, "-l", library_file)
    result = migjorn.parse(mcnp.with_name("model_materials.mcnp").read_text())
    assert cell(result, 3).material == 31

    png = tmp_path / "slice.png"
    output = run(
        "slice",
        mcnp.with_name("model_materials.mcnp"),
        "-o",
        png,
        "--step",
        exported,
        "-m",
        cells,
        "--resolution",
        "200",
    )
    assert png.stat().st_size > 10_000
    assert "no cell" not in output


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
    assert run("check", step).exit_code == 0

    (model / "other_registry.csv").write_bytes(registry.read_bytes())
    result = run("check", step)
    assert result.exit_code == 1
    assert "several registries found" in result.output
    assert run("check", step, "-r", registry).exit_code == 0

    # --no-registry leaves the registry untouched
    exported, cells = tmp_path / "export.step", tmp_path / "export_cells.csv"
    assert run("export", step, "-r", registry, "-o", exported).exit_code == 0
    mcnp = tmp_path / "model.mcnp"
    mcnp.write_text(models.mcnp_text())
    before = registry.read_bytes()
    args = ("compare", exported, mcnp, "-m", cells, "--points", "2000")
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


def test_spaceclaim_loaders(tmp_path):
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
