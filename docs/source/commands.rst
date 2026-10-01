Commands
========

All commands print their full list of options with ``radmod <command> --help``.
Lengths are in cm and volumes in cm\ :sup:`3`, except in CAD files (mm).

Workflow
--------

``radmod init ORIGINAL.step``
   Tag the components and create the registry. See :doc:`workflow`.

``radmod check SIMPLIFIED.step -r REGISTRY``
   Volume deviations and geometry diagnostics of a simplified model.

``radmod export SIMPLIFIED.step -r REGISTRY -o OUT.step``
   STEP file for the CAD-to-MCNP converter and its cell map.

``radmod compare OUT.step MODEL.mcnp -m CELLS.csv``
   CAD vs MCNP volumes and discrepancies per instance. ``--components 3,7.2`` limits
   it to some components (all instances of 3, instance 2 of 7), ``--points`` sets the
   samples per instance (100 000 by default).

``radmod materials MODEL.mcnp -r REGISTRY -m CELLS.csv -l LIBRARY.toml``
   Materials, densities, density correction factors and comments in the cells.

``radmod slice MODEL.mcnp -o SLICE.png``
   Slice plot. ``--normal`` is ``x``, ``y``, ``z`` or any vector such as ``1,1,0``;
   ``--origin`` is the centre of the image; ``--step`` overlays the CAD and, with
   ``--cell-map``, highlights CAD/MCNP mismatches.

Geometry tools
--------------

``radmod elbows MODEL.step -o OUT.step``
   Replaces every plain toroidal elbow (a solid made only of torus faces and two flat
   end caps, hollow or not) by mitred cylinder segments: two half segments at the
   ends and full ones in between, following the tangent polygon of the bend. The ends
   stay perpendicular, so the result joins the adjacent straight pipes exactly.

   The polygon is a bit longer than the arc, so the volume grows slightly (+1.3 % for
   90° in 4 segments, +0.3 % in 8). By default the radii are kept, and the density
   correction factor absorbs the difference; ``--preserve-volume`` scales the radii
   instead. ``--segments`` sets the number of segments and ``--max-angle`` the
   maximum turn per segment. Names and tags are kept.

``radmod fmesh BOXES.step --size 5``
   Prints an FMESH card for each box of the STEP file. Boxes can have any
   orientation: rotated boxes get a TR card. ``--bins 10,10,4`` sets the number of
   voxels instead of their size; ``--tally`` and ``--transform`` set the first tally
   and TR numbers.

SpaceClaim
----------

``radmod spaceclaim-loaders FOLDER``
   Writes the loader scripts to publish once in SpaceClaim. See :doc:`spaceclaim`.
