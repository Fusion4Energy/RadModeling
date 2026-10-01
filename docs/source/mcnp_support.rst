MCNP geometry support
=====================

``compare`` and ``slice`` evaluate the MCNP geometry with RadModeling's own point
classifier, on top of the `migjorn <https://pypi.org/project/migjorn/>`_ parser.

Supported
---------

* Surfaces P (coefficients or three points), PX, PY, PZ, SO, S, SX, SY, SZ, C/X, C/Y,
  C/Z, CX, CY, CZ, K/X, K/Y, K/Z, KX, KY, KZ (including one-sheet cones), SQ, GQ, TX,
  TY, TZ, and X, Y, Z defined by two points.
* Macrobodies RPP, BOX, SPH, RCC and TRC, as bodies and through their facets.
* TR and \*TR transformations on surfaces (3, 12 or 13 entries).
* Intersections, unions, complements ``#n`` and ``#( )``, and LIKE n BUT cells.

Not supported
-------------

* Universes and lattices: cells in universes other than 0 are ignored and filled
  cells count as their envelope.
* TRCL on cells, periodic surfaces, and the macrobodies RHP/HEX, REC, ELL, WED and
  ARB.
* The repeat, insert and multiply shortcuts (``2r``, ``3i``, ``2m``) in surface cards.

Unsupported features only matter for the cells that use them: they raise an error
when such a cell is evaluated, and the rest of the model works normally.

Performance
-----------

Cells and bounded surfaces (spheres, cylinders, tori, macrobodies, axis-aligned
planes) are only evaluated on the points inside their bounding boxes. As a reference,
on a model of 1000 components a slice of 800 × 500 pixels takes about 2 s, and
``compare`` with 20 000 points per component about 9 s.
