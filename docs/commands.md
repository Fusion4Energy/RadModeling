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

Tag the components and create the registry. See
[CAD to MCNP workflow](workflow.md).

### check

```bash
radmod check SIMPLIFIED.step -r REGISTRY
```

Volume deviations and geometry diagnostics of a simplified model.

### export

```bash
radmod export SIMPLIFIED.step -r REGISTRY -o OUT.step
```

STEP file for the CAD-to-MCNP converter, each solid named with its registry row
(`[rm12.3] Bolt`). The converter copies the names into the cell comments, which is
how `compare`, `materials` and `slice` find the cells of each component.

### compare

```bash
radmod compare OUT.step MODEL.mcnp
```

CAD vs MCNP volumes and discrepancies per instance. `--components 3,7.2` limits it
to some components (all instances of 3, instance 2 of 7), `--points` sets the
samples per instance (100 000 by default).

### materials

```bash
radmod materials MODEL.mcnp -r REGISTRY -l LIBRARY.toml
```

Materials, densities and density correction factors in the tagged cells.
Corrections closer to 1 than `--dcf-min` (0.1 %) or than three times their
uncertainty are not applied. Components whose material is `void` (any case) get
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
radmod atlas MODEL.mcnp --step OUT.step -o ATLAS.pdf
```

PDF with a summary of the problems of every slice, evenly spaced slices across the
model (`--planes x:3,y:3,z:6`) and, with `--report COMPARE.csv`, a zoomed slice
through each of the `--worst` instances of a `compare` report.

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
