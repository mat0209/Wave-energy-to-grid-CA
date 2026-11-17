"""O'ahu capacity-expansion model v2: myopic 2030 -> 2040 -> 2045 pathways with wave, plus a 2045 wave
capex sweep. Run prepare_oahu.py first. Results go to results/oahu/.

Usage:
  python run_oahu.py                      # 8 pathways (2 policies x 4 wave cases) + 2045 sweep
  python run_oahu.py --only rps:moderate  # one pathway
  python run_oahu.py --solver highs       # default: Gurobi if licensed, else HiGHS
"""
import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

import oahu_assumptions as A
from common import annuity, haversine_km

ROOT = Path(__file__).parent
INPUTS, RESULTS = ROOT / "inputs" / "oahu", ROOT / "results" / "oahu"
NETS = RESULTS / "networks"
RENEWABLE = ["solar", "rooftop_solar", "onwind", "offwind", "wave", "msw", "biodiesel"]
THERMAL = ["steam", "cc", "ct", "ice", "msw"]
VRE = ["solar", "rooftop_solar", "onwind", "offwind"]
WAVE_COSTS = pd.read_csv(ROOT / A.WAVE_COST_FILE)


# --- Costs -------------------------------------------------------------------------------------------
def interp_year(d, year):
    xs = sorted(d)
    return float(np.interp(year, xs, [d[x] for x in xs]))


def tech_cost(tech, year):
    """Annualized $/MW-yr and overnight $/kW for a new-build technology."""
    t = A.TECH[tech]
    capex = interp_year(t["capex"], year) * A.HAWAII_COST_MULTIPLIER
    return (annuity(A.DISCOUNT_RATE, t["life"]) * capex + t["fom"]) * 1e3, capex


def wave_cost(year, scenario):
    """Your table, linearly interpolated between years: (capex $/MW, opex $/MW-yr)."""
    g = WAVE_COSTS[WAVE_COSTS.scenario == scenario].sort_values("year")
    return (np.interp(year, g.year, g.capex_musd_per_mw) * 1e6,
            np.interp(year, g.year, g.opex_musd_per_mw_yr) * 1e6)


def cable_cost(km):
    return annuity(A.DISCOUNT_RATE, A.CABLE_LIFETIME) * (A.CABLE_COST_PER_MW_KM * km + A.CABLE_LANDFALL_PER_MW)


def fuel_cost(fuel, heat_rate, kind):
    return A.FUEL_PRICE[fuel] * heat_rate / 1000 + A.VOM[kind]


def offwind_cable_km():
    s = A.OFFSHORE_WIND_SITE
    lat, lon = A.LANDING_POINTS[s["landing"]]
    return float(haversine_km(s["lat"], s["lon"], lat, lon)) * A.CABLE_ROUTING_FACTOR


# --- Network -------------------------------------------------------------------------------------------
def steps(hours):
    return int(np.ceil(hours / A.STEP_H))


def add_fleet(n, ts, fleet, year, policy):
    full_rps = policy == "rps" and A.RPS_TARGET[year] >= 1.0 and A.FOSSIL_RETIRE_AT_FULL_RPS
    for unit, u in fleet.iterrows():
        if A.RETIREMENTS.get(unit, 9999) <= year:
            continue
        if u.kind in THERMAL:
            fuels = A.FUEL_SWITCH.get(u.plant, [u.fuel])
            if full_rps:      # fossil-only units cannot run under 100% RPS: treated as deactivated
                fuels = [f for f in fuels if f in RENEWABLE]
                if not fuels:
                    continue
            for fuel in fuels:
                kw = dict(bus="oahu", carrier=fuel, p_nom=u.p_nom, efficiency=3412 / u.heat_rate,
                          marginal_cost=fuel_cost(fuel, u.heat_rate, u.kind), kind=u.kind, group=unit,
                          tech="existing", quick_start=u.kind == "ice" and u.plant not in A.QUICK_START_EXCLUDE)
                if u.kind in A.UC_CLASSES:
                    # start the year offline and free to start (PyPSA's default assumes "already on")
                    kw.update(committable=True, p_min_pu=u.p_min_pu, min_up_time=steps(A.MIN_UP_H[u.kind]),
                              min_down_time=steps(A.MIN_DOWN_H[u.kind]), up_time_before=0, down_time_before=100,
                              start_up_cost=A.START_COST_PER_MW[u.kind] * u.p_nom)
                if u.kind == "msw":
                    kw.update(p_min_pu=A.MSW_MIN_PU, p_max_pu=A.MSW_MAX_PU)
                n.add("Generator", unit if len(fuels) == 1 else f"{unit} [{fuel}]", **kw)
        elif u.kind in ("solar", "onwind"):
            profile = "solar_cf" if u.kind == "solar" else "wind_cf"
            n.add("Generator", unit, bus="oahu", carrier=u.kind, p_nom=u.p_nom, p_max_pu=ts[profile],
                  kind=u.kind, group=unit, profile=profile, tech="existing")
        elif u.kind == "battery":
            n.add("StorageUnit", unit, bus="oahu", carrier="battery", p_nom=u.p_nom,
                  max_hours=u.energy_mwh / u.p_nom, efficiency_store=A.BATTERY_ONE_WAY_EFF,
                  efficiency_dispatch=A.BATTERY_ONE_WAY_EFF, cyclic_state_of_charge=True, tech="existing")


def add_carried(n, ts, carried, year):
    """Capacity built in earlier pathway years and still within its lifetime (sunk cost)."""
    for c in carried:
        if c["build_year"] + c["life"] <= year:
            continue
        a = dict(c["attrs"])
        if c["profile"]:
            a["p_max_pu"] = ts[c["profile"]]
        n.add(c["component"], c["name"], p_nom=c["p_nom"], p_nom_extendable=False, capital_cost=0.0, **a)


def carried_mw(carried, year, tech, site=None):
    return sum(c["p_nom"] for c in carried if c["tech"] == tech and c.get("site") == site
               and c["build_year"] + c["life"] > year)


def build_network(ts, sites, fleet, year, policy, wave, carried, wave_override=None):
    """wave: None or a cost scenario; wave_override: (capex $/MW, opex $/MW-yr) for the sweep."""
    n = pypsa.Network()
    n.set_snapshots(ts.index)
    n.snapshot_weightings.loc[:, :] = A.STEP_H

    n.add("Carrier", ["AC", "wave_hub", "solar", "rooftop_solar", "onwind", "offwind", "wave", "wave_export",
                      "battery", "msw", "biodiesel", "shed"])
    for fuel in ["lsfo", "diesel"]:
        n.add("Carrier", fuel, co2_emissions=A.CO2_T_PER_MMBTU[fuel] * 3.412)   # t / MWh_thermal
    n.add("Bus", "oahu", x=-158.0, y=21.45, carrier="AC")
    n.add("Load", "demand", bus="oahu", p_set=ts.load_mw * A.DEMAND_GROWTH[year])
    n.add("Generator", "rooftop_solar", bus="oahu", carrier="rooftop_solar", p_nom=A.ROOFTOP_PV_MW[year],
          p_max_pu=ts.rooftop_cf, kind="rooftop_solar", group="rooftop_solar", tech="existing")
    add_fleet(n, ts, fleet, year, policy)

    # New-build options
    def cap(tech):
        mx = A.TECH[tech]["max_mw"]
        return np.inf if mx is None else max(mx - carried_mw(carried, year, tech), 0.0)

    for tech, profile in [("solar", "solar_cf"), ("onwind", "wind_cf"), ("offwind", "offwind_cf")]:
        cc, _ = tech_cost(tech, year)
        if tech == "offwind":
            cc += cable_cost(offwind_cable_km())
        n.add("Generator", f"{tech}_new", bus="oahu", carrier=tech, p_nom_extendable=True, p_nom_max=cap(tech),
              p_max_pu=ts[profile], capital_cost=cc, lifetime=A.TECH[tech]["life"], kind=tech, group=f"{tech}_new",
              profile=profile, tech=tech)
    cc, _ = tech_cost("battery", year)
    n.add("StorageUnit", "battery_new", bus="oahu", carrier="battery", p_nom_extendable=True,
          max_hours=A.BATTERY_HOURS, capital_cost=cc, efficiency_store=A.BATTERY_ONE_WAY_EFF,
          efficiency_dispatch=A.BATTERY_ONE_WAY_EFF, cyclic_state_of_charge=True,
          lifetime=A.TECH["battery"]["life"], tech="battery")
    cc, _ = tech_cost("biodiesel", year)
    hr = A.TECH["biodiesel"]["heat_rate"]
    n.add("Generator", "biodiesel_new", bus="oahu", carrier="biodiesel", p_nom_extendable=True,
          efficiency=3412 / hr, marginal_cost=fuel_cost("biodiesel", hr, "ice"), capital_cost=cc,
          lifetime=A.TECH["biodiesel"]["life"], kind="ice", group="biodiesel_new", tech="biodiesel", quick_start=True)

    # Wave: one offshore hub per site with its own optimally sized export cable
    if wave:
        capex, opex = wave_override or wave_cost(year, wave)
        wave_cc = annuity(A.DISCOUNT_RATE, A.WAVE_LIFETIME) * capex + opex
        for site, s in sites.iterrows():
            hub = f"hub_{site}"
            n.add("Bus", hub, x=s.lon, y=s.lat, carrier="wave_hub")
            n.add("Generator", f"wave_{site}", bus=hub, carrier="wave", p_nom_extendable=True,
                  p_nom_max=max(s.p_nom_max_mw - carried_mw(carried, year, "wave", site), 0.0),
                  p_max_pu=ts[f"wave_cf_{site}"], marginal_cost=A.WAVE_MARGINAL_COST, capital_cost=wave_cc,
                  lifetime=A.WAVE_LIFETIME, kind="wave", group=f"wave_{site}", profile=f"wave_cf_{site}",
                  tech="wave", site=site, wave_capex_usd_per_mw=capex, wave_opex_usd_per_mw_yr=opex)
            n.add("Link", f"cable_{site}", bus0=hub, bus1="oahu", carrier="wave_export", p_nom_extendable=True,
                  efficiency=A.CABLE_EFFICIENCY, capital_cost=cable_cost(s.cable_km), lifetime=A.CABLE_LIFETIME,
                  tech="cable", site=site)
    add_carried(n, ts, carried, year)    # after the hubs, which carried wave capacity sits on
    n.add("Generator", "load_shedding", bus="oahu", carrier="shed", p_nom=1e5, marginal_cost=A.VOLL,
          kind="shed", group="shed", tech="shed")
    return n


# --- Reliability, policy and fuel-switch constraints ------------------------------------------------------------
def make_extra(policy, year, ts):
    share = A.RPS_TARGET[year] if policy == "rps" else None

    def extra(n, sns):
        m = n.model
        snap = pd.Index(sns, name="snapshot")
        da = lambda s: xr.DataArray(np.asarray(s, float), coords={"snapshot": snap}, dims="snapshot")
        w = da(n.snapshot_weightings.generators.loc[sns])
        gen = n.generators
        p = m["Generator-p"]
        p_nom_g = m["Generator-p_nom"] if "Generator-p_nom" in m.variables else None
        ext = gen.index[gen.p_nom_extendable]
        cf = n.get_switchable_as_dense("Generator", "p_max_pu", sns)

        # Hawai'i RPS: renewable share of net generation
        if share is not None:
            real = gen.index[gen.carrier != "shed"]
            ren = gen.index[gen.carrier.isin(RENEWABLE)]
            m.add_constraints((p.sel(name=ren) * w).sum() - share * (p.sel(name=real) * w).sum() >= 0, name="rps")

        # Dual-fuel units commit on one fuel at a time
        uc = gen.index[gen.committable]
        for group, members in gen.loc[uc].groupby("group").groups.items():
            if len(members) > 1:
                m.add_constraints(m["Generator-status"].sel(name=list(members)).sum("name") <= 1,
                                  name=f"one_fuel_{group}")

        # Energy reserve margin (HECO AOS): available capacity >= (1 + margin) x load, every step
        load = da(n.loads_t.p_set.loc[sns].sum(axis=1))
        is_wave = gen.carrier == "wave"
        firm = gen[~gen.p_nom_extendable & gen.kind.isin(THERMAL)].drop_duplicates("group")
        vre = gen[~gen.p_nom_extendable & gen.carrier.isin(VRE)]
        const = (firm.p_nom * gen.p_max_pu[firm.index]).sum() * A.ERM_FIRM_DERATE \
            + (cf[vre.index] * vre.p_nom).sum(axis=1)
        avail = da(const)
        ext_nw = [g for g in ext if not is_wave[g]]
        if ext_nw:
            cf_ext = xr.DataArray(cf[ext_nw].to_numpy(), coords={"snapshot": snap, "name": ext_nw},
                                  dims=["snapshot", "name"])
            avail = avail + (p_nom_g.sel(name=ext_nw) * cf_ext).sum("name")
        su = n.storage_units
        if len(su):
            rs = m.add_variables(lower=0, coords=[snap, pd.Index(su.index, name="name")], name="erm_storage")
            fix, sext = su.index[~su.p_nom_extendable], su.index[su.p_nom_extendable]
            if len(fix):
                m.add_constraints(rs.sel(name=fix) <= xr.DataArray(su.p_nom[fix].values, coords={"name": fix},
                                                                   dims="name"), name="erm_storage_pnom_fix")
            if len(sext):
                m.add_constraints(rs.sel(name=sext) - m["StorageUnit-p_nom"].sel(name=sext) <= 0,
                                  name="erm_storage_pnom_ext")
            m.add_constraints(rs * A.ERM_STORAGE_HOURS - m["StorageUnit-state_of_charge"] <= 0, name="erm_storage_soc")
            avail = avail + rs.sum("name")
        hubs = sorted(gen.loc[is_wave, "bus"].unique())
        if hubs:
            rw = m.add_variables(lower=0, coords=[snap, pd.Index(hubs, name="hub")], name="erm_wave")
            links = n.links
            for hub in hubs:
                gs = gen.index[is_wave & (gen.bus == hub)]
                site_cf = da(cf[gs[0]]) * A.CABLE_EFFICIENCY
                g_fix, g_ext = [g for g in gs if g not in ext], [g for g in gs if g in ext]
                cap_g = gen.p_nom[g_fix].sum() + (p_nom_g.sel(name=g_ext).sum() if g_ext else 0)
                m.add_constraints(rw.sel(hub=hub) - cap_g * site_cf <= 0, name=f"erm_wave_avail_{hub}")
                ls = links.index[links.bus0 == hub]
                l_fix, l_ext = ls[~links.p_nom_extendable[ls].values], ls[links.p_nom_extendable[ls].values]
                cap_l = links.p_nom[l_fix].sum() + (m["Link-p_nom"].sel(name=l_ext).sum() if len(l_ext) else 0)
                m.add_constraints(rw.sel(hub=hub) - cap_l <= 0, name=f"erm_wave_cable_{hub}")
            avail = avail + rw.sum("hub")
        m.add_constraints(avail >= (1 + A.ERM_MARGIN) * load, name="erm")

        # Spinning (contingency) reserve: online headroom + quick-start engines + battery headroom
        head = 0
        if len(uc):
            pn = xr.DataArray(gen.p_nom[uc].values, coords={"name": uc}, dims="name")
            head = (m["Generator-status"].sel(name=uc) * pn - p.sel(name=uc)).sum("name")
        quick = gen[gen.quick_start.fillna(False).astype(bool)]
        q_fix, q_ext = quick.index[~quick.p_nom_extendable], quick.index[quick.p_nom_extendable]
        if len(q_fix):
            head = head + float(quick.p_nom[q_fix].sum()) - p.sel(name=q_fix).sum("name")
        if len(q_ext):
            head = head + p_nom_g.sel(name=q_ext).sum() - p.sel(name=q_ext).sum("name")
        if len(su):
            rb = m.add_variables(lower=0, coords=[snap, pd.Index(su.index, name="name")], name="spin_storage")
            pn_fix = xr.DataArray(np.where(su.p_nom_extendable, 0.0, su.p_nom), coords={"name": su.index}, dims="name")
            lhs = rb + m["StorageUnit-p_dispatch"] - m["StorageUnit-p_store"]
            if len(sext):
                m.add_constraints(rb.sel(name=sext) + m["StorageUnit-p_dispatch"].sel(name=sext)
                                  - m["StorageUnit-p_store"].sel(name=sext) - m["StorageUnit-p_nom"].sel(name=sext) <= 0,
                                  name="spin_storage_headroom_ext")
            if len(fix):
                m.add_constraints(lhs.sel(name=fix) <= pn_fix.sel(name=fix), name="spin_storage_headroom_fix")
            m.add_constraints(rb * A.RESERVE_STORAGE_HOURS - m["StorageUnit-state_of_charge"] <= 0, name="spin_storage_soc")
            head = head + rb.sum("name")
        m.add_constraints(head >= A.CONTINGENCY_MW, name="spinning_reserve")

    return extra


# --- Solve -------------------------------------------------------------------------------------------------------
def pick_solver(pref):
    if pref in ("auto", "gurobi"):
        try:
            import gurobipy as gp
            env = gp.Env(empty=True)
            env.setParam("OutputFlag", 0)
            env.start()
            m = gp.Model(env=env)
            m.addVars(3000)           # above the 2,000-variable limit of the pip trial license
            m.optimize()
            return "gurobi"
        except Exception as e:
            if pref == "gurobi":
                raise
            print(f"Gurobi unavailable ({e}); using HiGHS")
    return "highs"


SOLVER_OPTIONS = {"gurobi": {"Threads": 8},
                  "highs": {"solver": "ipm", "run_crossover": "on", "threads": 6}}


def solve(n, policy, year, ts, solver):
    status, condition = n.optimize(solver_name=solver, solver_options=SOLVER_OPTIONS[solver],
                                   linearized_unit_commitment=True, extra_functionality=make_extra(policy, year, ts),
                                   include_objective_constant=False, log_to_console=False)
    assert status == "ok" and str(condition) == "optimal", (policy, year, status, condition)
    duals = {"rps": float(n.model.constraints["rps"].dual) if "rps" in n.model.constraints else 0.0,
             "erm": n.model.constraints["erm"].dual.to_series(),
             "spin": n.model.constraints["spinning_reserve"].dual.to_series()}
    return duals


# --- Results -----------------------------------------------------------------------------------------------------
def new_builds(n, year):
    """Extendable capacity built this year, to carry into later years as sunk capacity."""
    out = []
    keep = ["bus", "carrier", "efficiency", "marginal_cost", "kind", "group", "tech", "site", "profile"]
    for comp, df in [("Generator", n.generators), ("StorageUnit", n.storage_units), ("Link", n.links)]:
        for name, r in df[df.p_nom_extendable & (df.p_nom_opt > 0.1)].iterrows():
            attrs = {k: r[k] for k in keep if k in r and not (isinstance(r[k], float) and np.isnan(r[k]))
                     and r[k] != ""}
            profile = attrs.pop("profile", None)
            if comp == "StorageUnit":
                attrs.update(max_hours=r.max_hours, efficiency_store=r.efficiency_store,
                             efficiency_dispatch=r.efficiency_dispatch, cyclic_state_of_charge=True)
            if comp == "Link":
                attrs.update(bus0=r.bus0, bus1=r.bus1)
                attrs.pop("bus", None)
            base = name.removesuffix("_new")
            if "group" in attrs:
                attrs["group"] = f"{base}_{year}"     # its own group: only dual-fuel units share one
            out.append(dict(component=comp, name=f"{base}_{year}", p_nom=float(r.p_nom_opt), attrs=attrs,
                            profile=profile, tech=r.get("tech"), site=r.get("site") if comp != "StorageUnit" else None,
                            build_year=year, life=float(r.lifetime), annual_cost=float(r.capital_cost)))
    for o in out:
        if isinstance(o["site"], float):
            o["site"] = None
    return out


def wave_value(n, duals, year, policy, sites, ts):
    """First-MW value of wave at each site from this solve's prices: energy + RPS credit + ERM credit."""
    w = n.snapshot_weightings.generators
    lam = n.buses_t.marginal_price["oahu"]
    share = A.RPS_TARGET[year] if policy == "rps" else 0.0
    ann = annuity(A.DISCOUNT_RATE, A.WAVE_LIFETIME) + A.WAVE_SWEEP_OPEX_FRAC
    rows = []
    for site, s in sites.iterrows():
        cf = ts[f"wave_cf_{site}"]
        energy = float((lam * cf * A.CABLE_EFFICIENCY * w).sum())
        rps = duals["rps"] * (1 - share) * float((cf * w).sum())
        erm = float((duals["erm"].reindex(cf.index).fillna(0) * cf * A.CABLE_EFFICIENCY).sum())
        cable = cable_cost(s.cable_km)
        rows.append(dict(site=site, cf=cf.mean(), energy_k=energy / 1e3, rps_k=rps / 1e3, erm_k=erm / 1e3,
                         cable_k=cable / 1e3, breakeven_capex_per_kw=(energy + rps + erm - cable) / ann / 1e3))
    return pd.DataFrame(rows)


def summarize(n, duals, ctx, carried):
    w = n.snapshot_weightings.generators
    gen, su, links = n.generators, n.storage_units, n.links
    energy = n.generators_t.p.mul(w, axis=0).sum()
    avail = (n.get_switchable_as_dense("Generator", "p_max_pu").mul(gen.p_nom_opt) * w.values[:, None]).sum()
    load = float(n.loads_t.p_set.mul(w, axis=0).sum().sum())
    real = gen.index[gen.carrier != "shed"]
    ren = gen.index[gen.carrier.isin(RENEWABLE)]
    co2 = sum(energy[g] / gen.efficiency[g] * n.carriers.co2_emissions.get(gen.carrier[g], 0.0) for g in real)
    year = ctx["year"]
    carried_cost = sum(c["annual_cost"] * c["p_nom"] for c in carried if c["build_year"] + c["life"] > year)
    row = dict(ctx, system_cost_musd=(n.objective + carried_cost) / 1e6, new_build_musd=n.objective / 1e6,
               carried_musd=carried_cost / 1e6, load_twh=load / 1e6,
               rps_share_pct=100 * energy[ren].sum() / energy[real].sum(), co2_kt=co2 / 1e3,
               unserved_mwh=energy["load_shedding"],
               avg_price=float((n.buses_t.marginal_price["oahu"] * n.loads_t.p_set.sum(axis=1)).sum()
                               / n.loads_t.p_set.sum(axis=1).sum()),
               rps_price=duals["rps"], erm_price_kw_yr=float(duals["erm"].sum()) / 1e3,
               spin_price_mw_yr=float(duals["spin"].sum()))
    # capacity and generation by technology group
    tech = gen.tech.where(gen.tech != "existing", gen.carrier)
    once = ~(gen.group.duplicated() & (gen.tech == "existing"))   # a dual-fuel unit counts once
    for t, mw in gen.p_nom_opt[once].groupby(tech[once]).sum().items():
        row[f"cap_{t}_mw"] = mw
    for t, mwh in energy.groupby(gen.carrier).sum().items():
        row[f"gen_{t}_gwh"] = mwh / 1e3
    row["cap_battery_mw"] = su.p_nom_opt.sum()
    row["cap_battery_new_mw"] = su.p_nom_opt[su.p_nom_extendable].sum()
    row["cap_battery_mwh"] = (su.p_nom_opt * su.max_hours).sum()
    for t in ["solar", "onwind", "offwind", "biodiesel", "wave"]:
        row[f"new_{t}_mw"] = gen.p_nom_opt[gen.p_nom_extendable & (gen.tech == t)].sum()
    wv = gen.index[gen.carrier == "wave"]
    row["wave_mw"] = gen.p_nom_opt[wv].sum()
    row["wave_gwh"] = energy[wv].sum() / 1e3
    row["wave_curtail_pct"] = 100 * (1 - energy[wv].sum() / avail[wv].sum()) if row["wave_mw"] > 1 else np.nan
    for site in sorted({s for s in gen.get("site", pd.Series(dtype=object)) if isinstance(s, str) and s}):
        row[f"mw_wave_{site}"] = gen.p_nom_opt[(gen.carrier == "wave") & (gen.site == site)].sum()
        row[f"mw_cable_{site}"] = links.p_nom_opt[links.site == site].sum()
    vre = gen.index[gen.carrier.isin(VRE + ["wave"])]
    row["vre_curtail_pct"] = 100 * (1 - energy[vre].sum() / avail[vre].sum())
    return row


def run_case(ts, sites, fleet, year, policy, wave, carried, solver, tag, wave_override=None, save=True):
    n = build_network(ts, sites, fleet, year, policy, wave, carried, wave_override)
    duals = solve(n, policy, year, ts, solver)
    capex = (wave_override or wave_cost(year, wave))[0] / 1e3 if wave else np.nan
    ctx = dict(year=year, policy=policy, wave=wave or "none", wave_capex_per_kw=capex)
    row = summarize(n, duals, ctx, carried)
    wv = wave_value(n, duals, year, policy, sites, ts).assign(year=year, policy=policy, wave=wave or "none", run=tag)
    if save:
        NETS.mkdir(parents=True, exist_ok=True)
        n.export_to_netcdf(NETS / f"{tag}.nc", float32=True, compression={"zlib": True, "complevel": 4})
    print(f"{tag:34s} cost ${row['system_cost_musd']:7.1f}M  RPS {row['rps_share_pct']:5.1f}%  "
          f"wave {row['wave_mw']:7.1f} MW  new solar {row['new_solar_mw']:6.0f}  batt {row['cap_battery_new_mw']:6.0f}  "
          f"biodiesel {row['new_biodiesel_mw']:5.0f}  OSW {row['new_offwind_mw']:5.0f}  shed {row['unserved_mwh']:6.0f} MWh")
    return n, row, wv


def run_pathway(ts, sites, fleet, policy, wave, solver):
    carried, rows, values = [], [], []
    for year in A.YEARS:
        n, row, wv = run_case(ts, sites, fleet, year, policy, wave, carried, solver, f"{policy}_{wave or 'none'}_{year}")
        carried = carried + new_builds(n, year)
        rows.append(row)
        values.append(wv)
    return rows, values, carried


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solver", default="auto", choices=["auto", "gurobi", "highs"])
    ap.add_argument("--only", nargs="*", help="pathways as policy:wave, e.g. rps:moderate least_cost:none")
    ap.add_argument("--skip-sweep", action="store_true")
    args = ap.parse_args()
    for name in ["pypsa", "linopy", "gurobipy"]:
        logging.getLogger(name).setLevel(logging.ERROR)
    solver = pick_solver(args.solver)
    print("solver:", solver)

    ts = pd.read_csv(INPUTS / "timeseries.csv", index_col=0, parse_dates=True)
    sites = pd.read_csv(INPUTS / "wave_sites.csv", index_col=0)
    fleet = pd.read_csv(INPUTS / "fleet.csv", index_col=0)
    RESULTS.mkdir(parents=True, exist_ok=True)

    wave_cases = [None] + A.WAVE_SCENARIOS
    todo = [(p, w) for p in A.POLICIES for w in wave_cases]
    if args.only:
        todo = [(s.split(":")[0], None if s.split(":")[1] == "none" else s.split(":")[1]) for s in args.only]
    rows, values, carried_by = [], [], {}
    for policy, wave in todo:
        r, v, carried = run_pathway(ts, sites, fleet, policy, wave, solver)
        rows += r
        values += v
        carried_by[(policy, wave)] = carried
    pd.DataFrame(rows).to_csv(RESULTS / ("pathways.csv" if not args.only else "pathways_partial.csv"), index=False)
    pd.concat(values).round(3).to_csv(RESULTS / "wave_value.csv", index=False)

    if args.skip_sweep or ("rps", None) not in carried_by:
        return
    # 2045 build curve: start from the no-wave RPS pathway's 2030 + 2040 builds, vary wave capex in 2045
    base = [c for c in carried_by[("rps", None)] if c["build_year"] < 2045]
    sweep = [summarize_ref(rows)]
    for capex in A.WAVE_SWEEP_CAPEX:
        override = (capex * 1e6, capex * 1e6 * A.WAVE_SWEEP_OPEX_FRAC)
        _, row, _ = run_case(ts, sites, fleet, 2045, "rps", "sweep", base, solver, f"sweep_{capex:g}",
                             wave_override=override)
        sweep.append(dict(row, policy="rps_2045", wave_capex_per_kw=capex * 1e3))
    pd.DataFrame(sweep).to_csv(RESULTS / "sweep_summary.csv", index=False)


def summarize_ref(rows):
    ref = next(r for r in rows if r["policy"] == "rps" and r["wave"] == "none" and r["year"] == 2045)
    return dict(ref, policy="rps_2045", wave_capex_per_kw=np.nan)


if __name__ == "__main__":
    main()
