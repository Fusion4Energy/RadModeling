# RadModeling: run 'radmod check' on the open document without leaving SpaceClaim.
#
# Saves a STEP copy next to the document (<name>_check.step), runs 'radmod check' on it
# with the registry, shows its report and colours the components with problems in
# red. The document itself is not changed and stays the working file.
import json
import os


def rm_check_model():
    document = GetRootPart().Document
    path = document.Path
    if not path:
        rm_message("Save the document first: the STEP copy is written next to it.")
        return
    registry = rm_find_file(
        "_registry.csv", "Select the RadModeling registry", "Registry (*.csv)|*.csv"
    )
    if not registry:
        return
    stem = os.path.splitext(path)[0]
    step = stem + "_check.step"
    problems = stem + "_check_problems.json"

    DocumentSave.Execute(step, ExportOptions.Create())
    if document.Path != path:
        rm_message(
            "SpaceClaim now points to the STEP copy: save your work as .scdocx again."
        )
    if os.path.exists(problems):
        os.remove(problems)

    code, output = rm_run_radmod(
        ["check", step, "--registry", registry, "--highlight", problems]
    )
    if code != 0 or not os.path.exists(problems):
        rm_message("radmod check failed:\n\n" + output)
        return
    with open(problems) as infile:
        rm_highlight(json.load(infile))
    rm_message(output + "\nComponents with problems are in red.")


rm_check_model()
