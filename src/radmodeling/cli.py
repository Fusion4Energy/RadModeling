"""The ``radmod`` command line interface."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn

import numpy as np
import typer
from rich.console import Console
from rich.markup import escape as esc
from rich.table import Table

if TYPE_CHECKING:
    from radmodeling.compare import SliceResult

app = typer.Typer(
    help="CAD preparation and CAD-to-MCNP quality assurance tools.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()

RegistryOption = Annotated[
    Path | None,
    typer.Option(
        "--registry",
        "-r",
        help="Component registry CSV. Default: the only *_registry.csv next to the "
        "input files or in the current folder.",
    ),
]


def _fail(message: str) -> NoReturn:
    console.print(f"[bold red]Error:[/] {esc(message)}")
    raise typer.Exit(1)


REGISTRY_SUFFIX = "_registry.csv"


def _registry_candidates(near: list[Path]) -> list[Path]:
    """The registries in the first folder that has any: those of the files, then cwd."""
    folders = list(dict.fromkeys(p.resolve().parent for p in near))
    folders.append(Path.cwd())
    for folder in folders:
        found = sorted(
            p for p in folder.iterdir() if p.name.lower().endswith(REGISTRY_SUFFIX)
        )
        if found:
            return found
    return []


def _registry(
    given: Path | None, near: list[Path], required: bool = True
) -> Path | None:
    """The registry given with -r, or else the only one found near the input files."""
    if given is not None:
        return given
    found = _registry_candidates(near)
    if len(found) == 1:
        console.print(f"Registry: {esc(str(found[0]))}")
        return found[0]
    if len(found) > 1:
        names = ", ".join(p.name for p in found)
        _fail(f"several registries found ({names}): choose one with -r")
    if required:
        _fail(f"no *{REGISTRY_SUFFIX} found next to the input files: give it with -r")
    return None


def _writable(path: Path) -> None:
    """Fail now rather than after the work if the registry cannot be saved."""
    try:
        with open(path, "a", encoding="utf-8-sig"):
            pass
    except PermissionError:
        _fail(f"cannot write {path}: close it in Excel (or any other program)")


def _ids(text: str | None) -> list[int] | None:
    if not text:
        return None
    return [int(t) for t in text.replace(",", " ").split()]


def _keys_text(keys) -> str:
    return ", ".join(map(str, keys))


# --- CAD workflow --------------------------------------------------------------------


@app.command()
def init(
    original: Annotated[Path, typer.Argument(help="Original STEP model.")],
    simplified: Annotated[
        Path | None,
        typer.Option(
            help="Tagged STEP to write, the one to simplify. "
            "Default: ORIGINAL_simplified.step."
        ),
    ] = None,
    registry: Annotated[
        Path | None, typer.Option("--registry", "-r", help="Registry CSV to write.")
    ] = None,
    per_solid: Annotated[
        bool, typer.Option(help="Make every solid its own component.")
    ] = False,
    force: Annotated[
        bool, typer.Option(help="Overwrite an existing registry and model.")
    ] = False,
) -> None:
    """Tag every part with a unique ID and create the registry.

    All the instances of a part share its tag and stay linked, and each instance gets
    its own row in the registry. Simplify the written STEP in any CAD program. The
    \\[rmID] tags in the component names must be kept; everything else (names,
    hierarchy) can change freely.
    """
    from radmodeling import workflow

    simplified = simplified or original.with_name(f"{original.stem}_simplified.step")
    registry = registry or original.with_name(f"{original.stem}_registry.csv")
    for path in (registry, simplified):
        if path.exists() and not force:
            _fail(f"{path} already exists. Use --force to overwrite it.")
    with console.status("Reading and tagging the model..."):
        result = workflow.init(original, simplified, registry, per_solid=per_solid)
    total = sum(c.original_volume_cm3 or 0 for c in result)
    console.print(
        f"[green]Tagged {len(result.ids)} components[/] with {len(result)} instances "
        f"({total:.6g} cm3).\n"
        f"  Model to simplify: {esc(str(simplified))}\n"
        f"  Registry:          {esc(str(registry))}\n"
        "Fill the 'material' column (and density or mass) of the registry, then "
        "simplify the model."
    )


@app.command()
def check(
    simplified: Annotated[Path, typer.Argument(help="Simplified STEP model.")],
    registry: RegistryOption = None,
    tolerance: Annotated[
        float, typer.Option(help="Maximum volume difference allowed [%].")
    ] = 1.0,
    highlight: Annotated[
        Path | None,
        typer.Option(
            help="Write the IDs with problems as JSON for the CAD adapters.",
            hidden=True,
        ),
    ] = None,
) -> None:
    """Compare a simplified model with the registry and diagnose its geometry.

    Updates the simplified volumes in the registry.
    """
    from radmodeling import workflow
    from radmodeling.registry import Registry

    registry = _registry(registry, [simplified])
    assert registry is not None
    _writable(registry)
    reg = Registry.load(registry)
    with console.status("Analysing the simplified model..."):
        result = workflow.check(simplified, reg, tolerance)
    reg.save(registry)

    exceeding = result.exceeding()
    matching = result.matching
    # Component IDs for the CAD adapters, which cannot tell instances apart
    issues = {
        "volume": sorted({c.id for c in exceeding}),
        "unconvertible": result.with_issue(lambda r: r.unconvertible_surfaces),
        "torus": result.with_issue(lambda r: r.has_torus),
        "invalid": result.with_issue(lambda r: not r.valid),
        "deleted": sorted({k.id for k in matching.missing}),
        "extra": sorted({i.id for i in matching.extra if i.id is not None}),
        "moved": sorted({k.id for k in matching.moved}),
        "new": sorted({i.id for i in matching.new if i.id is not None}),
    }

    table = Table(title=f"Components beyond ±{tolerance:g}% volume", show_lines=False)
    for column in ("ID", "Name", "Original cm3", "Simplified cm3", "Diff %"):
        table.add_column(column, justify="right" if "cm3" in column else "left")
    for c in sorted(exceeding, key=lambda c: -abs(c.volume_diff_pct or 0))[:30]:
        table.add_row(
            str(c.key),
            esc(c.name),
            f"{c.original_volume_cm3:.6g}",
            f"{c.simplified_volume_cm3:.6g}",
            f"{c.volume_diff_pct:+.2f}",
        )
    if exceeding:
        console.print(table)
        if len(exceeding) > 30:
            console.print(f"... and {len(exceeding) - 30} more (see the registry)")

    def line(label: str, items: list, hint: str = "") -> None:
        style = "green" if not items else "yellow"
        shown = _keys_text(items[:20]) + (" ..." if len(items) > 20 else "")
        console.print(
            f"[{style}]{label}: {len(items)}[/] {shown} {hint if items else ''}"
        )

    found = len(matching.found)
    console.print(
        f"Instances found: {found} of {len(reg)} in the registry "
        f"({len(reg.ids)} components)"
    )
    line("Instances beyond volume tolerance", [c.key for c in exceeding])
    line(
        "Surfaces a CSG converter cannot translate",
        issues["unconvertible"],
        "(splines, offsets...)",
    )
    line("With tori", issues["torus"], "(elbows: the 'Replace elbows' SpaceClaim tool)")
    line("Invalid solids", issues["invalid"])
    line("Deleted instances", matching.missing)
    line(
        "Extra instances",
        sorted(i.id for i in matching.extra if i.id is not None),
        "(copies: they become new instances on export)",
    )
    line("Moved instances", matching.moved, "(further than their own size)")
    line(
        "New components",
        issues["new"],
        "(tags not in the registry yet: added on export)",
    )
    untagged = [i.name for i in matching.untagged]
    console.print(
        f"[{'green' if not untagged else 'yellow'}]Untagged parts: "
        f"{len(untagged)}[/] "
        + esc(", ".join(untagged[:10]))
        + (" (tag them: the 'Tag new components' SpaceClaim tool)" if untagged else "")
    )
    if highlight:
        highlight.write_text(json.dumps(issues, indent=1))


@app.command()
def export(
    simplified: Annotated[Path, typer.Argument(help="Simplified STEP model.")],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="STEP file to convert to MCNP. Default: NAME_for_conversion.step "
            "next to NAME_registry.csv.",
        ),
    ] = None,
    registry: RegistryOption = None,
) -> None:
    """Write the STEP file for the CAD-to-MCNP converter.

    Solids are written one per product, sorted by component ID and instance, and
    named with both ('\\[rm12.3] Bolt'). The converter copies the names into the cell
    comments, which is how 'compare', 'materials' and 'slice' find each component's
    cells.
    """
    from radmodeling import workflow
    from radmodeling.registry import Registry

    registry = _registry(registry, [simplified])
    assert registry is not None
    _writable(registry)
    reg = Registry.load(registry)
    if output is None:
        base = registry.name[: -len(REGISTRY_SUFFIX)]
        output = registry.with_name(f"{base}_for_conversion.step")
    with console.status("Exporting..."):
        result = workflow.export(simplified, reg, output)
    reg.save(registry)
    keys = result.keys
    console.print(
        f"[green]Exported {len(keys)} solids[/] of {len(set(keys))} instances to "
        f"{esc(str(output))}"
    )
    if result.added:
        console.print(
            f"[yellow]{len(result.added)} new bodies were added to the registry with "
            f"status 'new':[/] {_keys_text(result.added)}. Check their material."
        )
    if result.untagged:
        ids = sorted({k.id for k in result.untagged})
        console.print(
            f"[yellow]{len(result.untagged)} of them were untagged parts[/] (IDs "
            f"{_keys_text(ids)}). The CAD model does not have these tags, so the next "
            "export would add them again: write them in the CAD names (the 'new' "
            "rows of the registry give each part's name and position). Next time, "
            "tag new parts before exporting with the 'Tag new components' tool."
        )


# --- MCNP ----------------------------------------------------------------------------


@app.command()
def materials(
    mcnp: Annotated[Path, typer.Argument(help="MCNP input from the converter.")],
    library: Annotated[
        Path, typer.Option("--library", "-l", help="Material library TOML.")
    ],
    registry: RegistryOption = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="MCNP input to write.")
    ] = None,
    dcf: Annotated[
        bool,
        typer.Option(help="Apply density correction factors from the MCNP volumes."),
    ] = True,
    dcf_min: Annotated[
        float,
        typer.Option(help="Smallest density correction applied [%]."),
    ] = 0.1,
) -> None:
    """Assign materials and densities (with DCF) to the tagged cells.

    Density correction factors closer to 1 than --dcf-min, or than three times their
    own uncertainty, are not applied: they cannot be told apart from 1.
    """
    from radmodeling.mcnp.geometry import read_model
    from radmodeling.mcnp.materials import (
        AssignmentError,
        MaterialLibrary,
        assign_materials,
    )
    from radmodeling.registry import Registry

    output = output or mcnp.with_name(f"{mcnp.stem}_materials{mcnp.suffix}")
    registry = _registry(registry, [mcnp])
    assert registry is not None
    model = read_model(mcnp)
    try:
        report = assign_materials(
            model,
            Registry.load(registry),
            MaterialLibrary.load(library),
            use_dcf=dcf,
            dcf_min=dcf_min / 100,
        )
    except AssignmentError as exc:
        _fail(str(exc))
    model.save(str(output))

    table = Table(title="Materials")
    for column in ("Material", "MCNP", "Cells", "Mass \\[kg]"):
        table.add_column(column)
    for name, number in report.material_numbers.items():
        table.add_row(
            esc(name),
            f"m{number}",
            str(report.cells_per_material.get(name, 0)),
            f"{report.mass_per_material_g.get(name, 0) / 1000:.4g}",
        )
    console.print(table)
    console.print(f"Void components: {report.void_components}")
    if report.missing_cards:
        names = {number: name for name, number in report.material_numbers.items()}
        missing = ", ".join(f"m{n} ({names[n]})" for n in report.missing_cards)
        console.print(f"[yellow]Material cards to add to the input:[/] {esc(missing)}")
    if dcf:
        console.print(
            f"Density correction factors applied: {len(report.dcf_applied)}; "
            f"negligible, left at 1: {len(report.dcf_negligible)} (closer to 1 than "
            f"{dcf_min:g} % or three times their uncertainty)"
        )
    for key, value in report.dcf_out_of_range:
        console.print(f"[yellow]Component {key}: unusual DCF {value:.3f}[/]")
    console.print(f"[green]Written[/] {esc(str(output))}")


@app.command()
def compare(
    step: Annotated[Path, typer.Argument(help="STEP written by 'export'.")],
    mcnp: Annotated[Path, typer.Argument(help="MCNP input converted from it.")],
    registry: Annotated[
        Path | None,
        typer.Option(
            "--registry",
            "-r",
            help="Registry to update with the MCNP volumes. Default: the only "
            "*_registry.csv next to the input files or in the current folder.",
        ),
    ] = None,
    no_registry: Annotated[
        bool, typer.Option("--no-registry", help="Do not update any registry.")
    ] = False,
    points: Annotated[int, typer.Option(help="Sample points per component.")] = 100_000,
    components: Annotated[
        str | None,
        typer.Option(help="Only these components, e.g. '3,7.2' (7.2: instance 2)."),
    ] = None,
    report: Annotated[
        Path | None, typer.Option(help="Write the full results as CSV.")
    ] = None,
    threshold: Annotated[
        float, typer.Option(help="Discrepancy reported as a problem [%].")
    ] = 1.0,
    seed: Annotated[int | None, typer.Option(help="Random seed.")] = None,
) -> None:
    """Compare every instance's CAD solids with its MCNP cells by point sampling.

    Gives the MCNP volume of each instance (for the density correction factors) and
    the volume where CAD and MCNP disagree.
    """
    from radmodeling.compare import compare_component
    from radmodeling.mcnp.geometry import Geometry
    from radmodeling.mcnp.tags import cells_by_instance
    from radmodeling.registry import Key, Registry
    from radmodeling.workflow import exported_solids

    if not no_registry:
        registry = _registry(registry, [step, mcnp], required=False)
        if registry is not None:
            _writable(registry)
        else:
            console.print(
                "[yellow]No registry found: MCNP volumes will not be saved[/]"
            )
    with console.status("Reading the models..."):
        try:
            solids = exported_solids(step)
        except ValueError as exc:
            _fail(str(exc))
        geometry = Geometry.from_file(mcnp)
    cells = cells_by_instance(geometry.model)
    if not cells:
        _fail(
            f"no cell comment of {mcnp} has an '[rmID.instance]' tag: convert "
            f"{step} keeping the solid names as cell comments"
        )
    orphans = sorted(set(cells) - set(solids))
    if orphans:
        console.print(
            f"[yellow]Cells tagged with instances not in {esc(str(step))}:[/] "
            + _keys_text(orphans)
        )
    selected = sorted(solids)
    if components:
        wanted = components.replace(",", " ").split()
        ids = {int(t) for t in wanted if "." not in t}
        keys = {Key.parse(t) for t in wanted if "." in t}
        selected = [k for k in selected if k.id in ids or k in keys]
    rng = np.random.default_rng(seed)

    results = []
    with console.status("") as status:
        for i, key in enumerate(selected, 1):
            status.update(f"Component {key} ({i}/{len(selected)})")
            results.append(
                compare_component(
                    key,
                    solids[key],
                    cells.get(key, []),
                    geometry,
                    n_points=points,
                    rng=rng,
                )
            )

    problems = [
        r
        for r in results
        if r.error or r.touches_sampling_box or not r.discrepancy_pct <= threshold
    ]
    table = Table(title=f"Instances with problems (discrepancy > {threshold:g}%)")
    for column in ("ID", "Cells", "CAD cm3", "MCNP cm3", "Discrepancy %", "Note"):
        table.add_column(column)
    for r in sorted(problems, key=lambda r: -np.nan_to_num(r.discrepancy_pct, nan=1e9)):
        note = r.error or (
            "no tagged cells"
            if not r.cells
            else "cells exceed the sampled box"
            if r.touches_sampling_box
            else ""
        )
        table.add_row(
            str(r.key),
            _cells_text(r.cells),
            f"{r.cad_volume_cm3:.6g}",
            f"{r.mcnp_volume_cm3:.6g} ± {r.mcnp_volume_std_cm3:.2g}",
            f"{r.discrepancy_pct:.3g}",
            note,
        )
    if problems:
        console.print(table)
    good = len(results) - len(problems)
    console.print(f"[green]{good} instances agree[/], {len(problems)} with problems.")

    if registry and not no_registry:
        reg = Registry.load(registry)
        for r in results:
            if r.key in reg and not r.error:
                reg[r.key].mcnp_volume_cm3 = r.mcnp_volume_cm3
                reg[r.key].mcnp_volume_std_cm3 = r.mcnp_volume_std_cm3
        reg.save(registry)
        console.print(f"MCNP volumes saved in {esc(str(registry))} (used for the DCF).")
    if report:
        with open(report, "w", encoding="utf-8", newline="") as outfile:
            writer = csv.writer(outfile)
            writer.writerow(
                [
                    "component_id",
                    "instance",
                    "cells",
                    "cad_volume_cm3",
                    "mcnp_volume_cm3",
                    "mcnp_volume_std_cm3",
                    "cad_only_cm3",
                    "mcnp_only_cm3",
                    "discrepancy_pct",
                    "touches_sampling_box",
                    "error",
                ]
            )
            for r in results:
                writer.writerow(
                    [
                        r.key.id,
                        r.key.instance,
                        " ".join(map(str, r.cells)),
                        r.cad_volume_cm3,
                        r.mcnp_volume_cm3,
                        r.mcnp_volume_std_cm3,
                        r.cad_only_cm3,
                        r.mcnp_only_cm3,
                        r.discrepancy_pct,
                        r.touches_sampling_box,
                        r.error,
                    ]
                )
        console.print(f"Results written to {esc(str(report))}")


def _cells_text(cells: list[int]) -> str:
    if not cells:
        return "-"
    if len(cells) > 3 and cells == list(range(cells[0], cells[-1] + 1)):
        return f"{cells[0]}-{cells[-1]}"
    return " ".join(map(str, cells))


@app.command(name="slice")
def slice_(
    mcnp: Annotated[Path, typer.Argument(help="MCNP input.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="PNG to write.")],
    normal: Annotated[
        str, typer.Option(help="'x', 'y', 'z' or a vector such as '0,1,1'.")
    ] = "z",
    origin: Annotated[
        str | None, typer.Option(help="Centre of the slice 'x,y,z' [cm].")
    ] = None,
    width: Annotated[float | None, typer.Option(help="Width [cm].")] = None,
    height: Annotated[float | None, typer.Option(help="Height [cm].")] = None,
    resolution: Annotated[int, typer.Option(help="Pixels along the width.")] = 800,
    step: Annotated[
        Path | None,
        typer.Option(
            help="STEP model to overlay. With the one from 'export', CAD/MCNP "
            "mismatches are highlighted."
        ),
    ] = None,
    library: Annotated[
        Path | None, typer.Option("--library", "-l", help="For material names.")
    ] = None,
) -> None:
    """Plot a slice of the MCNP geometry, optionally overlaid with the CAD.

    Without --origin/--width the slice covers the CAD model (if given) or must be set.
    """
    from radmodeling.compare import Plane
    from radmodeling.plot import render_slice
    from radmodeling.slices import load_scene, make_slice

    with console.status("Reading the models..."):
        scene = load_scene(mcnp, step, _library_names(library))
    centre, extent = None, None
    bounds = scene.bounds_cm()
    if bounds is not None:
        lo, hi = bounds
        centre, extent = (lo + hi) / 2, (hi - lo) * 1.05
    if origin:
        centre = np.array([float(v) for v in origin.split(",")])
    if centre is None:
        _fail("give --origin (or a --step model to centre the slice on)")

    if normal.lower() in ("x", "y", "z"):
        axes = {"x": (1, 2), "y": (0, 2), "z": (0, 1)}[normal.lower()]
        w = width or (extent[axes[0]] if extent is not None else None)
        h = height or (extent[axes[1]] if extent is not None else None)
        if w is None:
            _fail("give --width")
        plane = Plane.from_basis(centre, normal, w, h)
    else:
        w = width or (float(np.linalg.norm(extent)) if extent is not None else None)
        if w is None:
            _fail("give --width")
        vector = [float(v) for v in normal.split(",")]
        plane = Plane.from_normal(centre, vector, w, height)

    with console.status("Slicing..."):
        item = make_slice(scene, plane, resolution)
    render_slice(item.result, output, scene.slots, scene.material_names, item.outlines)
    console.print(f"[green]Written[/] {esc(str(output))}")
    _print_slice_problems(item.result)


def _library_names(library: Path | None) -> dict[int, str]:
    """Material names of the library, by MCNP material number."""
    if library is None:
        return {}
    from radmodeling.mcnp.materials import MaterialLibrary

    return {
        m.number: f"{m.name} (m{m.number})"
        for m in MaterialLibrary.load(library).materials
        if m.number is not None
    }


def _print_slice_problems(result: SliceResult) -> None:
    if result.lost_pixels:
        lost = result.lost_pixels
        console.print(f"[yellow]{lost} pixels are in no cell (undefined geometry)[/]")
    if result.overlap_pixels:
        console.print(
            f"[yellow]{result.overlap_pixels} pixels are in more than one cell[/]"
        )
    if result.mismatch_pixels is not None:
        console.print(f"CAD/MCNP mismatching pixels: {result.mismatch_pixels}")


@app.command()
def atlas(
    mcnp: Annotated[Path, typer.Argument(help="MCNP input.")],
    step: Annotated[
        Path,
        typer.Option(
            help="STEP model to overlay. With the one from 'export', CAD/MCNP "
            "mismatches are highlighted."
        ),
    ],
    output: Annotated[Path, typer.Option("--output", "-o", help="PDF to write.")],
    planes: Annotated[
        str,
        typer.Option(help="Evenly spaced planes normal to each axis, e.g. 'x:3,z:6'."),
    ] = "x:3,y:3,z:3",
    report: Annotated[
        Path | None,
        typer.Option(help="Report CSV of 'compare': adds a slice per worst instance."),
    ] = None,
    worst: Annotated[
        int, typer.Option(help="Maximum number of instances sliced from the report.")
    ] = 20,
    threshold: Annotated[
        float, typer.Option(help="Discrepancy that makes an instance a problem [%].")
    ] = 1.0,
    resolution: Annotated[int, typer.Option(help="Pixels along the width.")] = 400,
    library: Annotated[
        Path | None, typer.Option("--library", "-l", help="For material names.")
    ] = None,
) -> None:
    """Write a PDF atlas of slices: across the whole model and through the worst
    instances of a 'compare' report.

    The first pages summarise the pixels in no cell, in several cells and where CAD
    and MCNP disagree for every slice. Material colours are the same on all pages.
    """
    from radmodeling.plot import write_atlas
    from radmodeling.slices import (
        instance_plane,
        load_scene,
        make_slice,
        overview_planes,
        parse_plane_counts,
        read_problems,
    )

    try:
        counts = parse_plane_counts(planes)
    except ValueError as exc:
        _fail(str(exc))
    with console.status("Reading the models..."):
        scene = load_scene(mcnp, step, _library_names(library))
    bounds = scene.bounds_cm()
    if bounds is None:
        _fail(f"{step} has no solids")
    todo = overview_planes(*bounds, counts)

    if report:
        if not scene.instances:
            _fail(f"--report needs the STEP file written by 'export', not {step}")
        problems = read_problems(report, threshold)
        missing = [p.key for p in problems if p.key not in scene.instances]
        if missing:
            console.print(
                f"[yellow]Instances of the report not in {esc(str(step))}:[/] "
                + _keys_text(missing)
            )
        problems = [p for p in problems if p.key in scene.instances]
        for problem in problems[:worst]:
            instance = scene.instances[problem.key]
            title = f"{problem.key} {instance.name}: discrepancy "
            title += f"{problem.discrepancy_pct:.3g} %"
            if problem.note:
                title += f" ({problem.note})"
            todo.append((title, instance_plane(instance.solids)))
        if len(problems) > worst:
            console.print(
                f"[yellow]{len(problems) - worst} more problem instances[/] were left "
                "out (see --worst)"
            )
    if not todo:
        _fail("no slices to draw: give --planes or a --report with problems")

    slices = []
    with console.status("") as status:
        for i, (title, plane) in enumerate(todo, 1):
            status.update(f"Slice {i}/{len(todo)}: {esc(title)}")
            slices.append(make_slice(scene, plane, resolution, title))
    header = [f"MCNP input: {mcnp}", f"CAD: {step}"]
    if report:
        header.append(f"Compare report: {report} (threshold {threshold:g} %)")
    with console.status("Writing the PDF..."):
        write_atlas(slices, output, scene.slots, scene.material_names, header)

    bad = [s for s in slices if s.result.lost_pixels or s.result.overlap_pixels]
    bad += [s for s in slices if s.result.mismatch_pixels and s not in bad]
    console.print(
        f"[green]Written {len(slices)} slices[/] to {esc(str(output))}. "
        f"Slices with problems: {len(bad)}"
    )


# --- geometry tools ------------------------------------------------------------------


@app.command()
def fmesh(
    step: Annotated[Path, typer.Argument(help="STEP with one box per mesh tally.")],
    size: Annotated[float | None, typer.Option(help="Voxel size [cm].")] = None,
    bins: Annotated[
        str | None, typer.Option(help="Voxels per axis instead, e.g. '10,10,5'.")
    ] = None,
    tally: Annotated[int, typer.Option(help="Number of the first tally.")] = 14,
    particle: Annotated[str, typer.Option(help="Particle designator.")] = "n",
    transform: Annotated[
        int, typer.Option(help="Number of the first TR card for rotated boxes.")
    ] = 900,
) -> None:
    """Print FMESH cards for the boxes of a STEP file (any orientation)."""
    from radmodeling.cad.step import read_step
    from radmodeling.fmesh import box_from_solid, comment_line, fmesh_card

    if (size is None) == (bins is None):
        _fail("give either --size or --bins")
    counts = _ids(bins)
    if counts is not None and len(counts) != 3:
        _fail("--bins needs three values")
    # The cards go to stdout, ready to redirect into an input; messages to stderr
    messages = Console(stderr=True)
    number, tr = tally, transform
    for part in read_step(step):
        for solid in part.solids:
            try:
                box = box_from_solid(solid)
            except ValueError as exc:
                messages.print(f"[yellow]Skipping {esc(part.name)}: {esc(str(exc))}[/]")
                continue
            try:
                card = fmesh_card(
                    box, number, particle, size, tuple(counts) if counts else None, tr
                )
            except ValueError as exc:
                messages.print(f"[bold red]Error:[/] {esc(str(exc))}")
                raise typer.Exit(1) from None
            print(comment_line(part.name))
            print(card, end="")
            number += 10
            tr += 0 if box.axis_aligned else 1


# --- SpaceClaim ----------------------------------------------------------------------

LOADER = """# Python Script, API Version = {api}
# RadModeling loader for '{tool}'. Publish it once as a SpaceClaim tool: it runs the
# tool from the radmodeling installation, so updating radmodeling updates the tool.
import os
import traceback

RADMODELING_SPACECLAIM = r"{folder}"
RADMODELING_PYTHON = r"{python}"  # runs radmod for the tools that need it
try:
    execfile(os.path.join(RADMODELING_SPACECLAIM, "_rm.py"))
    execfile(os.path.join(RADMODELING_SPACECLAIM, "{script}"))
except Exception:
    # tools published to the ribbon fail silently: show the error
    import clr

    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import MessageBox

    MessageBox.Show("RadModeling '{tool}' failed:\\n\\n" + traceback.format_exc())
"""


@app.command(name="spaceclaim-loaders")
def spaceclaim_loaders(
    output: Annotated[Path, typer.Argument(help="Folder for the loader scripts.")],
    api_version: Annotated[
        str, typer.Option(help="SpaceClaim scripting API version.")
    ] = "V241",
) -> None:
    """Write the loader scripts to publish once as SpaceClaim tools.

    Each loader runs its tool from this radmodeling installation. With an editable
    install (pip install -e) every 'git pull' updates the tools in SpaceClaim with no
    need to publish them again.
    """
    import sys

    import radmodeling

    folder = Path(radmodeling.__file__).resolve().parent / "spaceclaim"
    output.mkdir(parents=True, exist_ok=True)
    for script in sorted(folder.glob("*.py")):
        if script.name.startswith("_"):
            continue
        tool = script.stem.replace("_", " ").capitalize()
        path = output / f"RadModeling - {tool}.py"
        path.write_text(
            LOADER.format(
                api=api_version,
                tool=tool,
                folder=folder,
                python=sys.executable,
                script=script.name,
            ),
            encoding="utf-8",
        )
        console.print(f"Written {esc(str(path))}")
    console.print(
        "In SpaceClaim, open each loader in the script editor and publish it as a "
        "tool (Publish > Publish as tool). This is needed only once."
    )


if __name__ == "__main__":
    app()
