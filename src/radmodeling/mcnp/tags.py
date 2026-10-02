"""The link between the MCNP cells and the registry: the tags in the cell comments.

``export`` names every solid with its registry key, ``[rm12.3] Bolt`` for instance 3
of component 12, and the converter writes the name of the solid of every cell as a
``$`` comment on the cell card. GEOUNED writes the whole CAD path::

    103  10  -7.93  -11 12
               imp:n=1.0   imp:p=1.0
               $/for_conversion/[rm12.3] Bolt1

The tag travels with the cell card through any renumbering or editing, so the cells
of a component are found by their tags alone, never by their numbers.
"""

from __future__ import annotations

import re
from collections import defaultdict

import migjorn

from radmodeling.registry import Key, find_key

COMMENT_LINE_RE = re.compile(r"^ {0,4}c( |$)", re.IGNORECASE)


def cell_key(cell: migjorn.Cell) -> Key | None:
    """The registry key tagged in the ``$`` comments of a cell, or None."""
    for line in cell.text.splitlines():
        if COMMENT_LINE_RE.match(line):
            continue  # 'c' lines before a card can be about anything
        _, dollar, comment = line.partition("$")
        key = find_key(comment) if dollar else None
        if key is not None:
            return key
    return None


def cells_by_instance(model: migjorn.Model) -> dict[Key, list[int]]:
    """The tagged cells of each registry key, in input order."""
    result: dict[Key, list[int]] = defaultdict(list)
    for cell in model.cells():
        key = cell_key(cell)
        if key is not None and cell.id is not None:
            result[key].append(cell.id)
    return dict(result)
