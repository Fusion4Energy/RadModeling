# RadModeling: colour the instances whose volume deviates from the original.
# Each instance is compared with the original volume of one instance, so deleting
# some instances of a component does not affect the others.
#
# Red: beyond the tolerance. Blue transparent: within it. Grey: untagged bodies.
# The same check, plus geometry diagnostics, is 'radmod check' on an exported STEP.
# The tolerance is the VolumeTolerance script parameter (1 % by default).


def rm_check_volumes():
    tolerance = rm_volume_tolerance()
    if tolerance is None:
        return
    registry = rm_read_registry()
    by_id, untagged = rm_bodies_by_id()
    exceeding, fine, problems = [], [], []
    n_instances = n_exceeding = 0
    for component_id, bodies in sorted(by_id.items()):
        target, problem = rm_instance_volume(registry.get(component_id))
        if problem:
            problems.append("%d %s" % (component_id, problem))
            continue
        for instance in rm_instances(bodies):
            difference = (rm_volume_cm3(instance) - target) / target * 100
            n_instances += 1
            if abs(difference) > tolerance:
                n_exceeding += 1
                exceeding.extend(instance)
            else:
                fine.extend(instance)

    rm_set_color(fine, RM_BLUE, transparent=True)
    rm_set_color(exceeding, RM_RED)
    rm_set_color(untagged, RM_GREY)
    rm_message(
        "Instances beyond +/-%g%% volume: %d of %d\nUntagged bodies: %d\n"
        "Components not checked: %s"
        % (
            tolerance,
            n_exceeding,
            n_instances,
            len(untagged),
            "\n  ".join([""] + problems) if problems else "none",
        )
    )


rm_check_volumes()
