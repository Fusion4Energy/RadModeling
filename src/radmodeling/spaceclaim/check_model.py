# RadModeling: run 'radmod check' on the open document without leaving SpaceClaim.
#
# Saves a STEP copy next to the document (<name>.step, replacing the previous copy),
# runs 'radmod check' on it with the registry, shows its report and colours the
# components with problems in red. The document itself is not changed and stays the
# working file. The volume tolerance is the VolumeTolerance script parameter (1 % by
# default).
import json
import os
import tempfile


def rm_check_model():
    tolerance = rm_volume_tolerance()
    if tolerance is None:
        return
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
    step = stem + ".step"

    DocumentSave.Execute(step, ExportOptions.Create())
    if document.Path != path:
        rm_message(
            "SpaceClaim now points to the STEP copy: save your work as .scdocx again."
        )
    handle, problems = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    try:
        code, output = rm_run_radmod(
            [
                "check",
                step,
                "--registry",
                registry,
                "--highlight",
                problems,
                "--tolerance",
                "%g" % tolerance,
            ]
        )
        # radmod only writes the file when the check ran: an empty one means it failed
        if code != 0 or not os.path.getsize(problems):
            rm_message("radmod check failed:\n\n" + output)
            return
        with open(problems) as infile:
            rm_highlight(json.load(infile))
    finally:
        os.remove(problems)
    rm_message(output + "\nComponents with problems are in red.")


rm_check_model()
