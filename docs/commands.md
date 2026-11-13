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

CAD vs MCNP volumes and discrepancies per instance, and the volume shared by
overlapping cells of different instances (or of the same one). The instances beyond
the `max-discrepancy` setting (see [config](#config)) are reported as problems. The
MCNP volumes and the density correction factors they give (`dcf`) are saved in the
registry (`--no-registry` skips it); nothing else is written. See
[Compare CAD and MCNP](workflow.md#6-compare-cad-and-mcnp).

* `--components 3,7.2`: only all the instances of component 3 and instance 2 of 7.
* `--points`: samples per instance (100 000). Raise it to find smaller overlaps.
* `--seed`: each instance draws its points from the seed and its key, so a result
  does not depend on the other instances compared or on `--jobs`.
* `--jobs`: worker processes (one per CPU, at most 8). Each one keeps its own copy
  of the model in memory: lower it for very large models.

### materials

```bash
radmod materials MODEL.mcnp -r REGISTRY -l LIBRARY.toml
```

Materials and densities (times the registry's `dcf`) in the tagged cells, written to
`MODEL_materials.mcnp` (or `-o`). Components whose material is `void` get material 0.
`--no-dcf` ignores the density correction factors. See
[Assign the materials](workflow.md#7-assign-the-materials) for the DCF and the
library format.

### slice

```bash
radmod slice MODEL.mcnp [EXPORT.step]
```

Slice plot, written to `MODEL_slice.png` (or `-o`), with the cells coloured by
material. A STEP file overlays the CAD and, with the one from `export`, highlights
CAD/MCNP mismatches.

* `--normal`: `x`, `y`, `z` (default) or any vector such as `1,1,0`.
* `--origin`, `--width`, `--height`: centre and size of the image (cm). Without them
  the slice covers the CAD model.
* `-l`: material library, to show the material names.
* `--resolution`: pixels across (800).

### atlas

```bash
radmod atlas MODEL.mcnp EXPORT.step
```

PDF (`MODEL_atlas.pdf`, or `-o`) with a summary of the problems, evenly spaced slices
across the model and a slice through each problem found, with small problems zoomed
on their own pages. It searches every instance itself, like `compare`, so it does not
need an earlier `compare`. See [Look at it](workflow.md#8-look-at-it).

* `--planes`: evenly spaced planes normal to each axis (`x:3,y:3,z:3`).
* `--worst`: maximum number of problem instances sliced (20).
* `--resolution`: pixels across (400).
* `--components`, `--points`, `--seed`, `--jobs`: as in `compare`.

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
