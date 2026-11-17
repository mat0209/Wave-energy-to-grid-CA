"""Turn raw West Coast data into model-ready 3-hourly inputs in inputs/west/.

Sources (see README): EIA-930 2023 hourly demand + generation by BA, EIA-860 2023 fleet,
Open-Meteo ERA5 2023 offshore winds, your wave curves and coordinates.

Outputs:
  inputs/west/wave_sites.csv    site, lat, lon, landing, zone, cable_km, p_nom_max_mw, annual_cf
  inputs/west/fleet.csv         existing nameplate MW by zone x carrier
  inputs/west/timeseries.csv    3-hourly load, hydro, solar/wind/offshore-wind/wave CFs
  inputs/west/solar_wind_profiles.csv  existing MW, reported TWh and CF by zone
"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

import west_assumptions as A
from common import haversine_km

ROOT = Path(__file__).parent
DATA, RAW, INPUTS = ROOT / "data", ROOT / "data" / "raw", ROOT / "inputs" / "west"
STEP_H = 3
HOURS = pd.date_range(f"{A.YEAR}-01-01", periods=8760, freq="h")


# --- Wave ----------------------------------------------------------------
def load_wave():
    raw = pd.read_csv(DATA / "generation_profiles.csv")
    assert len(raw) == 8760 // STEP_H and (raw.hour.diff().dropna() == STEP_H).all()
    kw = raw.filter(like="_power(kW)")
    kw.columns = [c.replace("_power(kW)", "") for c in kw.columns]
    assert (kw >= 0).all().all() and (kw.max() <= A.WAVE_RATED_KW * 1.001).all()
    assert np.allclose(kw.to_numpy() / A.WAVE_RATED_KW, raw.filter(like="_power_ratio").to_numpy(), atol=1e-6)
    return (kw / A.WAVE_RATED_KW).clip(0, 1) * A.WAVE_AVAILABILITY * A.WAVE_ARRAY_LOSS


def load_sites(cf):
    sites = pd.read_excel(DATA / "Coordinates of Interest.xlsx", sheet_name="WECC")
    sites = sites.rename(columns={"Name": "site", "Lat": "lat", "Lon": "lon"}).set_index("site")
    assert set(sites.index) == set(cf.columns), (sites.index, cf.columns)

    land = pd.DataFrame(A.LANDING_POINTS, index=["lat", "lon", "zone"]).T
    dist = pd.DataFrame({name: haversine_km(sites.lat, sites.lon, float(p.lat), float(p.lon))
                         for name, p in land.iterrows()})
    sites["landing"] = dist.idxmin(axis=1)
    sites["zone"] = sites.landing.map(land.zone)
    sites["cable_km"] = (dist.min(axis=1) * A.CABLE_ROUTING_FACTOR).round(1)
    sites["p_nom_max_mw"] = [A.WAVE_CAP_MW.get(s, A.WAVE_CAP_DEFAULT_MW) for s in sites.index]
    sites["annual_cf"] = cf.mean().round(3)
    return sites


# --- EIA-930 -------------------------------------------------------------
def read_930(kind, value_cols, key):
    files = sorted((RAW / "eia930").glob(f"EIA930_{kind}_{A.YEAR}_*.csv"))
    df = pd.concat([pd.read_csv(f, thousands=",", low_memory=False) for f in files])
    df["time"] = pd.to_datetime(df["UTC Time at End of Hour"], format="%m/%d/%Y %I:%M:%S %p") \
        - pd.Timedelta(hours=1)
    return {c: df.pivot_table(index="time", columns=key, values=c, aggfunc="sum") for c in value_cols}


def clean_demand(df):
    """Drop non-positive values and spikes, then fill gaps (including the year's first UTC hours)."""
    df = df.reindex(HOURS)
    df = df.where(df > 0)
    df = df.where(df < 3 * df.median())
    return df.interpolate(limit_direction="both").fillna(0)


def clean_generation(df):
    """Generation can legitimately be zero (solar at night); only fill gaps and drop negatives."""
    return df.reindex(HOURS).interpolate(limit_direction="both").fillna(0).clip(lower=0)


def eia930_zone_series(ciso_share):
    demand_col = "Demand (MW) (Adjusted)"
    gen_cols = {f"Net Generation (MW) from {f} (Adjusted)": f.split()[0].lower()
                for f in ["Hydropower and Pumped Storage", "Solar", "Wind"]}
    bal = read_930("BALANCE", [demand_col, *gen_cols], "Balancing Authority")
    sub = read_930("SUBREGION", ["Demand (MW)"], "Sub-Region")["Demand (MW)"]

    demand = clean_demand(bal[demand_col])
    sub = clean_demand(sub)
    out = {}
    for z in A.ZONES:
        bas = [b for b in A.ZONE_BAS[z] if b in demand]
        out[f"load_{z}"] = demand[bas].sum(axis=1) + sub[A.CISO_SUBREGIONS.get(z, [])].sum(axis=1)

    for col, short in gen_cols.items():
        g = clean_generation(bal[col])
        for z in A.ZONES:
            bas = [b for b in A.ZONE_BAS[z] if b in g]
            out[f"{short}_{z}"] = g[bas].sum(axis=1) + g["CISO"] * ciso_share[short][z]
    return pd.DataFrame(out)


# --- EIA-860 -------------------------------------------------------------
def load_fleet():
    gen = pd.read_excel(RAW / "eia860" / "3_1_Generator_Y2023.xlsx", sheet_name="Operable", header=1,
                        usecols=["Plant Code", "Technology", "Nameplate Capacity (MW)", "Status"])
    plants = pd.read_excel(RAW / "eia860" / "2___Plant_Y2023.xlsx", header=1,
                           usecols=["Plant Code", "Latitude", "Longitude", "Balancing Authority Code"])
    df = gen[gen.Status.isin(["OP", "SB"])].merge(plants, on="Plant Code", how="left")
    ba_zone = {ba: z for z, bas in A.ZONE_BAS.items() for ba in bas}
    df["zone"] = df["Balancing Authority Code"].map(ba_zone)
    ciso = df["Balancing Authority Code"] == "CISO"
    df.loc[ciso, "zone"] = A.ciso_zone(df.loc[ciso, "Latitude"], df.loc[ciso, "Longitude"])
    df["carrier"] = df.Technology.map(A.TECH_MAP)
    df = df.dropna(subset=["zone", "carrier"])

    fleet = df.pivot_table(index="zone", columns="carrier", values="Nameplate Capacity (MW)",
                           aggfunc="sum", fill_value=0).reindex(A.ZONES)
    c = df[ciso].pivot_table(index="zone", columns="carrier", values="Nameplate Capacity (MW)",
                             aggfunc="sum", fill_value=0)
    ciso_share = {k: (c[carrier] / c[carrier].sum()).reindex(A.ZONES, fill_value=0)
                  for k, carrier in [("hydropower", "hydro"), ("solar", "solar"), ("wind", "wind")]}
    return fleet, ciso_share


# --- Weather (Open-Meteo ERA5) --------------------------------------------
def openmeteo(name, points, variables, extra=None):
    path = INPUTS / f"openmeteo_{name}.json"
    if not path.exists():
        lats, lons = zip(*points.values())
        params = {"latitude": ",".join(map(str, lats)), "longitude": ",".join(map(str, lons)),
                  "start_date": f"{A.YEAR}-01-01", "end_date": f"{A.YEAR}-12-31",
                  "hourly": ",".join(variables), "timezone": "GMT", **(extra or {})}
        url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=120) as r:
            path.write_bytes(r.read())
    payload = json.loads(path.read_text())
    payload = payload if isinstance(payload, list) else [payload]
    frames = {}
    for key, js in zip(points, payload):
        h = js["hourly"]
        frames[key] = pd.DataFrame({v: h[v] for v in variables}, index=pd.to_datetime(h["time"]))
    return frames


def wind_cf(v, rated=12.0, losses=0.88):
    p = np.where(v < 3.0, 0, np.where(v < rated, ((v - 3.0) / (rated - 3.0)) ** 3, 1))
    return pd.Series(np.where(v >= 25.0, 0, p) * losses, index=v.index)


def main():
    INPUTS.mkdir(parents=True, exist_ok=True)
    snapshots = pd.date_range(f"{A.YEAR}-01-01", periods=8760 // STEP_H, freq=f"{STEP_H}h")

    wave = load_wave()
    wave.index = snapshots
    sites = load_sites(wave)

    fleet, ciso_share = load_fleet()
    obs = eia930_zone_series(ciso_share)

    osw_pts = {k: v[:2] for k, v in A.OFFSHORE_WIND.items()}
    wnd = openmeteo("offshore_wind", osw_pts, ["wind_speed_100m"], {"wind_speed_unit": "ms"})

    # Solar and onshore wind profiles = 2023 reported fleet output / EIA-860 nameplate.
    hourly = obs.copy()
    stats = []
    for z in A.ZONES:
        for tech in ("solar", "wind"):
            cf = obs[f"{tech}_{z}"] / fleet.loc[z, tech]
            cf = cf.where(cf <= 1.05).interpolate(limit_direction="both").clip(0, 1)
            hourly[f"{tech}_cf_{z}"] = cf
            stats.append({"zone": z, "tech": tech, "existing_mw": fleet.loc[z, tech],
                          "reported_twh": obs[f"{tech}_{z}"].sum() / 1e6, "cf": cf.mean()})
    for name in A.OFFSHORE_WIND:
        hourly[f"osw_cf_{name}"] = wind_cf(wnd[name].wind_speed_100m, rated=11.0, losses=0.85).reindex(HOURS)

    hourly = hourly.drop(columns=[f"{t}_{z}" for t in ("solar", "wind") for z in A.ZONES])
    assert not hourly.isna().any().any()
    ts = hourly.resample(f"{STEP_H}h").mean().join(wave.add_prefix("wave_cf_"))

    sites.to_csv(INPUTS / "wave_sites.csv")
    fleet.round(1).to_csv(INPUTS / "fleet.csv")
    ts.to_csv(INPUTS / "timeseries.csv", index_label="snapshot")
    pd.DataFrame(stats).round(3).to_csv(INPUTS / "solar_wind_profiles.csv", index=False)

    print(sites.to_string(), "\n")
    print(fleet.round(0).to_string(), "\n")
    print(pd.DataFrame(stats).round(3).to_string(), "\n")
    for z in A.ZONES:
        print(f"{z}: demand {obs[f'load_{z}'].sum() / 1e6:.1f} TWh, peak {obs[f'load_{z}'].max():.0f} MW, "
              f"hydro {obs[f'hydropower_{z}'].sum() / 1e6:.1f} TWh (max {obs[f'hydropower_{z}'].max():.0f} MW)")
    print("offshore wind CF:", {k: round(ts[f'osw_cf_{k}'].mean(), 3) for k in A.OFFSHORE_WIND})


if __name__ == "__main__":
    main()
