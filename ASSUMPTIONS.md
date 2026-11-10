# Assumptions and model setup

Base case: the PyPSA-USA California reference config
(`pypsa-usa/workflow/repo_data/config/config.california.yaml`), run with the
overlays in `configs/` and split into 25 nodes.

## Changes vs. the reference model

- **Renewable profiles:** atlite with ERA5 2019 weather instead of GODEEEP, whose data is only on Sherlock. The weather year stays 2019 (`configs/config.california.atlite.yaml`).
- **Single horizon:** 2035 only, instead of 2030/2035/2040/2045, so it runs on a laptop. There is no 2036 in the CPUC SERVM demand (`configs/config.california.light.yaml`).
- **Reliability:** a 15% energy reserve margin (ERM) is on (`opts: REM-ERM-3h`). With 4 zones it binds in 3 of 2,920 time steps and keeps 2.2 GW of peaking gas (OCGT); with 25 nodes it binds in 4.
- **Export price:** exports are paid a flat **40 $/MWh** instead of the EIA "wholesale" series (`configs/config.california.light.yaml`).
  - The EIA series is actually retail-priced (PyPSA-USA issue #807). It paid ~168 $/MWh against ~54 $/MWh in-state, so the model built generation just to export.
  - 40 $/MWh matches observed wholesale prices: the CAISO 2019 average day-ahead price was ~38 $/MWh ([DMM 2019 annual report](https://www.caiso.com/Documents/2019AnnualReportonMarketIssuesandPerformancePresentationtoFERC-Jul142020.pdf)), and the Western EIM averaged ~40 $/MWh in 2024 ([DMM 2024 annual report](https://www.caiso.com/documents/presentation-2024-annual-report-on-market-issues-and-performance.pdf)).
  - It is also below the 60 $/MWh import price, which rules out buy-to-resell arbitrage.
  - Real exports are probably worth even less, since California mostly exports at midday, when prices are lowest.
- **Spatial resolution:** 25 nodes instead of 4 California zones, so coastal grid limits exist in the model (see below).
- **Unchanged:** 3-hour resolution, unit commitment, imports and the AB 32 CO₂ cap.

### Effect of the export price (2035, 4-zone network)

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

- The CO₂ cap leaves out emissions from imports and out-of-state contracted plants, about 9.4 Mt.
- The ERM counts the ~20 GW of imports as firm capacity.
- Gas with carbon capture (CCS) can be built, with no cost for CO₂ transport and storage.

## Spatial resolution

The base uses **25 nodes**. The reference's 4 ReEDS zones are too coarse for wave power: zone p9 holds 44 counties,
including the whole coast from Del Norte to Santa Barbara, so coastal grid limits (e.g. Humboldt can export only
216 MW) are invisible to the model. The 58-county model shows them too, but sits at the memory limit of a 17 GB laptop.
`notebooks/spatial_resolution.ipynb` compares the options and lists the open decisions. Because the comparison has no
wave power, one central wave scenario should be re-run on the 58-county model as a check.

- **58 counties:** overlay `configs/config.california.county.yaml`, run `california-2035-county` (separate run: the
  4-zone substation network lacks the county column). Its latest solve took ~30 min of computing (keep the Mac awake);
  Gurobi's factorisation needs ~10 GB, so it is at the limit of a 17 GB laptop. Building the floating offshore wind
  profile by county takes ~2 h.
- **25 nodes (base):** coastal counties kept, inland counties grouped (`configs/county_groups_25.csv`, overlay
  `configs/config.california.hybrid25.yaml`, same run as the 58-county model so its profiles are reused). Built into
  `base/network_2035_hybrid25.nc` with our fork's `model_topology.county_groups`. The scenario notebook uses it by
  default (`NETWORK = "hybrid25"`). Renewables keep their county-level resource; conventional plants are aggregated by node.
  Solved in ~14 min (Gurobi factorisation ~6 GB). Against the 4-zone network: cost without AC grid +0.4%, CO2 price 46 vs
  50 $/t, near-identical mix. Against the 58-county model: cost without AC grid -0.4%, exports -2%, coastal prices (without Del Norte) within
  0.18 $/MWh on average. It differs at San Diego (17.6 vs 5.5 TWh generated, limits into it scaled from 6,083 to 1,998 MW),
  and in the North Bay and Mendocino, where it probably overstates imports and congestion (notebook, section 5). Once the
  wave profiles give candidate sites and capacities, well-connected coastal counties may be merged (rule in the notebook:
  own node if export limit < 2 × largest wave capacity tested there).
- **Del Norte** has no link to any other California county in the NARIS 2024 data, only to Oregon (221 MW out).
- Summing county-level transfer limits overstates zone-level NARIS limits by 1.15× to 3×. In the 25-node network,
  corridors crossing a ReEDS zone boundary are scaled down per direction so each boundary total equals the zone-level
  limit. Corridors inside a zone keep the plain sum, which may still be too generous.
- **Interface limits in county mode:** PyPSA-USA skipped all six CAISO interface limits with county nodes, because
  they name ReEDS zones and the trade buses are named after counties. Fixed in our fork (`external_zone` on trade
  buses). Re-solving the 58-county model with the fix took exports from 41 to 20.8 TWh and their peak from 20.8 to
  10.5 GW (A: 20 TWh, 10.1 GW). Command in the notebook, section 4.
- **System cost across resolutions:** it includes the annualised cost of the existing AC grid, recomputed link by link,
  so it changes with the number of nodes (0.96 G$ with 4 zones, 3.4 G$ with 58 counties). Compare costs without it.

Refresh `base/spatial/` after solving any of these runs. The 25-node run is read from `outputs/reference_hybrid25/`:

```bash
pypsa-usa/.venv/bin/python scripts/export_spatial.py
```

Rebuild the 25-node network from the clustering step (seconds), then copy it into `base/`:

```bash
cd pypsa-usa/workflow
../.venv/bin/python -m snakemake \
  resources/california-2035-county/networks/western/elec_scounty_c25_ec_lv1.0_REM-ERM-3h_E.nc \
  -j2 --rerun-triggers mtime --forcerun cluster_network --configfile \
  repo_data/config/config.california.yaml ../../configs/config.california.atlite.yaml \
  ../../configs/config.california.light.yaml ../../configs/config.california.county.yaml \
  ../../configs/config.california.hybrid25.yaml
cd ../..
pypsa-usa/.venv/bin/python scripts/update_base.py
```

## Building the network (Snakemake)

Only needed to change the network itself (year, zones, technologies, weather). Scenario parameters in the
notebook don't need it. The command below builds the 4-zone network; the base needs the extra steps under
*Spatial resolution*. A first build downloads ~12 GB of data into `pypsa-usa/workflow/` and needs an EIA API
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
`notebooks/ca_helpers.py`. It starts from `base/network_2035_hybrid25.nc`, so it needs neither Snakemake nor the data.

- **Parameters** at the top of the notebook: `export_price`, `import_price`, `erm_margin`. They are applied to the
  prepared network at solve time, so changing them needs no Snakemake rebuild. Changing the export price in the
  notebook matches a full Snakemake rebuild: relative objective difference 2×10⁻⁷, identical capacities.
- **Renewable policy** (`renewables`, off by default). Eligible carriers: solar, onshore wind, floating offshore wind,
  geothermal, biomass, hydro (wave once added), both in California and as out-of-state contracted resources on the
  import buses. Nuclear counts only with `nuclear_counts: True` (SB 100 counts it as clean).
  - `"annual"`: over the year, eligible generation minus exports ≥ `renewable_share` × California load. Exports are
    subtracted so renewable power sold out of state does not count. With 1.0, fossil and unspecified imports can
    only cover storage and transmission losses over the year, but gas can still run in some hours.
  - `"hourly"` (hour-by-hour matching, as in "24/7 carbon-free energy"): in each hour, clean supply = eligible
    generation + storage discharge − storage charging − exports, capped at that hour's load. The sum over the year
    must reach `renewable_share` × California load. A surplus in one hour does not count for another, and charging
    counts against the hour it happens in, so gas stored in a battery is not counted as clean.
  - `"strict"`: every hour. Fossil plants (CCGT, OCGT, CCS, coal, oil) and unspecified imports are retired
    (`p_nom_max = 0`), so they give no reserve-margin capacity either. Nuclear too, unless `nuclear_counts`.
  - The `renewables` table and the `renewable share %` metric report the share for every scenario. The base case
    is at 53% (net of exports).
- **New nuclear** (`new_nuclear`, `False` by default): California bans new nuclear plants (1976 moratorium), so only
  existing units remain (Diablo Canyon, the Palo Verde contract). PyPSA-USA offers a new-nuclear candidate at every
  node with no capacity limit; with `nuclear_counts: True` and new nuclear allowed, the 100% annual case builds
  19 GW of it.
- **Solve:** reuses PyPSA-USA's `solve_network.py`, so the constraints are the same as in Snakemake. With the base
  parameters and the 4-zone network, the notebook checks that it reproduces the Snakemake result exactly
  (`base/snakemake_reference.yaml`). There is no such reference for the 25-node network.
- **Run stats:** each solve writes its time and the kernel's peak RAM to `outputs/<SCENARIO>/run_stats.yaml`. Restart the
  kernel first for a clean reading. A 25-node solve takes ~15 min; with `NETWORK = "zones4"`, 2–4 min and 3–5 GB.
- **Outputs:** `outputs/<SCENARIO>/` holds `parameters.yaml`, `metrics.csv`, the CSV tables and the PNG figures
  (in git), and the solved `network.nc` (not in git, 40–80 MB). A result is reused only if its saved parameters match.
- **Comparison:** the last cell builds `outputs/comparison.csv` from every scenario's `metrics.csv`.

`outputs/reference_hybrid25/` is the base case. `outputs/reference/` has the same parameters on the 4-zone network, and
`outputs/reference_exports50/` and `outputs/reference_wholesale_exports/` keep earlier 4-zone runs (exports at 50 $/MWh
and at the EIA "wholesale" price).
