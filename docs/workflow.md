---
icon: lucide/workflow
---

# CAD to MCNP workflow

This page follows a model from the original CAD to a checked MCNP input. Every step is
a `radmod` command; `radmod <command> --help` lists all the options.

A model goes through three STEP files, all named after the original:

| File                          | Role                                                     |
| ----------------------------- | -------------------------------------------------------- |
| `machine.step`                | The original CAD, never changed.                         |
| `machine_simplified.step`     | The model being simplified, written tagged by `init`.    |
| `machine_for_conversion.step` | The input of the converter, written by `export`.         |

Next to them, `machine_registry.csv` keeps the materials and the original volumes.

## 1. Tag the original model

```bash
radmod init machine.step
```

This writes `machine_simplified.step` and `machine_registry.csv`. Every component (every
part of the assembly that holds solids) gets a unique ID in its name, for example
`Pump casing [rm12]`.

A part placed several times, such as a bolt used 20 times, is one component: all its
instances share the tag and **stay linked** in the tagged model, so simplifying one
bolt simplifies all of them and the file does not grow. The registry still has one row
per instance (`12.1`, `12.2`... for component 12), with its own original volume,
position and material, because each instance becomes its own MCNP cells.

Use `--per-solid` to make every solid its own component instead.

!!! tip

    Remove the parts that clearly do not belong to the model before running `init`,
    so that they never get an ID.

## 2. Fill the registry

Open the registry in Excel or any editor and fill in the `material` column, and
`density_g_cm3` or `mass_g` where the material's default density does not apply.
Write `void` for components that should be void. The registry can be edited at any
time; the commands only change the columns they own.

The `level_1`, `level_2`... columns hold the original assembly tree, so a whole
subassembly can be selected with Excel's filters (*Data > Filter*). To give it a
material, filter it, type the material in the first visible row, select the visible
rows of the column and press ++ctrl+d++: Excel only fills the visible rows. Instances
of the same component can have different materials.

| Column                                       | Meaning                                                                                                                        |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `id`                                         | Component ID, the number in the `[rmID]` tag. Never change it.                                                                 |
| `instance`                                   | Instance number, 1 for components placed once. Never change it.                                                                |
| `level_1`, `level_2`...                      | The assemblies above the component in the original tree, from the root.                                                        |
| `name`                                       | Name of the component.                                                                                                         |
| `material`                                   | Name in the material library (or an alias), or `void`. *You fill it.*                                                          |
| `density_g_cm3`                              | Density, when it differs from the material's default. *Optional.*                                                              |
| `mass_g`                                     | Mass of the instance; gives the density from the original volume. *Optional.*                                                  |
| `original_volume_cm3`, `original_solids`     | Written by `init`, per instance.                                                                                               |
| `simplified_volume_cm3`, `simplified_solids` | Written by `check` and `export`.                                                                                               |
| `volume_diff_pct`                            | Simplified vs original volume. Computed.                                                                                       |
| `mcnp_volume_cm3`, `mcnp_volume_std_cm3`     | Volume of the component's MCNP cells and its statistical uncertainty (one standard deviation), written by `compare`.           |
| `dcf`                                        | Density correction factor, original volume / MCNP volume. Computed.                                                            |
| `status`                                     | `deleted` if the instance is no longer in the model, `new` if it was added after `init`.                                       |
| `comment`                                    | Free text.                                                                                                                     |
| `x_cm`, `y_cm`, `z_cm`                       | Centre of mass of the original instance, written by `init`. It tells the instances apart after the simplification.             |

The file is saved as UTF-8 with a BOM so that Excel opens it correctly. Files saved
back by Excel with semicolons, decimal commas or Windows encodings are read fine.

## 3. Simplify

Simplify `machine_simplified.step` in SpaceClaim or any other CAD program (in
SpaceClaim, save it as `machine_simplified.scdocx`). The only rule
is to **keep the `[rmID]` tags in the component names**; renaming the rest, moving
components in the tree, splitting or merging bodies within a component are all fine.
The [SpaceClaim tools](spaceclaim.md) help along the way: colour the components whose
volume drifted, recover a volume by extruding faces, show components by material.

Keep the instances of a part linked unless one of them really needs a different
simplification. Making one independent is fine: it keeps its tag and its row.

**Tag the parts you add.** Run the
[*Tag new components*](spaceclaim.md#tag-new-components) SpaceClaim tool after adding
parts, or write a tag with an unused number yourself, such as `Shield [rm90]`.
`export` adds tags that are not in the registry yet as new components with status
`new`; give them a material then. Parts left untagged also get an ID on export, but
that ID only exists in the registry: the next export would add them again, so tag
them before exporting.

Copies of a tagged component become new instances of it with the same material. A
component whose tag disappears is marked as `deleted`.

Instances carry the same tag, so they are told apart by their position: each one is
matched with the registry row whose original centre of mass is nearest. An instance
further from that position than its own size is not considered the same instance, but
a deleted one plus a copy, because the two look alike. Components with a single
instance and a single body are matched anyway and reported as moved.

## 4. Check the simplified model

```bash
radmod check machine_simplified.step -r machine_registry.csv --tolerance 1
```

`-r` can be left out when `machine_registry.csv` is the only registry next to the
model or in the current folder: every command finds it and prints which one it used.

`radmod` reads STEP files, not SpaceClaim's `.scdocx`. Save a STEP copy of the
document over `machine_simplified.step` to check it, or use the
[*Check model*](spaceclaim.md#check-model) SpaceClaim tool, which does both in one
click. The STEP written by `init` is not needed once the work has started: the
registry keeps the original volumes and positions.

Reports the instances beyond the volume tolerance, and the geometry that will cause
trouble in the conversion: surfaces a CSG converter cannot translate (splines, offset
surfaces...), tori and invalid solids. It also lists deleted instances, extra
instances (copies of a component), moved components, new components (tags not in the
registry yet) and untagged parts.

To get rid of the tori of pipe elbows, use the
[*Replace elbows*](spaceclaim.md#replace-elbows) SpaceClaim tool.

## 5. Export for the conversion

```bash
radmod export machine_simplified.step -r machine_registry.csv
```

Writes every solid as its own product, sorted by component ID and instance, and named
with both: `[rm12.3] Bolt` is instance 3 of component 12. Unlike the component tags,
which `init` puts at the end of the names, these tags come first: some converters cut
or drop the end of long names. Instances are unlinked here, as converters need every
solid on its own. The output is `machine_for_conversion.step`, named after the
registry (`-o` writes it elsewhere). Convert it with GEOUNED or similar.

The converter copies the name of each solid into a comment on its cell. GEOUNED
writes the CAD path of the solid:

```text
103   10  -7.9300000 -11 12
           imp:n=1.0   imp:p=1.0
           $/machine_for_conversion/[rm12.3] Bolt1
```

This tag is the link between the cell and its registry row. It stays with the cell
through renumbering, merging models or editing by hand, and the commands below find
the cells of each component by their tags alone, never by their numbers. Cells
without a tag (voids, graveyard) are ignored. Keep the solid names in the comments if
the converter has an option for it, and convert the STEP file as written by
`export`: renaming its solids loses the tags.

## 6. Compare CAD and MCNP

```bash
radmod compare machine_for_conversion.step model.mcnp -r machine_registry.csv \
    --report compare.csv
```

For every instance, random points are classified against both the CAD solids and the
MCNP cells. The points (`--points`, 100 000 by default) are spread over the bounding
box of each solid, not over the box of the whole instance, so an instance made of
hundreds of small solids scattered across the machine is sampled as densely as a
compact one. Points where the boxes of several solids overlap are weighted down
accordingly, so the estimates stay unbiased. `--components 3,7.2` limits it to all
the instances of component 3 and instance 2 of component 7. This gives:

* the **MCNP volume** of the component with its uncertainty. The estimate only depends
  on the points where CAD and MCNP disagree, so it is very precise when the conversion
  is good. It goes into the registry and sets the density correction factor.
* the **discrepancy**: the volume where CAD and MCNP disagree, relative to the CAD
  volume. Above the `--threshold` (1 % by default) the component is reported.
* a warning when the cells reach the border of the sampled boxes, meaning they extend
  well beyond the CAD solids.

Instances of the STEP file without tagged cells are reported with a 100 %
discrepancy, and cell tags of instances that are not in the STEP file are listed.

## 7. Assign the materials

```bash
radmod materials model.mcnp -r machine_registry.csv -l materials.toml
```

Writes `model_materials.mcnp` with the material number and density of every tagged
cell (density × DCF, as mass density). Every problem (components without material,
unknown materials, tags of components not in the registry...) is reported at once so
the registry can be fixed in one go.

Density correction factors that cannot be told apart from 1 are not applied: those
closer to 1 than `--dcf-min` (0.1 % by default) and those within three standard
deviations of 1, from the uncertainty of the MCNP volume. Such corrections are below
the accuracy of the comparison, and applying them would only change the densities in
their last digits at every new `compare`. Larger corrections are always applied,
however small: the volume removed when simplifying a component is real missing mass,
usually in the same direction for all components, and it adds up. The registry still
shows every `dcf`, and the command reports how many were applied and how many left
at 1. Use `--no-dcf` to ignore the density correction factors altogether.

The material library is a TOML file:

```toml
[SS316L]
number = 10            # MCNP material number, auto-assigned when missing
density = 7.93         # g/cm3, default for the components made of it
aliases = ["316L", "AISI 316L"]
```

`materials` only edits the cells: the material cards themselves (compositions,
libraries) belong to the material definition process, not to the geometry, and are
added to the input separately. The command lists the material numbers it used that
have no card in the input yet.

## 8. Look at it

```bash
radmod slice model_materials.mcnp -o slice.png --normal z --origin 0,0,120 \
    --width 400 --step machine_for_conversion.step
```

Plots the MCNP cells coloured by material with the CAD section on top. Regions in no
cell (where particles would get lost), overlapping cells and, with the STEP file from
`export`, CAD/MCNP mismatches are highlighted. Without `--origin` and `--width` the
slice covers the whole CAD model.

For a document that covers the whole model, write an atlas:

```bash
radmod atlas model_materials.mcnp --step machine_for_conversion.step -o atlas.pdf \
    --planes x:3,y:3,z:6 --report compare.csv --worst 20
```

The PDF has:

* **summary pages** with one row per slice: the pixels in no cell, in several cells
  and where CAD and MCNP disagree, problems in red;
* **overview slices**: `--planes x:3,y:3,z:6` cuts the CAD bounding box with 3 evenly
  spaced planes normal to x, 3 normal to y and 6 normal to z, never on its faces;
* **problem slices**: with the `--report` written by `compare`, one page for each of
  the `--worst` instances beyond `--threshold`, worst first, after those that could
  not be evaluated or have no tagged cells. Each cuts the instance through its
  centre, normal to its thinnest direction so that it shows its largest section,
  zoomed on it with some surroundings.

Materials keep the same colour on every page. `--resolution` (400 pixels across by
default, half that of `slice`) trades detail for time: every page classifies all its
pixels against the MCNP cells and the CAD. A few isolated mismatching pixels along
the CAD outlines are normal: they are pixels on the surfaces, where the tessellated
CAD and the exact MCNP surfaces differ by less than a pixel.
