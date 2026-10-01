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
    Path, typer.Option("--registry", "-r", help="Component registry CSV.")
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


def _ids(text: str | None) -> list[int] | None:
    if not text:
        return None
    return [int(t) for t in text.replace(",", " ").split()]


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
    """Tag every component with a unique ID and create the registry.

    Simplify the tagged STEP in any CAD program. The '[rm:ID]' tags in the component
    names must be kept; everything else (names, hierarchy) can change freely.
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
        f"[green]Tagged {len(result)} components[/] ({total:.6g} cm3).\n"
        f"  Tagged model: {esc(str(tagged))}\n  Registry:     {esc(str(registry))}\n"
        "Fill the 'material' column (and density or mass) of the registry, then "
        "simplify the tagged model."
    )


@app.command()
def check(
    simplified: Annotated[Path, typer.Argument(help="Simplified STEP model.")],
    registry: RegistryOption,
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

    reg = Registry.load(registry)
    with console.status("Analysing the simplified model..."):
        result = workflow.check(simplified, reg, tolerance)
    reg.save(registry)

    exceeding = result.exceeding()
    issues = {
        "volume": [c.id for c in exceeding],
        "unconvertible": result.with_issue(lambda r: r.unconvertible_surfaces),
        "torus": result.with_issue(lambda r: r.has_torus),
        "invalid": result.with_issue(lambda r: not r.valid),
        "duplicated": result.duplicated(),
        "deleted": result.missing_ids,
        "unknown": result.unknown_ids,
    }

    table = Table(title=f"Components beyond ±{tolerance:g}% volume", show_lines=False)
    for column in ("ID", "Name", "Original cm3", "Simplified cm3", "Diff %"):
        table.add_column(column, justify="right" if "cm3" in column else "left")
    for c in sorted(exceeding, key=lambda c: -abs(c.volume_diff_pct or 0))[:30]:
        table.add_row(
            str(c.id),
            esc(c.name),
            f"{c.original_volume_cm3:.6g}",
            f"{c.simplified_volume_cm3:.6g}",
            f"{c.volume_diff_pct:+.2f}",
        )
    if exceeding:
        console.print(table)
        if len(exceeding) > 30:
            console.print(f"... and {len(exceeding) - 30} more (see the registry)")

    def line(label: str, ids: list[int], hint: str = "") -> None:
        style = "green" if not ids else "yellow"
        shown = ", ".join(map(str, ids[:20])) + (" ..." if len(ids) > 20 else "")
        console.print(f"[{style}]{label}: {len(ids)}[/] {shown} {hint if ids else ''}")

    found = len(result.states)
    console.print(f"Components found: {found} of {len(reg)} in the registry")
    line("Beyond volume tolerance", issues["volume"])
    line(
        "Surfaces a CSG converter cannot translate",
        issues["unconvertible"],
        "(splines, offsets...)",
    )
    line("With tori", issues["torus"], "(consider 'radmod elbows')")
    line("Invalid solids", issues["invalid"])
    line("Tag in several places", issues["duplicated"], "(copied components?)")
    line("Deleted", issues["deleted"])
    line("Tags not in the registry", issues["unknown"])
    console.print(
        f"[{'green' if not result.untagged else 'yellow'}]Untagged parts: "
        f"{len(result.untagged)}[/] "
        + esc(", ".join(p.name for p in result.untagged[:10]))
        + (" (they get new IDs on export)" if result.untagged else "")
    )
    if highlight:
        highlight.write_text(json.dumps(issues, indent=1))
        console.print(f"Problems written to {esc(str(highlight))}")


@app.command()
def export(
    simplified: Annotated[Path, typer.Argument(help="Simplified STEP model.")],
    registry: RegistryOption,
    output: Annotated[
        Path, typer.Option("--output", "-o", help="STEP file to convert to MCNP.")
    ],
    cell_map: Annotated[
        Path | None, typer.Option("--cell-map", "-m", help="Cell map CSV to write.")
    ] = None,
) -> None:
    """Write the STEP file for the CAD-to-MCNP converter and its cell map.

    Solids are written one per product, sorted by component ID: the n-th solid
    becomes the n-th cell of the converted model.
    """
    from radmodeling import workflow
    from radmodeling.registry import Registry

    cell_map = cell_map or output.with_name(f"{output.stem}_cells.csv")
    reg = Registry.load(registry)
    with console.status("Exporting..."):
        try:
            result, added = workflow.export(simplified, reg, output, cell_map)
        except ValueError as exc:
            _fail(str(exc))
    reg.save(registry)
    console.print(
        f"[green]Exported {len(result.component_ids)} solids[/] of "
        f"{len(set(result.component_ids))} components.\n"
        f"  STEP:     {esc(str(output))}\n  Cell map: {esc(str(cell_map))}"
    )
    if added:
        console.print(
            f"[yellow]{len(added)} untagged parts were added to the registry as new "
            f"components:[/] {added}. Give them a material."
        )


# --- MCNP ----------------------------------------------------------------------------


@app.command()
def materials(
    mcnp: Annotated[Path, typer.Argument(help="MCNP input from the converter.")],
    registry: RegistryOption,
    cell_map: CellMapOption,
    library: Annotated[
        Path, typer.Option("--library", "-l", help="Material library TOML.")
    ],
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
    for component_id, value in report.dcf_out_of_range:
        console.print(f"[yellow]Component {component_id}: unusual DCF {value:.3f}[/]")
    console.print(f"[green]Written[/] {esc(str(output))}")


@app.command()
def compare(
    step: Annotated[Path, typer.Argument(help="STEP written by 'export'.")],
    mcnp: Annotated[Path, typer.Argument(help="MCNP input converted from it.")],
    cell_map: CellMapOption,
    registry: Annotated[
        Path | None,
        typer.Option("--registry", "-r", help="Registry to update with MCNP volumes."),
    ] = None,
    first_cell: FirstCellOption = 1,
    points: Annotated[int, typer.Option(help="Sample points per component.")] = 100_000,
    components: Annotated[
        str | None, typer.Option(help="Only these component IDs, e.g. '3,7,12'.")
    ] = None,
    report: Annotated[
        Path | None, typer.Option(help="Write the full results as CSV.")
    ] = None,
    threshold: Annotated[
        float, typer.Option(help="Discrepancy reported as a problem [%].")
    ] = 1.0,
    seed: Annotated[int | None, typer.Option(help="Random seed.")] = None,
) -> None:
    """Compare every component's CAD solids with its MCNP cells by point sampling.

    Gives the MCNP volume of each component (for the density correction factors) and
    the volume where CAD and MCNP disagree.
    """
    from radmodeling.cad.step import read_step
    from radmodeling.cellmap import CellMap
    from radmodeling.compare import compare_component
    from radmodeling.mcnp.geometry import Geometry
    from radmodeling.registry import Registry
    from radmodeling.workflow import group_by_component

    cmap = CellMap.load(cell_map)
    cells_by_component = cmap.cells_by_component(first_cell)
    with console.status("Reading the models..."):
        states, _ = group_by_component(read_step(step))
        geometry = Geometry.from_file(mcnp)
    selected = _ids(components) or sorted(cells_by_component)
    rng = np.random.default_rng(seed)

    results = []
    with console.status("") as status:
        for i, component_id in enumerate(selected, 1):
            status.update(f"Component {component_id} ({i}/{len(selected)})")
            if component_id not in states:
                console.print(
                    f"[yellow]Component {component_id} not in {esc(str(step))}[/]"
                )
                continue
            results.append(
                compare_component(
                    component_id,
                    states[component_id].solids,
                    cells_by_component.get(component_id, []),
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
    table = Table(title=f"Components with problems (discrepancy > {threshold:g}%)")
    for column in ("ID", "Cells", "CAD cm3", "MCNP cm3", "Discrepancy %", "Note"):
        table.add_column(column)
    for r in sorted(problems, key=lambda r: -np.nan_to_num(r.discrepancy_pct, nan=1e9)):
        note = r.error or (
            "cells exceed the sampled box" if r.touches_sampling_box else ""
        )
        table.add_row(
            str(r.component_id),
            _cells_text(r.cells),
            f"{r.cad_volume_cm3:.6g}",
            f"{r.mcnp_volume_cm3:.6g} ± {r.mcnp_volume_std_cm3:.2g}",
            f"{r.discrepancy_pct:.3g}",
            note,
        )
    if problems:
        console.print(table)
    good = len(results) - len(problems)
    console.print(f"[green]{good} components agree[/], {len(problems)} with problems.")

    if registry:
        reg = Registry.load(registry)
        for r in results:
            if r.component_id in reg and not r.error:
                reg[r.component_id].mcnp_volume_cm3 = r.mcnp_volume_cm3
        reg.save(registry)
        console.print(f"MCNP volumes saved in {esc(str(registry))} (used for the DCF).")
    if report:
        with open(report, "w", encoding="utf-8", newline="") as outfile:
            writer = csv.writer(outfile)
            writer.writerow(
                [
                    "component_id",
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
                        r.component_id,
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
    from radmodeling.workflow import group_by_component

    geometry = Geometry.from_file(mcnp)
    states = {}
    if step:
        states, _ = group_by_component(read_step(step))
    solids = [s for state in states.values() for s in state.solids]

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
        if cell_map:
            with console.status("Comparing CAD and MCNP..."):
                result.cad_component = classify_cad_on_plane(
                    {i: s.solids for i, s in states.items()}, plane, n_u, n_v
                )
                owner = {}
                for cid, cells in (
                    CellMap.load(cell_map).cells_by_component(first_cell).items()
                ):
                    owner.update(dict.fromkeys(cells, cid))
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
                api=api_version, tool=tool, folder=folder, script=script.name
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
