"""Single-bus Oahu capacity expansion model with offshore wave hubs, plus a wave-cost sweep.

Run prepare_oahu.py first. Results go to results/oahu/.
"""
import logging
from pathlib import Path

import pandas as pd
import pypsa

import oahu_assumptions as A
from common import annuity

ROOT = Path(__file__).parent
INPUTS, RESULTS = ROOT / "inputs" / "oahu", ROOT / "results" / "oahu"
STEP_H = 3
POLICIES = {"least_cost": None, "renewable_100": 0.0}   # CO2 cap in t/yr (None = no cap)


def capital_cost(capex_per_kw, fom_frac, lifetime):
    """Overnight $/kW -> annualized $/MW/yr."""
    return (annuity(A.DISCOUNT_RATE, lifetime) + fom_frac) * capex_per_kw * 1e3


def fuel_cost(fuel, efficiency, vom):
    return A.FUEL_PRICE[fuel] * A.MMBTU_PER_MWH / efficiency + vom


def build_network(ts, sites, wave_capex_per_kw=A.WAVE_CAPEX_PER_KW, co2_cap=None, with_wave=True):
    n = pypsa.Network()
    n.set_snapshots(ts.index)
    n.snapshot_weightings.loc[:, :] = STEP_H

    fuels = {"lsfo": "oil", "diesel": "oil", "biodiesel": "biofuel"}
    n.add("Carrier", ["AC", "solar", "wind", "wave", "wave_export", "battery", "waste", "shed"])
    n.add("Carrier", "oil", co2_emissions=A.CO2_PER_MWH_TH["lsfo"])
    n.add("Carrier", "oil_diesel", co2_emissions=A.CO2_PER_MWH_TH["diesel"])
    n.add("Carrier", "biofuel", co2_emissions=A.CO2_PER_MWH_TH["biodiesel"])

    n.add("Bus", "oahu", x=-158.0, y=21.45)
    n.add("Load", "oahu_load", bus="oahu", p_set=ts.load_mw)

    # Existing thermal fleet (sunk cost, dispatch only)
    for name, (mw, eff, fuel, vom) in A.EXISTING_THERMAL.items():
        carrier = "oil_diesel" if fuel == "diesel" else fuels[fuel]
        n.add("Generator", name, bus="oahu", carrier=carrier, p_nom=mw,
              efficiency=eff, marginal_cost=fuel_cost(fuel, eff, vom))
    n.add("Generator", "waste", bus="oahu", carrier="waste", p_nom=A.WASTE_MW,
          p_max_pu=0.85, p_min_pu=0.7, marginal_cost=10.0)

    # Existing variable renewables
    n.add("Generator", "solar_existing", bus="oahu", carrier="solar",
          p_nom=A.EXISTING_SOLAR_MW, p_max_pu=ts.solar_cf)
    n.add("Generator", "wind_existing", bus="oahu", carrier="wind",
          p_nom=A.EXISTING_WIND_MW, p_max_pu=ts.wind_cf)
    n.add("StorageUnit", "battery_existing", bus="oahu", carrier="battery",
          p_nom=A.EXISTING_BATTERY_MW, max_hours=A.EXISTING_BATTERY_HOURS,
          efficiency_store=A.BATTERY_ONE_WAY_EFF, efficiency_dispatch=A.BATTERY_ONE_WAY_EFF,
          cyclic_state_of_charge=True)

    # New-build options
    for name, (capex, fom, life, pmax), cf in [("solar_new", A.NEW_SOLAR, ts.solar_cf),
                                               ("wind_new", A.NEW_WIND, ts.wind_cf)]:
        n.add("Generator", name, bus="oahu", carrier=name.split("_")[0], p_nom_extendable=True,
              p_nom_max=pmax, p_max_pu=cf, capital_cost=capital_cost(capex, fom, life))

    capex, fom, life, _ = A.NEW_BATTERY
    n.add("StorageUnit", "battery_new", bus="oahu", carrier="battery", p_nom_extendable=True,
          max_hours=A.NEW_BATTERY_HOURS, capital_cost=capital_cost(capex, fom, life),
          efficiency_store=A.BATTERY_ONE_WAY_EFF, efficiency_dispatch=A.BATTERY_ONE_WAY_EFF,
          cyclic_state_of_charge=True)

    capex, fom, life, _ = A.NEW_FIRM_BIOFUEL
    n.add("Generator", "biofuel_ct_new", bus="oahu", carrier="biofuel", p_nom_extendable=True,
          efficiency=0.33, marginal_cost=fuel_cost("biodiesel", 0.33, 8.0),
          capital_cost=capital_cost(capex, fom, life))

    # Wave: one offshore hub per site, with its own optimally sized export cable
    if with_wave:
        for site, s in sites.iterrows():
            hub = f"hub_{site}"
            n.add("Bus", hub, x=s.lon, y=s.lat)
            n.add("Generator", f"wave_{site}", bus=hub, carrier="wave", p_nom_extendable=True,
                  p_nom_max=s.p_nom_max_mw, p_max_pu=ts[f"wave_cf_{site}"],
                  capital_cost=capital_cost(wave_capex_per_kw, A.WAVE_FOM_FRAC, A.WAVE_LIFETIME))
            cable_per_mw = A.CABLE_COST_PER_MW_KM * s.cable_km + A.CABLE_LANDFALL_PER_MW
            n.add("Link", f"cable_{site}", bus0=hub, bus1="oahu", carrier="wave_export",
                  p_nom_extendable=True, efficiency=A.CABLE_EFFICIENCY,
                  capital_cost=annuity(A.DISCOUNT_RATE, A.CABLE_LIFETIME) * cable_per_mw)

    n.add("Generator", "load_shedding", bus="oahu", carrier="shed", p_nom=1e5, marginal_cost=A.VOLL)

    if co2_cap is not None:
        n.add("GlobalConstraint", "co2_cap", type="primary_energy",
              carrier_attribute="co2_emissions", sense="<=", constant=co2_cap)
    return n


def summarize(n, policy, wave_capex):
    w = n.snapshot_weightings.generators
    gen = n.generators.assign(energy=n.generators_t.p.mul(w, axis=0).sum(),
                              avail=n.generators_t.p_max_pu.reindex(columns=n.generators.index)
                              .fillna(1).mul(w, axis=0).sum())
    cap = gen.p_nom_opt
    is_wave = gen.carrier == "wave"
    wave_avail = (gen.avail * cap)[is_wave].sum()
    wave_energy = gen.energy[is_wave].sum()
    demand = n.loads_t.p_set.mul(w, axis=0).sum().sum()
    co2 = sum(gen.energy[g] / gen.efficiency[g] * n.carriers.co2_emissions.get(gen.carrier[g], 0)
              for g in gen.index)
    row = {
        "policy": policy, "wave_capex_per_kw": wave_capex,
        "system_cost_musd": n.objective / 1e6,
        "wave_mw": cap[is_wave].sum(),
        "wave_cable_mw": n.links.p_nom_opt.sum() if len(n.links) else 0.0,
        "solar_new_mw": cap["solar_new"], "wind_new_mw": cap["wind_new"],
        "battery_new_mw": n.storage_units.p_nom_opt["battery_new"],
        "biofuel_ct_new_mw": cap["biofuel_ct_new"],
        "wave_share_pct": 100 * wave_energy / demand,
        "wave_curtail_pct": 100 * (1 - wave_energy / wave_avail) if wave_avail > 0 else 0.0,
        "oil_gwh": gen.energy[gen.carrier.isin(["oil", "oil_diesel"])].sum() / 1e3,
        "co2_kt": co2 / 1e3,
        "unserved_mwh": gen.energy["load_shedding"],
    }
    row.update({f"mw_{g}": cap[g] for g in gen.index[is_wave]})
    row.update({f"mw_{l}": n.links.p_nom_opt[l] for l in n.links.index})
    return row


def main():
    logging.getLogger("pypsa").setLevel(logging.WARNING)
    logging.getLogger("linopy").setLevel(logging.WARNING)
    ts = pd.read_csv(INPUTS / "timeseries.csv", index_col=0, parse_dates=True)
    sites = pd.read_csv(INPUTS / "wave_sites.csv", index_col=0)
    RESULTS.mkdir(parents=True, exist_ok=True)

    rows = []
    for policy, co2_cap in POLICIES.items():
        runs = [(None, False)] + [(c, True) for c in A.WAVE_CAPEX_SWEEP]
        for capex, with_wave in runs:
            n = build_network(ts, sites, capex or A.WAVE_CAPEX_PER_KW, co2_cap, with_wave)
            status, condition = n.optimize(solver_name="highs", log_to_console=False,
                                           include_objective_constant=False)
            assert status == "ok", (policy, capex, status, condition)
            rows.append(summarize(n, policy, capex))
            tag = f"{policy}_{'nowave' if capex is None else f'wave{capex}'}"
            print(f"{tag:28s} cost ${rows[-1]['system_cost_musd']:7.1f}M  wave {rows[-1]['wave_mw']:6.1f} MW")
            if capex in (None, 5000):
                n.export_to_netcdf(RESULTS / f"network_{tag}.nc")

    out = pd.DataFrame(rows)
    out.to_csv(RESULTS / "sweep_summary.csv", index=False)
    print(out.round(1).to_string())


if __name__ == "__main__":
    main()
