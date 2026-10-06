"""Rendering of geometry slices with matplotlib."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Circle, Patch  # noqa: E402

from radmodeling.compare import SliceResult  # noqa: E402

if TYPE_CHECKING:
    from radmodeling.slices import Marker, Slice

# Categorical slots in fixed order (validated for colour-vision deficiencies). The
# palette's red slot is left out so that red only ever means a problem.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]
OTHER = "#b9b8b3"
SURFACE = "#fcfcfb"  # void cells
NO_CELL = "#0b0b0b"  # undefined region: particles would be lost here
CRITICAL = "#d03b3b"  # overlaps and CAD/MCNP mismatches
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
BORDER = "#52514e"


def material_slots(material_counts: dict[int, int]) -> dict[int, int]:
    """Give the most used materials of the model a fixed colour slot each."""
    ranked = sorted(
        (m for m in material_counts if m > 0), key=lambda m: (-material_counts[m], m)
    )
    return {m: i for i, m in enumerate(ranked[: len(SERIES)])}


def render_slice(
    result: SliceResult,
    path: str | os.PathLike,
    slots: dict[int, int],
    material_names: dict[int, str] | None = None,
    cad_outlines: list[np.ndarray] | None = None,
    title: str = "",
    markers: Sequence[Marker] = (),
) -> None:
    """Draw a slice (see :func:`slice_figure`) to an image file."""
    fig = slice_figure(result, slots, material_names, cad_outlines, title, markers)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def slice_figure(
    result: SliceResult,
    slots: dict[int, int],
    material_names: dict[int, str] | None = None,
    cad_outlines: list[np.ndarray] | None = None,
    title: str = "",
    markers: Sequence[Marker] = (),
) -> Figure:
    """Draw MCNP cells coloured by material, CAD outlines and the discrepancies.

    Args:
        cad_outlines: polylines in the plane coordinates (u, v), in cm.
        markers: circles around small problems, labelled with their zoom page.
    """
    material_names = material_names or {}
    plane = result.plane
    extent = (-plane.width / 2, plane.width / 2, -plane.height / 2, plane.height / 2)

    # Index image: 0 no cell, 1 void, 2 other, 3.. slots
    image = np.full(result.materials.shape, 2, dtype=int)
    image[result.materials == -1] = 0
    image[result.materials == 0] = 1
    for material, slot in slots.items():
        image[result.materials == material] = 3 + slot
    colours = [NO_CELL, SURFACE, OTHER, *SERIES]

    fig, ax = plt.subplots(figsize=(9, 9 * plane.height / plane.width + 0.8))
    ax.imshow(
        image,
        cmap=ListedColormap(colours),
        vmin=0,
        vmax=len(colours) - 1,
        extent=extent,
        interpolation="nearest",
    )
    # Thin borders between cells keep neighbouring cells of one material apart
    edges = _cell_edges(result.cells)
    ax.imshow(
        np.ma.masked_where(~edges, edges),
        cmap=ListedColormap([BORDER]),
        extent=extent,
        interpolation="nearest",
        alpha=0.6,
    )

    legend = []
    used = set(np.unique(result.materials).tolist())
    for material, slot in sorted(slots.items(), key=lambda item: item[1]):
        if material in used:
            label = material_names.get(material, f"m{material}")
            legend.append(Patch(facecolor=SERIES[slot], label=label))
    if any(m > 0 and m not in slots for m in used):
        legend.append(Patch(facecolor=OTHER, label="Other materials"))
    if 0 in used:
        legend.append(Patch(facecolor=SURFACE, edgecolor=BORDER, label="Void"))
    if -1 in used:
        legend.append(Patch(facecolor=NO_CELL, label="No cell (lost particles)"))

    layers = [(result.overlaps, "xxxx", "Overlap (several cells)")]
    if result.mismatch is not None:
        layers.append((result.mismatch & ~result.overlaps, "////", "CAD/MCNP mismatch"))
    for problems, hatch, label in layers:
        if not problems.any():
            continue
        ax.contourf(
            np.linspace(extent[0], extent[1], problems.shape[1]),
            np.linspace(extent[3], extent[2], problems.shape[0]),
            problems.astype(float),
            levels=[0.5, 1.5],
            colors="none",
            hatches=[hatch],
        )
        ax.imshow(
            np.ma.masked_where(~problems, problems),
            cmap=ListedColormap([CRITICAL]),
            extent=extent,
            interpolation="nearest",
            alpha=0.55,
        )
        legend.append(Patch(facecolor=CRITICAL, alpha=0.55, hatch=hatch, label=label))

    for marker in markers:
        ax.add_patch(
            Circle(
                (marker.u, marker.v),
                marker.radius,
                fill=False,
                edgecolor=CRITICAL,
                linewidth=1.5,
            )
        )
        if marker.label:
            ax.annotate(
                marker.label,
                (marker.u + marker.radius * 0.71, marker.v + marker.radius * 0.71),
                xytext=(3, 3),
                textcoords="offset points",
                color=CRITICAL,
                fontsize=9,
                weight="bold",
            )
    if markers:
        legend.append(
            Line2D(
                [],
                [],
                marker="o",
                linestyle="none",
                markerfacecolor="none",
                markeredgecolor=CRITICAL,
                label="Small problem",
            )
        )

    for line in cad_outlines or []:
        ax.plot(line[:, 0], line[:, 1], color=TEXT, linewidth=0.8)
    if cad_outlines:
        legend.append(Line2D([], [], color=TEXT, linewidth=0.8, label="CAD"))

    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_xlabel(_axis_label(plane.u), color=TEXT_SECONDARY)
    ax.set_ylabel(_axis_label(plane.v), color=TEXT_SECONDARY)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(OTHER)
    ax.set_title(title or _plane_title(result), color=TEXT, fontsize=11, loc="left")
    if legend:
        ax.legend(
            handles=legend,
            loc="upper left",
            bbox_to_anchor=(1.01, 1),
            frameon=False,
            fontsize=8,
            labelcolor=TEXT,
        )
    fig.patch.set_facecolor("white")
    return fig


def _cell_edges(cells: np.ndarray) -> np.ndarray:
    edges = np.zeros(cells.shape, dtype=bool)
    edges[:, 1:] |= cells[:, 1:] != cells[:, :-1]
    edges[1:, :] |= cells[1:, :] != cells[:-1, :]
    return edges


def _axis_label(axis: np.ndarray) -> str:
    names = "xyz"
    if np.count_nonzero(np.abs(axis) > 1e-9) == 1:
        index = int(np.argmax(np.abs(axis)))
        sign = "" if axis[index] > 0 else "-"
        return f"{sign}{names[index]} [cm] (from slice centre)"
    return f"({axis[0]:.2f}, {axis[1]:.2f}, {axis[2]:.2f}) [cm] (from slice centre)"


def _plane_title(result: SliceResult) -> str:
    o = result.plane.origin
    n = result.plane.normal
    return (
        f"Slice through ({o[0]:g}, {o[1]:g}, {o[2]:g}) cm, "
        f"normal ({n[0]:.2f}, {n[1]:.2f}, {n[2]:.2f})"
    )


# --- atlas -------------------------------------------------------------------------

A4_PORTRAIT = (8.27, 11.69)  # inches
SUMMARY_ROWS = 40  # slices listed per summary page


def write_atlas(
    slices: Sequence[Slice],
    path: str | os.PathLike,
    slots: dict[int, int],
    material_names: dict[int, str] | None = None,
    header: Sequence[str] = (),
) -> None:
    """Write a PDF with a summary of the problems of every slice, then the slices.

    Args:
        header: lines describing the model, printed on the first summary page.
    """
    # Every slice is followed by its zooms, titled with the slice's page
    pages: list[tuple[Slice, str]] = []
    n_pages = len(slices) + sum(len(item.zooms) for item in slices)
    n_summary = max(-(-n_pages // SUMMARY_ROWS), 1)
    for item in slices:
        number = n_summary + len(pages) + 1
        pages.append((item, f"{number}. {item.title}" if item.title else ""))
        for k, zoom in enumerate(item.zooms, 1):
            pages.append((zoom, f"{number + k}. Zoom {zoom.title} (page {number})"))
    with PdfPages(path, metadata={"Title": "RadModeling atlas"}) as pdf:
        for page in range(n_summary):
            first = page * SUMMARY_ROWS
            fig = _summary_figure(
                pages[first : first + SUMMARY_ROWS],
                header=header if page == 0 else (),
                page=(page + 1, n_summary),
            )
            pdf.savefig(fig)
            plt.close(fig)
        for number, (item, title) in enumerate(pages, n_summary + 1):
            markers = [
                replace(m, label=f"{m.label} (p. {number + ord(m.label) - 64})")
                if m.label
                else m
                for m in item.markers
            ]
            fig = slice_figure(
                item.result, slots, material_names, item.outlines, title, markers
            )
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)


def _summary_figure(
    pages: Sequence[tuple[Slice, str]],
    header: Sequence[str],
    page: tuple[int, int],
) -> Figure:
    fig = plt.figure(figsize=A4_PORTRAIT)
    fig.patch.set_facecolor("white")
    top = 0.95
    title = "RadModeling atlas"
    if page[1] > 1:
        title += f" (summary {page[0]} of {page[1]})"
    fig.text(0.08, top, title, fontsize=15, color=TEXT, weight="bold")
    top -= 0.035
    lines = [*header, f"Created {datetime.now():%Y-%m-%d %H:%M}"] if header else []
    for line in lines:
        fig.text(0.08, top, line, fontsize=8.5, color=TEXT_SECONDARY)
        top -= 0.02
    if header:
        fig.text(
            0.08,
            top - 0.01,
            "Pixels in no cell (lost particles), in several cells (overlaps) and "
            "where CAD and MCNP disagree. Problems in red.",
            fontsize=8.5,
            color=TEXT_SECONDARY,
        )
        top -= 0.04

    columns = ["Page", "Slice", "No cell", "Overlap", "Mismatch"]
    rows, colours = [], []
    for item, title in pages:
        r = item.result
        mismatch = r.mismatch_pixels
        counts = [r.lost_pixels, r.overlap_pixels, mismatch]
        number, _, title = title.partition(". ")
        if title.startswith("Zoom "):
            title = "    " + title
        rows.append([number, title, *("-" if c is None else str(c) for c in counts)])
        colours.append([TEXT, TEXT, *(CRITICAL if c else TEXT for c in counts)])
    ax = fig.add_axes((0.08, 0.04, 0.84, top - 0.05))
    ax.axis("off")
    if not rows:
        return fig
    table = ax.table(
        cellText=rows,
        colLabels=columns,
        colWidths=[0.07, 0.63, 0.1, 0.1, 0.1],
        loc="upper left",
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    height = min(0.024 / (top - 0.05), 1 / (len(rows) + 1))
    for (row, column), cell in table.get_celld().items():
        cell.set_height(height)
        cell.set_edgecolor(OTHER)
        cell.set_linewidth(0.4)
        if row == 0:
            cell.set_text_props(color=TEXT, weight="bold")
        else:
            cell.set_text_props(color=colours[row - 1][column])
        if column >= 2:
            cell.get_text().set_horizontalalignment("right")
    return fig
