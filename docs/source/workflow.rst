CAD to MCNP workflow
====================

This page follows a model from the original CAD to a checked MCNP input. Every step is
a ``radmod`` command; ``radmod <command> --help`` lists all the options.

1. Tag the original model
-------------------------

.. code-block:: bash

   radmod init machine.step

This writes ``machine_tagged.step`` and ``machine_registry.csv``. Every component (every
part of the assembly that holds solids) gets a unique ID in its name, for example
``Pump casing [rm:12]``.

A part placed several times, such as a bolt used 20 times, is one component: all its
instances share the tag and **stay linked** in the tagged model, so simplifying one
bolt simplifies all of them and the file does not grow. The registry still has one row
per instance (``12.1``, ``12.2``... for component 12), with its own original volume,
position and material, because each instance becomes its own MCNP cells.

Use ``--per-solid`` to make every solid its own component instead.

.. tip::

   Remove the parts that clearly do not belong to the model before running ``init``,
   so that they never get an ID.

2. Fill the registry
--------------------

Open the registry in Excel or any editor and fill in the ``material`` column, and
``density_g_cm3`` or ``mass_g`` where the material's default density does not apply.
Write ``void`` for components that should be void. The registry can be edited at any
time; the commands only change the columns they own.

The ``level_1``, ``level_2``... columns hold the original assembly tree, so a whole
subassembly can be selected with Excel's filters (*Data > Filter*). To give it a
material, filter it, type the material in the first visible row, select the visible
rows of the column and press *Ctrl+D*: Excel only fills the visible rows. Instances of
the same component can have different materials.

.. list-table:: Registry columns
   :header-rows: 1
   :widths: 25 75

   * - Column
     - Meaning
   * - ``id``
     - Component ID, the number in the ``[rm:ID]`` tag. Never change it.
   * - ``instance``
     - Instance number, 1 for components placed once. Never change it.
   * - ``level_1``, ``level_2``...
     - The assemblies above the component in the original tree, from the root.
   * - ``name``
     - Name of the component.
   * - ``material``
     - Name in the material library (or an alias), or ``void``. *You fill it.*
   * - ``density_g_cm3``
     - Density, when it differs from the material's default. *Optional.*
   * - ``mass_g``
     - Mass of the instance; gives the density from the original volume. *Optional.*
   * - ``original_volume_cm3``, ``original_solids``
     - Written by ``init``, per instance.
   * - ``simplified_volume_cm3``, ``simplified_solids``
     - Written by ``check`` and ``export``.
   * - ``volume_diff_pct``
     - Simplified vs original volume. Computed.
   * - ``mcnp_volume_cm3``
     - Volume of the component's MCNP cells, written by ``compare``.
   * - ``dcf``
     - Density correction factor, original volume / MCNP volume. Computed.
   * - ``status``
     - ``deleted`` if the instance is no longer in the model, ``new`` if it was
       added after ``init``.
   * - ``comment``
     - Free text.
   * - ``x_cm``, ``y_cm``, ``z_cm``
     - Centre of mass of the original instance, written by ``init``. It tells the
       instances apart after the simplification.

The file is saved as UTF-8 with a BOM so that Excel opens it correctly. Files saved
back by Excel with semicolons, decimal commas or Windows encodings are read fine.

3. Simplify
-----------

Simplify ``machine_tagged.step`` in SpaceClaim or any other CAD program. The only rule
is to **keep the** ``[rm:ID]`` **tags in the component names**; renaming the rest,
moving components in the tree, splitting or merging bodies within a component are all
fine. The SpaceClaim tools (:doc:`spaceclaim`) help along the way: colour the
components whose volume drifted, recover a volume by extruding faces, show components
by material.

Keep the instances of a part linked unless one of them really needs a different
simplification. Making one independent is fine: it keeps its tag and its row.

New parts (without a tag) get an ID on export, and copies of a tagged component become
new instances of it with the same material. A component whose tag disappears is marked
as ``deleted``.

Instances carry the same tag, so they are told apart by their position: each one is
matched with the registry row whose original centre of mass is nearest. An instance
further from that position than its own size is not considered the same instance, but
a deleted one plus a copy, because the two look alike. Components with a single
instance and a single body are matched anyway and reported as moved.

4. Check the simplified model
-----------------------------

.. code-block:: bash

   radmod check simplified.step -r machine_registry.csv --tolerance 1

``-r`` can be left out when ``machine_registry.csv`` is the only registry next to the
model or in the current folder: every command finds it and prints which one it used.

Reports the instances beyond the volume tolerance, and the geometry that will cause
trouble in the conversion: surfaces a CSG converter cannot translate (splines, offset
surfaces...), tori and invalid solids. It also lists deleted instances, extra
instances (copies of a component), moved components and untagged parts.
``--highlight problems.json`` writes the IDs per problem for the *Highlight problems*
SpaceClaim tool.

To get rid of the tori of pipe elbows, see ``radmod elbows`` in :doc:`commands`.

5. Export for the conversion
----------------------------

.. code-block:: bash

   radmod export simplified.step -r machine_registry.csv -o for_conversion.step

Writes every solid as its own product, sorted by component ID and instance, and the
cell map ``for_conversion_cells.csv``: the converter makes the n-th solid the n-th
cell, and the cell map gives the component and instance of each. Instances are
unlinked here, as converters need every solid on its own. Convert
``for_conversion.step`` with GEOUNED or similar.

6. Compare CAD and MCNP
-----------------------

.. code-block:: bash

   radmod compare for_conversion.step model.mcnp -m for_conversion_cells.csv \
       -r machine_registry.csv --report compare.csv

For every instance, random points in its bounding box are classified against both
the CAD solids and the MCNP cells. ``--components 3,7.2`` limits it to all the
instances of component 3 and instance 2 of component 7. This gives:

* the **MCNP volume** of the component with its uncertainty. The estimate only depends
  on the points where CAD and MCNP disagree, so it is very precise when the conversion
  is good. It goes into the registry and sets the density correction factor.
* the **discrepancy**: the volume where CAD and MCNP disagree, relative to the CAD
  volume. Above the ``--threshold`` (1 % by default) the component is reported.
* a warning when the cells reach the border of the sampled box, meaning they extend
  well beyond the CAD component.

Use ``--first-cell`` if the converter did not start numbering cells at 1. A wrong cell
numbering shows up immediately as large discrepancies.

7. Assign the materials
-----------------------

.. code-block:: bash

   radmod materials model.mcnp -r machine_registry.csv -m for_conversion_cells.csv \
       -l materials.toml

Writes ``model_materials.mcnp`` with the material number and density of every cell
(density × DCF, as mass density), a ``$ rm:ID.INSTANCE name`` comment on each cell
(``$ rm:12.3 Bolt``) and the material cards from the library. Every problem
(components without material, unknown materials, missing cells...) is reported at once
so the registry can be fixed in one go. Use ``--no-dcf`` to ignore the density correction factors.

The material library is a TOML file:

.. code-block:: toml

   [SS316L]
   number = 10            # MCNP material number, auto-assigned when missing
   density = 7.93         # g/cm3, default for the components made of it
   aliases = ["316L", "AISI 316L"]
   composition = """
   26000.31c -0.655
   24000.31c -0.170
   28000.31c -0.120
   """

Material cards already present in the input are kept. Materials without a
``composition`` are assigned but their cards must be added by hand (the command lists
them).

8. Look at it
-------------

.. code-block:: bash

   radmod slice model_materials.mcnp -o slice.png --normal z --origin 0,0,120 \
       --width 400 --step for_conversion.step -m for_conversion_cells.csv

Plots the MCNP cells coloured by material with the CAD section on top. Regions in no
cell (where particles would get lost), overlapping cells and CAD/MCNP mismatches are
highlighted. Without ``--origin`` and ``--width`` the slice covers the whole CAD model.
