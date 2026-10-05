# RadModeling: give the next free [rmN] tags to the components added after 'radmod
# init', so that 'radmod check' and 'radmod export' track them from now on.
#
# Select bodies or faces to tag only their components; with nothing selected, all the
# untagged bodies are tagged. A body is tagged through the component that holds it, so
# all the bodies of a component and all the linked instances of a part share one tag.
# Bodies directly in the root part are tagged themselves. The new IDs come after those
# of the registry and of the tags already in the document.


def rm_selected_bodies():
    bodies = []
    for item in Selection.GetActive().Items:
        body = item if hasattr(item, "Faces") else getattr(item, "Parent", None)
        if body is not None and hasattr(body, "Faces") and body not in bodies:
            bodies.append(body)
    return bodies


def rm_tag_target(body):
    """The component that holds the body, or the body itself in the root part."""
    try:
        component = body.Parent.Parent
    except AttributeError:
        component = None
    if component is not None and hasattr(component, "Template"):
        return component
    return body


def rm_tag_new_components():
    registry = rm_read_registry()
    by_id, untagged = rm_bodies_by_id()
    selected = rm_selected_bodies()
    if selected:
        candidates = [b for b in selected if rm_body_id(b) is None]
    else:
        candidates = untagged
    if not candidates:
        rm_message(
            "There are no untagged bodies%s." % (" selected" if selected else "")
        )
        return

    first = next_id = max(list(registry) + list(by_id) + [0]) + 1
    for body in candidates:
        if rm_body_id(body) is not None:
            continue  # tagged with a linked instance or another body of its component
        target = rm_tag_target(body)
        name = (rm_name(target) or "Component").strip()
        RenameObject.Execute(Selection.Create(target), "%s [rm%d]" % (name, next_id))
        next_id += 1

    if next_id == first:
        rm_message("Nothing to tag.")
        return
    rm_message(
        "Tagged %d components: [rm%d] to [rm%d].\n"
        "Check model adds them to the registry: give them a material then."
        % (next_id - first, first, next_id - 1)
    )


rm_tag_new_components()
