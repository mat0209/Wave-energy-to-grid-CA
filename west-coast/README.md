# Wave energy capacity expansion: U.S. West Coast 3-zone model (PyPSA)

Branch `west-coast` of the wave-energy project (see `wave-overview` for the other branches).

- Zones: PNW (WA+OR), CA_N, CA_S, joined by COI / PDCI / Path 26, each expandable.
- 11 wave sites (`data/generation_profiles.csv`), each on its own offshore hub bus with an optimizer-sized
  export cable; 3-hourly year.
- Demand from EIA-930 2023; existing fleet from EIA-860 2023 by zone.
- Policies `least_cost` and `clean_100`; new options solar, wind, offshore wind, batteries, H2 CT, wave.
- Wave capex sweep.

## Setup

Raw public data is not in the repository. Download into `data/raw/`:

| Folder | Source |
|---|---|
| `data/raw/eia860/` | EIA-860 2023 (`eia8602023.zip`, unzipped) |
| `data/raw/eia930/` | EIA-930 2023 balance and subregion files (`EIA930_BALANCE_2023_*.csv`, `EIA930_SUBREGION_2023_*.csv`), eia.gov/electricity/gridmonitor |
| `data/raw/census/` | Census states 1:500k 2023 (`cb_2023_us_state_500k.zip`, unzipped) |

`inputs/west/` already holds the processed inputs.

## Run

```
python prepare_west.py && python run_west.py && python make_west_figures.py
python make_maps.py --west-scenario clean_100:5000
```

## Files

| File | Role |
|---|---|
| `west_assumptions.py` | site caps, costs, fuels, zones, corridors, losses |
| `prepare_west.py`, `run_west.py`, `make_west_figures.py` | inputs, sweep, figures |
| `make_maps.py` | site / cable / build map |
| `common.py`, `plot_style.py` | shared helpers and chart style |
| `results/west/sweep_summary.csv`, `network_*.nc` | sweep results and solved networks |
