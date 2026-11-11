# Wave energy inputs

Data collected for adding wave power to the California 2035 model. Nothing here is in the model yet.

## Files

| File | Content |
|---|---|
| `data/raw/coordinates_wecc.csv` | 11 candidate sites (WECC_A to WECC_J, PACWAVE): name, latitude, longitude |
| `data/raw/generation_profiles.csv` | 2019 output of one device at each site, every 3 h (2920 steps): `<site>_power(kW)` and `<site>_power_ratio` |
| `data/costs.csv` | CapEx (M$/MW) and OpEx (M$/MW-yr) for 2025, 2030, 2040, 2050, in three tiers: aggressive (cheapest), moderate, conservative (most expensive) |
| `data/density.csv` | Installed capacity per km² for three vessel-lane widths (0, 250, 500 m) |
| `data/assumptions.yaml` | Device, spacing, cluster, lifetime, profile details, sources |
| `wave_energy.ipynb` | Figures of the above, saved to `figures/` |

## Assumptions as collected

- **Device:** CorPower buoy rated at 300 kW, 150 m spacing, clusters of 28 (7 × 4).
- **Density:** 0.3 MW per 150 m × 150 m = 13.3 MW/km² with no gaps. Vessel transit safety lanes lower it to a
  realistic 5–13 MW/km²:

  | Lane | Area per cluster | MW/km² | Scenario |
  |---|---|---|---|
  | None | 0.60 × 1.05 = 0.63 km² | 8.4 ÷ 0.63 = 13.3 | Aggressive |
  | 250 m | 0.85 × 1.30 = 1.105 km² | 8.4 ÷ 1.105 = 7.6 | Moderate |
  | 500 m | 1.10 × 1.55 = 1.705 km² | 8.4 ÷ 1.705 = 4.9 | Conservative |

  Per 100 km² of usable ocean: 490 MW (1,633 buoys), 760 MW (2,533 buoys), 1,330 MW (4,433 buoys).
- **Lifetime:** 20 years.
- **Profiles:** 2019 weather, the same year as the model's solar and wind.

## Sources

- Baca, Philip, Greene and Battey (2022), NREL, *Expert Elicitation for Wave Energy LCOE Futures*,
  https://www.osti.gov/biblio/1885577
- Neary et al. (2016), Sandia National Laboratories, *Levelized Cost of Energy for Marine Energy Conversion (MEC)
  Technologies*, https://www.osti.gov/servlets/purl/1406167
- CorPower Ocean (2026), *24/7 Clean Energy at Lowest Cost*, https://corpowerocean.com/247-clean-energy-wave-power/
- Têtu and Chozas (2021), Aalborg University, *A Proposed Guidance for the Economic Assessment of Wave Energy
  Converters at Early Development Stages*, Energies 14(15): 4699, https://doi.org/10.3390/en14154699

## Open points (to check before using the data in the model)

1. **Rated power: 300 or 400 kW?** The buoy is 300 kW, but in the profiles `power(kW) / power_ratio` = 400 kW.
2. **Capacity factors look high:** mean power ratio 49–78 % (the 5 California sites: 78, 67, 71, 49, 57 %); wave
   power is usually quoted at 25–45 %. WECC_F sits at 100 % of rated power about a third of the year. Are
   availability, array and electrical losses included?
3. **OpEx looks low:** 0.4 % of CapEx per year (e.g. 0.0373 / 9.33). Check units and the source value.
4. **Timezone of the profiles:** the `hour` column has no timezone; the model's snapshots are UTC.
5. **Sites outside California:** WECC_A–E and PACWAVE are in Oregon/Washington; the model covers California only.
6. **Usable area per site (km²):** needed to cap the capacity at each site (marine sanctuaries, shipping lanes,
   military areas).
7. **Grid connection:** which model node each site connects to, and whether the export cable is in the CapEx.
8. **2035 costs:** the model year is 2035; the table has 2030 and 2040.
9. **Discount rate** to annualize the CapEx.
10. **Unit typo in the notes:** 13.3 MW/km², not kW/km².
