"""Map between the order of the solids in the exported STEP file and component IDs.

CAD-to-MCNP converters create the cells in the order of the solids of the STEP file,
so the n-th solid becomes cell ``first_cell + n - 1``.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class CellMap:
    """Order of the solids in the exported STEP file and their component IDs."""

    component_ids: list[int]  # component of the n-th solid (0-based)

    def save(self, path: str | os.PathLike) -> None:
        with open(path, "w", encoding="utf-8", newline="") as outfile:
            writer = csv.writer(outfile)
            writer.writerow(["solid_index", "component_id"])
            for index, component_id in enumerate(self.component_ids, start=1):
                writer.writerow([index, component_id])

    @classmethod
    def load(cls, path: str | os.PathLike) -> CellMap:
        with open(path, encoding="utf-8-sig", newline="") as infile:
            rows = sorted(
                (int(r["solid_index"]), int(r["component_id"]))
                for r in csv.DictReader(infile)
            )
        if [i for i, _ in rows] != list(range(1, len(rows) + 1)):
            raise ValueError(f"{path}: solid indices must be 1, 2, 3... without gaps")
        return cls([c for _, c in rows])

    def cells_by_component(self, first_cell: int = 1) -> dict[int, list[int]]:
        result: dict[int, list[int]] = defaultdict(list)
        for index, component_id in enumerate(self.component_ids):
            result[component_id].append(first_cell + index)
        return dict(result)
