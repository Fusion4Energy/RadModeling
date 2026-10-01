Installation
============

RadModeling needs Python 3.10 or newer. It works on Windows, Linux and macOS; only
the SpaceClaim tools need Windows.

For users
---------

.. code-block:: bash

   pip install git+https://github.com/Fusion4Energy/RadModeling.git

This installs the ``radmod`` command. Check it with ``radmod --help``.

.. note::

   On machines that block executables in the Python ``Scripts`` folder ("Access is
   denied"), write ``python -m radmod`` wherever the documentation says ``radmod``:
   it is the same command with the same arguments, e.g. ``python -m radmod check ...``.

For development, or to get SpaceClaim tools that update with the code
---------------------------------------------------------------------

.. code-block:: bash

   git clone https://github.com/Fusion4Energy/RadModeling.git
   cd RadModeling
   pip install -e ".[dev]"

With an editable install (``-e``), a ``git pull`` updates both ``radmod`` and the
SpaceClaim tools. See :doc:`spaceclaim`.

Dependencies
------------

* `OCP <https://github.com/CadQuery/OCP>`_ (OpenCascade) to read and write STEP files
  and to analyse the geometry.
* `migjorn <https://pypi.org/project/migjorn/>`_ to read and edit MCNP inputs
  losslessly.
* numpy, matplotlib, typer and rich.

The CAD-to-MCNP conversion itself is done by another tool, typically
`GEOUNED <https://github.com/GEOUNED-org/GEOUNED>`_, which needs its own installation.
