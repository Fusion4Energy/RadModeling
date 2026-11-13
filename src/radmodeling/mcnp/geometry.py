"""Point classification for MCNP constructive solid geometry.

Works on a :mod:`migjorn` model and supports the cell and surface cards produced by
CAD-to-MCNP converters and the most common hand-written ones:

* surfaces: P (4 or 9 values), PX/PY/PZ, SO/S/SX/SY/SZ, C/X C/Y C/Z, CX/CY/CZ,
  K/X K/Y K/Z, KX/KY/KZ, SQ, GQ, TX/TY/TZ, X/Y/Z (two point pairs)
* macrobodies RPP, BOX, SPH, RCC and TRC, including their facets
* TR and *TR transformations on surfaces
* cell geometry with intersections, unions (:), complements (#) and LIKE n BUT

Cells in universes other than 0 are ignored and filled cells are treated as their
envelope, which is what a comparison against the CAD envelope needs.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import migjorn
import numpy as np

type Points = np.ndarray  # (N, 3) in cm


class McnpParseError(ValueError):
    pass


# --- transformations -------------------------------------------------------------


@dataclass
class Transform:
    displacement: np.ndarray
    rotation: np.ndarray  # rows are the auxiliary axes in main coordinates
    origin_in_main: bool = True  # M = 1

    def to_auxiliary(self, points: Points) -> Points:
        if self.origin_in_main:
            return (points - self.displacement) @ self.rotation.T
        return points @ self.rotation.T + self.displacement

    def to_main(self, points: Points) -> Points:
        if self.origin_in_main:
            return points @ self.rotation + self.displacement
        return (points - self.displacement) @ self.rotation

    @classmethod
    def from_values(cls, values: list[float], degrees: bool) -> Transform:
        displacement = np.array(values[:3], dtype=float)
        if len(values) == 3:
            return cls(displacement, np.eye(3))
        if len(values) not in (12, 13):
            raise McnpParseError("Only TR cards with 3, 12 or 13 entries are supported")
        matrix = np.array(values[3:12], dtype=float)
        if degrees:
            matrix = np.cos(np.radians(matrix))
        # Entries are xx' yx' zx' xy' yy' zy' xz' yz' zz': each triplet is an
        # auxiliary axis expressed in main coordinates.
        rotation = matrix.reshape(3, 3)
        u, _, vt = np.linalg.svd(rotation)  # nearest orthonormal matrix, as MCNP does
        rotation = u @ vt
        origin_in_main = len(values) == 12 or values[12] != -1
        return cls(displacement, rotation, origin_in_main)


# --- surfaces --------------------------------------------------------------------

type SurfaceFunction = Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]

_AXES = {"x": 0, "y": 1, "z": 2}


def _quadric(
    a: float = 0,
    b: float = 0,
    c: float = 0,
    d: float = 0,
    e: float = 0,
    f: float = 0,
    g: float = 0,
    h: float = 0,
    j: float = 0,
    k: float = 0,
) -> SurfaceFunction:
    def evaluate(x, y, z):
        # Factored: fewer temporary arrays than the ten terms
        return (
            x * (a * x + d * y + f * z + g)
            + y * (b * y + e * z + h)
            + z * (c * z + j)
            + k
        )

    return evaluate


def _plane(normal: np.ndarray, offset: float) -> SurfaceFunction:
    nx, ny, nz = normal
    return lambda x, y, z: nx * x + ny * y + nz * z - offset


def _cylinder(axis: str, centre: tuple[float, float], radius: float) -> SurfaceFunction:
    i, j = [k for k in range(3) if k != _AXES[axis]]

    def evaluate(x, y, z):
        coords = (x, y, z)
        return (coords[i] - centre[0]) ** 2 + (coords[j] - centre[1]) ** 2 - radius**2

    return evaluate


def _cone(axis: str, apex: np.ndarray, t2: float, sheet: float) -> SurfaceFunction:
    a = _AXES[axis]
    i, j = [k for k in range(3) if k != a]

    def evaluate(x, y, z):
        coords = (x - apex[0], y - apex[1], z - apex[2])
        value = coords[i] ** 2 + coords[j] ** 2 - t2 * coords[a] ** 2
        if sheet:
            # Only one nappe exists: points on the other side are outside it
            value = np.where(sheet * coords[a] > 0, value, np.abs(value) + 1.0)
        return value

    return evaluate


def _torus(axis: str, centre: np.ndarray, a: float, b: float, c: float):
    k = _AXES[axis]
    i, j = [n for n in range(3) if n != k]

    def evaluate(x, y, z):
        coords = (x - centre[0], y - centre[1], z - centre[2])
        rho = np.sqrt(coords[i] ** 2 + coords[j] ** 2)
        return (coords[k] / b) ** 2 + ((rho - a) / c) ** 2 - 1.0

    return evaluate


def _plane_from_points(p: np.ndarray) -> SurfaceFunction:
    p1, p2, p3 = p.reshape(3, 3)
    normal = np.cross(p2 - p1, p3 - p1)
    if np.linalg.norm(normal) == 0:
        raise McnpParseError("The three points of the plane are aligned")
    offset = float(normal @ p1)
    # MCNP: the origin has negative sense; if the plane contains the origin, the
    # points (0,0,inf), (0,inf,0) and (inf,0,0) are tested in turn
    reference = -offset
    if abs(offset) < 1e-12 * max(1.0, float(np.abs(p).max())):
        for k in (2, 1, 0):
            if abs(normal[k]) > 1e-12:
                reference = -normal[k]
                break
    if reference > 0:
        normal, offset = -normal, -offset
    return _plane(normal, offset)


def _axisymmetric(axis: str, values: list[float]) -> SurfaceFunction:
    """X, Y, Z surfaces defined by two (coordinate, radius) pairs."""
    if len(values) != 4:
        raise McnpParseError(
            f"{axis.upper()} surfaces are only supported with 2 points"
        )
    s1, r1, s2, r2 = values
    a = _AXES[axis]
    if np.isclose(s1, s2):
        normal = np.zeros(3)
        normal[a] = 1
        return _plane(normal, s1)
    if np.isclose(r1, r2):
        return _cylinder(axis, (0.0, 0.0), abs(r1))
    slope = (r2 - r1) / (s2 - s1)
    apex = np.zeros(3)
    apex[a] = s1 - r1 / slope
    sheet = 1.0 if s1 > apex[a] else -1.0
    return _cone(axis, apex, slope**2, sheet)


def surface_function(kind: str, v: list[float]) -> SurfaceFunction:
    """Return f(x, y, z) whose sign is the MCNP sense of the surface."""
    kind = kind.lower()
    try:
        if kind == "p":
            if len(v) == 4:
                return _plane(np.array(v[:3]), v[3])
            return _plane_from_points(np.array(v[:9]))
        if kind in ("px", "py", "pz"):
            normal = np.zeros(3)
            normal[_AXES[kind[1]]] = 1
            return _plane(normal, v[0])
        if kind == "so":
            return _quadric(1, 1, 1, k=-(v[0] ** 2))
        if kind == "s":
            x0, y0, z0, r = v[:4]
            return _quadric(
                1,
                1,
                1,
                g=-2 * x0,
                h=-2 * y0,
                j=-2 * z0,
                k=x0 * x0 + y0 * y0 + z0 * z0 - r * r,
            )
        if kind in ("sx", "sy", "sz"):
            centre = np.zeros(3)
            centre[_AXES[kind[1]]] = v[0]
            return surface_function("s", [*centre, v[1]])
        if kind in ("c/x", "c/y", "c/z"):
            return _cylinder(kind[2], (v[0], v[1]), v[2])
        if kind in ("cx", "cy", "cz"):
            return _cylinder(kind[1], (0.0, 0.0), v[0])
        if kind in ("k/x", "k/y", "k/z"):
            sheet = v[4] if len(v) > 4 else 0.0
            return _cone(kind[2], np.array(v[:3]), v[3], sheet)
        if kind in ("kx", "ky", "kz"):
            apex = np.zeros(3)
            apex[_AXES[kind[1]]] = v[0]
            sheet = v[2] if len(v) > 2 else 0.0
            return _cone(kind[1], apex, v[1], sheet)
        if kind == "sq":
            a, b, c, d, e, f, g, x0, y0, z0 = v[:10]
            return lambda x, y, z: (
                a * (x - x0) ** 2
                + b * (y - y0) ** 2
                + c * (z - z0) ** 2
                + 2 * d * (x - x0)
                + 2 * e * (y - y0)
                + 2 * f * (z - z0)
                + g
            )
        if kind == "gq":
            return _quadric(*v[:10])
        if kind in ("tx", "ty", "tz"):
            return _torus(kind[1], np.array(v[:3]), v[3], v[4], v[5])
        if kind in ("x", "y", "z"):
            return _axisymmetric(kind, v)
    except (IndexError, ValueError) as exc:
        raise McnpParseError(f"Wrong number of parameters for surface {kind}") from exc
    raise McnpParseError(f"Unsupported surface type {kind!r}")


def _unit(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=float)
    return vector / np.linalg.norm(vector)


def macrobody_facets(kind: str, v: list[float]) -> list[SurfaceFunction]:
    """Facet functions of a macrobody, outward normal positive, in MCNP facet order."""
    kind = kind.lower()
    if kind == "rpp":
        x0, x1, y0, y1, z0, z1 = v[:6]
        return [
            lambda x, y, z: x - x1,
            lambda x, y, z: x0 - x,
            lambda x, y, z: y - y1,
            lambda x, y, z: y0 - y,
            lambda x, y, z: z - z1,
            lambda x, y, z: z0 - z,
        ]
    if kind == "box":
        corner = np.array(v[:3])
        vectors = [np.array(v[3 + 3 * i : 6 + 3 * i]) for i in range((len(v) - 3) // 3)]
        facets = []
        for vec in vectors:
            n = _unit(vec)
            facets.append(_plane(n, float(n @ (corner + vec))))
            facets.append(_plane(-n, float(-n @ corner)))
        return facets
    if kind == "sph":
        return [surface_function("s", v[:4])]
    if kind in ("rcc", "trc"):
        base, height = np.array(v[:3]), np.array(v[3:6])
        length = float(np.linalg.norm(height))
        n = height / length
        r1 = v[6]
        r2 = v[7] if kind == "trc" else r1

        def lateral(x, y, z):
            d = np.stack([x - base[0], y - base[1], z - base[2]], axis=-1)
            s = d @ n
            rho = np.sqrt(np.maximum(np.einsum("...i,...i->...", d, d) - s * s, 0))
            return rho - (r1 + (r2 - r1) * s / length)

        return [
            lateral,
            _plane(n, float(n @ (base + height))),
            _plane(-n, float(-n @ base)),
        ]
    raise McnpParseError(f"Unsupported macrobody {kind.upper()}")


MACROBODIES = {
    "rpp",
    "box",
    "sph",
    "rcc",
    "trc",
    "rhp",
    "hex",
    "rec",
    "ell",
    "wed",
    "arb",
}


# --- conservative bounding boxes ---------------------------------------------------
#
# Cells are only evaluated on the points inside a box that is sure to contain them.
# The boxes come from bounded surfaces (spheres, macrobodies, tori), cylinders and
# axis-aligned planes; anything else is considered unbounded.

type Box = tuple[np.ndarray, np.ndarray]  # (low corner, high corner)
INF = np.inf


def in_box(points: Points, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Mask of the points (N, d) inside the box from lo to hi. Infinite bounds are not
    compared, and columns are compared one at a time: much faster than ``np.all``."""
    inside = np.ones(len(points), dtype=bool)
    for k in range(points.shape[1]):
        if lo[k] > -INF:
            inside &= points[:, k] >= lo[k]
        if hi[k] < INF:
            inside &= points[:, k] <= hi[k]
    return inside


def _filters(box: Box) -> bool:
    """Whether a box is worth finding the points inside it: not for a half-space (of
    a PX plane), as evaluating the surface is cheaper than the filtering."""
    return int(np.isfinite(box[0]).sum() + np.isfinite(box[1]).sum()) >= 2


def _box(lo, hi) -> Box:
    return np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)


def _intersect(a: Box | None, b: Box | None) -> Box | None:
    if a is None:
        return b
    if b is None:
        return a
    return np.maximum(a[0], b[0]), np.minimum(a[1], b[1])


def _hull(a: Box | None, b: Box | None) -> Box | None:
    if a is None or b is None:
        return None
    return np.minimum(a[0], b[0]), np.maximum(a[1], b[1])


def _sphere_box(centre, radius) -> Box:
    centre = np.asarray(centre, dtype=float)
    return centre - abs(radius), centre + abs(radius)


def surface_bounds(kind: str, v: list[float]) -> tuple[Box | None, Box | None]:
    """Boxes containing the negative and the positive side of a surface."""
    kind = kind.lower()
    try:
        if kind in ("px", "py", "pz"):
            axis = _AXES[kind[1]]
            lo, hi = np.full(3, -INF), np.full(3, INF)
            below, above = hi.copy(), lo.copy()
            below[axis] = above[axis] = v[0]
            return (lo, below), (above, hi)
        if kind == "so":
            return _sphere_box([0, 0, 0], v[0]), None
        if kind == "s":
            return _sphere_box(v[:3], v[3]), None
        if kind in ("sx", "sy", "sz"):
            centre = np.zeros(3)
            centre[_AXES[kind[1]]] = v[0]
            return _sphere_box(centre, v[1]), None
        if kind in ("c/x", "c/y", "c/z", "cx", "cy", "cz"):
            axis = _AXES[kind[-1]]
            centre = [0.0, 0.0] if len(v) == 1 else v[:2]
            radius = v[-1] if len(v) == 1 else v[2]
            lo, hi = np.full(3, -INF), np.full(3, INF)
            others = [k for k in range(3) if k != axis]
            for k, c in zip(others, centre, strict=True):
                lo[k], hi[k] = c - abs(radius), c + abs(radius)
            return (lo, hi), None
        if kind in ("tx", "ty", "tz"):
            axis = _AXES[kind[1]]
            centre = np.array(v[:3])
            reach = np.full(3, abs(v[3]) + abs(v[5]))
            reach[axis] = abs(v[4])
            return (centre - reach, centre + reach), None
        if kind == "rpp":
            return _box(v[0:6:2], v[1:6:2]), None
        if kind == "sph":
            return _sphere_box(v[:3], v[3]), None
        if kind == "box":
            corner = np.array(v[:3])
            vectors = [np.array(v[3 + 3 * i : 6 + 3 * i]) for i in range(3)]
            corners = np.array(
                [
                    corner + a * vectors[0] + b * vectors[1] + c * vectors[2]
                    for a in (0, 1)
                    for b in (0, 1)
                    for c in (0, 1)
                ]
            )
            return _box(corners.min(axis=0), corners.max(axis=0)), None
        if kind in ("rcc", "trc"):
            base, height = np.array(v[:3]), np.array(v[3:6])
            radius = max(abs(v[6]), abs(v[7]) if kind == "trc" else 0.0)
            ends = np.array([base, base + height])
            return _box(ends.min(axis=0) - radius, ends.max(axis=0) + radius), None
    except (IndexError, ValueError):
        return None, None
    return None, None


def _transform_box(box: Box | None, transform: Transform) -> Box | None:
    if box is None or not (np.all(np.isfinite(box[0])) and np.all(np.isfinite(box[1]))):
        return None
    corners = np.array(
        [
            [box[i][0], box[j][1], box[k][2]]
            for i in (0, 1)
            for j in (0, 1)
            for k in (0, 1)
        ]
    )
    main = transform.to_main(corners)
    return main.min(axis=0), main.max(axis=0)


@dataclass
class Surface:
    name: str
    kind: str
    function: SurfaceFunction | None
    transform: int | None = None
    unsupported: str = ""  # reason why the surface cannot be evaluated
    # Boxes containing its negative and positive sides, before the transformation
    bounds: tuple[Box | None, Box | None] = (None, None)

    def evaluate(self, points: Points, transforms: dict[int, Transform]) -> np.ndarray:
        if self.function is None:
            raise McnpParseError(f"Surface {self.name}: {self.unsupported}")
        if self.transform is not None:
            if self.transform not in transforms:
                raise McnpParseError(
                    f"Surface {self.name}: TR{self.transform} not found"
                )
            points = transforms[self.transform].to_auxiliary(points)
        return self.function(points[:, 0], points[:, 1], points[:, 2])


# --- cell geometry expressions -----------------------------------------------------


@dataclass
class Node:
    op: str  # "surf", "and", "or", "not", "cell"
    children: list[Node] = field(default_factory=list)
    surface: str = ""
    positive: bool = True
    cell: int = 0

    def surfaces(self) -> set[str]:
        if self.op == "surf":
            return {self.surface}
        result: set[str] = set()
        for child in self.children:
            result |= child.surfaces()
        return result

    def complemented_cells(self) -> set[int]:
        if self.op == "cell":
            return {self.cell}
        result: set[int] = set()
        for child in self.children:
            result |= child.complemented_cells()
        return result


_TOKEN = re.compile(r"\s*(\(|\)|:|#|[+-]?\d+(?:\.\d+)?)")


def parse_geometry(text: str) -> Node:
    tokens, position = [], 0
    text = text.strip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            raise McnpParseError(
                f"Invalid geometry near {text[position : position + 20]!r}"
            )
        tokens.append(match.group(1))
        position = match.end()
        while position < len(text) and text[position].isspace():
            position += 1
    if not tokens:
        raise McnpParseError("Empty cell geometry")

    index = 0

    def peek() -> str | None:
        return tokens[index] if index < len(tokens) else None

    def take() -> str:
        nonlocal index
        index += 1
        return tokens[index - 1]

    def union() -> Node:
        terms = [intersection()]
        while peek() == ":":
            take()
            terms.append(intersection())
        return terms[0] if len(terms) == 1 else Node("or", terms)

    def intersection() -> Node:
        factors = [factor()]
        while peek() not in (None, ":", ")"):
            factors.append(factor())
        return factors[0] if len(factors) == 1 else Node("and", factors)

    def factor() -> Node:
        token = take()
        if token == "(":
            node = union()
            if take() != ")":
                raise McnpParseError("Unbalanced parentheses")
            return node
        if token == "#":
            nxt = take()
            if nxt == "(":
                node = union()
                if take() != ")":
                    raise McnpParseError("Unbalanced parentheses")
                return Node("not", [node])
            return Node("cell", cell=int(nxt))
        if token in (")", ":"):
            raise McnpParseError(f"Unexpected {token!r} in cell geometry")
        positive = not token.startswith("-")
        return Node("surf", surface=token.lstrip("+-"), positive=positive)

    try:
        node = union()
    except IndexError as exc:
        raise McnpParseError("Incomplete cell geometry") from exc
    if index != len(tokens):
        raise McnpParseError("Unbalanced parentheses")
    return node


# --- cells and surfaces from a migjorn model ---------------------------------------


@dataclass
class Cell:
    number: int
    material: int
    density: float | None
    geometry: Node | None
    universe: int = 0
    filled: bool = False
    like: int | None = None
    unsupported: str = ""  # reason why the cell cannot be evaluated


def _param(card: migjorn.Cell, key: str) -> str | None:
    for param in card.params:
        if param.key.lower() == key:
            return param.value
    return None


def _cell_from_card(card: migjorn.Cell) -> Cell:
    number = card.id
    assert number is not None
    filled = _param(card, "fill") is not None
    if card.like is not None:
        # LIKE n BUT: material and geometry are resolved against the base cell later
        mat, rho = _param(card, "mat"), _param(card, "rho")
        cell = Cell(
            number,
            material=int(mat) if mat is not None else -1,
            density=float(rho) if rho is not None else None,
            geometry=None,
            universe=card.universe or 0,
            filled=filled,
            like=card.like,
        )
        if _param(card, "trcl") is not None:
            cell.unsupported = "LIKE BUT with TRCL"
        return cell
    cell = Cell(
        number,
        material=card.material or 0,
        density=card.density,
        geometry=None,
        universe=card.universe or 0,
        filled=filled,
    )
    try:
        cell.geometry = parse_geometry(card.geometry_text)
    except McnpParseError as exc:
        cell.unsupported = str(exc)
    if _param(card, "trcl") is not None:
        cell.unsupported = "TRCL"
    return cell


def _surfaces_from_card(card: migjorn.Surface) -> list[Surface]:
    """Surfaces of a card. Macrobodies give one surface per facet plus the body."""
    name = str(card.id)
    kind = (card.kind or "").lower()
    transform = card.transform
    if transform is not None and transform < 0:
        return [Surface(name, kind, None, None, "periodic surfaces are not supported")]
    values = list(card.coeffs)

    if kind in MACROBODIES:
        try:
            facets = macrobody_facets(kind, values)
        except McnpParseError as exc:
            return [Surface(name, kind, None, transform, str(exc))]
        surfaces = [
            Surface(f"{name}.{i}", kind, f, transform) for i, f in enumerate(facets, 1)
        ]

        def body(x, y, z, facets=facets):
            return np.max([f(x, y, z) for f in facets], axis=0)

        bounds = surface_bounds(kind, values)
        surfaces.append(Surface(name, kind, body, transform, bounds=bounds))
        return surfaces
    try:
        function = surface_function(kind, values)
    except McnpParseError as exc:
        return [Surface(name, kind, None, transform, str(exc))]
    bounds = surface_bounds(kind, values)
    return [Surface(name, kind, function, transform, bounds=bounds)]


def read_model(path: str | os.PathLike) -> migjorn.Model:
    """Read an MCNP input with migjorn, whatever its text encoding."""
    with open(path, "rb") as infile:
        data = infile.read()
    for encoding in ("utf-8", "cp1252"):
        try:
            return migjorn.parse(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return migjorn.parse(data.decode("latin-1"))


# --- the full model --------------------------------------------------------------


class Geometry:
    """The cells, surfaces and transformations of an MCNP model."""

    def __init__(self, model: migjorn.Model):
        self.model = model
        self.cells: dict[int, Cell] = {}
        self.surfaces: dict[str, Surface] = {}
        self.transforms: dict[int, Transform] = {}
        self._cell_boxes: dict[int, Box | None] = {}
        self._surface_boxes: dict[tuple[str, bool], Box | None] = {}

        for card in model.transforms():
            if card.id is not None:
                self.transforms[card.id] = Transform.from_values(
                    list(card.coeffs), degrees=card.degrees
                )
        for card in model.surfaces():
            if card.id is not None:
                for surface in _surfaces_from_card(card):
                    self.surfaces[surface.name] = surface
        for card in model.cells():
            if card.id is None:
                raise McnpParseError(f"Unreadable cell card: {card.text.strip()!r}")
            self.cells[card.id] = _cell_from_card(card)
        self._resolve_like_but()

    @classmethod
    def from_file(cls, path: str | os.PathLike) -> Geometry:
        return cls(read_model(path))

    def _resolve_like_but(self) -> None:
        for cell in self.cells.values():
            if cell.like is None:
                continue
            seen = {cell.number}
            base = cell
            while base.like is not None:
                if base.like not in self.cells or base.like in seen:
                    raise McnpParseError(f"Cell {cell.number}: invalid LIKE chain")
                seen.add(base.like)
                base = self.cells[base.like]
            cell.geometry = base.geometry
            cell.unsupported = cell.unsupported or base.unsupported
            if cell.material == -1:
                cell.material = base.material
                cell.density = (
                    cell.density if cell.density is not None else base.density
                )

    def level_zero_cells(self) -> list[int]:
        return [n for n, c in self.cells.items() if c.universe == 0]

    def surface_box(self, name: str, positive: bool) -> Box | None:
        if (name, positive) not in self._surface_boxes:
            self._surface_boxes[name, positive] = self._surface_box(name, positive)
        return self._surface_boxes[name, positive]

    def _surface_box(self, name: str, positive: bool) -> Box | None:
        surface = self.surfaces.get(name)
        if surface is None:
            return None
        box = surface.bounds[1 if positive else 0]
        if surface.transform is not None:
            transform = self.transforms.get(surface.transform)
            return _transform_box(box, transform) if transform else None
        return box

    def cell_box(self, number: int) -> Box | None:
        """A box that contains the cell, or None if it could not be bounded."""
        if number not in self._cell_boxes:
            geometry = self.cells[number].geometry
            self._cell_boxes[number] = (
                self._node_box(geometry) if geometry is not None else None
            )
        return self._cell_boxes[number]

    def _node_box(self, node: Node) -> Box | None:
        if node.op == "surf":
            return self.surface_box(node.surface, node.positive)
        if node.op == "and":
            box: Box | None = None
            for child in node.children:
                box = _intersect(box, self._node_box(child))
            return box
        if node.op == "or":
            boxes = [self._node_box(child) for child in node.children]
            result = boxes[0]
            for other in boxes[1:]:
                result = _hull(result, other)
            return result
        return None  # complements are unbounded

    def evaluator(self, points: Points) -> Evaluator:
        return Evaluator(self, points)


class Evaluator:
    """Classifies a fixed set of points, caching surface senses and cell masks."""

    def __init__(self, geometry: Geometry, points: Points, subsets: bool = True):
        self.geometry = geometry
        self.points = np.asarray(points, dtype=float)
        self.subsets = subsets  # evaluate cells only inside their bounding boxes
        self._senses: dict[str, np.ndarray] = {}
        self._cells: dict[int, np.ndarray] = {}
        self._in_progress: set[int] = set()
        self._x_order: np.ndarray | None = None
        self._x_sorted = np.empty(0)

    def sense(self, surface: str) -> np.ndarray:
        if surface not in self._senses:
            if surface not in self.geometry.surfaces:
                raise McnpParseError(f"Surface {surface} is not defined")
            evaluated = self.geometry.surfaces[surface]
            # Outside the box of one side, the sense is known without evaluating
            for positive_box in (False, True):
                box = self.geometry.surface_box(surface, positive_box)
                if box is None or not self.subsets or not _filters(box):
                    continue
                index = self._points_in(box)
                sense = np.full(len(self.points), not positive_box)
                values = evaluated.evaluate(
                    self.points[index], self.geometry.transforms
                )
                sense[index] = values > 0
                break
            else:
                values = evaluated.evaluate(self.points, self.geometry.transforms)
                sense = values > 0
            self._senses[surface] = sense
        return self._senses[surface]

    def _points_in(self, box: Box) -> np.ndarray:
        """Indices of the points inside a box, using the points sorted along x."""
        if self._x_order is None:
            self._x_order = np.argsort(self.points[:, 0], kind="stable")
            self._x_sorted = self.points[self._x_order, 0]
        start = np.searchsorted(self._x_sorted, box[0][0], "left")
        end = np.searchsorted(self._x_sorted, box[1][0], "right")
        candidates = self._x_order[start:end]
        yz = self.points[candidates, 1:]
        return candidates[in_box(yz, box[0][1:], box[1][1:])]

    def cell(self, number: int) -> np.ndarray:
        """Boolean mask of the points inside a cell."""
        if number not in self._cells:
            if number not in self.geometry.cells:
                raise McnpParseError(f"Cell {number} is not defined")
            if number in self._in_progress:
                raise McnpParseError(f"Cell {number} is defined through itself")
            cell = self.geometry.cells[number]
            if cell.unsupported:
                raise McnpParseError(
                    f"Cell {number}: {cell.unsupported} is not supported"
                )
            self._in_progress.add(number)
            geometry = cell.geometry
            assert geometry is not None
            self._cells[number] = self._evaluate_in_box(number, geometry)
            self._in_progress.discard(number)
        return self._cells[number]

    def _evaluate_in_box(self, number: int, geometry: Node) -> np.ndarray:
        """Evaluate a cell only on the points inside its bounding box."""
        box = self.geometry.cell_box(number)
        if box is None or not self.subsets:
            return self._evaluate(geometry)
        index = self._points_in(box)
        if len(index) > 0.5 * len(self.points):
            return self._evaluate(geometry)  # not worth it: shares the surface cache
        mask = np.zeros(len(self.points), dtype=bool)
        if len(index):
            sub = Evaluator(self.geometry, self.points[index])
            sub._in_progress = self._in_progress
            mask[index] = sub._evaluate(geometry)
        return mask

    def _evaluate(self, node: Node) -> np.ndarray:
        if node.op == "surf":
            sense = self.sense(node.surface)
            return sense if node.positive else ~sense
        if node.op == "cell":
            return ~self.cell(node.cell)
        if node.op == "not":
            return ~self._evaluate(node.children[0])
        masks = (self._evaluate(child) for child in node.children)
        result = next(masks).copy()
        for mask in masks:
            if node.op == "and":
                result &= mask
            else:
                result |= mask
        return result

    def cell_index(
        self, cells: list[int] | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """Assign every point to a cell.

        Returns:
            Cell number per point (0 where no cell contains it) and the number of cells
            containing each point (values above 1 reveal overlapping cells).
        """
        cells = self.geometry.level_zero_cells() if cells is None else cells
        owner = np.zeros(len(self.points), dtype=np.int64)
        count = np.zeros(len(self.points), dtype=np.int32)
        for number in cells:
            mask = self.cell(number)
            owner[mask & (count == 0)] = number
            count += mask
        return owner, count
