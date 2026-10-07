---
icon: lucide/house
hide:
  - toc
---

<div class="rm-hero" markdown>

![RadModeling logo](assets/logo.svg){ .rm-hero__logo }

<div class="rm-hero__text" markdown>

# <span class="rm-rad">Rad</span>Modeling

<p class="rm-hero__tagline">CAD preparation and CAD-to-MCNP quality assurance</p>

[Get started](installation.md){ .md-button .md-button--primary }
[Workflow](workflow.md){ .md-button }

</div>

</div>

RadModeling prepares CAD models for Monte Carlo radiation transport and checks the
MCNP geometry obtained from them. It is a Python package with a command line tool,
`radmod`, plus a few thin SpaceClaim tools for the interactive part of the work.

```text
machine.step ──radmod init──▶ machine_simplified.step + machine_registry.csv
                                   │
                    simplify in SpaceClaim (or any CAD program)
                                   │
machine_simplified.step ──radmod check──▶ volumes, tori, splines, invalid solids
                        ──radmod export─▶ machine_export.step
                                   │
                         GEOUNED (or another converter)
                                   │
machine_export.mcnp ──radmod compare───▶ CAD vs MCNP volumes, density correction factors
                    ──radmod materials─▶ materials and densities in the cells
                    ──radmod slice─────▶ slice plots with the CAD overlaid
                    ──radmod atlas─────▶ PDF of slices: whole model + every problem
```

## What it gives you

<div class="grid cards" markdown>

-   :lucide-tag: **Stable component IDs**

    ---

    Every component gets a tag such as `Pump casing [rm12]` in its name. Tags survive
    STEP round trips through any CAD program, so the simplified model is always
    matched back to the original components, whatever is renamed or moved in the
    assembly tree.

-   :lucide-link: **Linked instances**

    ---

    All the instances of a repeated part share one tag and stay linked in the CAD
    program, so each part is simplified only once. Each instance is still tracked on
    its own, down to its MCNP cells.

-   :lucide-table: **One registry**

    ---

    An Excel-friendly CSV with one row per instance: material, density or mass,
    original, simplified and MCNP volumes, and the resulting density correction
    factor. The assembly tree is in one column per level, ready for Excel's filters.

-   :lucide-scan-search: **CAD vs MCNP comparison without running MCNP**

    ---

    RadModeling evaluates the MCNP cells itself, so it measures the volume of each
    component's cells, how much of it disagrees with the CAD, and plots slices with
    the CAD contours on top.

-   :lucide-box: **Geometry tools**

    ---

    In SpaceClaim: FMESH cards from the selected boxes, toroidal elbows to mitred
    cylinders.

-   :lucide-refresh-cw: **SpaceClaim tools published once**

    ---

    They load the latest code from the radmodeling installation, so updating
    radmodeling updates them.

</div>

The source code is hosted on
[GitHub](https://github.com/Fusion4Energy/RadModeling).
