---
icon: lucide/square-terminal
---

# Commands

All commands print their full list of options with `radmod <command> --help`.
Lengths are in cm and volumes in cm³, except in CAD files (mm).

The registry is given with `-r`. Without it, the commands use the only
`*_registry.csv` file next to their input files, or else in the current folder, and
print which one they picked. If there are several, they stop and ask for `-r`.
`compare` only updates the registry it finds when there is one; `--no-registry`
skips the update.

## Workflow

### init

```bash
radmod init ORIGINAL.step
```

Tag the components and create the registry: writes `ORIGINAL_simplified.step`, the
model to simplify, and `ORIGINAL_registry.csv`. See
[CAD to MCNP workflow](workflow.md).

### check

```bash
radmod check SIMPLIFIED.step -r REGISTRY
```

Volume deviations and geometry diagnostics of a simplified model.

### export

```bash
radmod export SIMPLIFIED.step -r REGISTRY
```

STEP file for the CAD-to-MCNP converter (`NAME_export.step` next to
`NAME_registry.csv`, or `-o`), each solid named with its registry row
(`[rm12.3] Bolt`). The converter copies the names into the cell comments, which is
how `compare`, `materials` and `slice` find the cells of each component.

### compare

```bash
radmod compare EXPORT.step MODEL.mcnp
```

CAD vs MCNP volumes and discrepancies per instance, written to `MODEL_compare.csv`
(or `--report`; not written with `--components`), and the volume shared by the
cells of different instances (or by cells of the same instance). A clash in the CAD,
such as a bolt left inside a plate, converts faithfully: each instance agrees with its
own cells, but their cells overlap. Each pair is measured from the points sampled
around the smaller instance (the more densely sampled one); a very small overlap
can be missed, so raise `--points` to look closer. `--components 3,7.2` limits it
to some components (all instances of 3, instance 2 of 7), `--points` sets the
samples per instance (100 000 by default). Every instance gets its own random
points from `--seed` and its key, so a result does not depend on the other instances
compared.

The instances are compared in parallel by `--jobs` worker processes (one per CPU, at
most 8, by default). Each worker reads the STEP file and the MCNP input itself and
keeps its own copy in memory: lower `--jobs` for very large models.

The MCNP volumes go into the registry with the density correction factors they give
(`dcf`), except the factors edited by hand, which are kept and listed.

### materials

```bash
radmod materials MODEL.mcnp -r REGISTRY -l LIBRARY.toml
```

Materials, densities and density correction factors in the tagged cells.
The correction factors are the registry's `dcf` column, written by `compare` or by
hand; `--no-dcf` ignores them. Components whose material is `void` (any case) get
material 0 and no density in all their cells; they need no library entry.

### slice

```bash
radmod slice MODEL.mcnp -o SLICE.png
```

Slice plot. `--normal` is `x`, `y`, `z` or any vector such as `1,1,0`; `--origin`
is the centre of the image; `--step` overlays the CAD and, with the STEP file from
`export`, highlights CAD/MCNP mismatches.

### atlas

```bash
radmod atlas MODEL.mcnp --step EXPORT.step -o ATLAS.pdf
```

PDF with a summary of the problems of every slice, evenly spaced slices across the
model (`--planes x:3,y:3,z:6`) and a slice through each problem of the `--worst`
instances, found by sampling every instance like `compare` (`--points`). Small
problems are circled and zoomed on their own pages. The report of `compare`
(`MODEL_compare.csv` for `MODEL.mcnp` or `MODEL_*.mcnp`, or `--report`) is used when
found: only the instances it reports are searched. It must come from the current
geometry and STEP file; `--no-report` searches every instance. The instances are
searched and the slices classified in parallel, like `compare` (`--jobs`).

## Geometry tools

### fmesh

```bash
radmod fmesh BOXES.step --size 5
```

Prints an FMESH card for each box of the STEP file. Boxes can have any orientation:
rotated boxes get a TR card. `--bins 10,10,4` sets the number of voxels instead of
their size; `--tally` and `--transform` set the first tally and TR numbers. Solids
that are not boxes are skipped with a warning on stderr, so `> meshes.i` collects
only the cards.

## SpaceClaim

### spaceclaim-loaders

```bash
radmod spaceclaim-loaders FOLDER
```

Writes the loader scripts to publish once in SpaceClaim. See
[SpaceClaim tools](spaceclaim.md).
