# RadModeling: replace the selected toroidal pipe bends by mitred cylinder segments.
# Select a torus face of each bend (the outer one is enough: the inner torus of a
# hollow pipe is found) and run it.
#
# The bend may be a body of its own or part of a longer pipe. The ends of the mitred
# bend are perpendicular to the pipe, so it joins the straight parts exactly. The
# volume grows slightly (+1.3 % for 90 degrees in 4 segments): the density correction
# factor absorbs it. The geometry lives in _mitre.py, which is tested outside
# SpaceClaim.
#
# The result replaces the body with a new merged body of the same name, layer and
# colour in the same part, so linked instances change together (or with the separate
# pieces if they do not unite cleanly). The volumes are checked on the pieces:
# SpaceClaim measures the merged body wrong. The body is left unchanged if the pieces
# do not have the expected volumes.
import math
import os

from System.Collections.Generic import List

MAX_SEGMENT_ANGLE = 22.5  # degrees turned by each segment at most (90 at most)
MIN_SEGMENTS = 2
VOLUME_TOLERANCE_PCT = 0.5  # of the bend volume
RELATIVE_TOLERANCE = 1e-6  # of the bend size, to match tori and cross sections

execfile(os.path.join(RADMODELING_SPACECLAIM, "_mitre.py"))


def rm_xyz(v):
    return (v.X, v.Y, v.Z)


def rm_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def rm_cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def rm_to_world(frame, x, y, z=0.0):
    """Point of the bend frame (origin, x, y, z axes) in the body coordinates."""
    o, ex, ey, ez = frame
    return Point.Create(*[o[i] + x * ex[i] + y * ey[i] + z * ez[i] for i in range(3)])


def rm_to_bend(frame, point):
    o, ex, ey, ez = frame
    d = [point[i] - o[i] for i in range(3)]
    return (rm_dot(d, ex), rm_dot(d, ey), rm_dot(d, ez))


def rm_geometry(topology, kind):
    try:
        geometry = topology.Geometry
    except Exception:
        return None
    return geometry if type(geometry).__name__ == kind else None


def rm_find_elbow(body, torus):
    """The bend of the body around the torus: frame, radii, start angle and sweep."""
    f = torus.Frame
    frame = (rm_xyz(f.Origin), rm_xyz(f.DirX), rm_xyz(f.DirY), rm_xyz(f.DirZ))
    major = torus.MajorRadius
    tolerance = RELATIVE_TOLERANCE * (major + torus.MinorRadius)
    faces, radii = [], []
    for face in body.Faces:
        other = rm_geometry(face, "Torus")
        if other is None:
            continue
        g = other.Frame
        if (
            max(abs(c) for c in rm_to_bend(frame, rm_xyz(g.Origin))) > tolerance
            or abs(abs(rm_dot(rm_xyz(g.DirZ), frame[3])) - 1) > 1e-9
            or abs(other.MajorRadius - major) > tolerance
        ):
            continue
        faces.append(face)
        if not any(abs(other.MinorRadius - r) <= tolerance for r in radii):
            radii.append(other.MinorRadius)
    if len(radii) > 2:
        raise ValueError("more than two pipe radii share the bend")
    radii.sort(reverse=True)

    angles, inside = [], []
    for face in faces:
        for edge in face.Edges:
            circle = rm_geometry(edge, "Circle")
            if circle is None:
                continue
            centre = rm_to_bend(frame, rm_xyz(circle.Frame.Origin))
            angle = rm_cross_section_angle(
                centre, circle.Radius, major, radii, tolerance
            )
            if angle is not None:
                angles.append(angle)
        try:  # a point of the face tells which side of its ends the bend is
            point = face.Geometry.Evaluate(face.BoxUV.Center).Point
            x, y, _ = rm_to_bend(frame, rm_xyz(point))
            inside.append(math.atan2(y, x))
        except Exception:
            pass
    start, sweep = rm_bend_ends(angles, inside)
    return {
        "frame": frame,
        "major": major,
        "radii": radii,
        "start": start,
        "sweep": sweep,
        "tolerance": tolerance,
    }


def rm_same_elbow(a, b):
    tolerance = a["tolerance"]
    return (
        max(abs(c) for c in rm_to_bend(a["frame"], b["frame"][0])) <= tolerance
        and abs(a["major"] - b["major"]) <= tolerance
        and abs(a["sweep"] - b["sweep"]) <= 1e-6
    )


def rm_prism(frame, polygon, half_height):
    """Prism of a polygon of the bend plane, from -half_height to half_height."""
    base = Frame.Create(
        rm_to_world(frame, 0.0, 0.0, -half_height),
        Direction.Create(*frame[1]),
        Direction.Create(*frame[2]),
    )
    # A typed list: with a Python list the PointUV overload is just as good a match
    points = List[Point]([rm_to_world(frame, x, y, -half_height) for x, y in polygon])
    return Body.ExtrudeProfile(
        PolygonProfile(Plane.Create(base), points), 2 * half_height
    )


def rm_tube(frame, piece, radii):
    (x, y), (dx, dy) = piece["start"], piece["direction"]
    o, ex, ey, ez = frame
    direction = tuple(dx * ex[i] + dy * ey[i] for i in range(3))
    # The profile plane normal (x cross y) must be the tube direction
    base = Frame.Create(
        rm_to_world(frame, x, y),
        Direction.Create(*ez),
        Direction.Create(*rm_cross(direction, ez)),
    )
    plane = Plane.Create(base)
    length = piece["length"]

    def cylinder(radius):
        # The centre is optional, but IronPython passes None for its default
        profile = CircleProfile(plane, radius, PointUV.Create(0.0, 0.0))
        return Body.ExtrudeProfile(profile, length)

    tube = cylinder(radii[0])
    if len(radii) > 1:
        tube.Subtract(cylinder(radii[1]))
    return tube


def rm_copy_appearance(source, target):
    """Layer and colour of a body for its replacement (best effort)."""
    try:
        target.Layer = source.Layer
    except Exception:
        pass
    try:
        target.SetColor(None, source.GetColor(None))
    except Exception:
        pass


def rm_bend_line(elbow):
    m_to_mm = 1000.0
    return (
        "  Bend found: from %.4g deg, %.4g deg, bend radius %.4g mm, pipe radii %s mm."
        % (
            math.degrees(elbow["start"]),
            math.degrees(elbow["sweep"]),
            elbow["major"] * m_to_mm,
            " and ".join("%.4g" % (r * m_to_mm) for r in elbow["radii"]),
        )
    )


def rm_create(part, name, body):
    """Design bodies of the connected pieces of a modeler body (a design body must
    be connected: the rest of a pipe is two legs)."""
    return [DesignBody.Create(part, name, lump) for lump in body.SeparatePieces()]


def rm_mass(bodies):
    return sum(body.MassProperties.Mass for body in bodies)


def rm_pieces(design, elbow):
    """The rest of the body and the mitred segments, with their expected volumes."""
    frame, major, radii = elbow["frame"], elbow["major"], elbow["radii"]
    start, sweep = elbow["start"], elbow["sweep"]
    count = rm_segment_count(sweep, MAX_SEGMENT_ANGLE, MIN_SEGMENTS)
    bend, _ = rm_bend_volumes(sweep, major, radii, count)
    area = rm_pipe_area(radii)

    rest = design.Shape.Copy()
    polygon, half_height = rm_bend_cutter(start, sweep, major, radii[0])
    rest.Subtract(rm_prism(frame, polygon, half_height))
    pieces = []
    if rest.Faces.Count:  # nothing is left of a bare elbow
        pieces.append(("rest of the body", rest, design.MassProperties.Mass - bend))
    segments = rm_mitre_segments(start, sweep, major, radii[0], count)
    for i, piece in enumerate(segments):
        tube = rm_tube(frame, piece, radii)
        tube.Intersect(rm_prism(frame, piece["wedge"], piece["half_height"]))
        pieces.append(("segment %d" % (i + 1), tube, area * piece["side"]))
    return pieces, count


def rm_merged(pieces):
    """Modeler body uniting the pieces, or None if they do not unite into a single
    body without tori.

    Its volume cannot be checked: SpaceClaim's mass properties of such a union are
    wrong (a hollow pipe measured -520 and +104 mm3 off, while OCCT found the same
    body valid and exact), so the volumes are checked on the pieces instead.
    """
    try:
        result = pieces[0][1].Copy()
        for _, body, _ in pieces[1:]:
            result.Unite(body.Copy())
        if len(list(result.SeparatePieces())) != 1:
            return None
        if any(rm_geometry(face, "Torus") is not None for face in result.Faces):
            return None
        return result
    except Exception:
        return None


def rm_check_report(part, elbow, pieces):
    """Leave the pieces as bodies to look at, and compare their volumes with the
    expected ones."""
    m_to_mm3 = 1.0e9
    lines = [rm_bend_line(elbow)]
    for label, body, volume in pieces:
        created = rm_create(part, "RadModeling check: " + label, body)
        lines.append(
            "  %s: %.6g mm3, expected %.6g mm3."
            % (label, rm_mass(created) * m_to_mm3, volume * m_to_mm3)
        )
    lines.append("  The pieces are left as 'RadModeling check' bodies to look at")
    return "\n".join(lines)


def rm_replace(design, elbow):
    """Replace one bend of a design body.

    DesignBody.Shape is read-only, so the result is made of new bodies with the same
    name in the same part (linked instances follow): one merged body, or the pieces
    if they do not unite cleanly. The volumes are checked on the pieces, and the
    original is deleted only once they match. Returns the new bodies, the segments,
    the volume change relative to the bend and whether the bodies were merged.
    """
    part, name = design.Parent, rm_name(design)
    pieces, count = rm_pieces(design, elbow)
    bend, mitred = rm_bend_volumes(
        elbow["sweep"], elbow["major"], elbow["radii"], count
    )
    original = design.MassProperties.Mass
    tolerance = VOLUME_TOLERANCE_PCT / 100 * bend

    merged = rm_merged(pieces)  # from copies, before the pieces go to the document
    bodies = []
    for _, body, _ in pieces:
        bodies.extend(rm_create(part, name, body))
    change = rm_mass(bodies) - original
    if abs(change - (mitred - bend)) > tolerance:
        for body in bodies:
            body.Delete()
        raise ValueError(
            "the pieces do not have the expected volumes.\n"
            + rm_check_report(part, elbow, pieces)
        )
    if merged is not None:
        for body in bodies:
            body.Delete()
        bodies = [DesignBody.Create(part, name, merged)]
    for body in bodies:
        rm_copy_appearance(design, body)
    design.Delete()
    return bodies, count, change / bend, merged is not None


def rm_holder(bodies, elbow):
    """The body among the replacements that holds the torus of a bend."""
    for body in bodies:
        for face in body.Shape.Faces:
            torus = rm_geometry(face, "Torus")
            if torus is None:
                continue
            centre = rm_to_bend(elbow["frame"], rm_xyz(torus.Frame.Origin))
            if (
                max(abs(c) for c in centre) <= elbow["tolerance"]
                and abs(torus.MajorRadius - elbow["major"]) <= elbow["tolerance"]
            ):
                return body
    return None


def rm_replace_elbows():
    found, problems = [], []
    for item in Selection.GetActive().Items:
        try:
            face = item.Master
        except AttributeError:
            face = item
        torus = rm_geometry(getattr(face, "Shape", None), "Torus")
        if torus is None:
            continue
        design = face.Parent
        try:
            elbow = rm_find_elbow(design.Shape, torus)
        except ValueError as exc:
            problems.append("%s: not replaced, %s." % (rm_name(design), exc))
            continue
        if not any(d == design and rm_same_elbow(e, elbow) for d, e in found):
            found.append((design, elbow))
    if not found and not problems:
        rm_message("Select a torus face of each elbow to replace first.")
        return

    lines = []
    for i, (design, elbow) in enumerate(found):
        if design is None:
            lines.append("A bend was not replaced: its body was not found again.")
            continue
        name = rm_name(design)
        try:
            bodies, count, change, merged = rm_replace(design, elbow)
        except Exception as exc:
            lines.append("%s: not replaced, %s." % (name, exc))
            continue
        # The other bends of the body are now in one of its replacements
        found[i + 1 :] = [
            (rm_holder(bodies, e) if d == design else d, e) for d, e in found[i + 1 :]
        ]
        lines.append(
            "%s: %.4g deg bend -> %d segments, volume %+.2f%% of the bend, %s."
            % (
                name,
                math.degrees(elbow["sweep"]),
                count,
                change * 100,
                "merged" if merged else "%d separate bodies" % len(bodies),
            )
        )
    rm_message("\n".join(lines + problems))


rm_replace_elbows()
