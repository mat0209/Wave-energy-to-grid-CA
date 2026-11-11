"""Export the small data behind notebooks/spatial_resolution.ipynb into base/spatial/.

Reads the solved 4-zone and 58-county Snakemake runs, and the 25-node run from the
california_2035 notebook if it exists (none are in git: large), and writes county shapes,
per-county stats, transfer limits and run summaries, so the notebook runs without
Snakemake and never solves anything. Run from the repo root after any of these runs:

    pypsa-usa/.venv/bin/python scripts/export_spatial.py

base/spatial/ holds:
    counties.geojson    onshore county regions (simplified) with ReEDS zone and coastal flag
    offshore.geojson    offshore region of each coastal county
    county_stats.csv    load, potentials, new build and average price per county (58-county run)
    county_links.csv    directed county-to-county transfer limits (NARIS 2024) and congestion (58-county run)
    zone_links.csv      directed zone-to-zone transfer limits of the 4-zone model (NARIS 2024, zone level)
    hybrid25_links.csv  directed node-to-node limits of the 25-node network, with use in the 58-county and 25-node runs
    hybrid25_nodes.csv  average price per node of the 25-node run (once solved)
    node_mix.csv        generation and capacity per 25-node node, in the 58-county and 25-node runs (once solved)
    runs.csv            model size, solve time, memory and headline results of each solved run
    capacity.csv        optimal capacity by carrier (GW) of each solved run
"""

import logging
import os
import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pypsa
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("PYPSA_USA_DIR", ROOT / "pypsa-usa"))
WORKFLOW = REPO / "workflow"
OUT = ROOT / "base" / "spatial"
OUT.mkdir(parents=True, exist_ok=True)

RUNS = {  # option -> (Snakemake run name, network name)
    "zones4": ("california-2035", "elec_s75_c4_ec_lv1.0_REM-ERM-3h_E"),
    "county58": ("california-2035-county", "elec_scounty_c58_ec_lv1.0_REM-ERM-3h_E"),
}
HYBRID_DIR = ROOT / "outputs" / "25bus_base_2035"  # the 25-node run, solved from the california_2035 notebook
COUNTY_RUN, COUNTY_NET = RUNS["county58"]
GEO = WORKFLOW / f"resources/{COUNTY_RUN}/geospatial/western"

sys.path.insert(0, str(ROOT / "notebooks"))
import ca_helpers  # noqa: E402

logging.disable(logging.WARNING)


def solver_log_stats(log_path):
    """Model size, barrier time and Gurobi's factor-memory estimate from the solver log."""
    text = Path(log_path).read_text()
    size = re.search(r"Optimize a model with (\d+) rows, (\d+) columns and (\d+) nonzeros", text)
    barrier = re.search(r"Barrier solved model in \d+ iterations and ([\d.]+) seconds", text)
    factor = re.search(r"Factor NZ\s*:.*\(roughly ([\d.]+) ([GM]B) of memory\)", text)
    factor_gb = float(factor[1]) / (1 if factor[2] == "GB" else 1024)
    return {
        "rows_M": int(size[1]) / 1e6,
        "cols_M": int(size[2]) / 1e6,
        "nonzeros_M": int(size[3]) / 1e6,
        "barrier_s": float(barrier[1]),
        "factor_GB": factor_gb,
    }


def ac_buses(n):
    return n.buses.index[n.buses.carrier == "AC"]


def mean_price(n):
    w = n.snapshot_weightings.objective
    return n.buses_t.marginal_price[ac_buses(n)].mul(w, axis=0).sum() / w.sum()


def node_mix(n, node_of_bus):
    """Generation (TWh) and solar and gas capacity (GW) per node."""
    g = n.generators[n.generators.bus.isin(ac_buses(n))]
    node = g.bus.map(node_of_bus)
    energy = n.generators_t.p[g.index].mul(n.snapshot_weightings.objective, axis=0).sum()
    gas = g.carrier.isin(["CCGT", "CCGT-95CCS", "OCGT"])
    return pd.DataFrame({
        "generation_TWh": energy.groupby(node).sum() / 1e6,
        "solar_GW": g.p_nom_opt[g.carrier == "solar"].groupby(node).sum() / 1e3,
        "gas_GW": g.p_nom_opt[gas].groupby(node).sum() / 1e3,
    }).fillna(0)


def congested_share(n, links):
    """Share of snapshots in which each directed link runs at its limit."""
    flow = n.links_t.p0[links.index]
    return flow.ge(0.99 * links.p_nom_opt * links.p_max_pu).mean()


# --- geometry -------------------------------------------------------------------
county_n = pypsa.Network(WORKFLOW / f"results/{COUNTY_RUN}/western/networks/{COUNTY_NET}.nc")
names = gpd.read_file(GEO / "county_shapes.geojson").set_index("GEOID").NAME

onshore = gpd.read_file(GEO / "regions_onshore_scounty.geojson")
onshore["bus"] = onshore.name.str.split("_").str[1]
offshore = gpd.read_file(GEO / "regions_offshore_scounty.geojson")
offshore["bus"] = offshore.name.str.split("_").str[1]
coastal = set(offshore.bus)

buses = county_n.buses.loc[ac_buses(county_n)]
onshore = onshore.set_index("bus").loc[buses.index].drop(columns="name").rename_axis("bus")
onshore["county"] = names.reindex(onshore.index).values
onshore["zone"] = buses.reeds_zone
onshore["coastal"] = onshore.index.isin(coastal)
onshore["geometry"] = onshore.geometry.simplify(0.005)  # ~500 m, keeps the file small
onshore.reset_index()[["bus", "county", "zone", "coastal", "geometry"]].to_file(OUT / "counties.geojson")
offshore["geometry"] = offshore.geometry.simplify(0.005)
offshore[["bus", "geometry"]].to_file(OUT / "offshore.geojson")

# --- per-county stats (58-county run) -------------------------------------------
n = county_n
g = n.generators
built = (g.p_nom_opt - g.p_nom).clip(lower=0)
load = n.loads_t.p_set.mul(n.snapshot_weightings.objective, axis=0).sum().groupby(n.loads.bus).sum() / 1e6


def by_bus(series, carrier):
    return (series[g.carrier == carrier].groupby(g.bus).sum() / 1e3).reindex(buses.index).fillna(0)


stats = pd.DataFrame({
    "county": onshore.county,
    "zone": onshore.zone,
    "coastal": onshore.coastal,
    "load_TWh": load.reindex(buses.index).fillna(0),
    "price_usd_MWh": mean_price(n),
    "solar_potential_GW": by_bus(g.p_nom_max, "solar"),
    "solar_new_GW": by_bus(built, "solar"),
    "onwind_potential_GW": by_bus(g.p_nom_max, "onwind"),
    "onwind_new_GW": by_bus(built, "onwind"),
    "offwind_floating_potential_GW": by_bus(g.p_nom_max, "offwind_floating"),
})
stats.index.name = "bus"
stats.round(3).to_csv(OUT / "county_stats.csv")

L = n.links[(n.links.carrier == "AC") & n.links.bus0.isin(buses.index) & n.links.bus1.isin(buses.index)]
links = pd.DataFrame({"bus0": L.bus0, "bus1": L.bus1, "cap_MW": L.p_nom, "congested_share": congested_share(n, L)})
links.round(3).to_csv(OUT / "county_links.csv")

# --- 4-zone corridors (actual zone-level NARIS limits) ---------------------------
zones_n = pypsa.Network(WORKFLOW / "results/{}/western/networks/{}.nc".format(*RUNS["zones4"]))
Z = zones_n.links[zones_n.links.carrier == "AC"]
zone_links = pd.DataFrame({"bus0": Z.bus0, "bus1": Z.bus1, "cap_MW": Z.p_nom, "congested_share": congested_share(zones_n, Z)})
zone_links.round(3).to_csv(OUT / "zone_links.csv")

# --- 25-node corridors -------------------------------------------------------------
hybrid_n = pypsa.Network(HYBRID_DIR / "network.nc") if (HYBRID_DIR / "network.nc").exists() else None
prepared = pypsa.Network(ca_helpers.NETWORKS["hybrid25"][0])
H = prepared.links[prepared.links.carrier == "AC"]
groups = pd.read_csv(ROOT / "configs" / "county_groups_25.csv", comment="#", index_col="bus").node
county_flow = county_n.links_t.p0[L.index].T.groupby([L.bus0.map(groups), L.bus1.map(groups)]).sum().T  # 58-county flows
hybrid_links = pd.DataFrame({"bus0": H.bus0, "bus1": H.bus1, "cap_MW": H.p_nom})
hybrid_links["use_in_58_county_run"] = [
    county_flow[(a, b)].mean() / cap if (a, b) in county_flow and cap > 0 else 0.0
    for a, b, cap in zip(H.bus0, H.bus1, H.p_nom)
]
if hybrid_n is not None:
    hybrid_links["use_in_25_node_run"] = hybrid_n.links_t.p0[H.index].mean() / H.p_nom
    hybrid_links["congested_share"] = congested_share(hybrid_n, hybrid_n.links.loc[H.index])
    mean_price(hybrid_n).rename("price_usd_MWh").rename_axis("node").round(3).to_csv(OUT / "hybrid25_nodes.csv")
    nodes_of_hybrid = pd.Series(ac_buses(hybrid_n), index=ac_buses(hybrid_n))
    mix = pd.concat({"county58": node_mix(county_n, groups), "hybrid25": node_mix(hybrid_n, nodes_of_hybrid)}, axis=1)
    mix.columns = [f"{option}_{col}" for option, col in mix.columns]
    mix.rename_axis("node").round(3).to_csv(OUT / "node_mix.csv")
else:
    (OUT / "hybrid25_nodes.csv").unlink(missing_ok=True)
    (OUT / "node_mix.csv").unlink(missing_ok=True)
hybrid_links.round(3).to_csv(OUT / "hybrid25_links.csv")


# --- run summaries ----------------------------------------------------------------
def summarize(option, net, log_path, total_min, peak_ram_gb):
    price = mean_price(net)
    metrics = ca_helpers.key_metrics(net)
    ac = net.links[net.links.carrier == "AC"]
    cap = pd.concat([net.generators.groupby("carrier").p_nom_opt.sum(), net.storage_units.groupby("carrier").p_nom_opt.sum()])
    row = {
        "option": option,
        "nodes": len(ac_buses(net)),
        "corridors": len(ac) // 2,
        **solver_log_stats(log_path),
        "solve_total_min": total_min,
        "peak_ram_GB": peak_ram_gb,
        "objective_GUSD": net.objective / 1e9,
        "system_cost_GUSD": metrics["system cost G$"],
        "grid_capex_GUSD": (ac.p_nom_opt * ac.capital_cost).sum() / 1e9,
        "co2_price_usd_t": metrics["CO2 price $/t"],
        "avg_ca_price_usd_MWh": metrics["avg CA price $/MWh"],
        "exports_TWh": metrics["exports TWh"],
        "price_spread_usd_MWh": price.max() - price.min(),
        # Before our fix, county-level runs skipped every CAISO interface limit (see ASSUMPTIONS.md)
        "interface_limits_applied": option == "zones4" or "external_zone" in net.buses,
    }
    return row, cap / 1e3


runs, capacity = [], {}
for option, (run, network) in RUNS.items():
    net = zones_n if option == "zones4" else county_n
    bench = pd.read_csv(WORKFLOW / f"benchmarks/{run}/solve_network/western/{network}", sep="\t")
    log = WORKFLOW / f"logs/{run}/solve_network/western/{network}_solver.log"
    row, capacity[option] = summarize(option, net, log, bench.s.iloc[-1] / 60, None)
    runs.append(row)
if hybrid_n is not None:
    stats = yaml.safe_load((HYBRID_DIR / "run_stats.yaml").read_text())
    row, capacity["hybrid25"] = summarize("hybrid25", hybrid_n, HYBRID_DIR / "solver.log", stats["solve_minutes"], stats["peak_ram_GB"])
    runs.append(row)
pd.DataFrame(runs).round(3).to_csv(OUT / "runs.csv", index=False)
pd.DataFrame(capacity).round(3).fillna(0).to_csv(OUT / "capacity.csv", index_label="carrier")
print(f"Updated {OUT} ({len(runs)} solved runs)")
