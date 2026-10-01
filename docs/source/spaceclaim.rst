SpaceClaim tools
================

SpaceClaim is only needed for the interactive simplification. The tools below help
during that step. They read the registry written by ``radmod`` and colour, hide or
extrude things; all the rest of the logic lives in the Python package.

Setting them up (once)
----------------------

1. Install radmodeling on the Windows machine, preferably as an editable install
   (``pip install -e``, see :doc:`installation`) so that ``git pull`` updates the
   tools.
2. Write the loaders:

   .. code-block:: bash

      radmod spaceclaim-loaders C:\Users\me\RadModeling-tools

3. In SpaceClaim, open each loader in the script editor and publish it as a tool.

Each loader only points to the tool inside the radmodeling installation and runs it.
When radmodeling is updated, the published tools run the new code: there is nothing to
publish again. Use ``--api-version`` if your SpaceClaim needs a scripting API version
other than ``V241``.

The tools look for the ``*_registry.csv`` file next to the SpaceClaim document and ask
for it if there is none or more than one.

The instances of a component share their geometry, so they all have the same original
volume. *Check volumes* and *Adjust volume* compare each instance with it: deleting
some instances (a few bolts, say) does not affect the others, and an instance made
independent is checked on its own. The bodies of an instance are those of its part.

Linked instances share their name in SpaceClaim, so *Show by material* cannot tell
them apart: a component is shown if any of its instances is made of a chosen
material.

Tools
-----

**Check model**
   Runs ``radmod check`` on the open document without leaving SpaceClaim: saves a
   STEP copy next to it (``<name>_check.step``), checks it against the registry,
   shows the report and colours the components with problems in red. The document is
   not changed, so the ``.scdocx`` stays the working file. The registry must not be
   open in Excel, as the check updates it. SpaceClaim waits while the check runs.

   It runs ``radmod`` with the Python that wrote the loaders. Loaders written before
   this tool existed do not know it: write them again and publish *Check model*.

**Check volumes**
   Colours in red the instances whose volume differs from the original by more
   than 1 %, blue transparent the rest, and grey the untagged bodies.

**Adjust volume**
   Select one or more faces of a component and run it: the faces are extruded until
   the selected instance recovers its original volume (within 0.5 %).

**Show by material**
   Choose materials from a list to show only their components. ``(no material)``
   shows the components that still need one.

**Highlight problems**
   Colours in red the components listed in the JSON written by
   ``radmod check --highlight``.

**Load points**
   Draws the points of a CSV file (x, y, z in cm) as small spheres, for instance the
   lost particles found by `F4Enix <https://github.com/Fusion4Energy/F4Enix>`_.

The tolerances and limits are constants at the top of each script in
``src/radmodeling/spaceclaim``.

.. note::

   These scripts run in SpaceClaim's IronPython 2.7 and cannot be tested outside
   SpaceClaim. Keep them small: anything that can be computed from a STEP file
   belongs in the Python package, where it is tested.
