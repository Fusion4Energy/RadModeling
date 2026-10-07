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

The commands that read an MCNP input take it first, then the STEP file written by
`export` (`radmod compare MODEL.mcnp EXPORT.step`). Every command that writes a file
names it after its input (`MODEL_materials.mcnp`, `MODEL_atlas.pdf`...); `-o` writes it
elsewhere. Options shared by several commands (`--points`, `--seed`, `--jobs`,
`--components`, `-l`) mean the same in all of them.

## Workflow

### init

```bash
radmod init ORIGINAL.step
```

Tag the components and create the registry: writes `ORIGINAL_simplified.step`, the
model to simplify (or `-o`), and `ORIGINAL_registry.csv` (or `-r`). See
[CAD to MCNP workflow](workflow.md).

### check

```bash
radmod check SIMPLIFIED.step -r REGISTRY
```

Volume deviations and geometry diagnostics of a simplified model. The volume
tolerance is the `volume-tolerance` setting (see [config](#config)).

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
radmod compare MODEL.mcnp EXPORT.step
```

CAD vs MCNP volumes and discrepancies per instance, and the volume shared by the
cells of different instances (or by cells of the same instance). A clash in the CAD,
such as a bolt left inside a plate, converts faithfully: each instance agrees with its
own cells, but their cells overlap. Each pair is measured from the points sampled
around the smaller instance (the more densely sampled one); a very small overlap
can be missed, so raise `--points` to look closer. `--components 3,7.2` limits it
to some components (all instances of 3, instance 2 of 7), `--points` sets the
samples per instance (100 000 by default). Every instance gets its own random
points from `--seed` and its key, so a result does not depend on the other instances
compared. Instances whose discrepancy is beyond the `max-discrepancy` setting (1 % by default,
see [config](#config)) are reported as problems, with every overlapping pair. The
MCNP volumes are saved in the registry: `compare` writes no other file.

The instances are compared in parallel by `--jobs` worker processes (one per CPU, at
most 8, by default). Each worker reads the STEP file and the MCNP input itself and
keeps its own copy in memory: lower `--jobs` for very large models.

The MCNP volumes go into the registry with the density correction factors they give
(`dcf`): original / MCNP volume when they differ by more than the `volume-tolerance`
setting (1 % by default, as in `check`) plus three standard deviations, else 1. All the factors are
recomputed, including those edited by hand.

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
radmod slice MODEL.mcnp [EXPORT.step]
```

Slice plot, written to `MODEL_slice.png` (or `-o`). `--normal` is `x`, `y`, `z` or
any vector such as `1,1,0`; `--origin` is the centre of the image. A STEP file
overlays the CAD and, with the one from `export`, highlights CAD/MCNP mismatches. Cells are coloured by material; void
cells of a component (tagged) are light grey and the untagged void around the
components is near-white.

### atlas

```bash
radmod atlas MODEL.mcnp EXPORT.step
```

PDF (`MODEL_atlas.pdf`, or `-o`) with a summary of the problems of every slice, evenly spaced slices across the
model (`--planes x:3,y:3,z:6`) and a slice through each problem of the `--worst`
instances, found by sampling every instance like `compare` (`--points`). Small
problems are circled and zoomed on their own pages. It does not depend on an earlier
`compare`: every instance is searched again, so the atlas always shows the current
geometry. `--components` limits the search like in `compare`. The instances are searched and the slices
classified in parallel, like `compare` (`--jobs`).

## Settings

### config

```bash
radmod config                         # list the settings
radmod config volume-tolerance        # show one
radmod config volume-tolerance 1.5    # change it
radmod config volume-tolerance --unset
```

Your settings, kept in a file of your user account (the path is printed with the
list) and read by every command and by the SpaceClaim tools:

| Setting            | Used by                                                      | Default |
| ------------------ | ------------------------------------------------------------ | ------- |
| `volume-tolerance` | `check`, the DCF of `compare`, the SpaceClaim volume tools   | 1 %     |
| `max-discrepancy`  | problems reported by `compare` and sliced by `atlas`         | 1 %     |

## SpaceClaim

### spaceclaim-loaders

```bash
radmod spaceclaim-loaders FOLDER
```

Writes the loader scripts to publish once in SpaceClaim. See
[SpaceClaim tools](spaceclaim.md).
