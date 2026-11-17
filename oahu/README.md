# Wave energy capacity expansion: O'ahu (PyPSA)

Branch `oahu` of the wave-energy project. The other branches hold the West Coast model (`west-coast`), the
California PyPSA-USA wave runs (`california-pypsa-usa`), the Fig. 6-style PyPSA maps (`pypsa-plots`) and an
overview (`wave-overview`).

| | O'ahu (v2) |
|---|---|
| Structure | 1 island bus; every wave site has its own offshore hub bus and an export cable the optimizer sizes; myopic pathway 2030 -> 2040 -> 2045; 3-hourly year |
| Wave sites | 6 (`data/Oahu Wave Values - Sheet1.csv`), cap 3.8 GW each |
| Wave costs | aggressive / moderate / conservative table by year (`data/wave_costs.csv`) |
| Demand | temperature-driven placeholder (7.6 TWh gross, 1,200 MW peak); drop-in slot `data/oahu/load_hourly.csv` |
| Existing fleet | EIA-860 2023, unit by unit (23 oil units, H-POWER, biodiesel engines, wind, solar, batteries) + 2024–25 pipeline |
| Operations | linearized unit commitment for oil steam, combined-cycle and CT units |
| Reliability | 30% energy reserve margin every step (HECO Adequacy of Supply) + 135 MW spinning reserve |
| Policy | `least_cost` and the Hawai'i RPS (40% 2030, 70% 2040, 100% 2045) |
| New options | utility solar, onshore wind, floating offshore wind, 4-h batteries, biodiesel engines, wave |

## Setup

Python with PyPSA 1.3, linopy, cartopy, geopandas, openpyxl, and Gurobi or HiGHS.

Raw public data is not in the repository. Download into `data/raw/`:

| Folder | Source |
|---|---|
| `data/raw/eia860/` | EIA-860 2023, eia.gov/electricity/data/eia860 (`eia8602023.zip`, unzipped) |
| `data/raw/census/` | U.S. Census cartographic boundary file, states 1:500k 2023 (`cb_2023_us_state_500k.zip`, unzipped) |

`inputs/oahu/` already holds the processed fleet, time series and wave sites, so `prepare_oahu.py` is only
needed to rebuild them.

## Run

```
python prepare_oahu.py && python run_oahu.py && python make_oahu_figures.py   # ~12 min with Gurobi
python run_oahu_sensitivity.py                                                 # ~10 min
python make_maps.py --oahu-scenario rps_2045:3000
```

`run_oahu.py` uses Gurobi when a full license is found, otherwise HiGHS (`--solver highs`).
`--only rps:moderate least_cost:none` runs selected pathways.

## Files

| File | Role |
|---|---|
| `oahu_assumptions.py` | **Every tunable number**, each tagged with its source or marked `[PLACEHOLDER]` |
| `prepare_oahu.py` | EIA-860 -> `inputs/oahu/fleet.csv`; profiles + load -> `inputs/oahu/timeseries.csv` |
| `run_oahu.py` | network build, reliability/RPS constraints, pathways, 2045 sweep |
| `run_oahu_sensitivity.py` | one-at-a-time sensitivities on the 2045 result |
| `make_oahu_figures.py`, `make_maps.py` | figures and site / cable / build map |
| `common.py`, `plot_style.py` | shared helpers and chart style |
| `results/oahu/pathways.csv` | one row per pathway year (cost, prices, capacity, generation, wave by site) |
| `results/oahu/sweep_summary.csv` | 2045 wave capex sweep |
| `results/oahu/wave_value.csv` | first-MW value of wave per site (energy, RPS and reserve-margin credit) |
| `results/oahu/sensitivity.csv` | sensitivities |
| `results/oahu/networks/*.nc`, `sensitivity_networks/` | every solved network |
| `v1/`, `results/oahu_v1/` | archived v1 model (single year, synthetic fleet, CO2 cap) and its results; kept for reference |

## Data to download to improve the model

Ordered by how much they would change the results; put files in `data/oahu/`.

1. **Hourly O'ahu load.** Hawaiian Electric IGP inputs workbooks (O'ahu "Base Case Data and Underlying
   Forecast", March 2022), hawaiipowered.com/iadashboard; also the 2026 second-cycle workbooks
   (Docket 2026-0001). Save an 8760 series as `data/oahu/load_hourly.csv` (`datetime_hst, load_mw`).
2. **A real-year wave series.** DOE/NREL high-resolution wave hindcast (Hawai'i domain, 3-hourly) at the six
   coordinates, matched to the solar/wind weather years. Cross-check NDBC/CDIP buoys 51201, 51202, 51212.
3. **EIA-923 (2019, 2023).** Heat rates, delivered fuel prices for HI plants, wind/solar output for calibration.
4. **NREL ATB 2024** and the EIA capital cost report (Hawai'i location factor).
5. **EIA-860M** (projects online since EIA-860 2023).
6. **Hawaiian Electric Adequacy of Supply 2026** (planned deactivations, reserve-margin method).
7. **Rooftop PV and sales** (HECO quarterly installed PV, Key Performance Metrics).
8. **Resource limits** (HECO IGP resource potential; Covelli et al. 2024, *Applied Energy* 375:124054, GIS land
   analysis).

## Data sources used
- Wave curves, site coordinates and wave cost table (project inputs)
- EIA-860 2023; ERA5 via the Open-Meteo archive API (2019 solar, temperature, wind)
- Hawaiian Electric Adequacy of Supply (Jan 2026); Hawai'i Revised Statutes §269-92; EPA GHG Emission Factors Hub
- U.S. Census cartographic boundary file, states 1:500k (2023)
