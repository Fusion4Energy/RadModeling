"""``python -m radmod``: the same as the ``radmod`` command.

Useful where the ``radmod`` executable cannot be run, e.g. on locked-down machines
that block executables in the Python Scripts folder.
"""

from radmodeling.cli import app

app(prog_name="python -m radmod")
