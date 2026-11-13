---
icon: lucide/download
---

# Installation

RadModeling needs Python 3.12 or newer. It works on Windows, Linux and macOS; only
the SpaceClaim tools need Windows.

```bash
pip install radmodeling
```

This installs the `radmod` command. Check it with `radmod --help`, and update it with
`pip install -U radmodeling`, which also updates the [SpaceClaim tools](spaceclaim.md).

!!! note

    On machines that block executables in the Python `Scripts` folder ("Access is
    denied"), write `python -m radmod` wherever the documentation says `radmod`: it
    is the same command with the same arguments, e.g. `python -m radmod check ...`.

To work on RadModeling itself, see [Development](development.md).

## Dependencies

* [OCP](https://github.com/CadQuery/OCP) (OpenCascade) to read and write STEP files
  and to analyse the geometry.
* [migjorn](https://pypi.org/project/migjorn/) to read and edit MCNP inputs
  losslessly.
* numpy, matplotlib, typer and rich.

The CAD-to-MCNP conversion itself is done by another tool, typically
[GEOUNED](https://github.com/GEOUNED-org/GEOUNED), which needs its own installation.
