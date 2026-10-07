---
icon: lucide/code
---

# Development

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format .
```

The documentation is built with [Zensical](https://zensical.org):

```bash
pip install -e ".[docs]"
zensical serve
```

## Layout

```text
src/radmodeling/
  cli.py            the radmod command line interface: options and printing only
  settings.py       per-user settings (radmod config), shared with SpaceClaim
  registry.py       component registry and [rmID] tags
  workflow.py       init, check and export
  compare.py        CAD vs MCNP sampling and slices
  slices.py         slices of a model and the planes of the atlas
  plot.py           slice and atlas rendering
  fmesh.py          FMESH cards from boxes
  cad/              OpenCascade: STEP, meshes, sections, analysis
  mcnp/             MCNP geometry evaluation, cell tags, material assignment
  spaceclaim/       IronPython tools run by the SpaceClaim loaders
src/radmod/         alias so that "python -m radmod" works like "radmod"
tests/              pytest suite; tests/models.py builds the test CAD and MCNP
docs/               this documentation (Markdown, built by zensical.toml)
```

## Principles

* The `radmod` command is the public interface; the Python modules are internal and
  change with it. The commands stay consistent: an MCNP input comes first and the
  STEP file from `export` second, every written file has `-o` and a default named
  after the input, and options shared by several commands are defined once in
  `cli.py`. The library functions take and return loaded objects (a `Registry`, a
  model) and leave reading and saving the registry to the CLI.
* Everything that can be computed from files lives in the Python package and is
  tested. CAD programs only do what needs a human.
* MCNP inputs are read and written with migjorn, which preserves the input byte for
  byte except at the cards that are edited.
* Point classification of the CAD uses ray parity on a triangulation, with the points
  organised on lines so that each solid is intersected once per line.
