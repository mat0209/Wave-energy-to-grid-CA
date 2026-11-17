# PyPSA network maps in the style of Gonzalez et al. (2024), Fig. 6

Branch `pypsa-plots`. `make_pypsa_plots.py` draws, with PyPSA's own `n.plot.map` and legend helpers:
pies of annual generation (or capacity) by energy type at each bus, in the colours of Gonzalez et al.,
*Nat. Commun.* 15:6826 (2024), Fig. 6; existing transmission in salmon and new transmission as translucent
lavender, binned by capacity; blue ocean. It also saves PyPSA `n.statistics.*.plot.bar` charts.
The images are in `results/pypsa_plots/`.

The networks come from the other branches. Check them out next to this folder:

```
git clone -b oahu       <repo-url> oahu
git clone -b west-coast <repo-url> west-coast
git clone -b california-pypsa-usa <repo-url> california-pypsa-usa
git clone -b pypsa-plots <repo-url> pypsa-plots
cd pypsa-plots && python make_pypsa_plots.py
```

or set `OAHU_DIR`, `WEST_DIR` and `CA_OUTPUTS`. It also needs `data/raw/census/cb_2023_us_state_500k.shp`
(Census states 1:500k, 2023) and optionally `data/raw/naturalearth/naturalearth_lowres.shp` (the Natural Earth
low-res countries file that ships with pyogrio's test fixtures) for the Mexico backdrop.

Notes: pies leave out imports, exports and load shedding; carriers outside Fig. 6's list are grouped
(biodiesel -> Biomass, H-POWER -> Waste) or given their own colour (Clean firm H2). In the O'ahu pathway,
"existing" transmission includes cables built in earlier pathway steps.
