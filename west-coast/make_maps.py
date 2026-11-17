"""Maps of wave sites, grid connections and optimal build for Oahu and the West Coast.

Run after run_oahu.py and run_west.py. Writes results/<region>/fig_map_<region>.png.
Usage: python make_maps.py [--west-scenario clean_100:5000] [--oahu-scenario rps_2045:3000]
"""
import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D

try:   # this branch holds only the west model
    import oahu_assumptions as OA
except ImportError:
    OA = None
import west_assumptions as WA
from plot_style import BLUE_RAMP, COAST, INK, INK2, LAND, SERIES, SURFACE

ROOT = Path(__file__).parent
STATES = ROOT / "data" / "raw" / "census" / "cb_2023_us_state_500k.shp"
CF_CMAP = LinearSegmentedColormap.from_list("cf", BLUE_RAMP)
POLICY_LABEL = {"least_cost": "least cost", "renewable_100": "100% renewable", "clean_100": "100% clean",
                "rps_2045": "2045, 100% RPS"}

REGIONS = {
    "oahu": dict(assumptions=OA, states=["HI"], extent=(-158.64, -157.34, 21.2, 21.84),
                 figsize=(14, 5.6), size_refs=[100, 300, 600], max_area=900, legend_loc="lower left", center=(-157.98, 21.47), label_mode="radial",
                 title="Oahu"),
    "west": dict(assumptions=WA, states=["WA", "OR", "CA"], extent=(-128.2, -114.0, 32.3, 49.3),
                 figsize=(15.5, 9.2), size_refs=[100, 250, 500], max_area=520, legend_loc="outside", center=None, label_mode="west",
                 title="U.S. West Coast"),
}
LABEL_NUDGE = {"PACWAVE": (0, -0.3), "WECC_D": (0, 0.2)}   # degrees, to separate close neighbours


def size_of(mw, ref_max, max_area):
    return 15 + max_area * np.asarray(mw, float) / ref_max     # marker area proportional to MW


def base_map(ax, states, keep, extent):
    others = states[~states.STUSPS.isin(keep)]
    others.plot(ax=ax, color="#f6f5f2", edgecolor=COAST, linewidth=0.5)
    states[states.STUSPS.isin(keep)].plot(ax=ax, color=LAND, edgecolor=COAST, linewidth=0.8)
    x0, x1, y0, y1 = extent
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect(1 / np.cos(np.radians((y0 + y1) / 2)))
    ax.set_facecolor(SURFACE)
    ax.grid(False)
    ax.tick_params(labelsize=8)
    ax.set_xlabel("Longitude (°)", fontsize=8.5)
    ax.set_ylabel("Latitude (°)", fontsize=8.5)
    for side in ("top", "right"):
        ax.spines[side].set_visible(True)
    for spine in ax.spines.values():
        spine.set_color(COAST)


def label_xy(site, s, cfg, scale):
    if cfg["label_mode"] == "radial":
        cx, cy = cfg["center"]
        v = np.array([s.lon - cx, s.lat - cy])
        v = v / np.linalg.norm(v) * scale
        x, y, ha = s.lon + v[0], s.lat + v[1], "left" if v[0] > 0 else "right"
    else:
        x, y, ha = s.lon - scale, s.lat, "right"
    dx, dy = LABEL_NUDGE.get(site, (0, 0))
    return x + dx, y + dy, ha


def draw_connections(ax, sites, landings):
    for site, s in sites.iterrows():
        lat, lon = landings[s.landing][:2]
        ax.plot([s.lon, lon], [s.lat, lat], color=INK2, lw=1, ls=(0, (3, 2)), zorder=3)
    used = sites.landing.unique()
    for name in used:
        lat, lon = landings[name][:2]
        ax.scatter(lon, lat, marker="s", s=28, color=INK, zorder=5)


def panel_resource(ax, sites, cfg, landings, scale):
    draw_connections(ax, sites, landings)
    norm = Normalize(vmin=sites.annual_cf.min() - 0.03, vmax=sites.annual_cf.max())
    ax.scatter(sites.lon, sites.lat, c=sites.annual_cf, cmap=CF_CMAP, norm=norm, s=110,
               edgecolor=SURFACE, linewidth=2, zorder=6)
    for site, s in sites.iterrows():
        x, y, ha = label_xy(site, s, cfg, scale)
        ax.text(x, y, f"{site}\nCF {s.annual_cf:.2f} · {s.cable_km:.0f} km", ha=ha, va="center",
                fontsize=8, color=INK, zorder=7, linespacing=1.15)
    for name in sites.landing.unique():
        lat, lon = landings[name][:2]
        if cfg["label_mode"] == "radial":      # put landing names on the land side
            cx, cy = cfg["center"]
            v = np.array([cx - lon, cy - lat])
            off = v / np.linalg.norm(v) * 14
        else:
            off = np.array([6, -3])
        ax.annotate(name, (lon, lat), xytext=off, textcoords="offset points", fontsize=7.5, color=INK2,
                    ha="center" if cfg["label_mode"] == "radial" else "left", va="center")
    cax = ax.inset_axes([0.60, 0.93, 0.36, 0.025])
    cb = plt.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=CF_CMAP), cax=cax, orientation="horizontal")
    cb.set_label("Annual capacity factor (after losses)", color=INK2, fontsize=8)
    cb.ax.tick_params(labelsize=7.5)
    cb.outline.set_visible(False)
    ax.set_title("Wave resource and export-cable routes\nlabels: capacity factor · cable length")
    ax.legend(handles=[Line2D([], [], color=INK2, lw=1, ls=(0, (3, 2)), label="Export cable route"),
                       Line2D([], [], marker="s", color=INK, lw=0, markersize=5, label="Landing point")],
              loc="lower left", fontsize=8)


def panel_build(ax, sites, cfg, landings, row, scale, policy, capex, extra_handles=None):
    ref_max = max(cfg["size_refs"])
    built = pd.Series({s: row.get(f"mw_wave_{s}", 0.0) for s in sites.index}).fillna(0)
    cable = pd.Series({s: row.get(f"mw_cable_{s}", 0.0) for s in sites.index}).fillna(0)
    for site, s in sites.iterrows():
        lat, lon = landings[s.landing][:2]
        if cable[site] > 0.5:
            ax.plot([s.lon, lon], [s.lat, lat], color=SERIES[0], lw=0.8 + 4 * cable[site] / ref_max,
                    solid_capstyle="round", alpha=0.6, zorder=3)
    on = built > 0.5
    big_first = built[on].sort_values(ascending=False).index      # small circles stay visible on top
    ax.scatter(sites.lon[big_first], sites.lat[big_first], s=size_of(built[big_first], ref_max, cfg["max_area"]),
               color=SERIES[0], edgecolor=SURFACE, linewidth=2, alpha=0.9, zorder=6)
    ax.scatter(sites.lon[~on], sites.lat[~on], s=40, facecolor=SURFACE, edgecolor=INK2, linewidth=1.2, zorder=6)
    for site, s in sites.iterrows():
        x, y, ha = label_xy(site, s, cfg, scale * (1 + 0.8 * min(built[site] / ref_max, 1)))
        txt = f"{site}\n{built[site]:.0f} MW" + (f" · cable {cable[site]:.0f}" if built[site] > 0.5 else "")
        ax.text(x, y, txt, ha=ha, va="center", fontsize=8, color=INK if built[site] > 0.5 else INK2,
                zorder=7, linespacing=1.15)
    for name in sites.landing.unique():
        lat, lon = landings[name][:2]
        ax.scatter(lon, lat, marker="s", s=22, color=INK, zorder=5)

    handles = [plt.scatter([], [], s=size_of(r, ref_max, cfg["max_area"]), color=SERIES[0], edgecolor=SURFACE,
                           label=f"{r} MW") for r in cfg["size_refs"]]
    handles.append(plt.scatter([], [], s=40, facecolor=SURFACE, edgecolor=INK2, label="not built"))
    if extra_handles:
        handles += extra_handles
    where = (dict(loc="upper left", bbox_to_anchor=(1.02, 1.0)) if cfg["legend_loc"] == "outside"
             else dict(loc=cfg["legend_loc"]))
    ax.legend(handles=handles, title="Wave capacity built", fontsize=8, title_fontsize=8.5, labelspacing=1.3,
              borderpad=0.9, handletextpad=1.4, frameon=True, facecolor=SURFACE, edgecolor=COAST,
              framealpha=0.95, **where)
    ax.set_title(f"Optimal wave build: {POLICY_LABEL[policy]}, wave at ${capex:,}/kW\n"
                 f"total {built.sum():,.0f} MW; line width = export cable size")


def west_overlay(ax, row):
    """Inter-zone corridors (existing + expansion) and offshore wind built."""
    for name, (b0, b1, mw, km) in WA.CORRIDORS.items():
        (x0, y0), (x1, y1) = WA.ZONE_XY[b0], WA.ZONE_XY[b1]
        total = mw + row.get(f"mw_{name} expansion", 0.0)
        ax.plot([x0, x1], [y0, y1], color=SERIES[6], lw=1 + total / 2500, alpha=0.55, zorder=2,
                solid_capstyle="round")
        ax.text((x0 + x1) / 2 + 0.25, (y0 + y1) / 2, f"{name}\n{mw / 1e3:.1f} → {total / 1e3:.1f} GW",
                fontsize=7.5, color=SERIES[6], ha="left", va="center", zorder=7)
    for z, (x, y) in WA.ZONE_XY.items():
        ax.scatter(x, y, s=60, marker="o", color=SERIES[6], edgecolor=SURFACE, linewidth=1.5, zorder=4)
        ax.text(x + 0.3, y + 0.35, z.replace("_", " "), fontsize=9, fontweight="bold", color=INK, zorder=7)
    total_osw = 0.0
    for name, (lat, lon, zone, pmax) in WA.OFFSHORE_WIND.items():
        mw = row.get(f"mw_{name}", 0.0)
        total_osw += mw
        ax.scatter(lon, lat, marker="D", s=30 + 120 * mw / pmax, color=SERIES[2] if mw > 1 else SURFACE,
                   edgecolor=SERIES[2], linewidth=1.2, zorder=6)
        if mw > 1:
            ax.text(lon + 0.3, lat - 0.25, f"OSW {mw:,.0f} MW", fontsize=7, color=SERIES[2], zorder=7)
    return [Line2D([], [], marker="D", color=SERIES[2], markerfacecolor=SURFACE, lw=0, markersize=6,
                   label=f"Offshore wind area ({total_osw:,.0f} MW built)"),
            Line2D([], [], color=SERIES[6], lw=3, alpha=0.55, label="Inter-zone corridor (existing → optimal)")]


def make_map(region, policy, capex):
    cfg = REGIONS[region]
    A = cfg["assumptions"]
    sites = pd.read_csv(ROOT / "inputs" / region / "wave_sites.csv", index_col=0)
    sweep = pd.read_csv(ROOT / "results" / region / "sweep_summary.csv")
    match = sweep[(sweep.policy == policy) & (sweep.wave_capex_per_kw == capex)]
    assert len(match) == 1, f"no {policy} run at ${capex}/kW in results/{region}/sweep_summary.csv"
    row = match.iloc[0].to_dict()
    states = gpd.read_file(STATES).to_crs(4326)
    landings = A.LANDING_POINTS
    scale = 0.045 if region == "oahu" else 0.3

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=cfg["figsize"])
    for ax in (ax1, ax2):
        base_map(ax, states, cfg["states"], cfg["extent"])
    panel_resource(ax1, sites, cfg, landings, scale)
    extra = west_overlay(ax2, row) if region == "west" else None
    panel_build(ax2, sites, cfg, landings, row, scale, policy, capex, extra)
    fig.suptitle(f"{cfg['title']}: wave energy sites, grid connections and optimal build", x=0.01, ha="left",
                 fontweight="bold", fontsize=12)
    fig.tight_layout()
    out = ROOT / "results" / region / f"fig_map_{region}.png"
    fig.savefig(out, dpi=170, bbox_inches="tight")
    print("wrote", out)


def parse(s):
    policy, capex = s.split(":")
    return policy, int(capex)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--west-scenario", default="clean_100:5000", type=parse)
    args = ap.parse_args()
    make_map("west", *args.west_scenario)
