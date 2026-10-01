# RadModeling: colour the components whose volume deviates from the original.
#
# Red: beyond the tolerance. Blue transparent: within it. Grey: untagged bodies.
# The same check, plus geometry diagnostics, is 'radmod check' on an exported STEP.
TOLERANCE_PCT = 1.0


def rm_check_volumes():
    registry = rm_read_registry()
    by_id, untagged = rm_bodies_by_id()
    exceeding, fine, unknown = [], [], []
    for component_id, bodies in by_id.items():
        row = registry.get(component_id)
        original = rm_float(row.get("original_volume_cm3")) if row else None
        if not original:
            unknown.append(component_id)
            continue
        difference = (rm_volume_cm3(bodies) - original) / original * 100
        (exceeding if abs(difference) > TOLERANCE_PCT else fine).extend(bodies)

    rm_set_color(fine, RM_BLUE, transparent=True)
    rm_set_color(exceeding, RM_RED)
    rm_set_color(untagged, RM_GREY)
    rm_message(
        "Bodies beyond +/-%g%% volume: %d\nUntagged bodies: %d\n"
        "Tags missing from the registry: %s"
        % (TOLERANCE_PCT, len(exceeding), len(untagged), sorted(unknown) or "none")
    )


rm_check_volumes()
