"""Turn raw data into model-ready 3-hourly time series in inputs/oahu/.

Outputs:
  inputs/oahu/wave_sites.csv   site, lat, lon, landing, cable_km, p_nom_max_mw, annual_cf
  inputs/oahu/timeseries.csv   3-hourly: load_mw, solar_cf, wind_cf, wave_cf_<site>...
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

import oahu_assumptions as A
from common import haversine_km

ROOT = Path(__file__).parent
DATA, INPUTS = ROOT / "data", ROOT / "inputs" / "oahu"
STEP_H = 3


def load_wave():
    raw = pd.read_csv(DATA / "Oahu Wave Values - Sheet1.csv")
    assert len(raw) == 8760 // STEP_H and (raw.hour.diff().dropna() == STEP_H).all()

    kw = raw.filter(like="(kW)")
    kw.columns = [c.replace("power_Oahu", "").replace("(kW)", "") for c in kw.columns]
    kw = kw.rename(columns=lambda s: "WETS" if s == "WETS" else f"OAHU_{s}")

    assert (kw >= 0).all().all() and (kw.max() <= A.WAVE_RATED_KW * 1.001).all()
    ratio = raw.filter(like="power_ratio").to_numpy()
    assert np.allclose(kw.to_numpy() / A.WAVE_RATED_KW, ratio, atol=1e-6)

    return (kw / A.WAVE_RATED_KW).clip(0, 1) * A.WAVE_AVAILABILITY * A.WAVE_ARRAY_LOSS


def load_sites(cf):
    sites = pd.read_excel(DATA / "Coordinates of Interest.xlsx", sheet_name="Oahu")
    sites = sites.rename(columns={"Name": "site", "Lat": "lat", "Lon": "lon"}).set_index("site")
    assert set(sites.index) == set(cf.columns), (sites.index, cf.columns)

    land = pd.DataFrame(A.LANDING_POINTS, index=["lat", "lon"]).T
    dist = pd.DataFrame({name: haversine_km(sites.lat, sites.lon, p.lat, p.lon)
                         for name, p in land.iterrows()})
    sites["landing"] = dist.idxmin(axis=1)
    sites["cable_km"] = (dist.min(axis=1) * A.CABLE_ROUTING_FACTOR).round(1)
    sites["p_nom_max_mw"] = [A.WAVE_CAP_MW.get(s, A.WAVE_CAP_DEFAULT_MW) for s in sites.index]
    sites["annual_cf"] = cf.mean().round(3)
    return sites


def read_openmeteo(name):
    js = json.loads((INPUTS / name).read_text())["hourly"]
    idx = pd.to_datetime(js.pop("time"))
    return pd.DataFrame(js, index=idx)


def solar_cf():
    """Fixed-tilt PV: plane-of-array irradiance, simple cell-temperature derate, 14% losses."""
    w = read_openmeteo("openmeteo_solar.json")
    poa = w.global_tilted_irradiance.clip(lower=0)
    t_cell = w.temperature_2m + poa / 800 * 25
    return (poa / 1000 * (1 - 0.004 * (t_cell - 25)) * 0.86).clip(0, 1)


def wind_cf():
    """Generic 3.6 MW class turbine curve at 100 m, 12% wake/electrical/availability losses."""
    v = read_openmeteo("openmeteo_wind.json").wind_speed_100m
    cut_in, rated, cut_out = 3.0, 12.0, 25.0
    p = np.where(v < cut_in, 0, np.where(v < rated, ((v - cut_in) / (rated - cut_in)) ** 3, 1))
    p = np.where(v >= cut_out, 0, p)
    return pd.Series(p * 0.88, index=v.index)


def synthetic_load(index_utc):
    """Placeholder gross demand: evening-peaking daily shape, autumn seasonal peak, weekend dip."""
    local = index_utc + pd.Timedelta(hours=A.UTC_OFFSET_HOURS)
    daily = np.array([0.74, 0.70, 0.68, 0.67, 0.69, 0.75, 0.85, 0.93, 0.97, 1.00, 1.02, 1.03,
                      1.04, 1.04, 1.03, 1.02, 1.04, 1.12, 1.22, 1.20, 1.12, 1.01, 0.90, 0.81])
    seasonal = 1 + 0.06 * np.cos(2 * np.pi * (local.day_of_year.to_numpy() - 275) / 365)
    weekend = np.where(local.day_of_week.to_numpy() >= 5, 0.95, 1.0)
    shape = daily[local.hour.to_numpy()] * seasonal * weekend
    return pd.Series(shape / shape.sum() * A.ANNUAL_DEMAND_TWH * 1e6, index=index_utc)


def main():
    hourly_idx = pd.date_range(f"{A.WEATHER_YEAR}-01-01", periods=8760, freq="h")
    snapshots = pd.date_range(f"{A.WEATHER_YEAR}-01-01", periods=8760 // STEP_H, freq=f"{STEP_H}h")

    wave = load_wave()
    wave.index = snapshots
    sites = load_sites(wave)

    hourly = pd.DataFrame({
        "load_mw": synthetic_load(hourly_idx),
        "solar_cf": solar_cf().reindex(hourly_idx),
        "wind_cf": wind_cf().reindex(hourly_idx),
    })
    assert not hourly.isna().any().any()
    ts = hourly.resample(f"{STEP_H}h").mean()     # average over each 3-h block
    ts = ts.join(wave.add_prefix("wave_cf_"))

    sites.to_csv(INPUTS / "wave_sites.csv")
    ts.to_csv(INPUTS / "timeseries.csv", index_label="snapshot")

    print(sites.to_string())
    print(f"\nload: {ts.load_mw.mean() * 8.76:.0f} GWh/yr, peak {hourly.load_mw.max():.0f} MW")
    print(f"solar CF {ts.solar_cf.mean():.3f}, wind CF {ts.wind_cf.mean():.3f}")


if __name__ == "__main__":
    main()
