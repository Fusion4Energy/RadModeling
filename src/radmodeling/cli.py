"""The ``radmod`` command line interface."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Annotated, NoReturn

import numpy as np
import typer
from rich.console import Console
from rich.markup import escape as esc
from rich.table import Table

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
CellMapOption = Annotated[
    Path, typer.Option("--cell-map", "-m", help="Cell map CSV written by 'export'.")
]
FirstCellOption = Annotated[
    int, typer.Option(help="Cell number given by the converter to the first solid.")
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
    tagged: Annotated[Path | None, typer.Option(help="Tagged STEP to write.")] = None,
    registry: Annotated[
        Path | None, typer.Option("--registry", "-r", help="Registry CSV to write.")
    ] = None,
    per_solid: Annotated[
        bool, typer.Option(help="Make every solid its own component.")
    ] = False,
    force: Annotated[
        bool, typer.Option(help="Overwrite an existing registry.")
    ] = False,
) -> None:
    """Tag every part with a unique ID and create the registry.

    All the instances of a part share its tag and stay linked, and each instance gets
    its own row in the registry. Simplify the tagged STEP in any CAD program. The
    '[rmID]' tags in the component names must be kept; everything else (names,
    hierarchy) can change freely.
    """
    from radmodeling import workflow

    tagged = tagged or original.with_name(f"{original.stem}_tagged.step")
    registry = registry or original.with_name(f"{original.stem}_registry.csv")
    if registry.exists() and not force:
        _fail(f"{registry} already exists. Use --force to overwrite it.")
    with console.status("Reading and tagging the model..."):
        result = workflow.init(original, tagged, registry, per_solid=per_solid)
    total = sum(c.original_volume_cm3 or 0 for c in result)
    console.print(
        f"[green]Tagged {len(result.ids)} components[/] with {len(result)} instances "
        f"({total:.6g} cm3).\n"
        f"  Tagged model: {esc(str(tagged))}\n  Registry:     {esc(str(registry))}\n"
        "Fill the 'material' column (and density or mass) of the registry, then "
        "simplify the tagged model."
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
        typer.Option(help="Write the IDs with problems as JSON for the CAD adapters."),
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
    line("With tori", issues["torus"], "(consider 'radmod elbows')")
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
        console.print(f"Problems written to {esc(str(highlight))}")


@app.command()
def export(
    simplified: Annotated[Path, typer.Argument(help="Simplified STEP model.")],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="STEP file to convert to MCNP.")
    ],
    registry: RegistryOption = None,
    cell_map: Annotated[
        Path | None, typer.Option("--cell-map", "-m", help="Cell map CSV to write.")
    ] = None,
) -> None:
    """Write the STEP file for the CAD-to-MCNP converter and its cell map.

    Solids are written one per product, sorted by component ID and instance: the n-th
    solid becomes the n-th cell of the converted model.
    """
    from radmodeling import workflow
    from radmodeling.registry import Registry

    cell_map = cell_map or output.with_name(f"{output.stem}_cells.csv")
    registry = _registry(registry, [simplified])
    assert registry is not None
    _writable(registry)
    reg = Registry.load(registry)
    with console.status("Exporting..."):
        result = workflow.export(simplified, reg, output, cell_map)
    reg.save(registry)
    keys = result.cell_map.keys
    console.print(
        f"[green]Exported {len(keys)} solids[/] of {len(set(keys))} instances.\n"
        f"  STEP:     {esc(str(output))}\n  Cell map: {esc(str(cell_map))}"
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
    cell_map: CellMapOption,
    library: Annotated[
        Path, typer.Option("--library", "-l", help="Material library TOML.")
    ],
    registry: RegistryOption = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="MCNP input to write.")
    ] = None,
    first_cell: FirstCellOption = 1,
    dcf: Annotated[
        bool,
        typer.Option(help="Apply density correction factors from the MCNP volumes."),
    ] = True,
) -> None:
    """Assign materials, densities (with DCF) and component comments to the cells."""
    from radmodeling.cellmap import CellMap
    from radmodeling.mcnp.geometry import read_model
    from radmodeling.mcnp.materials import (
        AssignmentError,
        MaterialLibrary,
        assign_materials,
    )
    from radmodeling.registry import Registry

    output = output or mcnp.with_name(f"{mcnp.stem}_materials{mcnp.suffix}")
    registry = _registry(registry, [mcnp, cell_map])
    assert registry is not None
    model = read_model(mcnp)
    try:
        report = assign_materials(
            model,
            Registry.load(registry),
            CellMap.load(cell_map),
            MaterialLibrary.load(library),
            first_cell=first_cell,
            use_dcf=dcf,
        )
    except AssignmentError as exc:
        _fail(f"the registry needs fixing:\n{exc}")
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
    if report.cards_already_defined:
        console.print(
            "[yellow]Material cards already in the input were kept:[/] "
            f"{report.cards_already_defined}"
        )
    if report.without_composition:
        console.print(
            "[yellow]No composition in the library, add their cards by hand:[/] "
            f"{esc(str(report.without_composition))}"
        )
    for key, value in report.dcf_out_of_range:
        console.print(f"[yellow]Component {key}: unusual DCF {value:.3f}[/]")
    console.print(f"[green]Written[/] {esc(str(output))}")


@app.command()
def compare(
    step: Annotated[Path, typer.Argument(help="STEP written by 'export'.")],
    mcnp: Annotated[Path, typer.Argument(help="MCNP input converted from it.")],
    cell_map: CellMapOption,
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
    first_cell: FirstCellOption = 1,
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
    from radmodeling.cellmap import CellMap
    from radmodeling.compare import compare_component
    from radmodeling.mcnp.geometry import Geometry
    from radmodeling.registry import Key, Registry
    from radmodeling.workflow import exported_solids

    if not no_registry:
        registry = _registry(registry, [step, mcnp, cell_map], required=False)
        if registry is not None:
            _writable(registry)
        else:
            console.print(
                "[yellow]No registry found: MCNP volumes will not be saved[/]"
            )
    cmap = CellMap.load(cell_map)
    cells_by_instance = cmap.cells_by_instance(first_cell)
    with console.status("Reading the models..."):
        try:
            solids = exported_solids(step, cmap)
        except ValueError as exc:
            _fail(str(exc))
        geometry = Geometry.from_file(mcnp)
    selected = sorted(cells_by_instance)
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
                    cells_by_instance[key],
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
            "cells exceed the sampled box" if r.touches_sampling_box else ""
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
        Path | None, typer.Option(help="STEP model to overlay (from 'export').")
    ] = None,
    cell_map: Annotated[
        Path | None,
        typer.Option("--cell-map", "-m", help="With --step, highlight mismatches."),
    ] = None,
    first_cell: FirstCellOption = 1,
    library: Annotated[
        Path | None, typer.Option("--library", "-l", help="For material names.")
    ] = None,
) -> None:
    """Plot a slice of the MCNP geometry, optionally overlaid with the CAD.

    Without --origin/--width the slice covers the CAD model (if given) or must be set.
    """
    from radmodeling.cad.section import section_polylines
    from radmodeling.cad.step import read_step
    from radmodeling.cellmap import CellMap
    from radmodeling.compare import (
        MM_PER_CM,
        Plane,
        classify_cad_on_plane,
        near_plane,
        slice_mcnp,
    )
    from radmodeling.mcnp.geometry import Geometry
    from radmodeling.mcnp.materials import MaterialLibrary
    from radmodeling.plot import material_slots, render_slice
    from radmodeling.workflow import exported_solids

    geometry = Geometry.from_file(mcnp)
    cmap = CellMap.load(cell_map) if step and cell_map else None
    by_instance = {}
    if step and cmap:
        try:
            by_instance = exported_solids(step, cmap)
        except ValueError as exc:
            _fail(str(exc))
    solids = [s for group in by_instance.values() for s in group]
    if step and not cmap:
        solids = [s for part in read_step(step) for s in part.solids]

    centre, extent = None, None
    if solids:
        from radmodeling.cad.occ import bounding_box

        boxes = np.array([bounding_box(s) for s in solids]) / MM_PER_CM
        lo, hi = boxes[:, :3].min(axis=0), boxes[:, 3:].max(axis=0)
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

    n_u = resolution
    n_v = max(int(round(resolution * plane.height / plane.width)), 1)
    with console.status("Classifying the MCNP geometry..."):
        result = slice_mcnp(geometry, plane, n_u, n_v)

    outlines = []
    if solids:
        with console.status("Cutting the CAD..."):
            reach = np.hypot(plane.width, plane.height) / 2 * MM_PER_CM
            for solid in solids:
                if not near_plane(solid, plane, reach):
                    continue
                for line in section_polylines(
                    solid, plane.origin * MM_PER_CM, plane.normal
                ):
                    local = line / MM_PER_CM - plane.origin
                    outlines.append(np.column_stack([local @ plane.u, local @ plane.v]))
        if cmap:
            with console.status("Comparing CAD and MCNP..."):
                # Pixels are labelled 1, 2, 3... by instance
                label = {key: n for n, key in enumerate(sorted(by_instance), 1)}
                result.cad_component = classify_cad_on_plane(
                    {label[k]: group for k, group in by_instance.items()},
                    plane,
                    n_u,
                    n_v,
                )
                owner = {}
                for key, cells in cmap.cells_by_instance(first_cell).items():
                    owner.update(dict.fromkeys(cells, label[key]))
                result.mcnp_component = np.vectorize(lambda c: owner.get(int(c), 0))(
                    result.cells
                )

    counts: dict[int, int] = {}
    for cell in geometry.cells.values():
        counts[cell.material] = counts.get(cell.material, 0) + 1
    names = {}
    for card in geometry.model.materials():
        # 'materials' writes the material name as a comment on the card
        first_line = card.text.splitlines()[0]
        if card.id is not None and "$" in first_line:
            label = first_line.split("$", 1)[1].strip()
            names[card.id] = f"{label} (m{card.id})" if label else f"m{card.id}"
    if library:
        for material in MaterialLibrary.load(library).materials:
            if material.number is not None:
                names[material.number] = f"{material.name} (m{material.number})"
    render_slice(result, output, material_slots(counts), names, outlines)

    lost = int((result.materials == -1).sum())
    overlaps = int(result.overlaps.sum())
    console.print(f"[green]Written[/] {esc(str(output))}")
    if lost:
        console.print(f"[yellow]{lost} pixels are in no cell (undefined geometry)[/]")
    if overlaps:
        console.print(f"[yellow]{overlaps} pixels are in more than one cell[/]")
    if result.cad_component is not None and result.mcnp_component is not None:
        mismatch = int((result.cad_component != result.mcnp_component).sum())
        console.print(f"CAD/MCNP mismatching pixels: {mismatch}")


# --- geometry tools ------------------------------------------------------------------


@app.command()
def elbows(
    step: Annotated[Path, typer.Argument(help="STEP model.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="STEP to write.")],
    segments: Annotated[int, typer.Option(help="Segments per elbow.")] = 4,
    max_angle: Annotated[
        float | None,
        typer.Option(help="Maximum turn per segment [deg]; adds segments if needed."),
    ] = None,
    preserve_volume: Annotated[
        bool,
        typer.Option(help="Scale the radii to keep the volume instead of the radii."),
    ] = False,
) -> None:
    """Replace toroidal pipe elbows by mitred cylinder segments.

    Names and tags are kept, so the result can go straight to 'check' and 'export'.
    """
    from radmodeling.cad.elbows import replace_elbows
    from radmodeling.cad.step import Part, compound, read_step, write_step

    parts = read_step(step)
    result, count = [], 0
    with console.status("Replacing elbows..."):
        for part in parts:
            shapes, records = replace_elbows(
                part.shape, segments, max_angle, preserve_volume
            )
            if records:
                count += len(records)
                part = Part(part.name, part.path, compound(shapes), part.instance_name)
                for r in records:
                    change = r.new_volume_mm3 / r.original_volume_mm3 - 1
                    console.print(
                        f"{esc(part.name)}: {r.sweep_degrees:.4g} deg elbow -> "
                        f"{r.segments} solids, volume {change:+.2%}"
                    )
            result.append(part)
    write_step(result, output)
    console.print(f"[green]{count} elbows replaced.[/] Written {esc(str(output))}")


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
    from radmodeling.fmesh import box_from_solid, fmesh_card

    if (size is None) == (bins is None):
        _fail("give either --size or --bins")
    counts = _ids(bins)
    if counts is not None and len(counts) != 3:
        _fail("--bins needs three values")
    number, tr = tally, transform
    for part in read_step(step):
        for solid in part.solids:
            try:
                box = box_from_solid(solid)
            except ValueError as exc:
                console.print(f"[yellow]Skipping {esc(part.name)}: {esc(str(exc))}[/]")
                continue
            print(f"c {part.name}")
            print(
                fmesh_card(
                    box, number, particle, size, tuple(counts) if counts else None, tr
                ),
                end="",
            )
            number += 10
            tr += 0 if box.axis_aligned else 1


# --- SpaceClaim ----------------------------------------------------------------------

LOADER = """# Python Script, API Version = {api}
# RadModeling loader for '{tool}'. Publish it once as a SpaceClaim tool: it runs the
# tool from the radmodeling installation, so updating radmodeling updates the tool.
import os

RADMODELING_SPACECLAIM = r"{folder}"
RADMODELING_PYTHON = r"{python}"  # runs radmod for the tools that need it
execfile(os.path.join(RADMODELING_SPACECLAIM, "_rm.py"))
execfile(os.path.join(RADMODELING_SPACECLAIM, "{script}"))
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
