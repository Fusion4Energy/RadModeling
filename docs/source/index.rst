RadModeling
===========

RadModeling prepares CAD models for Monte Carlo radiation transport and checks the
MCNP geometry obtained from them. It is a Python package with a command line tool,
``radmod``, plus a few thin SpaceClaim tools for the interactive part of the work.

.. code-block:: text

   original.step ──radmod init──▶ tagged.step + registry.csv
                                      │
                       simplify in SpaceClaim (or any CAD program)
                                      │
   simplified.step ──radmod check──▶ volume deviations, tori, splines, invalid solids
                   ──radmod export─▶ for_conversion.step + cell map
                                      │
                            GEOUNED (or another converter)
                                      │
   model.mcnp ──radmod compare──▶ CAD vs MCNP volumes, density correction factors
              ──radmod materials─▶ materials, densities and comments in the cells
              ──radmod slice─────▶ slice plots with the CAD overlaid

What it gives you
-----------------

* **Stable component IDs.** Every component gets a tag such as ``Pump casing [rm:12]``
  in its name. Tags survive STEP round trips through any CAD program, so the simplified
  model is always matched back to the original components, whatever is renamed or
  moved in the assembly tree.
* **One registry.** An Excel-friendly CSV with one row per component: material,
  density or mass, original, simplified and MCNP volumes, and the resulting density
  correction factor.
* **CAD vs MCNP comparison without running MCNP.** RadModeling evaluates the MCNP
  cells itself, so it measures the volume of each component's cells, how much of it
  disagrees with the CAD, and plots slices with the CAD contours on top.
* **Geometry tools.** Toroidal elbows to mitred cylinders, FMESH cards from boxes
  drawn in CAD.
* **SpaceClaim tools published once.** They load the latest code from the
  radmodeling installation, so updating radmodeling updates them.

The source code is hosted on `GitHub <https://github.com/Fusion4Energy/RadModeling>`_.

.. toctree::
   :maxdepth: 2
   :caption: RadModeling

   installation
   workflow
   commands
   spaceclaim
   mcnp_support
   development

.. toctree::
   :maxdepth: 1
   :caption: Version 1 (SpaceClaim scripts)

   usage
   csv_workflow
   cad_to_mcnp_comparison
   miscellaneous
   legacy
