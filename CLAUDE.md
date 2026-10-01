# RadModeling

CAD preparation and CAD-to-MCNP quality assurance. Python package `radmodeling`
(`src/radmodeling`) with the `radmod` CLI (typer). Docs in `docs/source` (Sphinx).

## Commands

- Tests: `.venv/bin/pytest` (fast, ~5 s; builds its CAD with OCP, no fixture files)
- Lint/format: `.venv/bin/ruff check . && .venv/bin/ruff format .`
- Docs: `.venv/bin/sphinx-build -b html docs/source <out>`

## Architecture

- Components are identified by `[rm:ID]` tags in CAD names (`registry.py`), never by
  position or name. The registry CSV is the single source of truth for materials and
  volumes; computed columns (`volume_diff_pct`, `dcf`) are derived on load and save.
- STEP I/O is direct OCP/XCAF (`cad/step.py`), not build123d: build123d mangles names.
  Non-ASCII names are written as `\X2\` escapes. `cad/occ.py` hides OCP 7.x/8.x API
  differences (`TopoDS.Solid` vs `Solid_s`, collections module, `Bnd_Box.Get`).
- CAD point classification: ray parity on BRepMesh triangulations, points grouped on
  parallel lines (`cad/mesh.py`, grid-accelerated). Do not use OCCT Booleans to
  verify geometry in tests: they are unreliable for near-tangent tori/cylinders.
- MCNP inputs are parsed and edited with `migjorn` (lossless). `mcnp/geometry.py`
  adds the point classifier: surfaces, macrobodies, TR, complements, LIKE BUT, and
  conservative bounding boxes so cells/surfaces are only evaluated where needed.
- `compare.py` estimates MCNP volume as mesh volume + (MCNP-only − CAD-only) samples,
  which is unbiased and only noisy where CAD and MCNP disagree.
- `src/radmodeling/spaceclaim/*.py` are IronPython 2.7 scripts (no f-strings, no type
  hints, SpaceClaim API names are injected globals, helpers prefixed `rm_`). They are
  run through loaders written by `radmod spaceclaim-loaders`. They cannot be tested
  here: keep logic out of them.

## Conventions

- CAD lengths in mm, MCNP in cm; registry volumes in cm3 (`MM_PER_CM` in compare).
- Escape user strings in rich output with `esc()` in `cli.py`: tags look like markup.
- Slice colours follow the validated categorical palette in `plot.py`; red is
  reserved for problems (overlaps, mismatches).
