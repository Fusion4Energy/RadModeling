# RadModeling

CAD preparation and CAD-to-MCNP quality assurance. Python package `radmodeling`
(`src/radmodeling`) with the `radmod` CLI (typer). Docs in `docs` (Markdown, built by
Zensical from `zensical.toml`).
`src/radmod` is only an alias so `python -m radmod` works like the `radmod` command;
keep all code in `radmodeling`.

## Commands

- Tests: `.venv/bin/pytest` (fast, ~5 s; builds its CAD with OCP, no fixture files)
- Lint/format: `.venv/bin/ruff check . && .venv/bin/ruff format .`
- Types: `.venv/bin/pyright src tests` (OCP types come from `cadquery-ocp-stubs`)
- Docs: `.venv/bin/zensical build` (or `serve`); theme colours in
  `docs/assets/stylesheets/extra.css`
- Release: push a `vX.Y.Z` tag on master; `.github/workflows/release.yml` runs the
  tests and publishes to PyPI (Trusted Publishing). The version comes from the tag.

## Architecture

- Components are identified by `[rmID]` tags in CAD names (`registry.py`), never by
  position or name. The registry CSV is the single source of truth for materials and
  volumes; computed columns (`volume_diff_pct`, `dcf`) are derived on load and save.
- The tag is on the part definition: instances of a part share it and stay linked
  (`write_step` keeps instancing; only `export` flattens). Registry rows are per
  instance, keyed `(id, instance)`; instances are matched by centre of mass
  (`workflow.match_instances`).
- Cells are linked to registry rows by tags in their `$` comments, never by cell
  number (`mcnp/tags.py`). `export` names each solid `[rmID.instance] Name` (tag first:
  converters may cut or drop the end of names) and GEOUNED copies each solid's CAD
  path into a `$/…/name` comment on its cell.
- STEP I/O is direct OCP/XCAF (`cad/step.py`), not build123d: build123d mangles names.
  Non-ASCII names are written as `\X2\` escapes. `cad/occ.py` hides OCP 7.x/8.x API
  differences (`TopoDS.Solid` vs `Solid_s`, collections module, `Bnd_Box.Get`).
- CAD point classification: ray parity on BRepMesh triangulations, points grouped on
  parallel lines (`cad/mesh.py`, grid-accelerated). Do not use OCCT Booleans to
  verify geometry in tests: they are unreliable for near-tangent tori/cylinders.
  `triangulate` checks every face area and the volume against BRepGProp and refines
  the deflection: BRepMesh silently mis-meshes sliver faces (seen in real models).
- MCNP inputs are parsed and edited with `migjorn` (lossless). `mcnp/geometry.py`
  adds the point classifier: surfaces, macrobodies, TR, complements, LIKE BUT, and
  conservative bounding boxes so cells/surfaces are only evaluated where needed.
- `compare.py` estimates MCNP volume as mesh volume + (MCNP-only − CAD-only) samples,
  which is unbiased and only noisy where CAD and MCNP disagree. It also measures
  overlaps between instances (`TaggedCells`: each pair once, from the denser side).
  `atlas` reruns it with `locate=True` and slices through the clustered problem
  points (`Spot`); slice mismatches within `SURFACE_TOLERANCE` of agreement (twice
  the mesh deflection near large parts) are tessellation noise and dropped.
- `compare` and `atlas` run per-instance and per-slice tasks on spawned worker
  processes (`parallel.py`, `--jobs`). OCP shapes cannot be pickled: each worker
  loads the model itself with a picklable loader (`load_comparison`, `load_atlas`),
  and tasks are module-level functions or methods of the state class
  (`Comparison.compare`, `Atlas.draw`). Each instance seeds its own RNG from the
  seed and its key, so results do not depend on the number of jobs. Tests default
  to 1 job (`conftest.py`).
- `src/radmodeling/spaceclaim/*.py` are IronPython 2.7 scripts (no f-strings, no type
  hints, SpaceClaim API names are injected globals, helpers prefixed `rm_`). They are
  run through loaders written by `radmod spaceclaim-loaders`. They cannot be tested
  here: keep logic out of them.

## Conventions

- CAD lengths in mm, MCNP in cm; registry volumes in cm3 (`MM_PER_CM` in compare).
- CLI shape: MCNP input first, export STEP second; every written file has `-o` and a
  default named after the input; shared options are `Annotated` aliases in `cli.py`.
  Options are ordered by importance (typer shows them in definition order): output
  options first (`-o`, `-r`, `-l`), then the command's own, then `--components`,
  `--points`, `--seed`, `--jobs` last, always in that order.
- Per-user settings (`volume-tolerance`, `max-discrepancy`) are in `settings.py`, set
  with `radmod config` and read by the SpaceClaim tools too (path in the loaders).
  They are not command options as well: one place to set them.
  Tests get their own file through `RADMODELING_SETTINGS` (`conftest.py`).
- Escape user strings in rich output with `esc()` in `cli.py`: tags look like markup.
- Tags are `[rm12]`, not `[rm:12]`: SpaceClaim's STEP export drops colons from names.
- Slice colours follow the validated categorical palette in `plot.py`; red is
  reserved for problems (overlaps, mismatches).
