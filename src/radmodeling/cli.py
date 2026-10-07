"""The ``radmod`` command line interface."""

from __future__ import annotations

import json
import math
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn

import numpy as np
import typer
from rich.console import Console
from rich.markup import escape as esc
from rich.table import Table

from radmodeling import settings

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


McnpArgument = Annotated[Path, typer.Argument(help="MCNP input.")]
ExportArgument = Annotated[Path, typer.Argument(help="STEP file written by 'export'.")]
PointsOption = Annotated[int, typer.Option(help="Sample points per instance.")]
SeedOption = Annotated[
    int | None, typer.Option(help="Random seed: the same seed gives the same results.")
]
JobsOption = Annotated[
    int,
    typer.Option(
        "--jobs",
        "-j",
        help="Worker processes, each with a copy of the model in memory. "
        "Default: one per CPU, at most 8.",
    ),
]
ComponentsOption = Annotated[
    str | None,
    typer.Option(help="Only these components, e.g. '3,7.2' (7.2: instance 2)."),
]
LibraryOption = Annotated[
    Path | None,
    typer.Option("--library", "-l", help="Material library TOML, for material names."),
]
ResolutionOption = Annotated[int, typer.Option(help="Pixels along the width.")]


def _output(help: str):
    return typer.Option("--output", "-o", help=help)


def _fail(message: str) -> NoReturn:
    console.print(f"[bold red]Error:[/] {esc(message)}")
    raise typer.Exit(1)


def _settings() -> settings.Settings:
    try:
        return settings.load()
    except ValueError as exc:
        _fail(str(exc))


def _jobs(jobs: int) -> int:
    from radmodeling.parallel import default_jobs

    return jobs if jobs > 0 else default_jobs()


def _entropy(seed: int | None) -> int:
    """Seed of the random points of every instance: the given one or a random one."""
    return seed if seed is not None else int(np.random.default_rng().integers(2**63))


def _select(keys, components: str | None) -> list:
    from radmodeling.registry import select_keys

    if not components:
        return sorted(keys)
    try:
        return select_keys(keys, components)
    except ValueError as exc:
        _fail(str(exc))


def _with_suffix(path: Path, suffix: str) -> Path:
    """MODEL.ext -> MODEL_suffix: the name of the files written from an input."""
    return path.with_name(path.stem + suffix)


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


def _warn_if_stale(step: Path) -> None:
    """Warn when the CAD document the STEP was saved from has changed since."""
    document = step.with_suffix(".scdocx")
    if (
        step.exists()
        and document.exists()
        and document.stat().st_mtime > step.stat().st_mtime
    ):
        console.print(
            f"[yellow]Warning:[/] {esc(document.name)} was saved after "
            f"{esc(step.name)}: save a STEP copy of it first, or this is an older "
            "version of the model."
        )


def _keys_text(keys) -> str:
    return ", ".join(map(str, keys))


# --- CAD workflow --------------------------------------------------------------------


@app.command()
def init(
    original: Annotated[Path, typer.Argument(help="Original STEP model.")],
    output: Annotated[
        Path | None,
        _output(
            "Tagged STEP to write, the one to simplify. "
            "Default: ORIGINAL_simplified.step."
        ),
    ] = None,
    registry: Annotated[
        Path | None,
        typer.Option(
            "--registry",
            "-r",
            help="Registry CSV to write. Default: ORIGINAL_registry.csv.",
        ),
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

    simplified = output or _with_suffix(original, "_simplified.step")
    registry = registry or _with_suffix(original, REGISTRY_SUFFIX)
    for path in (registry, simplified):
        if path.exists() and not force:
            _fail(f"{path} already exists. Use --force to overwrite it.")
    with console.status("Reading and tagging the model..."):
        result = workflow.init(original, simplified, per_solid=per_solid)
    result.save(registry)
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
    highlight: Annotated[
        Path | None,
        typer.Option(
            help="Write the IDs with problems and warnings as JSON for the CAD "
            "adapters.",
            hidden=True,
        ),
    ] = None,
) -> None:
    """Compare a simplified model with the registry and diagnose its geometry.

    Updates the simplified volumes in the registry. The volume tolerance is the
    volume-tolerance setting (radmod config).
    """
    from radmodeling import workflow
    from radmodeling.registry import Registry

    _warn_if_stale(simplified)
    tolerance = _settings().volume_tolerance
    registry = _registry(registry, [simplified])
    assert registry is not None
    _writable(registry)
    reg = Registry.load(registry)
    with console.status("Analysing the simplified model..."):
        result = workflow.check(simplified, reg, tolerance)
    reg.save(registry)

    exceeding = result.exceeding()
    matching = result.matching
    issues = result.issues()

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

    def line(label: str, items: list, hint: str = "", category: str = "") -> None:
        style = (
            "green"
            if not items
            else "yellow"
            if category in workflow.WARNINGS
            else "red"
        )
        shown = _keys_text(items[:20]) + (" ..." if len(items) > 20 else "")
        console.print(
            f"[{style}]{label}: {len(items)}[/] {shown} {hint if items else ''}"
        )

    found = len(matching.found)
    console.print(
        f"Instances found: {found} of {len(reg)} in the registry "
        f"({len(reg.ids)} components)"
    )
    line(
        f"Instances beyond the volume tolerance ({tolerance:g} %)",
        [c.key for c in exceeding],
    )
    line(
        "Surfaces a CSG converter cannot translate",
        issues["unconvertible"],
        "(splines, offsets...)",
    )
    line(
        "With tori",
        issues["torus"],
        "(elbows: the 'Replace elbows' SpaceClaim tool)",
        "torus",
    )
    line("Invalid solids", issues["invalid"])
    line("Deleted instances", matching.missing, category="deleted")
    line(
        "Extra instances",
        sorted(i.id for i in matching.extra if i.id is not None),
        "(copies: they become new instances on export)",
    )
    line("Moved instances", matching.moved, "(further than their own size)", "moved")
    line(
        "New components",
        issues["new"],
        "(tags not in the registry yet: added with status 'new', fill their material)",
        "new",
    )
    untagged = [i.name for i in matching.untagged]
    console.print(
        f"[{'green' if not untagged else 'red'}]Untagged parts: "
        f"{len(untagged)}[/] "
        + esc(", ".join(untagged[:10]))
        + (" (tag them: the 'Tag new components' SpaceClaim tool)" if untagged else "")
    )
    if highlight:
        severity = {
            "problems": {k: v for k, v in issues.items() if k not in workflow.WARNINGS},
            "warnings": {k: v for k, v in issues.items() if k in workflow.WARNINGS},
        }
        highlight.write_text(json.dumps(severity, indent=1))


@app.command()
def export(
    simplified: Annotated[Path, typer.Argument(help="Simplified STEP model.")],
    output: Annotated[
        Path | None,
        _output(
            "STEP file to convert to MCNP. Default: NAME_export.step next to "
            "NAME_registry.csv."
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

    _warn_if_stale(simplified)
    registry = _registry(registry, [simplified])
    assert registry is not None
    _writable(registry)
    reg = Registry.load(registry)
    if output is None:
        base = registry.name[: -len(REGISTRY_SUFFIX)]
        output = registry.with_name(f"{base}_export.step")
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
        Path | None, _output("MCNP input to write. Default: MODEL_materials.mcnp.")
    ] = None,
    dcf: Annotated[
        bool,
        typer.Option(help="Apply density correction factors from the MCNP volumes."),
    ] = True,
) -> None:
    """Assign materials and densities (with DCF) to the tagged cells.

    The density correction factors are the registry's 'dcf' column: written by
    'compare' (1 unless the MCNP volume is beyond the volume tolerance by more than
    three times its uncertainty) or edited by hand.
    """
    from radmodeling.mcnp.geometry import read_model
    from radmodeling.mcnp.materials import (
        AssignmentError,
        MaterialLibrary,
        assign_materials,
    )
    from radmodeling.registry import Registry

    output = output or _with_suffix(mcnp, f"_materials{mcnp.suffix}")
    registry = _registry(registry, [mcnp])
    assert registry is not None
    model = read_model(mcnp)
    try:
        report = assign_materials(
            model,
            Registry.load(registry),
            MaterialLibrary.load(library),
            use_dcf=dcf,
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
            "Density correction factors (registry 'dcf'), instances:\n"
            f"  {len(report.dcf_applied)} corrected to the CAD volume\n"
            f"  {len(report.dcf_negligible)} not corrected: MCNP volume within the "
            "volume tolerance, given the uncertainty of 'compare' (DCF 1)"
        )
        if report.dcf_manual:
            keys = ", ".join(map(str, report.dcf_manual))
            console.print(
                f"  {len(report.dcf_manual)} edited by hand (neither 1 nor the volume "
                f"ratio): {keys}"
            )
        if report.dcf_missing:
            console.print(
                f"  [yellow]{len(report.dcf_missing)} without factor, not corrected: "
                "run 'compare' to compute them[/]"
            )
    for key, value in report.dcf_out_of_range:
        console.print(f"[yellow]Component {key}: unusual DCF {value:.3f}[/]")
    console.print(f"[green]Written[/] {esc(str(output))}")


@app.command()
def compare(
    mcnp: Annotated[Path, typer.Argument(help="MCNP input from the converter.")],
    step: ExportArgument,
    registry: RegistryOption = None,
    no_registry: Annotated[
        bool, typer.Option("--no-registry", help="Do not update any registry.")
    ] = False,
    output: Annotated[
        Path | None,
        _output(
            "Report CSV with the full results. Default: MODEL_compare.csv, where "
            "'atlas' finds it (not written with --components)."
        ),
    ] = None,
    components: ComponentsOption = None,
    points: PointsOption = 100_000,
    seed: SeedOption = None,
    jobs: JobsOption = 0,
) -> None:
    """Compare every instance's CAD solids with its MCNP cells by point sampling.

    Gives the MCNP volume of each instance, the volume where CAD and MCNP disagree
    and the volume of overlapping cells. Instances beyond the max-discrepancy
    setting (radmod config) are reported as problems. The MCNP volumes and the
    density correction factors (1 within the volume-tolerance setting) are saved in
    the registry.
    """
    from radmodeling.compare import (
        Comparison,
        overlap_pairs,
        report_path,
        write_report,
    )
    from radmodeling.mcnp.geometry import Geometry
    from radmodeling.mcnp.tags import cells_by_instance
    from radmodeling.parallel import Workers
    from radmodeling.registry import Registry
    from radmodeling.workflow import (
        exported_solids,
        load_comparison,
        record_mcnp_volumes,
    )

    config = _settings()
    max_discrepancy = config.max_discrepancy
    if not no_registry:
        registry = _registry(registry, [mcnp, step], required=False)
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
    selected = _select(solids, components)
    entropy = _entropy(seed)
    comparison = Comparison(geometry, solids, cells, selected, points, entropy)
    load = partial(load_comparison, mcnp, step, selected, points, entropy)

    results = []
    with console.status("") as status, Workers(comparison, load, _jobs(jobs)) as pool:
        status.update(f"Comparing {len(selected)} instances...")
        for i, result in enumerate(pool.map(Comparison.compare, selected), 1):
            status.update(f"Compared {i}/{len(selected)} instances")
            results.append(result)

    problems = [r for r in results if r.exceeds(max_discrepancy)]
    table = Table(
        title=f"Instances with problems (discrepancy > {max_discrepancy:g} %)"
    )
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

    pairs = overlap_pairs(results)
    overlap_table = Table(title="Overlapping instances")
    for column in ("ID", "ID", "Overlap cm3"):
        overlap_table.add_column(column)
    for (a, b), (volume, std) in sorted(pairs.items(), key=lambda p: -p[1][0]):
        overlap_table.add_row(
            str(a), "itself" if a == b else str(b), f"{volume:.3g} ± {std:.2g}"
        )
    if pairs:
        console.print(overlap_table)

    overlapping = {k for pair in pairs for k in pair}
    bad = {r.key for r in problems} | overlapping
    console.print(
        f"[green]{len(results) - len(bad)} instances agree[/], "
        f"{len(problems)} with discrepancies, "
        f"[{'red' if pairs else 'green'}]{len(pairs)} overlapping pairs[/]."
    )

    if registry and not no_registry:
        reg = Registry.load(registry)
        record_mcnp_volumes(reg, results, config.volume_tolerance)
        reg.save(registry)
        console.print(
            f"MCNP volumes and DCFs (volume tolerance {config.volume_tolerance:g} %) "
            f"saved in {esc(str(registry))}."
        )
    if output is None and not components:
        output = report_path(mcnp)
    if output:
        write_report(results, output)
        console.print(f"Results written to {esc(str(output))}")


def _cells_text(cells: list[int]) -> str:
    if not cells:
        return "-"
    if len(cells) > 3 and cells == list(range(cells[0], cells[-1] + 1)):
        return f"{cells[0]}-{cells[-1]}"
    return " ".join(map(str, cells))


@app.command(name="slice")
def slice_(
    mcnp: McnpArgument,
    step: Annotated[
        Path | None,
        typer.Argument(
            help="STEP model to overlay. With the one from 'export', CAD/MCNP "
            "mismatches are highlighted."
        ),
    ] = None,
    output: Annotated[
        Path | None, _output("PNG to write. Default: MODEL_slice.png.")
    ] = None,
    normal: Annotated[
        str, typer.Option(help="'x', 'y', 'z' or a vector such as '0,1,1'.")
    ] = "z",
    origin: Annotated[
        str | None, typer.Option(help="Centre of the slice 'x,y,z' [cm].")
    ] = None,
    width: Annotated[float | None, typer.Option(help="Width [cm].")] = None,
    height: Annotated[float | None, typer.Option(help="Height [cm].")] = None,
    resolution: ResolutionOption = 800,
    library: LibraryOption = None,
) -> None:
    """Plot a slice of the MCNP geometry, optionally overlaid with the CAD.

    Without --origin/--width the slice covers the CAD model (if given) or must be set.
    """
    from radmodeling.compare import Plane
    from radmodeling.plot import render_slice
    from radmodeling.slices import load_scene, make_slice, zoom_slices

    output = output or _with_suffix(mcnp, "_slice.png")
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
        _fail("give --origin (or a STEP model to centre the slice on)")

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
        zoom_slices(scene, item, resolution, max_zooms=0)  # circles small problems
    render_slice(
        item.result,
        output,
        scene.slots,
        scene.material_names,
        item.outlines,
        markers=item.markers,
    )
    console.print(f"[green]Written[/] {esc(str(output))}")
    _print_slice_problems(item.result)


def _library_names(library: Path | None) -> dict[int, str]:
    """Material names of the library, by MCNP material number."""
    if library is None:
        return {}
    from radmodeling.mcnp.materials import MaterialLibrary

    return MaterialLibrary.load(library).names_by_number()


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
    mcnp: McnpArgument,
    step: Annotated[
        Path,
        typer.Argument(
            help="STEP model to overlay. With the one from 'export', the problems are "
            "searched in 3D and CAD/MCNP mismatches are highlighted."
        ),
    ],
    output: Annotated[
        Path | None, _output("PDF to write. Default: MODEL_atlas.pdf.")
    ] = None,
    planes: Annotated[
        str,
        typer.Option(help="Evenly spaced planes normal to each axis, e.g. 'x:3,z:6'."),
    ] = "x:3,y:3,z:3",
    report: Annotated[
        Path | None,
        typer.Option(
            help="Report CSV of 'compare' on this geometry: only its problem instances "
            "are searched again, to locate the problems. Default: MODEL_compare.csv "
            "for MODEL.mcnp or MODEL_*.mcnp, if there is one."
        ),
    ] = None,
    no_report: Annotated[
        bool,
        typer.Option(
            "--no-report", help="Search every instance, even with a report around."
        ),
    ] = False,
    components: ComponentsOption = None,
    worst: Annotated[
        int, typer.Option(help="Maximum number of problem instances sliced.")
    ] = 20,
    points: PointsOption = 100_000,
    resolution: ResolutionOption = 400,
    library: LibraryOption = None,
    seed: SeedOption = None,
    jobs: JobsOption = 0,
) -> None:
    """Write a PDF atlas of slices: across the whole model and through every problem.

    With the STEP file from 'export', every instance is sampled like 'compare' does
    and the worst problems (CAD/MCNP mismatches, overlapping cells) get a slice
    through them. Small clusters of problem pixels are circled and zoomed in on
    their own pages. The first pages summarise the pixels in no cell, in several
    cells and where CAD and MCNP disagree for every slice.
    """
    from radmodeling.compare import find_report, read_problems
    from radmodeling.parallel import Workers
    from radmodeling.plot import write_atlas
    from radmodeling.slices import (
        Atlas,
        load_atlas,
        load_scene,
        overview_planes,
        parse_plane_counts,
        problem_pages,
        scene_comparison,
    )

    max_discrepancy = _settings().max_discrepancy
    output = output or _with_suffix(mcnp, "_atlas.pdf")
    try:
        counts = parse_plane_counts(planes)
    except ValueError as exc:
        _fail(str(exc))
    names = _library_names(library)
    with console.status("Reading the models..."):
        scene = load_scene(mcnp, step, names)
    bounds = scene.bounds_cm()
    if bounds is None:
        _fail(f"{step} has no solids")
    todo = overview_planes(*bounds, counts)

    if (report or components) and not scene.compares:
        option = "--report" if report else "--components"
        _fail(f"{option} needs the STEP file written by 'export', not {step}")
    if no_report:
        report = None
    elif report is None and scene.compares:
        report = find_report(mcnp)
    keys = []
    if not scene.compares:
        console.print(
            f"[yellow]{esc(str(step))} is not from 'export' (or no cell is tagged): "
            "only the overview slices are drawn, without searching for problems[/]"
        )
    elif report:
        console.print(
            f"[yellow]Using {esc(str(report))}: only the instances it reports are "
            "searched. It must come from 'compare' on this geometry and STEP file: "
            "run 'compare' again after changing them, or use --no-report.[/]"
        )
        keys = [p.key for p in read_problems(report, max_discrepancy)]
        missing = [k for k in keys if k not in scene.instances]
        if missing:
            _fail(
                f"instances of {report} not in {step}: {_keys_text(missing)} "
                "(a report of another model?)"
            )
        keys = _select(keys, components)
    else:
        keys = _select(scene.instances, components)

    entropy = _entropy(seed)
    work = Atlas(scene, scene_comparison(scene, keys, points, entropy), resolution)
    load = partial(load_atlas, mcnp, step, names, keys, points, entropy, resolution)
    with Workers(work, load, _jobs(jobs)) as pool:
        if work.comparison is not None:
            results = []
            with console.status(f"Searching {len(keys)} instances...") as status:
                for i, result in enumerate(pool.map(Atlas.search, keys), 1):
                    status.update(f"Searched {i}/{len(keys)} instances")
                    results.append(result)
            pages, n_problems = problem_pages(scene, results, max_discrepancy, worst)
            console.print(f"Instances with problems: {n_problems}")
            if n_problems > worst:
                console.print(
                    f"[yellow]{n_problems - worst} more problem instances[/] were "
                    "left out (see --worst)"
                )
            todo += pages
        if not todo:
            _fail("no slices to draw: give --planes or a model from 'export'")

        slices = []
        with console.status(f"Slicing {len(todo)} planes...") as status:
            for i, item in enumerate(pool.map(Atlas.draw, todo), 1):
                status.update(f"Sliced {i}/{len(todo)} planes")
                slices.append(item)
    header = [f"MCNP input: {mcnp}", f"CAD: {step}"]
    if report:
        header.append(f"Problem instances from: {report}")
    header.append(f"Maximum discrepancy: {max_discrepancy:g} %")
    with console.status("Writing the PDF..."):
        write_atlas(slices, output, scene.slots, scene.material_names, header)

    bad = [s for s in slices if s.result.lost_pixels or s.result.overlap_pixels]
    bad += [s for s in slices if s.result.mismatch_pixels and s not in bad]
    zooms = sum(len(item.zooms) for item in slices)
    console.print(
        f"[green]Written {len(slices)} slices and {zooms} zooms[/] to "
        f"{esc(str(output))}. Slices with problems: {len(bad)}"
    )


# --- settings ------------------------------------------------------------------------


@app.command()
def config(
    name: Annotated[
        str | None, typer.Argument(help="Setting to show or change.")
    ] = None,
    value: Annotated[str | None, typer.Argument(help="Its new value.")] = None,
    unset: Annotated[
        bool, typer.Option("--unset", help="Restore the default of the setting.")
    ] = False,
) -> None:
    """Show or change your settings, kept for every model and SpaceClaim tool.

    'radmod config' lists them, 'radmod config NAME' shows one,
    'radmod config NAME VALUE' changes it and 'radmod config NAME --unset' restores
    its default.
    """
    if unset and (name is None or value is not None):
        _fail("--unset takes the name of a setting and no value")
    try:
        if unset:
            assert name is not None
            settings.unset(name)
        elif value is not None:
            assert name is not None
            settings.set_value(name, value)
        stored = settings.stored()
    except ValueError as exc:
        _fail(str(exc))
    if name is not None and name not in settings.SETTINGS:
        _fail(f"unknown setting '{name}' (known: {', '.join(settings.SETTINGS)})")
    if name is not None and not unset and value is None:
        print(f"{stored.get(name, settings.SETTINGS[name][0]):g}")
        return
    table = Table(title="Settings")
    for column in ("Name", "Value", "Description"):
        table.add_column(column)
    for key, (default, description) in settings.SETTINGS.items():
        shown = f"{stored[key]:g}" if key in stored else f"{default:g} (default)"
        if name is None or key == name:
            table.add_row(key, shown, description)
    console.print(table)
    console.print(f"Saved in {esc(str(settings.path()))}")


# --- geometry tools ------------------------------------------------------------------


@app.command(hidden=True)
def fmesh(
    step: Annotated[Path, typer.Argument(help="STEP with one box per mesh tally.")],
    size: Annotated[float, typer.Option(help="Voxel size [cm].")],
    output: Annotated[
        Path | None, _output("File for the FMESH cards. Default: STEP_fmesh.txt.")
    ] = None,
) -> None:
    """Write FMESH cards for the boxes of a STEP file (any orientation).

    Run by the SpaceClaim Fmesh tool on the selected bodies: tally numbers, particle
    and voxels are meant to be edited in the written cards.
    """
    from radmodeling.fmesh import fmesh_cards

    output = output or _with_suffix(step, "_fmesh.txt")
    try:
        cards = fmesh_cards(step, size)
    except ValueError as exc:
        _fail(str(exc))
    for name, why in cards.skipped:
        console.print(f"[yellow]Skipping {esc(name)}:[/] {esc(why)}")
    for number, name, counts in cards.tallies:
        console.print(
            f"FMESH{number} {esc(name)}: {' x '.join(map(str, counts))} = "
            f"{math.prod(counts)} voxels"
        )
    if not cards.tallies:
        _fail("no boxes found")
    output.write_text(cards.text, encoding="utf-8")
    console.print(f"Cards written to {esc(str(output))}")


# --- SpaceClaim ----------------------------------------------------------------------

LOADER = """# Python Script, API Version = {api}
# RadModeling loader for '{tool}'. Publish it once as a SpaceClaim tool: it runs the
# tool from the radmodeling installation, so updating radmodeling updates the tool.
import os
import traceback

RADMODELING_SPACECLAIM = r"{folder}"
RADMODELING_PYTHON = r"{python}"  # runs radmod for the tools that need it
RADMODELING_SETTINGS = r"{settings}"  # radmod config
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
                settings=settings.path(),
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
