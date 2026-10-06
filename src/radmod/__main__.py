"""``python -m radmod``: the same as the ``radmod`` command.

Useful where the ``radmod`` executable cannot be run, e.g. on locked-down machines
that block executables in the Python Scripts folder.
"""

from radmodeling.cli import app

# Guarded: the worker processes of 'compare' and 'atlas' import this module again
if __name__ == "__main__":
    app(prog_name="python -m radmod")
