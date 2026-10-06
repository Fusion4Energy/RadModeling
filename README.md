![GitHub last commit](https://img.shields.io/github/last-commit/Fusion4Energy/RadModeling)
![GitHub issues](https://img.shields.io/github/issues/Fusion4Energy/RadModeling)
![](https://img.shields.io/badge/license-EU%20PL-blue)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-wordmark-dark.svg">
  <img alt="RadModeling" src="docs/assets/logo-wordmark-light.svg" width="420">
</picture>

# RadModeling

RadModeling prepares CAD models for Monte Carlo radiation transport and checks the
MCNP geometry obtained from them. It is a Python package with a command line tool,
`radmod`, plus a few thin SpaceClaim tools for the interactive part of the work.

```
machine.step ──radmod init──▶ machine_simplified.step + machine_registry.csv
                                   │
                    simplify in SpaceClaim (or any CAD program)
                                   │
machine_simplified.step ──radmod check──▶ volumes, tori, splines, invalid solids
                        ──radmod export─▶ machine_export.step
                                   │
                         GEOUNED (or another converter)
                                   │
machine_export.mcnp ──radmod compare───▶ CAD vs MCNP volumes, density correction factors
                    ──radmod materials─▶ materials and densities in the cells
                    ──radmod slice─────▶ slice plots with the CAD overlaid
                    ──radmod atlas─────▶ PDF of slices: whole model + every problem
```

* **Stable component IDs**: tags like `Pump casing [rm12]` in the component names
  survive any CAD program, so simplified models always match the original components.
* **One registry**: an Excel-friendly CSV with materials, densities, volumes and
  density correction factors.
* **CAD vs MCNP comparison without running MCNP**: the MCNP cells are evaluated
  directly to measure their volumes, their disagreement with the CAD, and to plot
  slices with the CAD on top.
* **Geometry tools**: FMESH cards from boxes; in SpaceClaim, toroidal elbows to mitred
  cylinders.
* **SpaceClaim tools published once** that always run the latest installed code.

## Installation

```bash
pip install git+https://github.com/Fusion4Energy/RadModeling.git
```

Where the `radmod` executable is blocked ("Access is denied"), write
`python -m radmod` instead, with the same arguments.

For development, or for SpaceClaim tools that update with `git pull`:

```bash
git clone https://github.com/Fusion4Energy/RadModeling.git
cd RadModeling
pip install -e ".[dev]"
```

## Quick start

```bash
radmod init machine.step                       # tag + registry
# fill the 'material' column of machine_registry.csv
# simplify machine_simplified.step in SpaceClaim, save it back as STEP
radmod check machine_simplified.step -r machine_registry.csv
radmod export machine_simplified.step -r machine_registry.csv
# convert machine_export.step to machine_export.mcnp with GEOUNED
radmod compare machine_export.step machine_export.mcnp -r machine_registry.csv
radmod materials machine_export.mcnp -r machine_registry.csv -l materials.toml
radmod slice machine_export_materials.mcnp -o slice.png --normal z --step machine_export.step
radmod atlas machine_export_materials.mcnp --step machine_export.step -o atlas.pdf
```

SpaceClaim tools: `radmod spaceclaim-loaders <folder>` and publish the loaders once.

Full documentation: https://radmodeling.readthedocs.io

## License

Copyright 2019 F4E | European Joint Undertaking for ITER and the Development of Fusion
Energy (‘Fusion for Energy’). Licensed under the EUPL, Version 1.1 or - as soon they
will be approved by the European Commission - subsequent versions of the EUPL (the
“Licence”). You may not use this work except in compliance with the Licence. You may
obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl.html
Unless required by applicable law or agreed to in writing, software distributed under
the Licence is distributed on an “AS IS” basis, WITHOUT WARRANTIES OR CONDITIONS OF
ANY KIND, either express or implied. See the Licence permissions and limitations under
the Licence.
