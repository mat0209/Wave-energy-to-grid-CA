"""Network maps drawn with PyPSA's own plotting (n.plot.map pies + PyPSA legend helpers) in the style of
Gonzalez et al. (2024), Nat. Commun. 15:6826, Fig. 6: blue ocean, white land, pies of generation by energy
type at each bus (same colours as Fig. 6), existing transmission in salmon and new transmission as wide
translucent lavender, both binned by capacity. Also PyPSA statistics bar charts.

Per network it writes, to results/pypsa_plots/:
  <name>_supply_map.png     pie at each bus = annual generation by energy type
  <name>_capacity_map.png   pie at each bus = installed capacity by energy type
  <name>_supply_bar.png     n.statistics.supply(...).plot.bar
  <name>_capacity_bar.png   n.statistics.optimal_capacity(...).plot.bar

"Existing" transmission = capacity the network started with (p_nom / s_nom). In the O'ahu pathway this
includes cables built in earlier pathway steps. "New" = capacity the optimizer added (p_nom_opt - p_nom).

Usage: python make_pypsa_plots.py
"""
import logging
import os
from pathlib import Path

import cartopy.crs as ccrs
from cartopy.mpl.ticker import LatitudeFormatter, LongitudeFormatter
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PatchCollection
import numpy as np
import pandas as pd
import pypsa
from pypsa.plot import add_legend_circles, add_legend_lines, add_legend_patches

ROOT = Path(__file__).parent
OUT = ROOT / "results" / "pypsa_plots"
STATES = ROOT / "data" / "raw" / "census" / "cb_2023_us_state_500k.shp"
COUNTRIES = ROOT / "data" / "raw" / "naturalearth" / "naturalearth_lowres.shp"   # Mexico backdrop
# The networks live in the other branches. Check them out side by side (see README) or set these variables.
OAHU = Path(os.environ.get("OAHU_DIR", ROOT.parent / "oahu"))
WEST = Path(os.environ.get("WEST_DIR", ROOT.parent / "west-coast"))
REPO = Path(os.environ.get("CA_OUTPUTS", ROOT.parent / "california-pypsa-usa" / "outputs"))

# --- Fig. 6 style --------------------------------------------------------------------------------------
OCEAN, LAND, BORDER = "#add8e6", "#ffffff", "#9a9a9a"
EXIST_TX = "#ffa07a"                       # light salmon
NEW_TX, NEW_TX_ALPHA = "#9370db", 0.32     # translucent lavender (reads #dbd3f4 on white, as in Fig. 6)
PIE_EDGE = "#333333"
CIRCLE = "#cdcdcd"

# Energy types and colours exactly as in Fig. 6's legend, in its order.
CAT_COLOR = {
    "Wind": "#00bfff", "Offshore Wind": "#0000ff", "Solar": "#ffd700", "Gas": "#808080", "Oil": "#000000",
    "Coal": "#8b4513", "Biomass": "#008000", "Geothermal": "#ff0000", "Storage": "#c1ffc1",
    "Hydro": "#b0c4de", "Nuclear": "#8a2be2", "Wave": "#ff00ff", "Waste": "#ffa500",
    "Clean firm (H2)": "#2e8b7a",          # not in Fig. 6 (West Coast model only)
}
CARRIER_CAT = {
    "onwind": "Wind", "wind": "Wind",
    "offwind": "Offshore Wind", "offwind_floating": "Offshore Wind",
    "solar": "Solar", "rooftop_solar": "Solar",
    "CCGT": "Gas", "CCGT-95CCS": "Gas", "OCGT": "Gas", "gas_cc": "Gas", "gas_ct": "Gas",
    "oil": "Oil", "lsfo": "Oil", "diesel": "Oil",
    "coal": "Coal",
    "biomass": "Biomass", "biodiesel": "Biomass",
    "geothermal": "Geothermal",
    "battery": "Storage", "4hr_battery_storage": "Storage", "8hr_battery_storage": "Storage",
    "PHS": "Storage", "phs": "Storage",
    "hydro": "Hydro", "nuclear": "Nuclear", "wave": "Wave", "msw": "Waste", "waste": "Waste",
    "clean_firm": "Clean firm (H2)",
}
# Not generation in Fig. 6's sense: left out of the pies.
SKIP_CARRIERS = {"shed", "load_shedding", "exports", "imports", "unspecified_imports"}

CASES = {
    # name: (network file, title, extent (lon0, lon1, lat0, lat1), largest pie radius (deg), states,
    #        existing-Tx bins, new-Tx bins, Tx unit, MW per Tx unit)
    "ca_25node_re100_wave_aggressive": (
        REPO / "25bus_re100-annual_2035_nuc_wave-aggressive_gurobi" / "network.nc",
        "California 25-node (PyPSA-USA), 2035, 100% renewable + nuclear, wave at aggressive costs",
        (-125.6, -113.6, 32.2, 42.3), 0.5, ["CA", "NV", "AZ", "OR", "UT", "ID"],
        [0, 10, 20, 30], [0, 2, 4, 6, 8, 10], "GW", 1e3),
    "ca_25node_base_wave_moderate": (
        REPO / "25bus_base_2035_wave-moderate_gurobi" / "network.nc",
        "California 25-node (PyPSA-USA), 2035, base policy, wave at moderate costs",
        (-125.6, -113.6, 32.2, 42.3), 0.5, ["CA", "NV", "AZ", "OR", "UT", "ID"],
        [0, 10, 20, 30], [0, 2, 4, 6, 8, 10], "GW", 1e3),
    "west3_clean100_wave5000": (
        WEST / "results" / "west" / "network_clean_100_wave5000.nc",
        "West Coast 3-zone model, 100% clean, wave at $5,000/kW",
        (-128.0, -113.8, 32.2, 49.0), 1.3, ["CA", "OR", "WA", "NV", "ID", "AZ", "UT", "MT"],
        [0, 2, 4, 6], [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5], "GW", 1e3),
    "oahu_rps2045_wave_moderate": (
        OAHU / "results" / "oahu" / "networks" / "rps_moderate_2045.nc",
        "O'ahu 2045, 100% RPS, wave at moderate costs",
        (-158.45, -157.4, 21.2, 21.85), 0.07, ["HI"],
        [0, 250, 500, 750], [0, 100, 200, 300, 400], "MW", 1),
}


def first_period(s):
    """Statistics on multi-period networks come back with one column per period: keep the single period."""
    return s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s


def by_bus(n, stat, div):
    """Generation or capacity per (bus, Fig. 6 energy type), from PyPSA statistics."""
    s = first_period(getattr(n.statistics, stat)(components=["Generator", "StorageUnit"],
                                                 groupby=["bus", "carrier"], nice_names=False)) / div
    if "component" in s.index.names:
        s = s.groupby(level=["bus", "carrier"]).sum()
    carrier = s.index.get_level_values("carrier")
    s = s[~carrier.isin(SKIP_CARRIERS)]
    unknown = sorted(set(s.index.get_level_values("carrier")) - set(CARRIER_CAT))
    if unknown:
        raise KeyError(f"no Fig. 6 category for carriers {unknown}: add them to CARRIER_CAT")
    s = s.groupby([s.index.get_level_values("bus"),
                   s.index.get_level_values("carrier").map(CARRIER_CAT).rename("carrier")]).sum()
    s = s[s > 1e-6 * s.groupby(level="bus").sum().max()]
    return s[s.index.get_level_values("bus").isin(n.buses.index[n.buses.carrier.isin(["AC", "wave_hub"])])]


def corridor_widths(df, nom, opt, ext, edges_exist, edges_new, per_unit, w_exist, w_new):
    """Existing / new capacity summed per corridor (bus pair), binned like Fig. 6. The whole corridor is drawn
    on its first branch; the other parallel branches get width 0."""
    if df.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float), 0.0, 0.0
    key = df.apply(lambda r: tuple(sorted((r.bus0, r.bus1))), axis=1)
    exist = (df[nom] / per_unit).groupby(key).sum()
    new = ((df[opt] - df[nom]).clip(lower=0).where(df[ext], 0) / per_unit).groupby(key).sum()
    first = df.index.to_series().groupby(key).first()

    def width(v, edges, ws):
        if v <= 0.005 * edges[-1]:
            return 0.0
        return ws[min(np.searchsorted(edges, v, side="left") - 1, len(ws) - 1)]

    we = pd.Series(0.0, index=df.index)
    wn = pd.Series(0.0, index=df.index)
    for k, br in first.items():
        we[br] = width(exist[k], edges_exist, w_exist)
        wn[br] = width(new[k], edges_new, w_new)
    return we, wn, exist.max(), new.max()


def two_sig(v):
    return float(f"{v:.2g}") if v > 0 else 0.0


def draw_land(ax, extent, states):
    if COUNTRIES.exists():
        world = gpd.read_file(COUNTRIES).to_crs(4326)
        ax.add_geometries(world[world.iso_a3.isin(["MEX"])].geometry, crs=ccrs.PlateCarree(),
                          facecolor=LAND, edgecolor="none", zorder=0)
    st = gpd.read_file(STATES).to_crs(4326)
    ax.add_geometries(st[st.STUSPS.isin(states)].geometry, crs=ccrs.PlateCarree(), facecolor=LAND,
                      edgecolor=BORDER, linewidth=0.5, zorder=0.5)
    ax.set_extent(extent, crs=ccrs.PlateCarree())     # before n.plot.map, so legend circles scale correctly


def frame_axes(ax, extent):
    """n.plot.map turns the axes off; turn them back on with Fig. 6's blue ocean and lat/lon ticks."""
    ax.axis("on")
    ax.set_facecolor(OCEAN)
    span = extent[1] - extent[0]
    ax.set_xticks(np.linspace(extent[0], extent[1], 5)[1:-1] if span < 3 else np.linspace(*extent[:2], 3),
                  crs=ccrs.PlateCarree())
    ax.set_yticks(np.linspace(extent[2], extent[3], 3), crs=ccrs.PlateCarree())
    ax.xaxis.set_major_formatter(LongitudeFormatter(number_format=".2f"))
    ax.yaxis.set_major_formatter(LatitudeFormatter(number_format=".2f"))
    ax.tick_params(labelsize=8.5, direction="in", length=4, top=True, right=True)
    ax.set_xlabel("Longitude", fontsize=9.5)
    ax.set_ylabel("Latitude", fontsize=9.5)
    ax.set_extent(extent, crs=ccrs.PlateCarree())


def draw(n, name, title, extent, rmax, states, edges_exist, edges_new, tx_unit, tx_per, stat, unit, div):
    s = by_bus(n, stat, div)
    totals = s.groupby(level="bus").sum()
    area_per_unit = rmax ** 2 / totals.max()           # PyPSA pie radius = sqrt(size)

    w_exist = [1.2 + 1.5 * i for i in range(len(edges_exist) - 1)]
    w_new = [2.5 + 1.6 * i for i in range(len(edges_new) - 1)]
    links = n.links[~n.links.carrier.isin(["imports", "exports", "unspecified_imports"])]
    lk_e, lk_n, lk_emax, lk_nmax = corridor_widths(links, "p_nom", "p_nom_opt", "p_nom_extendable",
                                                   edges_exist, edges_new, tx_per, w_exist, w_new)
    ln_e, ln_n, ln_emax, ln_nmax = corridor_widths(n.lines, "s_nom", "s_nom_opt", "s_nom_extendable",
                                                   edges_exist, edges_new, tx_per, w_exist, w_new)
    full = lambda w, idx: w.reindex(idx).fillna(0.0)
    link_e, link_n = full(lk_e, n.links.index), full(lk_n, n.links.index)
    line_e, line_n = (full(ln_e, n.lines.index), full(ln_n, n.lines.index)) if len(n.lines) else (0, 0)

    tall = (extent[3] - extent[2]) / (extent[1] - extent[0]) > 0.75
    fig, ax = plt.subplots(figsize=(8.6, 10.6) if tall else (9.6, 8.6),
                           subplot_kw={"projection": ccrs.PlateCarree()})
    draw_land(ax, extent, states)

    common = dict(ax=ax, geomap=False, boundaries=extent, auto_scale_branches=True)  # True = widths in pt
    # Layer 1: existing transmission (salmon); no buses
    n.plot.map(bus_size=0, bus_alpha=0, line_width=line_e, link_width=link_e,
               line_color=EXIST_TX, link_color=EXIST_TX, **common)
    # Layer 2: new transmission (translucent lavender over the salmon) and the pies
    n.plot.map(bus_size=s * area_per_unit, bus_color=CAT_COLOR, line_width=line_n, link_width=link_n,
               line_color=NEW_TX, link_color=NEW_TX, line_alpha=NEW_TX_ALPHA, link_alpha=NEW_TX_ALPHA, **common)
    for coll in ax.collections:          # PyPSA puts all pies in one PatchCollection (zorder 5)
        if isinstance(coll, PatchCollection) and coll.get_zorder() == 5:
            coll.set_edgecolor(PIE_EDGE)
            coll.set_linewidth(0.45)
    frame_axes(ax, extent)
    ax.set_title(title, fontsize=10)

    # Legends below the map, as in Fig. 6
    present = [c for c in CAT_COLOR if c in s.index.get_level_values("carrier")]
    bio_label = "Biomass (biodiesel)" if "biodiesel" in set(n.generators.carrier) else "Biomass"
    labels = [bio_label if c == "Biomass" else c for c in present]
    y0 = -0.07
    add_legend_patches(ax, [CAT_COLOR[c] for c in present], labels,
                       legend_kw=dict(loc="upper left", bbox_to_anchor=(-0.02, y0), ncol=2, frameon=False,
                                      fontsize=8.5, title="Generation" if stat == "supply" else "Capacity",
                                      title_fontsize=9, columnspacing=1.2, handlelength=1.3))

    def bins(edges):
        fmt = (lambda v: f"{v:g}")
        return [f"{fmt(a)} - {fmt(b)}" for a, b in zip(edges[:-1], edges[1:])]

    add_legend_lines(ax, w_exist, bins(edges_exist), patch_kw=dict(color=EXIST_TX),
                     legend_kw=dict(loc="upper left", bbox_to_anchor=(0.40, y0), frameon=False, fontsize=8.5,
                                    title=f"Existing Tx ({tx_unit})", title_fontsize=9, handlelength=1.6))
    add_legend_lines(ax, w_new, bins(edges_new), patch_kw=dict(color=NEW_TX, alpha=NEW_TX_ALPHA),
                     legend_kw=dict(loc="upper left", bbox_to_anchor=(0.60, y0), frameon=False, fontsize=8.5,
                                    title=f"New Tx ({tx_unit})", title_fontsize=9, handlelength=1.6))
    # Size legend: three log-spaced references like Fig. 6 (0.1 / 12 / 238 TWh), spaced by their real radius.
    refs = sorted({two_sig(totals.max() / 50), two_sig(totals.max() / 7), two_sig(totals.max())} - {0.0})
    fig.canvas.draw()
    pts_per_deg = ax.get_window_extent().width * 72 / fig.dpi / (extent[1] - extent[0])
    r_pts = [np.sqrt(v * area_per_unit) * pts_per_deg for v in refs]
    fs = 8.5
    gap = 1.2 * max(a + b for a, b in zip(r_pts[:-1], r_pts[1:])) + 4
    lbl = "Total Energy\nGeneration" if stat == "supply" else "Total installed\ncapacity"
    add_legend_circles(ax, [v * area_per_unit for v in refs], [f"{v:,g}" for v in refs],
                       patch_kw=dict(facecolor=CIRCLE, edgecolor="#b5b5b5"),
                       legend_kw=dict(loc="upper left", bbox_to_anchor=(0.80, y0), frameon=False, fontsize=fs,
                                      labelspacing=max(gap / fs - 0.7, 0.8), handletextpad=r_pts[-1] / fs,
                                      borderpad=0.6,
                                      title=f"{lbl} ({unit})", title_fontsize=9))

    fig.savefig(OUT / f"{name}_{'supply' if stat == 'supply' else 'capacity'}_map.png", dpi=170,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return totals, max(lk_emax, ln_emax), max(lk_nmax, ln_nmax)


def sanitize(n):
    """Define every carrier in use and give it its Fig. 6 colour and name (also used by the bar charts)."""
    used = set(n.buses.carrier) | set(n.generators.carrier) | set(n.storage_units.carrier) | set(n.links.carrier)
    missing = sorted(c for c in used if c and c not in n.carriers.index)
    if missing:
        n.add("Carrier", missing)
    other = {"AC": "#999999", "DC": "#999999", "wave_hub": "#999999", "wave_export": NEW_TX,
             "transmission": EXIST_TX, "imports": "#bdbdbd", "unspecified_imports": "#bdbdbd",
             "exports": "#757575", "shed": "#e34948", "load_shedding": "#e34948"}
    n.carriers["color"] = [CAT_COLOR[CARRIER_CAT[c]] if c in CARRIER_CAT else other.get(c, "#999999")
                           for c in n.carriers.index]
    raw = {"rooftop_solar": "Rooftop solar", "biodiesel": "Biodiesel", "msw": "Waste (H-POWER)",
           "CCGT": "Gas CC", "CCGT-95CCS": "Gas CC + CCS", "OCGT": "Gas CT", "gas_cc": "Gas CC",
           "gas_ct": "Gas CT", "4hr_battery_storage": "Battery 4h", "8hr_battery_storage": "Battery 8h",
           "battery": "Battery", "PHS": "Pumped hydro", "phs": "Pumped hydro",
           "offwind_floating": "Offshore Wind (floating)", "unspecified_imports": "Imports",
           "shed": "Load shedding", "wave_export": "Wave export cable"}
    if "nice_name" not in n.carriers:
        n.carriers["nice_name"] = ""
    n.carriers["nice_name"] = [raw.get(c, CARRIER_CAT.get(c, c)) for c in n.carriers.index]


def bars(n, name, title):
    """PyPSA's own statistics charts (carrier colours set to the Fig. 6 palette by sanitize)."""
    for stat, label in [("supply", "Annual supply"), ("optimal_capacity", "Optimal capacity")]:
        try:
            res = getattr(n.statistics, stat).plot.bar(components=["Generator", "StorageUnit"])
            fig = res[0] if isinstance(res, tuple) else res
            fig = getattr(fig, "figure", fig)
            fig.suptitle(f"{label}: {title}", x=0.01, ha="left", fontsize=10, fontweight="bold")
            fig.savefig(OUT / f"{name}_{'supply' if stat == 'supply' else 'capacity'}_bar.png", dpi=150,
                        bbox_inches="tight")
            plt.close("all")
        except Exception as e:   # PyPSA 1.3 statistics plots do not handle every multi-period network
            print(f"  {name}: statistics {stat}.plot.bar not available ({type(e).__name__}: {str(e)[:90]})")


def main():
    logging.disable(logging.WARNING)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (path, title, extent, rmax, states, e_ex, e_new, tx_unit, tx_per) in CASES.items():
        n = pypsa.Network(path)
        sanitize(n)
        oahu = "oahu" in name
        s_unit, s_div = ("GWh", 1e3) if oahu else ("TWh", 1e6)
        c_unit, c_div = ("MW", 1) if oahu else ("GW", 1e3)
        out = {}
        for stat, unit, div in [("supply", s_unit, s_div), ("optimal_capacity", c_unit, c_div)]:
            out[stat] = draw(n, name, title, extent, rmax, states, e_ex, e_new, tx_unit, tx_per, stat, unit, div)
        bars(n, name, title)
        tot, emax, nmax = out["supply"]
        print(f"{name}: buses with pies {len(tot)}, generation {tot.sum():,.1f} {s_unit}, capacity "
              f"{out['optimal_capacity'][0].sum():,.1f} {c_unit}; largest corridor existing {emax:,.2f} "
              f"{tx_unit}, new {nmax:,.2f} {tx_unit}")


if __name__ == "__main__":
    main()
