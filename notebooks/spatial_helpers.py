"""Helpers for the spatial-resolution notebook.

Compares ways of splitting California into model nodes (4 ReEDS zones, 58 counties,
25-node county grouping) using the data exported to base/spatial/ by
scripts/export_spatial.py and the grouping in configs/county_groups_25.csv.
Nothing here solves a network.
"""

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]  # repo root
SPATIAL = ROOT / "base" / "spatial"
CRS = 3310  # California Albers (metres)

COUNTIES = gpd.read_file(SPATIAL / "counties.geojson").set_index("bus").to_crs(CRS)
OFFSHORE = gpd.read_file(SPATIAL / "offshore.geojson").to_crs(CRS)
STATS = pd.read_csv(SPATIAL / "county_stats.csv", index_col="bus")
COUNTY_LINKS = pd.read_csv(SPATIAL / "county_links.csv", index_col="name")
ZONE_LINKS = pd.read_csv(SPATIAL / "zone_links.csv", index_col="name")
RUNS = pd.read_csv(SPATIAL / "runs.csv", index_col="option")
HYBRID_LINKS = pd.read_csv(SPATIAL / "hybrid25_links.csv", index_col="name")
CAPACITY = pd.read_csv(SPATIAL / "capacity.csv", index_col="carrier")
GROUPINGS = {  # option -> county grouping (bus, county, node, node_name, type)
    option: pd.read_csv(ROOT / "configs" / f"county_groups_{option[-2:]}.csv", comment="#", index_col="bus")
    for option in ["hybrid25"]
}

# option -> county-to-node mapping
OPTIONS = {
    "zones4": STATS.zone.rename("node"),
    "county58": pd.Series(STATS.index, index=STATS.index, name="node"),
    **{option: g.node for option, g in GROUPINGS.items()},
}
LABELS = {
    "zones4": "A. 4 ReEDS zones (current base)",
    "county58": "B. 58 counties",
    "hybrid25": "C. 25 nodes: coastal counties + inland groups",
}
SHORT = {"zones4": "A", "county58": "B", "hybrid25": "C"}
WEAK_MW = 400  # coastal corridors below this are highlighted
LABEL_ABOVE = {"I04"}  # node labels drawn above their point to avoid collisions
LABEL_LEFT = set()  # node labels drawn left of their point

# colours (validated reference palette; text stays in ink colours)
INK, INK2, MUTED, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#fcfcfb"
COAST, COAST_EDGE, OFFSHORE_FILL = "#a9c8ee", "#2a78d6", "#eef4fc"
WEAK = "#eb6834"
# map colouring: neighbouring nodes get different tints (blues = coastal nodes, warm tints = inland nodes)
COAST_TINTS = ["#a9c8ee", "#6fa3e0", "#cfe0f5", "#8db7e8"]
INLAND_TINTS = ["#e8dcc4", "#d3dfc4", "#e6cfd8", "#f0e4b0", "#d5d2ca", "#cfdcdc"]
NODE_EDGE = "#3d3c39"
OPTION_COLORS = {"zones4": "#2a78d6", "county58": "#6250d6", "hybrid25": "#1baf7a"}

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


# ---------------------------------------------------------------------------
# Nodes and corridors of an option
# ---------------------------------------------------------------------------


def node_names(option):
    if option in GROUPINGS:
        return GROUPINGS[option].drop_duplicates("node").set_index("node").node_name
    if option == "county58":
        return STATS.county
    nodes = OPTIONS[option].unique()
    return pd.Series(nodes, index=nodes)


def _fill_holes(geom):
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    return unary_union([Polygon(p.exterior) for p in polys])


def nodes(option):
    """Node polygons, with a coastal flag (any coastal county) and the counties they hold."""
    m = OPTIONS[option]
    gdf = COUNTIES.join(m).join(STATS[["load_TWh", "offwind_floating_potential_GW"]])
    out = gdf.dissolve("node", aggfunc={"coastal": "any", "county": "count", "load_TWh": "sum",
                                        "offwind_floating_potential_GW": "sum"})
    out = out.rename(columns={"county": "counties"})
    # close the slivers between simplified county shapes so they don't draw as node borders
    out["geometry"] = out.geometry.buffer(300).buffer(-300).apply(_fill_holes)
    out["name"] = node_names(option).reindex(out.index)
    return out


def directed_links(option):
    """Directed node-to-node transfer limits (MW).

    zones4 and hybrid25 use the limits of their own built network; county58 is one link per county pair.
    """
    if option == "zones4":
        return ZONE_LINKS[["bus0", "bus1", "cap_MW"]].rename(columns={"bus0": "n0", "bus1": "n1"})
    if option == "hybrid25":
        return HYBRID_LINKS[["bus0", "bus1", "cap_MW"]].rename(columns={"bus0": "n0", "bus1": "n1"})
    m = OPTIONS[option]
    links = COUNTY_LINKS.assign(n0=COUNTY_LINKS.bus0.map(m), n1=COUNTY_LINKS.bus1.map(m))
    links = links[links.n0 != links.n1]
    return links.groupby(["n0", "n1"], as_index=False).cap_MW.sum()


def corridors(option):
    """Undirected corridors with capacity in each direction (a < b alphabetically)."""
    d = directed_links(option)
    d["a"], d["b"] = np.minimum(d.n0, d.n1), np.maximum(d.n0, d.n1)
    d["dir"] = np.where(d.n0 == d.a, "cap_ab", "cap_ba")
    return d.pivot_table(index=["a", "b"], columns="dir", values="cap_MW", aggfunc="sum").fillna(0).reset_index()


def outbound(option):
    """Total transfer limit out of each node (MW)."""
    return directed_links(option).groupby("n0").cap_MW.sum()


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def options_table():
    rows = {}
    for option in OPTIONS:
        nd = nodes(option)
        row = {
            "nodes": len(nd),
            "coastal nodes": int(nd.coastal.sum()),
            "corridors": len(corridors(option)),
            "largest node (counties)": int(nd.counties.max()),
        }
        if option in RUNS.index:
            r = RUNS.loc[option]
            row.update({
                "LP rows (M)": r.rows_M,
                "LP columns (M)": r.cols_M,
                "total solve time (min)": round(r.solve_total_min, 1),
                "barrier time (min)": round(r.barrier_s / 60, 1),
                "Gurobi factor memory (GB)": r.factor_GB,
                "measured peak RAM (GB)": r.peak_ram_GB,
                "status": "solved" if r.interface_limits_applied else "solved WITHOUT interface limits: re-solve",
            })
        else:
            row["status"] = "built, not solved yet"
        rows[LABELS[option]] = row
    return pd.DataFrame(rows).T


def coastal_access():
    """For each coastal county: which node it sits in, how big that node is, and its export limit."""
    coast = STATS[STATS.coastal]
    out = pd.DataFrame({
        "county": coast.county,
        "offshore wind potential (GW)": coast.offwind_floating_potential_GW.round(1),
    })
    for option, m in OPTIONS.items():
        nd, ob = nodes(option), outbound(option)
        node = m.reindex(coast.index)
        out[f"{SHORT[option]}: counties in node"] = node.map(nd.counties).values
        out[f"{SHORT[option]}: node export limit (MW)"] = node.map(ob).fillna(0).round().astype(int).values
    return out.sort_values("county").set_index("county")


def groups_table(option="hybrid25"):
    """A county grouping: counties, load, offshore wind potential and export limit of each node."""
    groups = GROUPINGS[option]
    nd, ob = nodes(option), outbound(option)
    members = groups.groupby("node").county.agg(", ".join)
    out = pd.DataFrame({
        "name": nd.name,
        "type": groups.drop_duplicates("node").set_index("node")["type"],
        "counties": members,
        "load (TWh)": nd.load_TWh.round(1),
        "offshore wind potential (GW)": nd.offwind_floating_potential_GW.round(1),
        "export limit (MW)": ob.reindex(nd.index).fillna(0).round().astype(int),
    })
    return out.rename_axis("node")


def coastal_split_check(max_wave_MW, factor=2.0):
    """Which coastal counties need their own node, given the wave capacity we plan to test there.

    A coastal county keeps its own node when its export limit (58-county data) is below `factor` times
    the largest wave capacity tested there: otherwise the export limit could bind and merging it into a
    neighbour would hide that. `max_wave_MW` maps county name -> MW (counties left out get 0).
    """
    coast = STATS[STATS.coastal]
    export = outbound("county58").reindex(coast.index).fillna(0)
    wave = coast.county.map(max_wave_MW).fillna(0)
    out = pd.DataFrame({
        "county": coast.county,
        "export limit (MW)": export.round().astype(int),
        "max wave tested (MW)": wave.astype(int),
        "offshore wind potential (GW)": coast.offwind_floating_potential_GW.round(1),
    })
    # links already binding without wave (58-county run): a reason to keep a county separate anyway
    lk = COUNTY_LINKS[COUNTY_LINKS.bus0.isin(coast.index) | COUNTY_LINKS.bus1.isin(coast.index)]
    busiest = pd.concat([lk.set_index("bus0").congested_share, lk.set_index("bus1").congested_share])
    out["busiest link at limit (% of time)"] = (busiest.groupby(level=0).max().reindex(coast.index).fillna(0) * 100).round()
    out["keep own node"] = np.where(wave == 0, "no wave planned", np.where(export < factor * wave, "yes", "can merge"))
    for option in GROUPINGS:
        m = OPTIONS[option][STATS.coastal]  # coastal counties only
        alone = m.map(m.value_counts()) == 1
        out[f"own node in {SHORT[option]}"] = alone.reindex(coast.index).map({True: "yes", False: "merged"})
    return out.sort_values("export limit (MW)").set_index("county")


def hidden_congestion(option, min_share=0.3):
    """County links congested in the 58-county run that end up inside one node (constraint lost)."""
    m = OPTIONS[option]
    lk = COUNTY_LINKS.assign(n0=COUNTY_LINKS.bus0.map(m), n1=COUNTY_LINKS.bus1.map(m))
    lk = lk[(lk.n0 == lk.n1) & (lk.congested_share >= min_share)]
    return pd.DataFrame({
        "from": lk.bus0.map(STATS.county), "to": lk.bus1.map(STATS.county), "node": lk.n0.map(node_names(option)),
        "limit (MW)": lk.cap_MW.round(), "share of time at limit": lk.congested_share,
    }).sort_values("share of time at limit", ascending=False).reset_index(drop=True)


def itl_aggregation_check():
    """Zone-level NARIS limits vs the sum of county-level limits across the same boundary."""
    summed = directed_links("county58").assign(z0=lambda d: d.n0.map(STATS.zone), z1=lambda d: d.n1.map(STATS.zone))
    summed = summed[summed.z0 != summed.z1].groupby(["z0", "z1"]).cap_MW.sum()
    zone = ZONE_LINKS.set_index(["bus0", "bus1"]).cap_MW
    df = pd.DataFrame({"zone-level limit (MW)": zone, "sum of county limits (MW)": summed}).dropna().round()
    df["ratio"] = (df["sum of county limits (MW)"] / df["zone-level limit (MW)"]).round(2)
    df.index.names = ["from zone", "to zone"]
    return df


def results_table():
    """Headline results of every solved option."""
    r = pd.DataFrame({
        "interface limits applied": RUNS.interface_limits_applied,
        "system cost (G$)": RUNS.system_cost_GUSD,
        "of which existing + new AC grid (G$)": RUNS.grid_capex_GUSD,
        "system cost without AC grid (G$)": RUNS.system_cost_GUSD - RUNS.grid_capex_GUSD,
        "solver objective (G$)": RUNS.objective_GUSD,
        "CO2 price ($/t)": RUNS.co2_price_usd_t,
        "average CA price ($/MWh)": RUNS.avg_ca_price_usd_MWh,
        "price spread across nodes ($/MWh)": RUNS.price_spread_usd_MWh,
        "exports (TWh)": RUNS.exports_TWh,
    })
    cap = CAPACITY.T[["solar", "onwind", "CCGT", "CCGT-95CCS", "OCGT", "offwind_floating"]].add_suffix(" (GW)")
    out = r.join(cap).T
    out.columns = [LABELS[o] for o in out.columns]
    return out.map(lambda v: round(v, 2) if isinstance(v, float) else v)


def hybrid_solved():
    return "hybrid25" in RUNS.index


def corridor_use():
    """How hard the 25-node corridors touching the coast are used, in the 58-county run and (if solved) the 25-node run."""
    names = node_names("hybrid25")
    coastal = set(GROUPINGS["hybrid25"].node[GROUPINGS["hybrid25"]["type"] == "coast"])
    lk = HYBRID_LINKS[HYBRID_LINKS.bus0.isin(coastal) | HYBRID_LINKS.bus1.isin(coastal)]
    out = pd.DataFrame({"from": lk.bus0.map(names), "to": lk.bus1.map(names), "limit (MW)": lk.cap_MW.round()})
    out["mean use, 58-county run"] = lk.use_in_58_county_run
    if hybrid_solved():
        out["mean use, 25-node run"] = lk.use_in_25_node_run
        out["share of time at limit, 25-node run"] = lk.congested_share
    return out[out["limit (MW)"] < 2000].sort_values("limit (MW)").reset_index(drop=True)


def node_prices():
    """Average price per 25-node node: load-weighted county prices of the 58-county run vs the 25-node run."""
    groups = GROUPINGS["hybrid25"].node
    weighted = STATS.price_usd_MWh * STATS.load_TWh.clip(lower=1e-6)
    b = weighted.groupby(groups).sum() / STATS.load_TWh.clip(lower=1e-6).groupby(groups).sum()
    c = pd.read_csv(SPATIAL / "hybrid25_nodes.csv", index_col="node").price_usd_MWh
    out = pd.DataFrame({"name": node_names("hybrid25"), "58-county run ($/MWh)": b.round(2), "25-node run ($/MWh)": c.round(2)})
    out["difference ($/MWh)"] = (out["25-node run ($/MWh)"] - out["58-county run ($/MWh)"]).round(2)
    return out.sort_values("difference ($/MWh)")


def node_mix():
    """Where generation and capacity sit per 25-node node, 58-county run (B) vs 25-node run (C)."""
    mix = pd.read_csv(SPATIAL / "node_mix.csv", index_col="node")
    out = pd.DataFrame({
        "name": node_names("hybrid25"),
        "generation B (TWh)": mix.county58_generation_TWh.round(1),
        "generation C (TWh)": mix.hybrid25_generation_TWh.round(1),
    })
    out["difference (TWh)"] = (mix.hybrid25_generation_TWh - mix.county58_generation_TWh).round(1)
    out["solar B (GW)"] = mix.county58_solar_GW.round(2)
    out["solar C (GW)"] = mix.hybrid25_solar_GW.round(2)
    out["gas B (GW)"] = mix.county58_gas_GW.round(2)
    out["gas C (GW)"] = mix.hybrid25_gas_GW.round(2)
    return out.sort_values("difference (TWh)", key=abs, ascending=False)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def _tones(gdf, palette):
    """Greedy map colouring: neighbouring nodes get different tints (largest nodes first)."""
    tone = {}
    for k in gdf.area.sort_values(ascending=False).index:
        used = {tone[j] for j in tone if gdf.geometry[k].buffer(500).intersects(gdf.geometry[j])}
        free = [c for c in palette if c not in used]
        tone[k] = free[0] if free else palette[len(tone) % len(palette)]
    return tone


def _draw_option(ax, option, labels=True):
    nd = nodes(option)
    pts = nd.representative_point()
    tone = {**_tones(nd[nd.coastal], COAST_TINTS), **_tones(nd[~nd.coastal], INLAND_TINTS)}
    OFFSHORE.plot(ax=ax, facecolor=OFFSHORE_FILL, edgecolor=COAST, linewidth=0.5, linestyle=(0, (3, 2)))
    nd.plot(ax=ax, color=[tone[k] for k in nd.index], edgecolor="none")
    COUNTIES.boundary.plot(ax=ax, color=SURFACE, linewidth=0.4, alpha=0.9)  # counties inside a node
    nd.boundary.plot(ax=ax, color=NODE_EDGE, linewidth=0.5 if len(nd) > 30 else 1.0, zorder=2)  # node borders

    coastal = set(nd.index[nd.coastal])
    for _, r in corridors(option).iterrows():
        a, b = pts[r.a], pts[r.b]
        cap = max(r.get("cap_ab", 0), r.get("cap_ba", 0))
        weak = (r.a in coastal or r.b in coastal) and 10 <= cap < WEAK_MW and option != "zones4"
        lw = 0.5 + 2.4 * np.sqrt(cap / 15000)
        ax.plot([a.x, b.x], [a.y, b.y], color=WEAK if weak else MUTED, lw=max(lw, 1.5) if weak else lw,
                alpha=0.95 if weak else 0.6, solid_capstyle="round", zorder=3)
    ax.scatter(pts.x, pts.y, s=10, color=[COAST_EDGE if c else INK2 for c in nd.coastal], edgecolor=SURFACE,
               linewidth=1, zorder=4)
    if labels:
        for k, p in pts.items():
            above, left = k in LABEL_ABOVE, k in LABEL_LEFT
            offset = (-7, 0) if left else (0, 7 if above else -7)
            ax.annotate(nd.name[k], (p.x, p.y), xytext=offset, textcoords="offset points", fontsize=6.5,
                        color=INK2, ha="right" if left else "center",
                        va="center" if left else ("bottom" if above else "top"), zorder=5,
                        bbox=dict(boxstyle="round,pad=0.1", fc=SURFACE, ec="none", alpha=0.7))
    ax.set_axis_off()
    ax.set_title(f"{LABELS[option]}\n{len(nd)} nodes, {len(corridors(option))} corridors", loc="left",
                 fontsize=9.5, color=INK)


def _legend(fig):
    handles = [
        Patch(fc=COAST_TINTS[0], ec=NODE_EDGE, label="Node with coastline (blue tints)"),
        Patch(fc=INLAND_TINTS[0], ec=NODE_EDGE, label="Inland node (warm tints)"),
        Line2D([], [], color=NODE_EDGE, lw=1, label="Node border (white: county border inside a node)"),
        Patch(fc=OFFSHORE_FILL, ec=COAST, ls="--", label="Offshore region of a coastal county"),
        Line2D([], [], color=MUTED, lw=2, label="Corridor (width ∝ transfer limit)"),
        Line2D([], [], color=WEAK, lw=2, label=f"Weak coastal corridor (< {WEAK_MW} MW)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=8, frameon=False)


def fig_options():
    fig, axes = plt.subplots(1, 3, figsize=(15, 6.6))
    fig.patch.set_facecolor(SURFACE)
    for ax, option in zip(axes, OPTIONS):
        _draw_option(ax, option, labels=option == "zones4")
    _legend(fig)
    fig.subplots_adjust(bottom=0.08, top=0.9, wspace=0.02)
    return fig


def fig_hybrid(option="hybrid25"):
    fig, ax = plt.subplots(figsize=(8.5, 9.5))
    fig.patch.set_facecolor(SURFACE)
    _draw_option(ax, option, labels=True)
    _legend(fig)
    fig.subplots_adjust(bottom=0.06, top=0.95)
    return fig


def fig_congestion(min_share=0.2):
    """58-county run: average price per county and the links that bind most often."""
    fig, ax = plt.subplots(figsize=(8.5, 10))
    fig.patch.set_facecolor(SURFACE)
    gdf = COUNTIES.join(STATS.price_usd_MWh)
    OFFSHORE.plot(ax=ax, facecolor=OFFSHORE_FILL, edgecolor=COAST, linewidth=0.5, linestyle=(0, (3, 2)))
    lo, hi = 45, np.ceil(STATS.price_usd_MWh.max())  # Del Norte (Oregon-only) sits far below; annotated
    gdf.plot(ax=ax, column="price_usd_MWh", cmap="Blues", vmin=lo, vmax=hi, edgecolor=SURFACE, linewidth=0.6,
             legend=True, legend_kwds={"label": "Average price ($/MWh)", "shrink": 0.5, "extend": "min"})
    pts = COUNTIES.representative_point()
    for bus in STATS.index[STATS.price_usd_MWh < lo]:
        p = pts[bus]
        ax.annotate(f"{STATS.county[bus]}: {STATS.price_usd_MWh[bus]:.1f} $/MWh\n(no link to other CA counties)",
                    (p.x, p.y), xytext=(p.x + 120e3, p.y + 30e3), fontsize=7.5, color=INK, zorder=6,
                    arrowprops=dict(arrowstyle="-", color=INK2, lw=0.6))
    lk = COUNTY_LINKS[COUNTY_LINKS.congested_share >= min_share]
    for _, r in lk.iterrows():
        a, b = pts[r.bus0], pts[r.bus1]
        ax.plot([a.x, b.x], [a.y, b.y], color=WEAK, lw=0.8 + 3 * r.congested_share, solid_capstyle="round", zorder=3)
    ax.set_axis_off()
    ax.set_title(f"58-county run: average price and links at their limit ≥ {min_share:.0%} of the time "
                 "(width ∝ share)", loc="left", fontsize=9.5, color=INK)
    return fig


def fig_capacity():
    carriers = ["solar", "onwind", "CCGT", "CCGT-95CCS", "OCGT", "battery"]
    data = CAPACITY.loc[carriers]
    fig, ax = plt.subplots(figsize=(8, 3.6))
    x, width = np.arange(len(carriers)), 0.8 / data.shape[1]
    for i, option in enumerate(data.columns):
        offset = (i - (data.shape[1] - 1) / 2) * width
        bars = ax.bar(x + offset, data[option], width - 0.03, color=OPTION_COLORS[option], label=LABELS[option])
        ax.bar_label(bars, fmt="%.0f", fontsize=7, color=INK2, padding=1)
    ax.set_xticks(x, carriers)
    ax.set_ylabel("Optimal capacity (GW)")
    ax.legend(frameon=False, fontsize=8)
    ax.set_title("Capacity mix, 2035, by spatial resolution", loc="left", fontsize=10)
    return fig
