# Wave energy interconnection: project overview

Wave energy in PyPSA capacity expansion models for California, the U.S. West Coast and O'ahu. Each part of
the project is on its own branch:

| Branch | Contents |
|---|---|
| [`california-pypsa-usa`](../../tree/california-pypsa-usa) | Wave added to the PyPSA-USA California runs (`notebooks/wave_power.py`), Gurobi runs on the 4-zone and 25-node networks with aggressive / moderate / conservative wave costs, summaries and breakeven costs |
| [`oahu`](../../tree/oahu) | O'ahu v2: one island bus plus offshore wave hubs, EIA-860 unit fleet, unit commitment, 30% reserve margin, RPS pathway 2030–2045 |
| [`west-coast`](../../tree/west-coast) | 3-zone West Coast model (PNW, CA_N, CA_S) with 11 wave sites and a wave capex sweep |
| [`pypsa-plots`](../../tree/pypsa-plots) | PyPSA network maps in the style of Gonzalez et al. (2024) Fig. 6 for all of the above |

Key results
- California 25-node (Gurobi): no wave is built at the cost table's 2035 costs; Humboldt's first-MW breakeven
  is about $4.1–4.2k/kW at 100% renewables (aggressive 2035 capex $4.26k/kW) and about $2.6k/kW in the base case.
- O'ahu 2045, 100% RPS: wave cuts annual system cost from $1,323M (no wave) to $761–871M with 1.1–1.7 GW of wave.
  This comparison does not yet include long-duration (hydrogen) storage, and solar/wind build caps bind.

Raw public data (EIA-860, EIA-930, Census) is not committed; each branch's README lists the downloads.
