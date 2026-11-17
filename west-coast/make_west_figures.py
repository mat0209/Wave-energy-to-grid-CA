"""West Coast figures from inputs/west/ and results/west/. Run after run_west.py.
Usage: python make_west_figures.py [--dispatch-network results/west/network_clean_100_wave5000.nc]
"""
import argparse
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
import pypsa

import west_assumptions as A
from plot_style import INK, INK2, SERIES, SURFACE

ROOT = Path(__file__).parent
INPUTS, RESULTS = ROOT / "inputs" / "west", ROOT / "results" / "west"
POLICY_LABEL = {"least_cost": "Least cost (no CO₂ limit)", "clean_100": "100% clean electricity"}
MONTHS = list("JFMAMJJASOND")


def fig_breakeven():
    df = pd.read_csv(RESULTS / "sweep_summary.csv")
    sites = pd.read_csv(INPUTS / "wave_sites.csv", index_col=0)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    for color, (policy, g) in zip(SERIES, df.groupby("policy", sort=False)):
        base = g[g.wave_capex_per_kw.isna()].system_cost_busd.iloc[0]
        g = g.dropna(subset=["wave_capex_per_kw"])
        x = g.wave_capex_per_kw / 1e3
        style = dict(color=color, marker="o", markersize=7, markeredgecolor=SURFACE, markeredgewidth=2,
                     label=POLICY_LABEL[policy])
        ax1.plot(x, g.wave_mw / 1e3, **style)
        ax2.plot(x, (base - g.system_cost_busd) * 1e3, **style)
    cap = sites.p_nom_max_mw.sum() / 1e3
    ax1.axhline(cap, color=INK2, lw=1, ls=":")
    ax1.text(x.max(), cap + 0.12, f"site limit ({cap:.2f} GW)", ha="right", color=INK2, fontsize=9)
    ax1.set(title="Wave capacity built, West Coast", xlabel="Wave capex ($ thousand / kW)", ylabel="GW",
            ylim=(0, cap * 1.12))
    ax2.set(title="Annual system cost saving vs. no-wave case", xlabel="Wave capex ($ thousand / kW)",
            ylabel="$ million / yr")
    ax2.axhline(0, color=INK2, lw=1)
    ax1.legend(loc="center right")
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_wave_breakeven.png", dpi=160)


def fig_monthly():
    ts = pd.read_csv(INPUTS / "timeseries.csv", index_col=0, parse_dates=True)
    sites = pd.read_csv(INPUTS / "wave_sites.csv", index_col=0)
    m = ts.groupby(ts.index.month).mean()
    pnw = [f"wave_cf_{s}" for s in sites.index[sites.zone == "PNW"]]
    ca = [f"wave_cf_{s}" for s in sites.index[sites.zone != "PNW"]]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.3), gridspec_kw={"width_ratios": [3, 2]})
    series = [("Wave, PNW sites", m[pnw].mean(axis=1)), ("Wave, California sites", m[ca].mean(axis=1)),
              ("Solar, S. California", m.solar_cf_CA_S), ("Onshore wind, PNW", m.wind_cf_PNW),
              ("Offshore wind, Humboldt", m.osw_cf_osw_humboldt)]
    for color, (label, s) in zip(SERIES, series):
        ax1.plot(m.index, s, color=color, label=label)
    ax1.set(title="Monthly mean capacity factor", ylabel="Capacity factor", ylim=(0, 1.0),
            xticks=m.index, xticklabels=MONTHS)
    ax1.legend(loc="upper center", ncol=2, fontsize=8.5)
    load = sum(m[f"load_{z}"] for z in A.ZONES) / 1e3
    hydro = sum(m[f"hydropower_{z}"] for z in A.ZONES) / 1e3
    ax2.plot(m.index, load, color=INK, label="Demand (3 zones)")
    ax2.plot(m.index, hydro, color=INK2, ls="--", label="Hydro output, 2023")
    ax2.set(title="Demand and hydro, 2023 (EIA-930)", ylabel="GW", xticks=m.index,
            xticklabels=MONTHS, ylim=(0, load.max() * 1.2))
    ax2.legend(loc="lower left", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_monthly_resources.png", dpi=160)


def fig_dispatch(path):
    n = pypsa.Network(path)
    g = n.generators_t.p.T.groupby(n.generators.carrier).sum().T
    su = n.storage_units_t.p.T.groupby(n.storage_units.carrier).sum().T
    storage = su.drop(columns="hydro").sum(axis=1)
    get = lambda df, c: df[c] if c in df else 0.0
    stack = pd.DataFrame({
        "Nuclear": get(g, "nuclear"),
        "Geothermal + biomass": get(g, "geothermal") + get(g, "biomass"),
        "Hydro": get(g, "hydro") + get(su, "hydro"),
        "Wave": -n.links_t.p1.filter(like="cable_").sum(axis=1),
        "Wind (on + offshore)": get(g, "wind") + get(g, "offwind"),
        "Solar": get(g, "solar"),
        "Gas + coal": sum(get(g, c) for c in ["gas_cc", "gas_ct", "coal", "oil"]),
        "Clean firm (H₂ CT)": get(g, "clean_firm"),
        "Storage discharge": storage.clip(lower=0),
    }) / 1e3
    stack = stack.loc[:, stack.abs().sum() > 1]
    colors = {"Wave": SERIES[0], "Clean firm (H₂ CT)": SERIES[1], "Wind (on + offshore)": SERIES[2],
              "Solar": SERIES[3], "Storage discharge": SERIES[4], "Hydro": SERIES[5], "Nuclear": SERIES[6],
              "Geothermal + biomass": SERIES[7], "Gas + coal": INK2}
    load = n.loads_t.p_set.sum(axis=1) / 1e3
    charge = storage.clip(upper=0) / 1e3

    weeks = {"Winter week (Jan 16–23)": ("2023-01-16", "2023-01-23"),
             "Summer week (Jul 17–24)": ("2023-07-17", "2023-07-24")}
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6), sharey=True)
    for ax, (title, (a, b)) in zip(axes, weeks.items()):
        s = stack.loc[a:b]
        ax.stackplot(s.index, s.T.values, colors=[colors[c] for c in s.columns], labels=s.columns,
                     edgecolor=SURFACE, linewidth=0.5, step="post")
        ax.fill_between(charge.loc[a:b].index, charge.loc[a:b].values, 0, color=SERIES[4], alpha=0.4,
                        step="post", lw=0, label="Storage charging")
        ax.step(load.loc[a:b].index, load.loc[a:b].values, where="post", color=INK, lw=1.6, label="Demand")
        ax.set(title=title, ylabel="GW")
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=6, fontsize=8.5)
    policy, _, wave = Path(path).stem.removeprefix("network_").rpartition("_")
    wave = "no wave" if wave == "nowave" else f"wave at ${int(wave.removeprefix('wave')):,}/kW"
    fig.suptitle(f"West Coast dispatch (3 zones combined), {POLICY_LABEL[policy]}, {wave} (UTC)", x=0.01,
                 ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    fig.savefig(RESULTS / "fig_dispatch_weeks.png", dpi=160)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dispatch-network", default=RESULTS / "network_clean_100_wave5000.nc")
    args = ap.parse_args()
    fig_breakeven()
    fig_monthly()
    fig_dispatch(args.dispatch_network)
