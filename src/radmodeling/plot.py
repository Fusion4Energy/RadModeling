"""Rendering of geometry slices with matplotlib."""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from radmodeling.compare import SliceResult  # noqa: E402

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
) -> None:
    """Draw MCNP cells coloured by material, CAD outlines and the discrepancies.

    Args:
        cad_outlines: polylines in the plane coordinates (u, v), in cm.
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

    problems = result.overlaps.copy()
    if result.cad_component is not None and result.mcnp_component is not None:
        problems |= result.cad_component != result.mcnp_component
    if problems.any():
        ax.contourf(
            np.linspace(extent[0], extent[1], problems.shape[1]),
            np.linspace(extent[3], extent[2], problems.shape[0]),
            problems.astype(float),
            levels=[0.5, 1.5],
            colors="none",
            hatches=["////"],
        )
        ax.imshow(
            np.ma.masked_where(~problems, problems),
            cmap=ListedColormap([CRITICAL]),
            extent=extent,
            interpolation="nearest",
            alpha=0.55,
        )
        label = "CAD/MCNP mismatch" if result.cad_component is not None else "Overlap"
        legend.append(Patch(facecolor=CRITICAL, alpha=0.55, hatch="////", label=label))

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
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


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
