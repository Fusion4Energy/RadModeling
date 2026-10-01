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
    rm_message("Components in red:\n" + "\n".join(rm_highlight(problems)))


rm_highlight_problems()
