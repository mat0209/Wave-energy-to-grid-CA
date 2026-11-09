# Wave energy to grid — California

*Electric System Planning group project (CEE 276D).*

**Goal:** estimate what adding wave energy around 2036 would do to California's electricity system
(capacity mix, cost, CO₂ emissions, prices, flexibility needs), using the PyPSA-USA capacity
expansion model.

**Approach:**

1. Build a California base case for 2035 with PyPSA-USA (2035 is the CPUC demand year closest to 2036).
2. Add wave power on the coastal zones, with a resource profile and costs, as scenarios.
3. Compare scenarios with and without wave energy.

**Status:** the 4-zone base case runs and is checked. A 25-node network that keeps the coastal counties is solved and compared with the 58-county model; the base for the wave study is still to be chosen (see `notebooks/spatial_resolution.ipynb`). Wave scenarios are next.

## Repository layout

- `notebooks/california_2035.ipynb`: runs one scenario (name, network and parameters at the top). The logic is in `notebooks/ca_helpers.py`.
- `notebooks/spatial_resolution.ipynb`: compares ways to split California into nodes (4 zones, 58 counties, 25-node proposal) with run time and results for each, and lists the open decisions. It never solves anything. The logic is in `notebooks/spatial_helpers.py`.
- `outputs/<scenario>/`: tables (CSV) and figures (PNG) of each scenario, plus `outputs/comparison.csv`.
- `base/`: the 2035 networks built by PyPSA-USA (4 zones, 25 nodes), so the notebooks run without rebuilding them. `base/spatial/` holds the small data behind the spatial-resolution notebook.
- `configs/`: PyPSA-USA config overlays used to build `base/`, the 58-county and 25-node overlays, and the 25-node county grouping.
- `ASSUMPTIONS.md`: model setup, changes vs. the PyPSA-USA reference, known issues, how to rebuild `base/`.
- `pypsa-usa/`: PyPSA-USA, as a git submodule pointing to our fork ([mat0209/pypsa-usa](https://github.com/mat0209/pypsa-usa), branch `wave-to-grid`).

## Getting started

1. **Clone with the submodule:**

   ```bash
   git clone --recursive https://github.com/mat0209/Wave-energy-to-grid-CA.git
   cd Wave-energy-to-grid-CA
   ```

2. **Python environment** (needs [uv](https://docs.astral.sh/uv/)):

   ```bash
   cd pypsa-usa && uv sync && cd ..
   uv pip install --python pypsa-usa/.venv/bin/python ipykernel nbstripout "gurobipy==13.0.3"
   ```

   On macOS, if `uv sync` fails building `datrie` with a linker error, prefix it with an older SDK, e.g.
   `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk uv sync`.

3. **Gurobi license:** request a free academic license at [gurobi.com](https://www.gurobi.com/academia/academic-program-and-licenses/),
   download `gurobi.lic` from the WLS Manager and put it in your home folder (`~/gurobi.lic`).

4. **Keep notebook outputs out of git** (once per clone):

   ```bash
   pypsa-usa/.venv/bin/nbstripout --install --attributes .gitattributes
   ```

5. **Run:** open `notebooks/california_2035.ipynb`, select the kernel `pypsa-usa/.venv/bin/python`, set `SCENARIO`
   to a new name and run all cells. A 4-zone solve takes 2–4 min and 3–5 GB of RAM.

## Working together

- Work on a branch and open a pull request. Give each scenario its own name so results don't overwrite each other.
- **Changing PyPSA-USA code** (in `pypsa-usa/`): commit and push inside `pypsa-usa/` on branch `wave-to-grid`
  (to the fork), then commit the updated `pypsa-usa` pointer in this repo.
- After pulling, run `git submodule update` so `pypsa-usa/` matches the version this repo expects.
