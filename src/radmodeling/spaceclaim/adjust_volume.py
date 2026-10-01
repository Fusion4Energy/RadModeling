# RadModeling: extrude the selected faces until the component recovers its original
# volume (from the registry). Select one or more faces of a single component first.
TOLERANCE_PCT = 0.5
MAX_ITERATIONS = 25


def rm_adjust_volume():
    selection = Selection.GetActive()
    faces = list(selection.Items)
    if not faces:
        rm_message("Select the faces to extrude first.")
        return
    body = faces[0].Parent
    component_id = rm_body_id(body)
    if component_id is None:
        rm_message("The selected faces do not belong to a tagged component.")
        return
    row = rm_read_registry().get(component_id)
    target = rm_float(row.get("original_volume_cm3")) if row else None
    if not target:
        rm_message(
            "Component %d has no original volume in the registry." % component_id
        )
        return

    bodies = rm_bodies_by_id()[0][component_id]
    options = ExtrudeFaceOptions()
    options.ExtrudeType = ExtrudeType.ForceIndependent

    def error():
        return rm_volume_cm3(bodies) - target

    try:
        area_cm2 = sum(face.Area for face in faces) * 1.0e4
    except Exception:
        area_cm2 = None

    # Secant iterations on the total extrusion distance (mm)
    distance, value = 0.0, error()
    step = -value / area_cm2 * 10.0 if area_cm2 else 1.0
    iterations = 0
    while abs(value) / target * 100 > TOLERANCE_PCT and iterations < MAX_ITERATIONS:
        ExtrudeFaces.Execute(selection, MM(step), options)
        new_distance, new_value = distance + step, error()
        slope = (new_value - value) / step if step else 0.0
        distance, value = new_distance, new_value
        step = -value / slope if slope else step / 2.0
        iterations += 1

    rm_set_color(bodies, RM_BLUE, transparent=True)
    rm_message(
        "Component %d: extruded %.3f mm, volume difference %.3f%% (%d iterations)"
        % (component_id, distance, value / target * 100, iterations)
    )


rm_adjust_volume()
