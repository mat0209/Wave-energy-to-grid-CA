# Assumptions and model setup

Base case: the PyPSA-USA California reference config
(`pypsa-usa/workflow/repo_data/config/config.california.yaml`), run with two
overlays in this folder.

## Changes vs. the reference model

- **Renewable profiles:** atlite with ERA5 2019 weather instead of GODEEEP, whose data is only on Sherlock. The weather year stays 2019 (`configs/config.california.atlite.yaml`).
- **Single horizon:** 2035 only, instead of 2030/2035/2040/2045, so it runs on a laptop. There is no 2036 in the CPUC SERVM demand (`configs/config.california.light.yaml`).
- **Reliability:** a 15% energy reserve margin (ERM) is on (`opts: REM-ERM-3h`). It binds in 3 of 2,920 time steps and keeps 2.2 GW of peaking gas (OCGT).
- **Export price:** exports are paid a flat **40 $/MWh** instead of the EIA "wholesale" series (`configs/config.california.light.yaml`).
  - The EIA series is actually retail-priced (PyPSA-USA issue #807). It paid ~168 $/MWh against ~54 $/MWh in-state, so the model built generation just to export.
  - 40 $/MWh matches observed wholesale prices: the CAISO 2019 average day-ahead price was ~38 $/MWh ([DMM 2019 annual report](https://www.caiso.com/Documents/2019AnnualReportonMarketIssuesandPerformancePresentationtoFERC-Jul142020.pdf)), and the Western EIM averaged ~40 $/MWh in 2024 ([DMM 2024 annual report](https://www.caiso.com/documents/presentation-2024-annual-report-on-market-issues-and-performance.pdf)).
  - It is also below the 60 $/MWh import price, which rules out buy-to-resell arbitrage.
  - Real exports are probably worth even less, since California mostly exports at midday, when prices are lowest.
- **Unchanged:** 3-hour resolution, unit commitment, 4 California zones, imports and the AB 32 CO₂ cap.

### Effect of the export price (2035)

| | EIA "wholesale" (~168 $/MWh) | Flat 50 $/MWh | **Flat 40 $/MWh (base)** |
| --- | --- | --- | --- |
| Exports | 89 TWh | 25 TWh | 20 TWh |
| Export revenue | 14.9 G$ | 1.3 G$ | 0.8 G$ |
| System cost | 6.7 G$ | 16.5 G$ | 16.7 G$ |
| Solar | 85 GW | 90 GW | 88 GW |
| Gas with CCS | 17.6 GW | 7.8 GW | 7.2 GW |
| CO₂ price | 57 $/t | 46 $/t | 50 $/t |

Between 40 and 50 $/MWh the results barely move. The large shift comes from removing the retail-priced series.

## Known issues (not fixed yet)

- The CO₂ cap leaves out emissions from imports and out-of-state contracted plants, about 9.8 Mt.
- The ERM counts the 19 GW of imports as firm capacity.
- Gas with carbon capture (CCS) can be built, with no cost for CO₂ transport and storage.

## Building the network (Snakemake)

Only needed to change the network itself (year, zones, technologies, weather). Scenario parameters in the
notebook don't need it. A first build downloads ~12 GB of data into `pypsa-usa/workflow/` and needs an EIA API
key in `pypsa-usa/workflow/config/config.api.yaml` (free at [eia.gov](https://www.eia.gov/opendata/register.php)).

```bash
cd pypsa-usa/workflow
../.venv/bin/python -m snakemake -j2 --rerun-triggers mtime \
  --configfile repo_data/config/config.california.yaml \
  ../../configs/config.california.atlite.yaml \
  ../../configs/config.california.light.yaml
cd ../..
pypsa-usa/.venv/bin/python scripts/update_base.py   # copy the new network into base/
```

- With `--rerun-triggers mtime`, editing a config does not trigger a rebuild. Force one from the step the
  setting affects, for example `-R add_extra_components` for import/export settings.
- **Path with spaces:** Snakemake 7 breaks if the repo path contains a space (e.g. `CEE 276D`). Work from a
  space-free symlink (`ln -s "<repo>/pypsa-usa" ~/pypsa-usa`), and run Python through the symlink's absolute path
  (`~/pypsa-usa/.venv/bin/python -m snakemake`).
- Zenodo downloads are often cut off. If a retrieve rule fails, download the file with `curl -C -` in a loop.
- Results: `pypsa-usa/workflow/results/california-2035/`.
- **58-county variant:** add `../../configs/config.california.county.yaml` after the light config to get 58 county
  nodes instead of 4 ReEDS zones. With 4 zones, one zone holds the whole coast from Del Norte to Santa Barbara plus
  the Bay Area, so wave power would reach the Bay Area with no transmission limit. It is a separate run
  (`results/california-2035-county/`). The floating offshore wind profiles by county take ~2 h, and the solve needs
  ~13–15 GB of RAM.

## Scenario notebook

`notebooks/california_2035.ipynb` runs **one scenario**, named at the top. The code lives in
`notebooks/ca_helpers.py`. It starts from `base/network_2035.nc`, so it needs neither Snakemake nor the data.

- **Parameters** at the top of the notebook: `export_price`, `import_price`, `erm_margin`. They are applied to the
  prepared network at solve time, so changing them needs no Snakemake rebuild. Changing the export price in the
  notebook matches a full Snakemake rebuild: relative objective difference 2×10⁻⁷, identical capacities.
- **Solve:** reuses PyPSA-USA's `solve_network.py`, so the constraints are the same as in Snakemake. With the base
  parameters, the notebook checks that it reproduces the Snakemake result exactly (`base/snakemake_reference.yaml`).
- **Outputs:** `outputs/<SCENARIO>/` holds `parameters.yaml`, `metrics.csv`, the CSV tables and the PNG figures
  (in git), and the solved `network.nc` (not in git, ~40 MB). A result is reused only if its saved parameters match.
- **Comparison:** the last cell builds `outputs/comparison.csv` from every scenario's `metrics.csv`.
- Each solve takes ~1.5 min and 3–6 GB of RAM.

`outputs/reference_exports50/` and `outputs/reference_wholesale_exports/` keep the earlier runs
(exports at 50 $/MWh and at the EIA "wholesale" price).
