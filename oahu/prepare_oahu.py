"""Turn raw data into model-ready inputs for the O'ahu model (v2) in inputs/oahu/.

Outputs:
  wave_sites.csv   site, lat, lon, landing, cable_km, p_nom_max_mw, annual_cf
  fleet.csv        one row per existing unit (EIA-860 2023 operable + proposed online by FLEET_ONLINE_BY)
  timeseries.csv   3-hourly UTC: load_mw (gross), solar_cf, rooftop_cf, wind_cf, offwind_cf, wave_cf_<site>
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

import oahu_assumptions as A
from common import haversine_km

ROOT = Path(__file__).parent
DATA, INPUTS, EIA = ROOT / "data", ROOT / "inputs" / "oahu", ROOT / "data" / "raw" / "eia860"


# --- Wave ---------------------------------------------------------------------------------------------
def load_wave():
    raw = pd.read_csv(DATA / "Oahu Wave Values - Sheet1.csv")
    assert len(raw) == 8760 // A.STEP_H and (raw.hour.diff().dropna() == A.STEP_H).all()
    kw = raw.filter(like="(kW)")
    kw.columns = [c.replace("power_Oahu", "").replace("(kW)", "") for c in kw.columns]
    kw = kw.rename(columns=lambda s: "WETS" if s == "WETS" else f"OAHU_{s}")
    assert (kw >= 0).all().all() and (kw.max() <= A.WAVE_RATED_KW * 1.001).all()
    return (kw / A.WAVE_RATED_KW).clip(0, 1) * A.WAVE_AVAILABILITY * A.WAVE_ARRAY_LOSS


def nearest_landing(lat, lon, names):
    land = pd.DataFrame({k: A.LANDING_POINTS[k] for k in names}, index=["lat", "lon"]).T
    dist = pd.DataFrame({k: haversine_km(lat, lon, p.lat, p.lon) for k, p in land.iterrows()})
    return dist.idxmin(axis=1), (dist.min(axis=1) * A.CABLE_ROUTING_FACTOR).round(1)


def load_sites(cf):
    sites = pd.read_excel(DATA / "Coordinates of Interest.xlsx", sheet_name="Oahu")
    sites = sites.rename(columns={"Name": "site", "Lat": "lat", "Lon": "lon"}).set_index("site")
    assert set(sites.index) == set(cf.columns), (sites.index, cf.columns)
    sites["landing"], sites["cable_km"] = nearest_landing(sites.lat, sites.lon, A.WAVE_LANDINGS)
    sites["p_nom_max_mw"] = [A.WAVE_CAP_MW.get(s, A.WAVE_CAP_DEFAULT_MW) for s in sites.index]
    sites["annual_cf"] = cf.mean().round(3)
    return sites


# --- Weather-driven profiles (ERA5 via Open-Meteo, 2019) ------------------------------------------------
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


def turbine_cf(v, cut_in, rated, cut_out, losses):
    p = np.where(v < cut_in, 0, np.where(v < rated, ((v - cut_in) / (rated - cut_in)) ** 3, 1))
    return pd.Series(np.where(v >= cut_out, 0, p) * losses, index=v.index)


def wind_cf():
    """Generic 3.6 MW-class onshore turbine at 100 m (Kahuku ridge), 12% losses."""
    return turbine_cf(read_openmeteo("openmeteo_wind.json").wind_speed_100m, 3.0, 12.0, 25.0, 0.88)


def offwind_cf():
    """Generic 15 MW-class floating turbine at 100 m, BOEM O'ahu South call area."""
    v = read_openmeteo("openmeteo_offshore_wind.json").wind_speed_100m
    return turbine_cf(v, 3.0, 11.0, 25.0, A.OFFSHORE_WIND_LOSSES)


# --- Demand -----------------------------------------------------------------------------------------------
def synthetic_gross_load(index_utc, temperature):
    """Placeholder gross load: daily shape x weekend dip x cooling response, scaled to energy and peak."""
    local = index_utc + pd.Timedelta(hours=A.UTC_OFFSET_HOURS)
    daily = np.array([0.74, 0.70, 0.68, 0.67, 0.69, 0.75, 0.85, 0.93, 0.97, 1.00, 1.02, 1.03,
                      1.04, 1.04, 1.03, 1.02, 1.04, 1.12, 1.22, 1.20, 1.12, 1.01, 0.90, 0.81])
    t_day = temperature.groupby(local.date).transform("mean").to_numpy()
    cooling = 1 + A.COOLING_SLOPE * (t_day - t_day.mean())
    weekend = np.where(local.day_of_week.to_numpy() >= 5, 0.95, 1.0)
    shape = daily[local.hour.to_numpy()] * weekend * cooling
    mean = A.GROSS_ENERGY_TWH * 1e6 / 8760
    b = (A.GROSS_PEAK_MW - mean) / (shape.max() - shape.mean())
    load = mean + b * (shape - shape.mean())
    assert load.min() > 0.3 * mean, "energy/peak placeholders imply an implausible load factor"
    return pd.Series(load, index=index_utc)


def measured_gross_load(index_utc, rooftop_cf_hourly):
    """Your hourly load file (HST), mapped by hour-of-year onto the weather year, converted to gross."""
    f = pd.read_csv(ROOT / A.LOAD_FILE)
    assert len(f) >= 8760, f"{A.LOAD_FILE}: expected 8760 hourly rows, got {len(f)}"
    hst = pd.to_datetime(f.datetime_hst)
    f = f.assign(hoy=((hst - hst.dt.normalize().dt.to_period("Y").dt.start_time).dt.total_seconds()
                      // 3600).astype(int))
    by_hoy = f.groupby("hoy").load_mw.mean().reindex(range(8760)).interpolate()
    local = index_utc + pd.Timedelta(hours=A.UTC_OFFSET_HOURS)
    hoy = ((local - pd.Timestamp(f"{A.WEATHER_YEAR}-01-01")).total_seconds() // 3600).astype(int) % 8760
    load = pd.Series(by_hoy.to_numpy()[hoy], index=index_utc)
    if A.LOAD_FILE_BASIS == "net":
        load = load + A.LOAD_FILE_ROOFTOP_MW * rooftop_cf_hourly
    return load


# --- Fleet (EIA-860 2023) ------------------------------------------------------------------------------------
def classify(tech, prime):
    if tech == "Solar Photovoltaic":
        return "solar"
    if tech == "Onshore Wind Turbine":
        return "onwind"
    if tech == "Batteries":
        return "battery"
    if tech == "Municipal Solid Waste":
        return "msw"
    return {"ST": "steam", "CT": "cc", "CA": "cc", "GT": "ct", "IC": "ice"}[prime]


def read_fleet():
    plants = pd.read_excel(EIA / "2___Plant_Y2023.xlsx", header=1)
    plants = plants[(plants.State == "HI") & plants.County.str.contains("Honolulu", na=False)]
    gens = pd.read_excel(EIA / "3_1_Generator_Y2023.xlsx", sheet_name=None, header=1)
    store = pd.read_excel(EIA / "3_4_Energy_Storage_Y2023.xlsx", sheet_name=None, header=1)
    op = gens["Operable"].assign(online=lambda d: d["Operating Year"])
    pr = gens["Proposed"].assign(online=lambda d: d["Current Year"])
    pr = pr[pr.online <= A.FLEET_ONLINE_BY]
    g = pd.concat([op, pr], ignore_index=True)
    g = g[g["Plant Code"].isin(plants["Plant Code"]) & ~g["Plant Name"].isin(A.EXCLUDE_PLANTS)]
    mwh = pd.concat([store["Operable"], store["Proposed"]]).set_index(["Plant Code", "Generator ID"])
    num = lambda s: pd.to_numeric(s, errors="coerce")

    g = g.assign(
        unit=g["Plant Name"] + " " + g["Generator ID"].astype(str),
        plant=g["Plant Name"],
        kind=[classify(t, p) for t, p in zip(g.Technology, g["Prime Mover"])],
        fuel=g["Energy Source 1"].map(A.FUEL_OF),
        p_nom=num(g["Summer Capacity (MW)"]).fillna(num(g["Nameplate Capacity (MW)"])),
        min_mw=num(g["Minimum Load (MW)"]).fillna(0),
        energy_mwh=[num(mwh["Nameplate Energy Capacity (MWh)"]).get((p, i), np.nan)
                    for p, i in zip(g["Plant Code"], g["Generator ID"])],
    ).merge(plants[["Plant Code", "Latitude", "Longitude"]], on="Plant Code")
    for plant, v in A.BATTERY_MWH_OVERRIDE.items():
        g.loc[(g.plant == plant) & (g.kind == "battery"), "energy_mwh"] = v
    return g


def build_fleet(g):
    rows = []
    thermal = g[g.kind.isin(["steam", "ct", "ice"])]
    for r in thermal.itertuples():
        rows.append(dict(unit=r.unit, plant=r.plant, kind=r.kind, fuel=r.fuel, p_nom=r.p_nom,
                         min_mw=r.min_mw, online=r.online, lat=r.Latitude, lon=r.Longitude))
    # Kalaeloa combined cycle: two trains, each one CT plus half the steam turbine
    cc = g[g.kind == "cc"]
    if len(cc):
        st = cc[cc["Prime Mover"] == "CA"]
        for r in cc[cc["Prime Mover"] == "CT"].itertuples():
            rows.append(dict(unit=f"{r.plant} train {r.unit[-1]}", plant=r.plant, kind="cc", fuel="lsfo",
                             p_nom=r.p_nom + st.p_nom.sum() / 2, min_mw=r.min_mw + st.min_mw.sum() / 2,
                             online=r.online, lat=r.Latitude, lon=r.Longitude))
    # Aggregated: H-POWER, utility solar, wind, batteries
    for kind, unit in [("msw", "H Power"), ("solar", "solar_existing"), ("onwind", "onwind_existing"),
                       ("battery", "battery_existing")]:
        d = g[g.kind == kind]
        rows.append(dict(unit=unit, plant=unit, kind=kind, fuel="msw" if kind == "msw" else None,
                         p_nom=d.p_nom.sum(), min_mw=d.min_mw.sum(), online=int(d.online.max()),
                         energy_mwh=d.energy_mwh.sum() if kind == "battery" else np.nan,
                         lat=d.Latitude.mean(), lon=d.Longitude.mean(), n_units=len(d)))
    fleet = pd.DataFrame(rows).set_index("unit")
    fleet["heat_rate"] = [A.HEAT_RATE.get(u, A.HEAT_RATE.get(p, np.nan)) for u, p in zip(fleet.index, fleet.plant)]
    is_thermal = fleet.kind.isin(["steam", "cc", "ct", "ice", "msw"])
    missing = fleet.index[is_thermal & fleet.heat_rate.isna()]
    assert not len(missing), f"no heat rate for {list(missing)}"
    fleet["p_min_pu"] = np.where(fleet.kind.isin(A.UC_CLASSES), (fleet.min_mw / fleet.p_nom).clip(0, 0.9), 0.0)
    return fleet


def main():
    INPUTS.mkdir(parents=True, exist_ok=True)
    hourly_idx = pd.date_range(f"{A.WEATHER_YEAR}-01-01", periods=8760, freq="h")
    snapshots = pd.date_range(f"{A.WEATHER_YEAR}-01-01", periods=8760 // A.STEP_H, freq=f"{A.STEP_H}h")

    wave = load_wave()
    wave.index = snapshots
    sites = load_sites(wave)

    solar = solar_cf().reindex(hourly_idx)
    rooftop = solar * A.ROOFTOP_PV_DERATE
    temperature = read_openmeteo("openmeteo_solar.json").temperature_2m.reindex(hourly_idx)
    use_file = (ROOT / A.LOAD_FILE).exists()
    load = measured_gross_load(hourly_idx, rooftop) if use_file else synthetic_gross_load(hourly_idx, temperature)
    hourly = pd.DataFrame({"load_mw": load, "solar_cf": solar, "rooftop_cf": rooftop,
                           "wind_cf": wind_cf().reindex(hourly_idx),
                           "offwind_cf": offwind_cf().reindex(hourly_idx)})
    assert not hourly.isna().any().any(), hourly.isna().sum()
    ts = hourly.resample(f"{A.STEP_H}h").mean().join(wave.add_prefix("wave_cf_"))

    fleet = build_fleet(read_fleet())
    sites.to_csv(INPUTS / "wave_sites.csv")
    fleet.to_csv(INPUTS / "fleet.csv")
    ts.to_csv(INPUTS / "timeseries.csv", index_label="snapshot")

    pd.set_option("display.width", 200)
    print(sites.to_string(), "\n")
    print(fleet.drop(columns=["lat", "lon"]).round(2).to_string(), "\n")
    print(fleet.groupby("kind").p_nom.sum().round(1).to_string(), "\n")
    src = f"{A.LOAD_FILE} ({A.LOAD_FILE_BASIS})" if use_file else "synthetic placeholder"
    print(f"load ({src}): {hourly.load_mw.sum() / 1e6:.2f} TWh gross, hourly peak {hourly.load_mw.max():.0f} MW, "
          f"min {hourly.load_mw.min():.0f} MW")
    print("annual CF:", ts[["solar_cf", "rooftop_cf", "wind_cf", "offwind_cf"]].mean().round(3).to_dict())


if __name__ == "__main__":
    main()
