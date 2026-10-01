import math
from pathlib import Path

import migjorn
import models
import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCP.gp import gp_Pnt

from radmodeling import workflow
from radmodeling.cad.step import Part, read_step, write_step
from radmodeling.cellmap import CellMap
from radmodeling.compare import compare_component
from radmodeling.mcnp.geometry import Geometry
from radmodeling.mcnp.materials import (
    AssignmentError,
    MaterialLibrary,
    assign_materials,
)
from radmodeling.registry import Component, Registry, find_id, tag_name


def test_tags():
    assert tag_name("Pump [rm:3]", 7) == "Pump [rm:7]"
    assert find_id(["Root [rm:1]", "Body"]) == 1
    assert find_id(["Root [rm:1]", "Child [rm:2]", "Solid1"]) == 2
    assert find_id(["Root", "x"]) is None


def test_registry_reads_excel_european_csv(tmp_path):
    path = tmp_path / "registry.csv"
    path.write_bytes(
        "id;name;material;density_g_cm3;mass_g;original_volume_cm3\n"
        "1;Válvula;SS316L;7,93;;10,5\n"
        "2;Bomba;Tungsten;;193;10\n".encode("cp1252")
    )
    registry = Registry.load(path)
    assert registry[1].name == "Válvula"
    assert registry[1].density == pytest.approx(7.93)
    assert registry[2].density == pytest.approx(19.3)

    registry.save(path)
    again = Registry.load(path)
    assert again[1].name == "Válvula"
    assert again[1].original_volume_cm3 == pytest.approx(10.5)


def simplify(tagged, output):
    """Mimic a CAD session: move and rename things, shrink the ball, add a part."""
    parts = []
    for part in read_step(tagged):
        if "Ball" in part.name:
            ball = BRepPrimAPI_MakeSphere(gp_Pnt(50, 50, 100), 24).Shape()
            parts.append(Part("Simplified sphere [rm:3]", ("New", "Misc"), ball))
        else:
            parts.append(Part(part.name, ("New",), part.shape))
    parts.append(Part("Shield", ("New",), models.cylinder(300, 0, 0, 10, 10)))
    write_step(parts, output)


def test_init_check_export(tmp_path, cad_model):
    tagged = tmp_path / "tagged.step"
    registry_csv = tmp_path / "registry.csv"
    registry = workflow.init(cad_model, tagged, registry_csv)
    assert [c.name for c in registry] == ["Block", "Pipe", "Ball", "Bolts"]
    assert registry[4].original_solids == 2
    assert registry[2].path == "Machine / Piping"
    assert registry[3].original_volume_cm3 == pytest.approx(4 / 3 * math.pi * 2.5**3)

    simplified = tmp_path / "simplified.step"
    simplify(tagged, simplified)
    result = workflow.check(simplified, Registry.load(registry_csv), tolerance_pct=1)
    assert [c.id for c in result.exceeding()] == [3]
    assert result.registry[3].volume_diff_pct == pytest.approx(
        (24**3 / 25**3 - 1) * 100, rel=1e-4
    )
    assert [p.name for p in result.untagged] == ["Shield"]
    assert result.missing_ids == []

    registry = Registry.load(registry_csv)
    output = tmp_path / "export.step"
    cell_map, added = workflow.export(simplified, registry, output, tmp_path / "c.csv")
    assert added == [5]
    assert registry[5].status == "new"
    assert cell_map.component_ids == [1, 2, 3, 4, 4, 5]
    assert CellMap.load(tmp_path / "c.csv").component_ids == cell_map.component_ids
    # The exported file is flat and in cell order
    assert [p.name for p in read_step(output)] == [
        "Block [rm:1]",
        "Pipe [rm:2]",
        "Ball [rm:3]",
        "Bolts #1 [rm:4]",
        "Bolts #2 [rm:4]",
        "Shield [rm:5]",
    ]


def cell(model: migjorn.Model, number: int) -> migjorn.Cell:
    result = model.cell(number)
    assert result is not None
    return result


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
        CellMap([1, 2, 3, 4, 4]),
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
    assert "$ rm:2 Pipe DCF=1.0472" in cell(model, 2).text
    assert model.material(31) is not None

    # Running it again does not duplicate the comments
    assign_materials(
        model,
        make_registry(),
        CellMap([1, 2, 3, 4, 4]),
        MaterialLibrary.load(library_file),
    )
    assert cell(model, 2).text.count("rm:2") == 1


def test_assign_materials_reports_all_problems(library_file):
    registry = make_registry()
    registry[1].material = ""
    registry[2].material = "Unobtainium"
    with pytest.raises(AssignmentError) as error:
        assign_materials(
            migjorn.parse(models.mcnp_text()),
            registry,
            CellMap([1, 2, 3, 4, 4, 4, 4, 4, 4, 4]),  # more solids than cells
            MaterialLibrary.load(library_file),
        )
    message = str(error.value)
    assert "Component 1 (Block): no material" in message
    assert "Unobtainium" in message
    assert "not found in the MCNP input" in message


@pytest.mark.parametrize(("radius", "matches"), [(2.5, True), (2.6, False)])
def test_compare_component(radius, matches):
    geometry = Geometry(migjorn.parse(models.mcnp_text(ball_radius=radius)))
    result = compare_component(
        3,
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
        3, [models.ball()], [3], Geometry(migjorn.parse(text)), n_points=20_000
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
    output = run("check", tagged, "-r", registry, "--highlight", tmp_path / "h.json")
    assert "Beyond volume tolerance: 0" in output

    exported = tmp_path / "export.step"
    run("export", tagged, "-r", registry, "-o", exported)
    cells = tmp_path / "export_cells.csv"
    mcnp = tmp_path / "model.mcnp"
    mcnp.write_text(models.mcnp_text())

    output = run(
        "compare",
        exported,
        mcnp,
        "-m",
        cells,
        "-r",
        registry,
        "--seed",
        "1",
        "--points",
        "20000",
    )
    assert "4 components agree" in output
    assert Registry.load(registry)[3].dcf == pytest.approx(1, abs=0.01)

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


def test_spaceclaim_loaders(tmp_path):
    from typer.testing import CliRunner

    from radmodeling.cli import app

    result = CliRunner().invoke(app, ["spaceclaim-loaders", str(tmp_path)])
    assert result.exit_code == 0, result.output
    loaders = sorted(p.name for p in tmp_path.iterdir())
    assert "RadModeling - Check volumes.py" in loaders
    text = (tmp_path / "RadModeling - Check volumes.py").read_text()
    assert text.startswith("# Python Script, API Version = V241")
    folder = text.split('RADMODELING_SPACECLAIM = r"')[1].split('"')[0]
    for script in ("_rm.py", "check_volumes.py"):
        assert (Path(folder) / script).exists()
