"""Map between the order of the solids in the exported STEP file and the registry.

CAD-to-MCNP converters create the cells in the order of the solids of the STEP file,
so the n-th solid becomes cell ``first_cell + n - 1``. Each solid belongs to one
instance of a component, identified by its registry :class:`Key`.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass

from radmodeling.registry import Key


@dataclass
class CellMap:
    """Order of the solids in the exported STEP file and the instance of each."""

    keys: list[Key]  # instance of the n-th solid (0-based)

    def save(self, path: str | os.PathLike) -> None:
        with open(path, "w", encoding="utf-8", newline="") as outfile:
            writer = csv.writer(outfile)
            writer.writerow(["solid_index", "component_id", "instance"])
            for index, key in enumerate(self.keys, start=1):
                writer.writerow([index, key.id, key.instance])

    @classmethod
    def load(cls, path: str | os.PathLike) -> CellMap:
        with open(path, encoding="utf-8-sig", newline="") as infile:
            rows = sorted(
                (
                    int(r["solid_index"]),
                    Key(int(r["component_id"]), int(r.get("instance") or 1)),
                )
                for r in csv.DictReader(infile)
            )
        if [i for i, _ in rows] != list(range(1, len(rows) + 1)):
            raise ValueError(f"{path}: solid indices must be 1, 2, 3... without gaps")
        return cls([key for _, key in rows])

    def cells_by_instance(self, first_cell: int = 1) -> dict[Key, list[int]]:
        result: dict[Key, list[int]] = defaultdict(list)
        for index, key in enumerate(self.keys):
            result[key].append(first_cell + index)
        return dict(result)
