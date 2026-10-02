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
                   ──radmod export─▶ for_conversion.step
                                      │
                            GEOUNED (or another converter)
                                      │
   model.mcnp ──radmod compare───▶ CAD vs MCNP volumes, density correction factors
              ──radmod materials─▶ materials and densities in the cells
              ──radmod slice─────▶ slice plots with the CAD overlaid
              ──radmod atlas─────▶ PDF of slices: whole model + worst instances

What it gives you
-----------------

* **Stable component IDs.** Every component gets a tag such as ``Pump casing [rm12]``
  in its name. Tags survive STEP round trips through any CAD program, so the simplified
  model is always matched back to the original components, whatever is renamed or
  moved in the assembly tree.
* **Linked instances.** All the instances of a repeated part share one tag and stay
  linked in the CAD program, so each part is simplified only once. Each instance is
  still tracked on its own, down to its MCNP cells.
* **One registry.** An Excel-friendly CSV with one row per instance: material,
  density or mass, original, simplified and MCNP volumes, and the resulting density
  correction factor. The assembly tree is in one column per level, ready for Excel's
  filters.
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
