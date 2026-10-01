# RadModeling: colour in red the components listed by 'radmod check --highlight'
# (volume deviations, unconvertible surfaces, tori, invalid solids...) and show only
# them together with transparent context.
import json


def rm_highlight_problems():
    path = rm_find_file(
        ".json", "Select the file written by 'radmod check --highlight'", "JSON|*.json"
    )
    if not path:
        return
    with open(path) as infile:
        problems = json.load(infile)
    by_id, _ = rm_bodies_by_id()
    flagged = set()
    lines = []
    for category in sorted(problems):
        ids = [i for i in problems[category] if i in by_id]
        flagged.update(ids)
        lines.append("%s: %d" % (category, len(problems[category])))
    every = [b for bodies in by_id.values() for b in bodies]
    rm_set_color(every, RM_GREY, transparent=True)
    rm_set_color([b for i in flagged for b in by_id[i]], RM_RED)
    rm_message("Components in red:\n" + "\n".join(lines))


rm_highlight_problems()
