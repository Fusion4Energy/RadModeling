CAD to MCNP workflow
====================

This page follows a model from the original CAD to a checked MCNP input. Every step is
a ``radmod`` command; ``radmod <command> --help`` lists all the options.

1. Tag the original model
-------------------------

.. code-block:: bash

   radmod init machine.step

This writes ``machine_tagged.step`` and ``machine_registry.csv``. Every component (every
part of the assembly that holds solids, with each instance of a repeated part counted
separately) gets a unique ID in its name, for example ``Pump casing [rm:12]``. Its
original volume goes into the registry.

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

.. list-table:: Registry columns
   :header-rows: 1
   :widths: 25 75

   * - Column
     - Meaning
   * - ``id``
     - Component ID, the number in the ``[rm:ID]`` tag. Never change it.
   * - ``name``, ``path``
     - Name of the component and its position in the original assembly tree.
   * - ``material``
     - Name in the material library (or an alias), or ``void``. *You fill it.*
   * - ``density_g_cm3``
     - Density, when it differs from the material's default. *Optional.*
   * - ``mass_g``
     - Mass of the component; gives the density from the original volume. *Optional.*
   * - ``original_volume_cm3``, ``original_solids``
     - Written by ``init``.
   * - ``simplified_volume_cm3``, ``simplified_solids``
     - Written by ``check`` and ``export``.
   * - ``volume_diff_pct``
     - Simplified vs original volume. Computed.
   * - ``mcnp_volume_cm3``
     - Volume of the component's MCNP cells, written by ``compare``.
   * - ``dcf``
     - Density correction factor, original volume / MCNP volume. Computed.
   * - ``status``
     - ``deleted`` if the component is no longer in the model, ``new`` if it was
       added after ``init``.
   * - ``comment``
     - Free text.

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

New parts (without a tag) get an ID on export. A component whose tag disappears is
marked as ``deleted``.

4. Check the simplified model
-----------------------------

.. code-block:: bash

   radmod check simplified.step -r machine_registry.csv --tolerance 1

Reports the components beyond the volume tolerance, and the geometry that will cause
trouble in the conversion: surfaces a CSG converter cannot translate (splines, offset
surfaces...), tori, invalid solids, tags found in several places (copied components)
and untagged parts. ``--highlight problems.json`` writes the IDs per problem for the
*Highlight problems* SpaceClaim tool.

To get rid of the tori of pipe elbows, see ``radmod elbows`` in :doc:`commands`.

5. Export for the conversion
----------------------------

.. code-block:: bash

   radmod export simplified.step -r machine_registry.csv -o for_conversion.step

Writes every solid as its own product, sorted by component ID, and the cell map
``for_conversion_cells.csv``: the converter makes the n-th solid the n-th cell. Convert
``for_conversion.step`` with GEOUNED or similar.

6. Compare CAD and MCNP
-----------------------

.. code-block:: bash

   radmod compare for_conversion.step model.mcnp -m for_conversion_cells.csv \
       -r machine_registry.csv --report compare.csv

For every component, random points in its bounding box are classified against both
the CAD solids and the MCNP cells. This gives:

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
(density × DCF, as mass density), a ``$ rm:ID name`` comment on each cell and the
material cards from the library. Every problem (components without material, unknown
materials, missing cells...) is reported at once so the registry can be fixed in one
go. Use ``--no-dcf`` to ignore the density correction factors.

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
