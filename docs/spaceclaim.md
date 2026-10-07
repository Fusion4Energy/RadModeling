---
icon: lucide/mouse-pointer-click
---

# SpaceClaim tools

SpaceClaim is only needed for the interactive simplification and for drawing mesh
tallies. The tools below help during those steps. They read the registry written by
`radmod`, colour, hide or extrude things, or run `radmod` on the document; all the
rest of the logic lives in the Python package.

## Setting them up (once)

1. Install radmodeling on the Windows machine, preferably as an editable install
   (`pip install -e`, see [Installation](installation.md)) so that `git pull`
   updates the tools.
2. Write the loaders:

    ```bash
    radmod spaceclaim-loaders C:\Users\me\RadModeling-tools
    ```

3. In SpaceClaim, open each loader in the script editor and publish it as a tool.

Each loader only points to the tool inside the radmodeling installation and runs it.
When radmodeling is updated, the published tools run the new code: there is nothing to
publish again. Use `--api-version` if your SpaceClaim needs a scripting API version
other than `V241`.

The tools look for the `*_registry.csv` file next to the SpaceClaim document and ask
for it if there is none or more than one.

The instances of a component share their geometry, so they all have the same original
volume. *Check volumes* and *Adjust volume* compare each instance with it: deleting
some instances (a few bolts, say) does not affect the others, and an instance made
independent is checked on its own. The bodies of an instance are those of its part.

Linked instances share their name in SpaceClaim, so *Show by material* cannot tell
them apart: a component is shown if any of its instances is made of a chosen
material.

## Tools

### Check model

Runs `radmod check` on the open document without leaving SpaceClaim: saves a STEP
copy next to it with the same name (`machine_simplified.scdocx` →
`machine_simplified.step`, replacing the previous copy), checks it against the
registry, shows the report and colours the components with problems in red. Those
with only warnings are yellow: tori (fine in MCNP with their axis along X, Y or Z),
moved components (which may be moved on purpose), components with deleted instances
(the instances left are coloured) and new components (just added to the registry
with status `new`). The document is not changed, so the `.scdocx` stays the working file. The
registry must not be open in Excel, as the check updates it. SpaceClaim waits while the check runs. The volume tolerance is
the `volume-tolerance` setting, as for `radmod check`.

It runs `radmod` with the Python that wrote the loaders. Loaders written before this
tool existed do not know it: write them again and publish *Check model*.

### Tag new components

Gives the next free `[rmN]` tags to the components added after `radmod init`. Select
bodies or faces to tag only their components; with nothing selected, all the untagged
bodies are tagged. A component and all the linked instances of a part get a single
tag; bodies directly in the root part are tagged themselves. The new IDs come after
those of the registry and of the document, and *Check model* (or `radmod check`)
adds them to the registry.

### Check volumes

Colours in red the instances whose volume differs from the original by more than
the `volume-tolerance` setting (1 % by default), blue transparent the rest, and grey the untagged
bodies.

### Adjust volume

Select one or more faces of a component and run it: the faces are extruded until the
selected instance recovers its original volume, within half of the `volume-tolerance`
setting (0.5 % by default), well inside what the checks accept.

### Show by material

Choose materials from a list to show only their components. `(no material)` shows
the components that still need one.

### Replace elbows

Select a torus face of each pipe bend and run it: the bend is replaced by mitred
cylinder segments. The bend may be a body of its own or part of a longer pipe; the
inner torus of a hollow pipe is found from the outer one. Each segment turns at most
22.5°, with at least 2 segments (a 90° bend gets 4); the end ones are half segments
perpendicular to the pipe, so they join the straight parts exactly. The script
parameters `ElbowMaxAngle` and `ElbowMinSegments` change these numbers (see
[below](#parameters)), and the message after each run shows the values used.

The segments follow the tangent polygon of the bend, so the volume grows slightly
(+1.3 % for 90° in 4 segments, +0.3 % in 8): the density correction factor absorbs
it. The result replaces the body with a merged body of the same name (so the same
tag), layer and colour in the same part, so linked instances change together; if the
pieces do not unite into one body, they are kept as separate bodies. The volume is
checked on the pieces before merging, because SpaceClaim measures the merged body
wrong (OCCT finds it valid and exact: `radmod check` measures it right). If the
pieces do not have the expected volumes, for instance because other geometry of the
body falls next to the bend, the body is left unchanged and the pieces are left as
*RadModeling check* bodies to look at.

!!! warning

    Look at the result: the corners stick out a little beyond the original bend and
    may touch neighbouring parts.

### Load points

Draws the points of a CSV file (x, y, z in cm) as spheres of radius `PointRadius`
(20 mm by default), for instance the lost particles found by
[F4Enix](https://github.com/Fusion4Energy/F4Enix).

### Fmesh

Draw a box (any orientation) where each mesh tally should be, select the boxes (or
any of their faces) and run it: the FMESH cards are written to `<name>_fmesh.txt` next
to the document (save it first), replacing the previous file. Each card comes after a comment with
the name of its box. Tally numbers start at 14 and go up by 10; rotated boxes get a TR
card for the mesh, numbered from 900. The voxels are at most `FmeshVoxelSize` cm (5 by
default) along each axis, and the message lists how many each mesh has. The tally
numbers, particle (`n`) and voxels are meant to be edited in the file. Selected bodies
that are not boxes are skipped.

## Parameters

The tolerances and limits are constants at the top of each script in
`src/radmodeling/spaceclaim`. Some can be changed per document with script
parameters: in the *Groups* panel, *Create Parameter*, then give it the name the tool
reads and a value. Without the parameter, the tool uses its default.

| Parameter          | Tools                                                                | Default |
| ------------------ | -------------------------------------------------------------------- | ------- |
| `ElbowMaxAngle`    | *Replace elbows*: maximum turn per segment                           | 22.5°   |
| `ElbowMinSegments` | *Replace elbows*: minimum segments per bend                          | 2       |
| `PointRadius`      | *Load points*: sphere radius                                         | 20 mm   |
| `FmeshVoxelSize`   | *Fmesh*: maximum voxel size                                          | 5 cm    |

The volume tolerance is not a script parameter: *Check model*, *Check volumes* and
*Adjust volume* use the `volume-tolerance` setting, like `radmod check` and
`radmod compare`, so it is set once for all of them with
`radmod config volume-tolerance VALUE` (see [Commands](commands.md#config)). Loaders
written before the setting existed do not know where it is: write them again with
`radmod spaceclaim-loaders` and publish them again.
