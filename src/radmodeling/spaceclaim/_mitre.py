# Mitred pipe bends: the geometry behind the 'Replace elbows' SpaceClaim tool.
#
# Shared by SpaceClaim's IronPython 2.7 (loaded with execfile) and CPython, where it
# is tested: plain Python 2/3, no f-strings, no type hints, names prefixed with rm_.
#
# A bend is described in its own frame: origin at the centre of the torus, z along
# its axis, angles counterclockwise around z. The mitred bend follows the tangent
# polygon of the bend centre line: a half segment at each end, starting perpendicular
# to it so that it joins the adjacent pipe exactly, and full segments in between.
# Every cut, at the ends and between segments, is a plane through the axis, so each
# segment is a straight tube trimmed by a wedge, and the bend itself is removed from
# the pipe with a wedge.
import math

RM_TWO_PI = 2 * math.pi
RM_WEDGE_MARGIN = 1.5  # wedge height and reach beyond the pipe, in outer radii


def rm_polar(angle, radius):
    return (radius * math.cos(angle), radius * math.sin(angle))


def rm_cross_section_angle(centre, radius, major, minor_radii, tolerance):
    """Angle of a circle edge of the torus faces if it is a cross section of the
    pipe (centre in the bend frame), else None."""
    x, y, z = centre
    if abs(z) > tolerance or abs(math.hypot(x, y) - major) > tolerance:
        return None
    if not any(abs(radius - r) <= tolerance for r in minor_radii):
        return None
    return math.atan2(y, x) % RM_TWO_PI


def rm_bend_ends(angles, inside=(), tolerance=1e-7):
    """Start angle and sweep of a bend from the angles of its cross sections.

    The cross sections are at the two ends and on the seams of the faces. The bend
    fills every gap between consecutive cross sections but one: the largest gap free
    of the ``inside`` angles (points known to be in the bend, such as face centres).
    Without them a bend is assumed to turn less than 180 degrees.
    """
    distinct = []
    for angle in sorted(a % RM_TWO_PI for a in angles):
        if not distinct or angle - distinct[-1] > tolerance:
            distinct.append(angle)
    if len(distinct) > 1 and distinct[0] + RM_TWO_PI - distinct[-1] <= tolerance:
        distinct.pop()
    if len(distinct) < 2:
        raise ValueError("the torus does not have two ends")
    best = None
    for i, a in enumerate(distinct):
        b = distinct[(i + 1) % len(distinct)]
        gap = (b - a) % RM_TWO_PI
        filled = any(0 < (p - a) % RM_TWO_PI < gap for p in inside)
        if not filled and (best is None or gap > best[0]):
            best = (gap, b)
    if best is None:
        raise ValueError("the torus has no gap between its ends")
    gap, start = best
    return start, RM_TWO_PI - gap


def rm_segment_count(sweep, max_angle_degrees, minimum):
    """Segments needed so that none turns more than max_angle_degrees (<= 90)."""
    needed = int(math.ceil(math.degrees(sweep) / max_angle_degrees - 1e-9))
    return max(minimum, needed)


def rm_wedge(a, b, reach):
    """Polygon in the bend plane covering the sector between angles a < b (less than
    a turn) out to the distance reach from the centre."""
    count = max(1, int(math.ceil((b - a) / (math.pi / 4))))
    step = (b - a) / count
    far = reach / math.cos(step / 2)
    return [(0.0, 0.0)] + [rm_polar(a + i * step, far) for i in range(count + 1)]


def rm_bend_cutter(start, sweep, major, outer_radius):
    """Wedge that removes the bend from the pipe: polygon and half height."""
    margin = RM_WEDGE_MARGIN * outer_radius
    return rm_wedge(start, start + sweep, major + margin), margin


def rm_mitre_segments(start, sweep, major, outer_radius, count):
    """The count + 1 pieces of a mitred bend of count segments.

    Each piece is a dict: the ``wedge`` polygon and ``half_height`` that trim it, and
    a tube along the polygon side (``start`` point, unit ``direction`` and ``length``,
    in the bend plane) that overshoots both cuts, so that the wedge gives its ends.
    ``side`` is the length of the polygon side: the volume of the piece is the pipe
    cross section times it.
    """
    delta = sweep / count
    corners = [start + (i + 0.5) * delta for i in range(count)]
    cuts = [start] + corners + [start + sweep]
    corner_radius = major / math.cos(delta / 2)
    vertices = (
        [rm_polar(start, major)]
        + [rm_polar(a, corner_radius) for a in corners]
        + [rm_polar(start + sweep, major)]
    )
    margin = outer_radius * (math.tan(delta / 2) + 1)
    reach = (major + outer_radius) / math.cos(delta / 2) + outer_radius
    half_height = RM_WEDGE_MARGIN * outer_radius
    pieces = []
    for i in range(count + 1):
        (x0, y0), (x1, y1) = vertices[i], vertices[i + 1]
        side = math.hypot(x1 - x0, y1 - y0)
        dx, dy = (x1 - x0) / side, (y1 - y0) / side
        pieces.append(
            {
                "wedge": rm_wedge(cuts[i], cuts[i + 1], reach),
                "half_height": half_height,
                "start": (x0 - margin * dx, y0 - margin * dy),
                "direction": (dx, dy),
                "length": side + 2 * margin,
                "side": side,
            }
        )
    return pieces


def rm_pipe_area(minor_radii):
    """Cross section of a pipe: outer radius, and inner one if hollow."""
    radii = sorted(minor_radii, reverse=True)
    return math.pi * (radii[0] ** 2 - (radii[1] ** 2 if len(radii) > 1 else 0.0))


def rm_bend_volumes(sweep, major, minor_radii, count):
    """Volume of the toroidal bend and of its mitred replacement.

    A tube cut by oblique planes keeps the volume of its cross section times its
    axis length, so the mitred bend grows by tan(d/2) / (d/2) for a turn d per
    segment (+1.3 % for 90 degrees in 4 segments).
    """
    area = rm_pipe_area(minor_radii)
    delta = sweep / count
    return area * major * sweep, area * 2 * count * major * math.tan(delta / 2)
