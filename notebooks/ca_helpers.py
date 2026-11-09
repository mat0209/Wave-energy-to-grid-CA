"""Helpers for the California 2035 scenario notebook.

Loads the prepared PyPSA-USA network, applies scenario parameters, solves it
with PyPSA-USA's own `solve_network.py` (same constraints as Snakemake), and
builds result tables and figures.
"""

import contextlib
import logging
import os
import resource
import sys
import time
import warnings
from pathlib import Path
from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa
import xarray as xr
import yaml

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message="Coordinates across variables not equal")

ROOT = Path(__file__).resolve().parents[1]  # repo root
REPO = Path(os.environ.get("PYPSA_USA_DIR", ROOT / "pypsa-usa"))  # PyPSA-USA submodule
WORKFLOW = REPO / "workflow"
OUTPUTS = ROOT / "outputs"

# base/ holds the Snakemake-built 2035 network (refresh with scripts/update_base.py)
BASE = ROOT / "base"
# Prepared networks a scenario can start from: name -> (network, merged Snakemake config)
NETWORKS = {
    "zones4": (BASE / "network_2035.nc", BASE / "config_2035.yaml"),  # 4 ReEDS zones
    "hybrid25": (BASE / "network_2035_hybrid25.nc", BASE / "config_2035_hybrid25.yaml"),  # 25 nodes (base)
}
SOLVE_TIME = {"zones4": "2-4 min", "hybrid25": "~15 min, close other heavy apps"}
GEO = BASE / "geo"

sys.path.insert(0, str(WORKFLOW / "scripts"))
import solve_network as sn  # noqa: E402

with open(NETWORKS["zones4"][1]) as f:
    CONFIG = yaml.safe_load(f)

OPTS = [o for o in CONFIG["wildcards"]["opts"].split("-") if o]
if CONFIG["wildcards"]["sector"] != "E":
    OPTS.append("sector")

# Values the Snakemake base network was built with; the notebook overrides them per scenario.
BASE_PARAMS = {
    "export_price": CONFIG["electricity"]["exports"]["costs"],
    "import_price": CONFIG["electricity"]["imports"]["costs"],
    "erm_margin": CONFIG["electricity"]["erm"]["all"],
}

plt.rcParams.update({
    "figure.dpi": 110,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": "#e6e6e6",
    "grid.linewidth": 0.6,
    "axes.axisbelow": True,
    "font.size": 10,
})


@contextlib.contextmanager
def quiet():
    """Silence PyPSA/linopy warnings while loading/solving (the solver log still goes to solver.log)."""
    logging.disable(logging.WARNING)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def load(path) -> pypsa.Network:
    with quiet():
        return pypsa.Network(path)


# ---------------------------------------------------------------------------
# Scenario: parameters -> network -> solve
# ---------------------------------------------------------------------------


# Renewable policy. Eligible carriers count toward the "annual" target and stay on in "strict" mode,
# in California and as out-of-state contracted resources on the import buses. Add "wave" here once it exists
# under another carrier name.
RENEWABLE_CARRIERS = ["solar", "onwind", "offwind_floating", "geothermal", "biomass", "hydro", "wave"]
FOSSIL_CARRIERS = ["CCGT", "CCGT-95CCS", "OCGT", "coal", "oil", "waste", "unspecified_imports"]
RENEWABLE_DEFAULTS = {"renewables": None, "renewable_share": 1.0, "nuclear_counts": False}


def normalize_params(params: dict) -> dict:
    """Fill the renewable-policy defaults and drop the keys that have no effect.

    With renewables=None the result has the same keys as before the option existed, so cached
    results and the Snakemake reproduction check stay valid.
    """
    p = {**params, **{k: v for k, v in RENEWABLE_DEFAULTS.items() if k not in params}}
    mode = p["renewables"]
    if mode is None:
        return {k: v for k, v in p.items() if k not in RENEWABLE_DEFAULTS}
    if mode not in ("annual", "strict"):
        raise ValueError(f"renewables must be None, 'annual' or 'strict', got {mode!r}")
    if mode == "strict":
        p.pop("renewable_share")
    return p


def eligible_carriers(params: dict) -> list:
    return RENEWABLE_CARRIERS + (["nuclear"] if params.get("nuclear_counts") else [])


def apply_params(n: pypsa.Network, params: dict) -> None:
    """Write scenario parameters into a freshly loaded base network."""
    exports = n.links.index[n.links.carrier == "exports"]
    n.links.loc[exports, "marginal_cost"] = -float(params["export_price"])  # negative: exporting earns money

    imports = n.generators.index[n.generators.carrier == "unspecified_imports"]
    n.generators.loc[imports, "marginal_cost"] = float(params["import_price"])

    if params.get("renewables") == "strict":
        # Switch off every non-eligible generator (fossil, unspecified imports, and nuclear unless it counts).
        # Making them extendable with p_nom_max = 0 retires them (p_nom_opt = 0), so they also give no
        # capacity to the reserve margin, and the capacity table still shows them as retired.
        off_carriers = FOSSIL_CARRIERS + ([] if params.get("nuclear_counts") else ["nuclear"])
        off = n.generators.index[n.generators.carrier.isin(off_carriers)]
        n.generators.loc[off, "p_nom_extendable"] = True
        n.generators.loc[off, ["p_nom_min", "p_nom_max"]] = 0.0


def add_annual_renewable_constraint(n: pypsa.Network, params: dict) -> None:
    """Eligible generation minus exports >= share x California load, over the year (energy-weighted).

    Exports are subtracted so that renewable power sold out of state does not count toward California's
    target. With share = 1.0, fossil + unspecified imports can then cover at most the storage and
    transmission losses over the year, but single hours can still run on gas if other hours compensate.
    """
    m = n.model
    w = n.snapshot_weightings.generators
    w_da = xr.DataArray(w.values, coords={"snapshot": m["Generator-p"].indexes["snapshot"]}, dims="snapshot")

    gens = n.generators.index[n.generators.carrier.isin(eligible_carriers(params))]
    exports = n.links.index[(n.links.carrier == "exports") & n.links.bus0.isin(ca_buses(n))]
    loads = n.loads.index[ca_mask(n, "loads")]
    load_mwh = float(n.get_switchable_as_dense("Load", "p_set")[loads].sum(axis=1).mul(w).sum())

    lhs = (m["Generator-p"].sel(name=gens) * w_da).sum()
    if len(exports):
        lhs = lhs - (m["Link-p"].sel(name=exports) * w_da).sum()
    share = float(params["renewable_share"])
    m.add_constraints(lhs >= share * load_mwh, name="GlobalConstraint-CA_renewable_share")
    logging.getLogger(__name__).info(f"CA renewable target: {share:.0%} of {load_mwh / 1e6:.1f} TWh")


def solve(n: pypsa.Network, params: dict, log_path: Path, network: str = "zones4") -> pypsa.Network:
    """Solve exactly like the Snakemake solve_network rule, with scenario overrides in the config."""
    config = yaml.safe_load(NETWORKS[network][1].read_text())
    config["electricity"]["erm"] = {"all": float(params["erm_margin"])}
    solving = config["solving"]
    # solve_network.py reads a global `snakemake` object; give it the fields it uses.
    sn.snakemake = SimpleNamespace(
        params=SimpleNamespace(solving=solving, foresight=config["foresight"]),
        input=SimpleNamespace(interface_limits=config["electricity"]["transmission_interface_limits"]),
        config=config,
    )
    np.random.seed(solving["options"].get("seed", 123))

    # solve_network() passes the module-level extra_functionality to PyPSA; wrap it to add our constraint.
    original_extra = sn.extra_functionality
    if params.get("renewables") == "annual":
        def extra(n, snapshots):
            original_extra(n, snapshots)
            add_annual_renewable_constraint(n, params)
        sn.extra_functionality = extra
    try:
        with contextlib.chdir(WORKFLOW), quiet():  # config paths are relative to workflow/
            n = sn.prepare_network(n, solving["options"])
            sn.solve_network(
                n, config=config, solving=solving, opts=OPTS, log_fn=str(log_path), log_to_console=False, progress=False
            )
            if "ERM" in OPTS:
                sn.store_ERM_duals(n)
    finally:
        sn.extra_functionality = original_extra
    status = getattr(n.model, "status", "ok")  # quiet() hides solve_network's warning for a non-optimal solve
    if status != "ok":
        raise RuntimeError(f"Solve finished with status '{status}'; see {log_path}")
    return n


def peak_ram_gb() -> float:
    """Peak memory of this Python process since it started (restart the kernel for a clean reading)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e9 if sys.platform == "darwin" else peak / 1e6  # bytes on macOS, kB on Linux


def run_scenario(name: str, params: dict, force: bool = False, network: str = "zones4") -> pypsa.Network:
    """Solve a scenario (or load it from outputs/<name>/ if it was solved with the same parameters)."""
    out = OUTPUTS / name
    out.mkdir(parents=True, exist_ok=True)
    path, params_path = out / "network.nc", out / "parameters.yaml"
    params = normalize_params(params)
    if network != "zones4":
        params = {**params, "network": network}

    if path.exists() and not force and params_path.exists():
        saved = yaml.safe_load(params_path.read_text())
        if saved == params:
            print(f"[{name}] loading cached result (same parameters)")
            return load(path)
        print(f"[{name}] parameters changed since the last run -> re-solving")

    print(f"[{name}] solving ({SOLVE_TIME[network]})...")
    start = time.time()
    n = load(NETWORKS[network][0])
    apply_params(n, params)
    n = solve(n, params, out / "solver.log", network)
    stats = {"network": network, "solve_minutes": round((time.time() - start) / 60, 1), "peak_ram_GB": round(peak_ram_gb(), 1)}
    with quiet():
        n.export_to_netcdf(path)
    params_path.write_text(yaml.safe_dump(params, sort_keys=False))
    (out / "run_stats.yaml").write_text(yaml.safe_dump(stats, sort_keys=False))
    print(f"[{name}] solved in {stats['solve_minutes']} min, peak RAM {stats['peak_ram_GB']} GB, objective {n.objective:,.0f} $")
    return n


def check_against_snakemake(n: pypsa.Network, params: dict, network: str = "zones4") -> None:
    """With the base parameters, the notebook must reproduce the Snakemake solve."""
    if network != "zones4":
        print(f"No Snakemake reference for network '{network}': skipping the reproduction check.")
        return
    if normalize_params(params) != BASE_PARAMS:
        print(f"Parameters differ from the Snakemake base ({BASE_PARAMS}): skipping the reproduction check.")
        return
    ref = yaml.safe_load((BASE / "snakemake_reference.yaml").read_text())
    rel = abs(n.objective - ref["objective"]) / abs(ref["objective"])
    snk_cap = pd.Series(ref["capacity_GW"])
    diff = _capacity_by_carrier(n).sub(snk_cap, fill_value=0).abs().max()
    print(f"objective  notebook: {n.objective:,.0f} $   snakemake: {ref['objective']:,.0f} $   rel. diff: {rel:.2e}")
    print(f"max capacity difference: {diff:.2f} GW")
    assert rel < 1e-4 and diff < 0.1, "Notebook does not reproduce the Snakemake solve"
    print("OK: notebook reproduces the Snakemake result")


def _capacity_by_carrier(n):
    return pd.concat([
        n.generators.groupby("carrier").p_nom_opt.sum(),
        n.storage_units.groupby("carrier").p_nom_opt.sum(),
    ]).div(1e3)


# ---------------------------------------------------------------------------
# Labels, colors, scope
# ---------------------------------------------------------------------------

NICE_FALLBACK = {"unspecified_imports": "Unspecified Imports", "imports": "Imports", "exports": "Exports"}
COLOR_FALLBACK = {"unspecified_imports": "#c48be3", "imports": "#9f2ad5", "exports": "#B79B48"}
COLOR_OVERRIDE = {"PHS": "#6ec6d9"}  # PyPSA-USA gives PHS the same teal as hydro
TZ = "Etc/GMT+8"  # snapshots are UTC; figures use Pacific Standard Time (UTC-8, no DST so 3h blocks stay aligned)

STACK_ORDER = [
    "nuclear", "geothermal", "biomass", "hydro", "coal", "oil", "CCGT-95CCS", "CCGT", "OCGT",
    "onwind", "offwind_floating", "solar", "imports",
    "battery", "4hr_battery_storage", "8hr_battery_storage", "PHS",
]


def nice(n, carrier):
    name = n.carriers.nice_name.get(carrier, "")
    return name if isinstance(name, str) and name else NICE_FALLBACK.get(carrier, carrier)


def color(n, carrier):
    if carrier in COLOR_OVERRIDE:
        return COLOR_OVERRIDE[carrier]
    c = n.carriers.color.get(carrier, "")
    return c if isinstance(c, str) and c else COLOR_FALLBACK.get(carrier, "#999999")


def local_time(index):
    return pd.DatetimeIndex(index.get_level_values("timestep")).tz_localize("UTC").tz_convert(TZ)


def ca_buses(n):
    return n.buses.index[n.buses.reeds_state == "CA"]


def ca_mask(n, component):
    return getattr(n, component).bus.isin(ca_buses(n))


def weights(n):
    return n.snapshot_weightings.generators


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def capacity_table(n):
    """Existing = p_nom before the solve, optimal = p_nom_opt; built/retired are computed per asset."""
    df = pd.concat([
        n.generators[["carrier", "p_nom", "p_nom_opt"]],
        n.storage_units[["carrier", "p_nom", "p_nom_opt"]],
    ])
    df["built"] = (df.p_nom_opt - df.p_nom).clip(lower=0)
    df["retired"] = (df.p_nom - df.p_nom_opt).clip(lower=0)
    t = df.groupby("carrier")[["p_nom", "p_nom_opt", "built", "retired"]].sum() / 1e3
    t.columns = ["existing_GW", "optimal_GW", "built_GW", "retired_GW"]
    t.index = [nice(n, c) for c in t.index]
    return t[t.abs().sum(axis=1) > 0.01].sort_values("optimal_GW", ascending=False).round(2)


def production_table(n):
    w = weights(n)
    gen = n.generators_t.p.mul(w, axis=0).sum().groupby(n.generators.carrier).sum()
    dis = n.storage_units_t.p.clip(lower=0).mul(w, axis=0).sum().groupby(n.storage_units.carrier).sum()
    t = pd.concat([gen, dis]).div(1e6).rename("TWh").to_frame()
    t.index = [nice(n, c) for c in t.index]
    t = t[t.TWh.abs() > 0.01].sort_values("TWh", ascending=False)
    t["share_%"] = (100 * t.TWh / t.TWh.sum()).round(1)
    return t.round(2)


def cost_table(n):
    capex = n.statistics.capex(groupby="carrier").sum(axis=1)
    opex = n.statistics.opex(groupby="carrier").sum(axis=1)
    t = pd.DataFrame({"capex_G$": capex, "opex_G$": opex}).fillna(0).groupby(level="carrier").sum() / 1e9
    t["total_G$"] = t.sum(axis=1)
    t = t[t.abs().sum(axis=1) > 1e-3].sort_values("total_G$", ascending=False)
    t.loc["TOTAL"] = t.sum()
    return t.round(3)


def emissions_table(n):
    w = weights(n)
    g = n.generators
    ef = g.carrier.map(n.carriers.co2_emissions).fillna(0) / g.efficiency
    mt = n.generators_t.p.mul(w, axis=0).sum().mul(ef).div(1e6)
    where = np.where(ca_mask(n, "generators"), "in-state", "out-of-state / imports")
    t = mt.groupby([where, g.carrier.map(lambda c: nice(n, c))]).sum()
    return t[t.abs() > 1e-3].rename("MtCO2").to_frame().round(2)


def co2_summary(n):
    e = emissions_table(n).MtCO2
    with contextlib.chdir(WORKFLOW):
        lims = pd.read_csv(CONFIG["electricity"]["regional_Co2_limits"])
    period = int(n.investment_periods[0])
    lim = lims[(lims.name == "CA_AB32") & (lims.planning_horizon == period)].limit
    mu = n.global_constraints.mu[n.global_constraints.index.str.contains("co2_limit")]
    return pd.Series({
        "in-state MtCO2 (counted by the cap)": e.xs("in-state").sum(),
        "out-of-state + imports MtCO2 (not counted)": e.drop("in-state", level=0).sum(),
        "AB 32 cap MtCO2": lim.iloc[0] / 1e6 if len(lim) else np.nan,
        "CO2 shadow price $/t": abs(mu.iloc[0]) if len(mu) else np.nan,
    }).round(2)


def ca_balance(n):
    """Hourly California supply/demand by carrier (MW). Positive = supply, negative = charging / exports."""
    buses = ca_buses(n)
    g = n.generators[ca_mask(n, "generators")]
    su = n.storage_units[ca_mask(n, "storage_units")]
    L = n.links
    inbound = L[L.bus1.isin(buses) & ~L.bus0.isin(buses)]
    outbound = L[L.bus0.isin(buses) & ~L.bus1.isin(buses)]

    gen = n.generators_t.p[g.index].T.groupby(g.carrier).sum().T
    p_su = n.storage_units_t.p[su.index]
    dis = p_su.clip(lower=0).T.groupby(su.carrier).sum().T
    chg = p_su.clip(upper=0).T.groupby(su.carrier).sum().T.add_suffix(" charging")
    bal = pd.concat([gen, dis, chg], axis=1)
    bal["imports"] = -n.links_t.p1[inbound.index].sum(axis=1)
    bal["exports"] = -n.links_t.p0[outbound.index].sum(axis=1)
    load = n.loads_t.p[n.loads.index[ca_mask(n, "loads")]].sum(axis=1)
    bal["losses"] = load - bal.sum(axis=1)  # internal AC link losses (efficiency < 1)
    return bal, load


def price_table(n):
    buses = ca_buses(n)
    lmp = n.buses_t.marginal_price[buses]
    load = n.loads_t.p.T.groupby(n.loads.bus).sum().T.reindex(columns=buses)
    return pd.DataFrame({
        "mean_$/MWh": lmp.mean(),
        "load_weighted_$/MWh": (lmp * load).sum() / load.sum(),
        "max_$/MWh": lmp.max(),
    }).round(1)


def curtailment_table(n):
    w = weights(n)
    g = n.generators[ca_mask(n, "generators") & n.generators.carrier.isin(["solar", "onwind", "offwind_floating"])]
    pmax = n.get_switchable_as_dense("Generator", "p_max_pu")[g.index]
    avail = pmax.mul(g.p_nom_opt).mul(w, axis=0).sum().groupby(g.carrier).sum()
    used = n.generators_t.p[g.index].mul(w, axis=0).sum().groupby(g.carrier).sum()
    t = pd.DataFrame({"available_TWh": avail / 1e6, "dispatched_TWh": used / 1e6})
    t["curtailed_TWh"] = t.available_TWh - t.dispatched_TWh
    t["curtailed_%"] = 100 * t.curtailed_TWh / t.available_TWh
    t.index = [nice(n, c) for c in t.index]
    return t[t.available_TWh > 0.05].round(2)


def interchange_table(n):
    w = weights(n)
    bal, load = ca_balance(n)
    buses = ca_buses(n)
    out_i = n.links.index[n.links.bus0.isin(buses) & ~n.links.bus1.isin(buses)]
    mc = n.get_switchable_as_dense("Link", "marginal_cost")
    flow_out = n.links_t.p0[out_i].mul(w, axis=0)
    revenue = -(flow_out * mc[out_i]).sum().sum()
    lmp = n.buses_t.marginal_price[buses].mean(axis=1)
    return pd.Series({
        "imports_TWh": (bal["imports"] * w).sum() / 1e6,
        "exports_TWh": -(bal["exports"] * w).sum() / 1e6,
        "CA_load_TWh": (load * w).sum() / 1e6,
        "export_revenue_G$": revenue / 1e9,
        "avg_export_price_$/MWh": revenue / flow_out.sum().sum() if flow_out.sum().sum() else np.nan,
        "avg_CA_price_$/MWh": float((lmp * load).sum() / load.sum()),
    }).round(2)


def renewable_summary(n):
    """California's yearly energy mix against its load: eligible renewables (in state + contracted out of state)."""
    w = weights(n)
    gen = n.generators_t.p.mul(w, axis=0).sum().groupby(n.generators.carrier).sum() / 1e6
    ren = gen.reindex(RENEWABLE_CARRIERS).fillna(0).sum()
    nuc = gen.get("nuclear", 0.0)
    fossil = gen.reindex(FOSSIL_CARRIERS).fillna(0).sum()
    ic = interchange_table(n)
    load = ic["CA_load_TWh"]
    return pd.Series({
        "renewable TWh": ren,
        "nuclear TWh": nuc,
        "fossil + unspecified imports TWh": fossil,
        "exports TWh": ic["exports_TWh"],
        "CA load TWh": load,
        "renewable share of load % (net of exports)": 100 * (ren - ic["exports_TWh"]) / load,
        "renewable + nuclear share % (net of exports)": 100 * (ren + nuc - ic["exports_TWh"]) / load,
    }).round(2)


def erm_summary(n):
    if "erm_price" not in n.buses_t or n.buses_t.erm_price.empty:
        return pd.Series({"ERM active": False})
    p = n.buses_t.erm_price.astype(float).abs().max(axis=1)  # highest zonal ERM price per snapshot
    return pd.Series({
        "ERM active": True,
        "binding snapshots": int((p > 0.01).sum()),  # barrier without crossover leaves ~1e-9 noise
        "max ERM price $/MW": round(float(p.max()), 1),
        "of total snapshots": len(p),
    })


TABLES = {
    "capacity": capacity_table,
    "production": production_table,
    "costs": cost_table,
    "emissions": emissions_table,
    "co2_summary": co2_summary,
    "prices": price_table,
    "curtailment": curtailment_table,
    "interchange": interchange_table,
    "erm": erm_summary,
    "renewables": renewable_summary,
}


def show_tables(n):
    from IPython.display import display

    for name, f in TABLES.items():
        print(name.replace("_", " ").capitalize())
        display(f(n))


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def fig_capacity(n):
    t = capacity_table(n).sort_values("optimal_GW")
    fig, ax = plt.subplots(figsize=(7, 0.32 * len(t) + 1.2))
    y = np.arange(len(t))
    ax.barh(y + 0.2, t.existing_GW, height=0.38, color="#b9b9b9", label="Existing")
    ax.barh(y - 0.2, t.optimal_GW, height=0.38, color="#2a6fdb", label="Optimal 2035")
    ax.set_yticks(y, t.index)
    ax.set_xlabel("GW")
    ax.set_title("Installed capacity", loc="left", fontweight="bold")
    ax.grid(axis="y", visible=False)
    ax.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    return fig


def fig_production(n):
    w = weights(n)
    gen = n.generators_t.p.mul(w, axis=0).sum().groupby(n.generators.carrier).sum()
    dis = n.storage_units_t.p.clip(lower=0).mul(w, axis=0).sum().groupby(n.storage_units.carrier).sum()
    s = pd.concat([gen, dis]).div(1e6)
    s = s[s > 0.05].sort_values()
    fig, ax = plt.subplots(figsize=(7, 0.32 * len(s) + 1.2))
    ax.barh([nice(n, c) for c in s.index], s.values, color=[color(n, c) for c in s.index], height=0.6)
    for i, v in enumerate(s.values):
        ax.text(v, i, f" {v:.0f}", va="center", fontsize=9, color="#444444")
    ax.set_xlabel("TWh")
    ax.set_title("Annual production", loc="left", fontweight="bold")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return fig


def _week(index, center):
    ts = index.get_level_values("timestep")
    start = center - pd.Timedelta(days=3)
    return index[(ts >= start) & (ts < start + pd.Timedelta(days=7))]


def fig_dispatch(n, which="peak"):
    bal, load = ca_balance(n)
    ts = load.index.get_level_values("timestep")
    if which == "peak":
        center = ts[load.values.argmax()]
        title = f"Dispatch — peak-load week (around {center:%b %d})"
    else:
        center = pd.Timestamp(f"{ts[0].year}-01-15")
        title = "Dispatch — winter week (mid-January)"
    idx = _week(load.index, center)
    b = bal.loc[idx].drop(columns="losses") / 1e3
    x = local_time(idx).tz_localize(None)

    def key(col):
        base = col.replace(" charging", "")
        return STACK_ORDER.index(base) if base in STACK_ORDER else len(STACK_ORDER)

    pos = sorted([c for c in b.columns if (b[c] > 1e-3).any() and "charging" not in c and c != "exports"], key=key)
    neg = sorted([c for c in b.columns if (b[c] < -1e-3).any() and ("charging" in c or c == "exports")], key=key)

    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.stackplot(x, [b[c].clip(lower=0) for c in pos],
                 colors=[color(n, c) for c in pos], labels=[nice(n, c) for c in pos], linewidth=0)
    ax.stackplot(x, [b[c].clip(upper=0) for c in neg],
                 colors=[color(n, c.replace(" charging", "")) for c in neg],
                 labels=[nice(n, c.replace(" charging", "")) + (" (charging)" if "charging" in c else "") for c in neg],
                 linewidth=0, alpha=0.55)
    ax.plot(x, load.loc[idx] / 1e3, color="#111111", linewidth=2, label="California load")
    ax.axhline(0, color="#666666", linewidth=0.8)
    ax.set_ylabel("GW")
    ax.set_xlabel("Pacific Standard Time (UTC−8)")
    ax.set_title(title, loc="left", fontweight="bold")
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False, fontsize=8)
    fig.tight_layout()
    return fig


def fig_prices(n):
    lmp = n.buses_t.marginal_price[ca_buses(n)]
    prof = lmp.groupby(local_time(lmp.index).hour).mean()
    fig, ax = plt.subplots(figsize=(7, 3.6))
    for bus in prof.columns:
        ax.plot(prof.index, prof[bus], linewidth=2, marker="o", markersize=5, label=bus)
    ax.set_xticks(prof.index, [f"{h:02d}:00" for h in prof.index])
    ax.set_ylabel("$/MWh")
    ax.set_xlabel("Hour of day, PST (UTC−8), start of 3-hour block")
    ax.set_title("Average electricity price by hour of day (per zone)", loc="left", fontweight="bold")
    ax.legend(frameon=False, loc="center left", bbox_to_anchor=(1.01, 0.5), title="Zone")
    fig.tight_layout()
    return fig


PIE_SCALE = 0.25  # pie diameter in degrees of longitude per sqrt(GW)


def _pie(ax, x, y, values, colors, diameter):
    """Draw a pie at data coordinates (x, y); an inset axes keeps it circular on a lon/lat map."""
    inset = ax.inset_axes([x - diameter / 2, y - diameter / 2, diameter, diameter], transform=ax.transData)
    inset.pie(values, colors=colors, startangle=90, counterclock=False,
              wedgeprops={"linewidth": 0.6, "edgecolor": "white"})
    inset.set_aspect("equal")
    inset.axis("off")


def node_shapes(buses):
    """Polygons of the California nodes: ReEDS zones, or counties dissolved into their county groups."""
    import geopandas as gpd

    reeds = gpd.read_file(GEO / "reeds_shapes.geojson").set_index("name")
    if buses.isin(reeds.index).all():
        return reeds.loc[list(buses)]
    counties = gpd.read_file(BASE / "spatial" / "counties.geojson").set_index("bus")
    groups = pd.read_csv(ROOT / "configs" / "county_groups_25.csv", comment="#", index_col="bus")
    return counties.join(groups.node).dissolve("node").loc[list(buses)]


def fig_network(n):
    """Map: 2035 optimal capacity per California node (pies), inter-node transmission, interties.

    Interties are only drawn for ReEDS-zone networks: with county nodes the neighbours are counties.
    """
    import geopandas as gpd

    buses = ca_buses(n)
    reeds = gpd.read_file(GEO / "reeds_shapes.geojson").set_index("name")
    states = gpd.read_file(GEO / "state_boundaries.geojson")
    shapes = node_shapes(buses)
    many = len(buses) > 8  # smaller pies and no per-corridor labels when there are many nodes

    links = n.links
    internal = links[links.bus0.isin(buses) & links.bus1.isin(buses)]
    imp = links[(links.carrier == "imports") & links.bus1.isin(buses)]
    exp = links[(links.carrier == "exports") & links.bus0.isin(buses)]
    neighbors = sorted({b.split("_")[0] for b in imp.bus0} | {b.split("_")[0] for b in exp.bus1})

    fig, ax = plt.subplots(figsize=(9, 9))
    known = [z for z in neighbors if z in reeds.index]
    if known:
        reeds.loc[known].plot(ax=ax, color="#f3f3f3", edgecolor="white", linewidth=1)
    shapes.plot(ax=ax, color="#e2e2e2", edgecolor="white", linewidth=1.5)
    states.boundary.plot(ax=ax, color="#bdbdbd", linewidth=0.5)
    xy = n.buses.loc[buses, ["x", "y"]]

    # Interties to neighboring zones (import/export capacity, GW). Each neighbor is drawn just across
    # the border (nearest point of its zone to California, pushed outward) so the map stays on California.
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    ca_shape = shapes.union_all()
    ca_center = ca_shape.centroid
    for z in neighbors:
        if z not in reeds.index:
            continue
        edge = nearest_points(reeds.loc[z].geometry, ca_shape)[0]
        dx, dy = edge.x - ca_center.x, edge.y - ca_center.y
        norm = np.hypot(dx, dy) or 1.0
        anchor = Point(edge.x + 1.2 * dx / norm, edge.y + 1.2 * dy / norm)
        gi_tot = imp.loc[imp.bus0 == f"{z}_imports", "p_nom_opt"].sum() / 1e3
        ge_tot = exp.loc[exp.bus1 == f"{z}_exports", "p_nom_opt"].sum() / 1e3
        if gi_tot + ge_tot < 0.1:  # negligible intertie (e.g. p7, ~20 MW): skip so labels don't overlap
            continue
        for bus in buses:
            gi = imp.loc[(imp.bus0 == f"{z}_imports") & (imp.bus1 == bus), "p_nom_opt"].sum() / 1e3
            ge = exp.loc[(exp.bus1 == f"{z}_exports") & (exp.bus0 == bus), "p_nom_opt"].sum() / 1e3
            if gi + ge < 0.05:
                continue
            ax.plot([xy.at[bus, "x"], anchor.x], [xy.at[bus, "y"], anchor.y], color="#9f2ad5", linestyle="--",
                    linewidth=0.8 + 0.35 * max(gi, ge), alpha=0.6, zorder=2)
        ax.text(anchor.x, anchor.y, f"{z} · {reeds.at[z, 'reeds_ba']}\nin {gi_tot:.1f} / out {ge_tot:.1f} GW",
                fontsize=7.5, color="#5a1a7a", ha="center", va="center", zorder=4,
                bbox={"facecolor": "white", "edgecolor": "#d9c2e6", "alpha": 0.9, "pad": 2,
                      "boxstyle": "round,pad=0.3"})

    # Inter-zone transmission inside California (max of the two directions, GW)
    pairs = internal.assign(pair=[tuple(sorted(p)) for p in zip(internal.bus0, internal.bus1)])
    for (b0, b1), cap in (pairs.groupby("pair").p_nom_opt.max() / 1e3).items():
        ax.plot(xy.loc[[b0, b1], "x"], xy.loc[[b0, b1], "y"], color="#444444", linewidth=1 + 0.6 * cap, zorder=3,
                solid_capstyle="round")
        if not many:
            ax.text(xy.loc[[b0, b1], "x"].mean() + 0.25, xy.loc[[b0, b1], "y"].mean(), f"{cap:.1f} GW", fontsize=8,
                    color="#222222", zorder=4, bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 1})

    # Pies: optimal capacity by carrier at each California zone
    cap = pd.concat([
        n.generators[ca_mask(n, "generators")].groupby(["bus", "carrier"]).p_nom_opt.sum(),
        n.storage_units[ca_mask(n, "storage_units")].groupby(["bus", "carrier"]).p_nom_opt.sum(),
    ]).div(1e3)
    cap = cap[cap > 0.05]
    order = lambda c: STACK_ORDER.index(c) if c in STACK_ORDER else len(STACK_ORDER)  # noqa: E731
    pie_scale = PIE_SCALE / 2 if many else PIE_SCALE
    for bus in buses.intersection(cap.index.get_level_values("bus")):  # a small node can have no pie (Del Norte)
        s = cap.xs(bus, level="bus")
        s = s.loc[sorted(s.index, key=order)]
        _pie(ax, xy.at[bus, "x"], xy.at[bus, "y"], s.values, [color(n, c) for c in s.index],
             pie_scale * np.sqrt(s.sum()))
        total = s.sum()
        ax.text(xy.at[bus, "x"] - pie_scale * np.sqrt(total) / 2 - 0.05, xy.at[bus, "y"],
                f"{bus}\n{total:.0f} GW" if total >= 10 else f"{bus}\n{total:.1f} GW",
                fontsize=6.5 if many else 9, ha="right", va="center", fontweight="bold", zorder=5)

    lat0 = xy.y.mean()
    ax.set_aspect(1 / np.cos(np.radians(lat0)))
    x0, y0, x1, y1 = ca_shape.bounds
    ax.set_xlim(x0 - 1.0, x1 + 2.5)
    ax.set_ylim(y0 - 0.5, y1 + 1.8)
    ax.set_axis_off()
    title = "Network — optimal 2035 capacity by node and transmission" if many else (
        "Network — optimal 2035 capacity by zone, transmission and interties")
    ax.set_title(title, loc="left", fontweight="bold")

    carriers = sorted(cap.index.get_level_values("carrier").unique(), key=order)
    handles = [plt.Rectangle((0, 0), 1, 1, color=color(n, c)) for c in carriers]
    labels = [nice(n, c) for c in carriers]
    handles.append(plt.Line2D([], [], color="#444444", linewidth=3))
    labels.append("Transmission between nodes" if many else "Transmission between zones")
    if not many:
        handles.append(plt.Line2D([], [], color="#9f2ad5", linestyle="--", linewidth=1.5))
        labels.append("Interties (import / export capacity)")
    ax.legend(handles, labels, loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=8,
              title="Pie size ∝ √(total GW)", title_fontsize=8)
    fig.tight_layout()
    return fig


FIGURES = {
    "network": fig_network,
    "capacity": fig_capacity,
    "production": fig_production,
    "dispatch_peak_week": lambda n: fig_dispatch(n, "peak"),
    "dispatch_winter_week": lambda n: fig_dispatch(n, "winter"),
    "prices_by_hour": fig_prices,
}


def show_figures(n):
    for make in FIGURES.values():
        make(n)
        plt.show()


# ---------------------------------------------------------------------------
# Export and comparison
# ---------------------------------------------------------------------------


def export(n, name):
    out = OUTPUTS / name
    out.mkdir(parents=True, exist_ok=True)
    for tname, f in TABLES.items():
        f(n).to_csv(out / f"{tname}.csv")
    key_metrics(n).to_csv(out / "metrics.csv")  # what compare_scenarios() reads (network.nc is not in git)
    for fname, make in FIGURES.items():
        fig = make(n)
        fig.savefig(out / f"{fname}.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
    print(f"Wrote tables and figures to {out}")


def key_metrics(n):
    cap = capacity_table(n)
    co2 = co2_summary(n)
    ic = interchange_table(n)
    return pd.Series({
        "system cost G$": cost_table(n).loc["TOTAL", "total_G$"],
        "CO2 in-state Mt": co2.iloc[0],
        "CO2 incl. imports Mt": co2.iloc[0] + co2.iloc[1],
        "CO2 price $/t": co2.iloc[3],
        "avg CA price $/MWh": ic["avg_CA_price_$/MWh"],
        "solar GW": cap.optimal_GW.get("Solar", 0.0),
        "new battery GW": cap.loc[cap.index.str.contains("Battery", case=False), "built_GW"].sum(),
        "gas (CCGT+OCGT+CCS) GW": cap.loc[cap.index.str.contains("Gas|Ccs", case=False), "optimal_GW"].sum(),
        "exports TWh": ic["exports_TWh"],
        "renewable share %": renewable_summary(n)["renewable share of load % (net of exports)"],
    }).round(2)


def compare_scenarios():
    """One row per scenario folder in outputs/ that has a metrics.csv (written by export), with its parameters."""
    rows = {}
    for d in sorted(p for p in OUTPUTS.iterdir() if (p / "metrics.csv").exists()):
        params_path = d / "parameters.yaml"
        params = yaml.safe_load(params_path.read_text()) if params_path.exists() else {}
        metrics = pd.read_csv(d / "metrics.csv", index_col=0).iloc[:, 0]
        rows[d.name] = pd.concat([pd.Series(params, dtype=object), metrics])
    table = pd.DataFrame(rows).T
    table.to_csv(OUTPUTS / "comparison.csv")
    return table
