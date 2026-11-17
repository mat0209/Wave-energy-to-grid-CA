"""Three-zone West Coast (PNW, CA_N, CA_S) capacity expansion model with offshore wave hubs.

Run prepare_west.py first. Results go to results/west/.
Usage: python run_west.py [--workers N] [--only clean_100:5000 ...]
"""
import argparse
import logging
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import pypsa

import west_assumptions as A
from common import annuity

ROOT = Path(__file__).parent
INPUTS, RESULTS = ROOT / "inputs" / "west", ROOT / "results" / "west"
STEP_H = 3
POLICIES = {"least_cost": None, "clean_100": 0.0}       # CO2 cap in t/yr (None = no cap)
FOSSIL = ["gas_cc", "gas_ct", "coal", "oil"]
# Interior point without crossover: far faster than simplex for this LP size.
SOLVER_OPTIONS = {"solver": "ipm", "run_crossover": "off", "parallel": "on", "threads": 2}


def capital_cost(capex_per_kw, fom_frac, lifetime):
    """Overnight $/kW -> annualized $/MW/yr."""
    return (annuity(A.DISCOUNT_RATE, lifetime) + fom_frac) * capex_per_kw * 1e3


def load_inputs():
    ts = pd.read_csv(INPUTS / "timeseries.csv", index_col=0, parse_dates=True)
    sites = pd.read_csv(INPUTS / "wave_sites.csv", index_col=0)
    fleet = pd.read_csv(INPUTS / "fleet.csv", index_col=0)
    return ts, sites, fleet


def build_network(ts, sites, fleet, wave_capex_per_kw=A.WAVE_CAPEX_PER_KW, co2_cap=None, with_wave=True):
    n = pypsa.Network()
    n.set_snapshots(ts.index)
    n.snapshot_weightings.loc[:, :] = STEP_H

    fuel_of = {c: p[1] for c, p in A.THERMAL_PARAMS.items()}
    n.add("Carrier", ["AC", "solar", "wind", "offwind", "wave", "wave_export", "hydro", "phs",
                      "battery", "nuclear", "geothermal", "biomass", "clean_firm", "transmission", "shed"])
    for c in FOSSIL:
        n.add("Carrier", c, co2_emissions=A.CO2_PER_MWH_TH[fuel_of[c]])

    sb_cost = capital_cost(*A.NEW_BATTERY)
    cf_cost = capital_cost(*A.NEW_CLEAN_FIRM)
    cf_marginal = A.FUEL_PRICE["h2"] * A.MMBTU_PER_MWH / A.CLEAN_FIRM_EFF + 5.0

    for z in A.ZONES:
        x, y = A.ZONE_XY[z]
        n.add("Bus", z, x=x, y=y)
        n.add("Load", f"{z} load", bus=z, p_set=ts[f"load_{z}"])
        f = fleet.loc[z]

        for carrier, (eff, fuel, vom, pmin, pmax) in A.THERMAL_PARAMS.items():
            if f.get(carrier, 0) > 0:
                mc = (A.FUEL_PRICE[fuel] * A.MMBTU_PER_MWH / eff if fuel else 0) + vom
                n.add("Generator", f"{z} {carrier}", bus=z, carrier=carrier, p_nom=f[carrier],
                      efficiency=eff, marginal_cost=mc, p_min_pu=pmin, p_max_pu=pmax)

        for tech, (capex, fom, life, pmax) in [("solar", A.NEW_SOLAR), ("wind", A.NEW_WIND)]:
            n.add("Generator", f"{z} {tech}", bus=z, carrier=tech, p_nom=f[tech],
                  p_max_pu=ts[f"{tech}_cf_{z}"])
            n.add("Generator", f"{z} {tech} new", bus=z, carrier=tech, p_nom_extendable=True,
                  p_nom_max=pmax[z], p_max_pu=ts[f"{tech}_cf_{z}"],
                  capital_cost=capital_cost(capex, fom, life))

        # Hydro, driven by observed 2023 output: a fixed run-of-river share (minimum release)
        # plus a reservoir that can re-time the rest within one week of storage.
        inflow = ts[f"hydropower_{z}"]
        p_nom = max(f["hydro"], inflow.max() * 1.02)
        ror_pu = A.HYDRO_MIN_FLOW_FRAC * inflow / p_nom
        n.add("Generator", f"{z} hydro ror", bus=z, carrier="hydro", p_nom=p_nom,
              p_max_pu=ror_pu, p_min_pu=ror_pu)
        n.add("StorageUnit", f"{z} hydro", bus=z, carrier="hydro", p_nom=p_nom,
              max_hours=A.HYDRO_MAX_HOURS, inflow=(1 - A.HYDRO_MIN_FLOW_FRAC) * inflow,
              p_max_pu=1 - ror_pu, p_min_pu=0.0, efficiency_dispatch=1.0, cyclic_state_of_charge=True)
        if f["phs"] > 0:
            n.add("StorageUnit", f"{z} phs", bus=z, carrier="phs", p_nom=f["phs"],
                  max_hours=A.PHS_MAX_HOURS, efficiency_store=A.PHS_ONE_WAY_EFF,
                  efficiency_dispatch=A.PHS_ONE_WAY_EFF, cyclic_state_of_charge=True)
        n.add("StorageUnit", f"{z} battery", bus=z, carrier="battery", p_nom=f["battery"],
              max_hours=A.EXISTING_BATTERY_HOURS, efficiency_store=A.BATTERY_ONE_WAY_EFF,
              efficiency_dispatch=A.BATTERY_ONE_WAY_EFF, cyclic_state_of_charge=True)
        n.add("StorageUnit", f"{z} battery new", bus=z, carrier="battery", p_nom_extendable=True,
              max_hours=A.NEW_BATTERY_HOURS, capital_cost=sb_cost, efficiency_store=A.BATTERY_ONE_WAY_EFF,
              efficiency_dispatch=A.BATTERY_ONE_WAY_EFF, cyclic_state_of_charge=True)
        n.add("Generator", f"{z} clean_firm new", bus=z, carrier="clean_firm", p_nom_extendable=True,
              efficiency=A.CLEAN_FIRM_EFF, marginal_cost=cf_marginal, capital_cost=cf_cost)
        n.add("Generator", f"{z} load_shedding", bus=z, carrier="shed", p_nom=1e5, marginal_cost=A.VOLL)

    for name, (lat, lon, zone, pmax) in A.OFFSHORE_WIND.items():
        n.add("Generator", name, bus=zone, carrier="offwind", p_nom_extendable=True, p_nom_max=pmax,
              p_max_pu=ts[f"osw_cf_{name}"], capital_cost=capital_cost(*A.NEW_OFFSHORE_WIND))

    for name, (b0, b1, mw, km) in A.CORRIDORS.items():
        common = dict(bus0=b0, bus1=b1, carrier="transmission", p_min_pu=-1, efficiency=1.0)
        n.add("Link", name, p_nom=mw, **common)
        n.add("Link", f"{name} expansion", p_nom_extendable=True, **common,
              capital_cost=annuity(A.DISCOUNT_RATE, A.TRANSMISSION_LIFETIME) * A.TRANSMISSION_COST_PER_MW_KM * km)

    if with_wave:
        wave_cost = capital_cost(wave_capex_per_kw, A.WAVE_FOM_FRAC, A.WAVE_LIFETIME)
        for site, s in sites.iterrows():
            hub = f"hub_{site}"
            n.add("Bus", hub, x=s.lon, y=s.lat)
            n.add("Generator", f"wave_{site}", bus=hub, carrier="wave", p_nom_extendable=True,
                  p_nom_max=s.p_nom_max_mw, p_max_pu=ts[f"wave_cf_{site}"], capital_cost=wave_cost)
            cable_per_mw = A.CABLE_COST_PER_MW_KM * s.cable_km + A.CABLE_LANDFALL_PER_MW
            n.add("Link", f"cable_{site}", bus0=hub, bus1=s.zone, carrier="wave_export",
                  p_nom_extendable=True, efficiency=A.CABLE_EFFICIENCY,
                  capital_cost=annuity(A.DISCOUNT_RATE, A.CABLE_LIFETIME) * cable_per_mw)

    if co2_cap is not None:
        n.add("GlobalConstraint", "co2_cap", type="primary_energy",
              carrier_attribute="co2_emissions", sense="<=", constant=co2_cap)
    return n


def summarize(n, policy, wave_capex):
    w = n.snapshot_weightings.generators
    g = n.generators
    energy = n.generators_t.p.mul(w, axis=0).sum()
    avail = n.generators_t.p_max_pu.reindex(columns=g.index).fillna(1).mul(w, axis=0).sum() * g.p_nom_opt
    is_wave = g.carrier == "wave"
    demand = n.loads_t.p_set.mul(w, axis=0).sum().sum()
    new = g.p_nom_opt[g.p_nom_extendable].groupby(g.carrier).sum()
    co2 = sum(energy[i] / g.efficiency[i] * n.carriers.co2_emissions.get(g.carrier[i], 0) for i in g.index)
    sto_new = n.storage_units.query("p_nom_extendable").p_nom_opt.sum()
    links = n.links.p_nom_opt
    row = {
        "policy": policy, "wave_capex_per_kw": wave_capex,
        "system_cost_busd": n.objective / 1e9,
        "wave_mw": g.p_nom_opt[is_wave].sum(),
        "wave_cable_mw": links.filter(like="cable_").sum(),
        "wave_share_pct": 100 * energy[is_wave].sum() / demand,
        "wave_curtail_pct": 100 * (1 - energy[is_wave].sum() / avail[is_wave].sum())
        if g.p_nom_opt[is_wave].sum() > 1 else 0.0,
        "offwind_new_mw": new.get("offwind", 0), "solar_new_mw": new.get("solar", 0),
        "wind_new_mw": new.get("wind", 0), "battery_new_mw": sto_new,
        "clean_firm_new_mw": new.get("clean_firm", 0),
        "transmission_new_mw": links.filter(like=" expansion").sum(),
        "fossil_twh": energy[g.carrier.isin(FOSSIL)].sum() / 1e6,
        "co2_mt": co2 / 1e6,
        "unserved_gwh": energy[g.carrier == "shed"].sum() / 1e3,
    }
    zone_of = {i: n.links.at[f"cable_{i.removeprefix('wave_')}", "bus1"] for i in g.index[is_wave]}
    by_zone = g.p_nom_opt[is_wave].groupby(zone_of).sum()
    row.update({f"wave_mw_{z}": by_zone.get(z, 0.0) for z in A.ZONES})
    row.update({f"mw_{i}": g.p_nom_opt[i] for i in g.index[is_wave]})
    row.update({f"mw_{i}": links[i] for i in links.index if i.startswith("cable_")})
    row.update({f"mw_{i}": links[i] for i in links.index if i.endswith(" expansion")})
    row.update({f"mw_{i}": g.p_nom_opt[i] for i in g.index[g.carrier == "offwind"]})
    return row


def solve_one(job):
    policy, co2_cap, capex, save = job
    logging.getLogger("pypsa").setLevel(logging.WARNING)
    logging.getLogger("linopy").setLevel(logging.WARNING)
    ts, sites, fleet = load_inputs()
    n = build_network(ts, sites, fleet, capex or A.WAVE_CAPEX_PER_KW, co2_cap, with_wave=capex is not None)
    status, condition = n.optimize(solver_name="highs", solver_options=SOLVER_OPTIONS,
                                   log_to_console=False, include_objective_constant=False)
    assert status == "ok", (policy, capex, status, condition)
    tag = f"{policy}_{'nowave' if capex is None else f'wave{capex}'}"
    if save or capex in (None, A.WAVE_CAPEX_PER_KW):
        n.export_to_netcdf(RESULTS / f"network_{tag}.nc")
    row = summarize(n, policy, capex)
    print(f"{tag:24s} cost ${row['system_cost_busd']:6.2f}B  wave {row['wave_mw']:7.0f} MW  "
          f"offwind {row['offwind_new_mw']:6.0f} MW", flush=True)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--only", nargs="+", help="run selected jobs, e.g. clean_100:5000 least_cost:none")
    args = parser.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)

    jobs = [(p, cap, c, False) for p, cap in POLICIES.items() for c in [None, *A.WAVE_CAPEX_SWEEP]]
    if args.only:                    # selected jobs always save their solved network
        jobs = [(p, POLICIES[p], None if c == "none" else int(c), True)
                for p, c in (j.split(":") for j in args.only)]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(solve_one, jobs))

    if not args.only:
        out = pd.DataFrame(rows)
        out.to_csv(RESULTS / "sweep_summary.csv", index=False)
        print(out.loc[:, :"unserved_gwh"].round(2).to_string())


if __name__ == "__main__":
    main()
