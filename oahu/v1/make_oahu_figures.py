"""Oahu figures from inputs/oahu/ and results/oahu/. Run after run_oahu.py."""
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
import pypsa

from plot_style import INK, INK2, SERIES, SURFACE

ROOT = Path(__file__).parent
INPUTS, RESULTS = ROOT / "inputs" / "oahu", ROOT / "results" / "oahu"
POLICY_LABEL = {"least_cost": "Least cost (no CO₂ limit)", "renewable_100": "100% renewable"}


def fig_breakeven():
    df = pd.read_csv(RESULTS / "sweep_summary.csv")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    for color, (policy, g) in zip(SERIES, df.groupby("policy", sort=False)):
        base = g[g.wave_capex_per_kw.isna()].system_cost_musd.iloc[0]
        g = g.dropna(subset=["wave_capex_per_kw"])
        x = g.wave_capex_per_kw / 1e3
        ax1.plot(x, g.wave_mw, color=color, marker="o", markersize=7,
                 markeredgecolor=SURFACE, markeredgewidth=2, label=POLICY_LABEL[policy])
        ax2.plot(x, base - g.system_cost_musd, color=color, marker="o", markersize=7,
                 markeredgecolor=SURFACE, markeredgewidth=2, label=POLICY_LABEL[policy])
    ax1.axhline(df.filter(like="mw_wave_").iloc[-1].count() * 100, color=INK2, lw=1, ls=":")
    ax1.text(20, 615, "site limit (6 × 100 MW)", ha="right", color=INK2, fontsize=9)
    ax1.set(title="Wave capacity built", xlabel="Wave capex ($ thousand / kW)", ylabel="MW",
            ylim=(0, 680))
    ax2.set(title="Annual system cost saving vs. no-wave case", xlabel="Wave capex ($ thousand / kW)",
            ylabel="$ million / yr")
    ax2.axhline(0, color=INK2, lw=1)
    ax1.legend(loc="center right")
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_wave_breakeven.png", dpi=160)


def fig_monthly():
    ts = pd.read_csv(INPUTS / "timeseries.csv", index_col=0, parse_dates=True)
    m = ts.groupby(ts.index.month).mean()
    wave = m.filter(like="wave_cf_")
    months = list("JFMAMJJASOND")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [3, 2]})
    ax1.fill_between(m.index, wave.min(axis=1), wave.max(axis=1), color=SERIES[0], alpha=0.18, lw=0)
    ax1.plot(m.index, wave.mean(axis=1), color=SERIES[0], label="Wave (mean of 6 sites, band = range)")
    ax1.plot(m.index, m.solar_cf, color=SERIES[1], label="Solar")
    ax1.plot(m.index, m.wind_cf, color=SERIES[2], label="Wind")
    ax1.set(title="Monthly mean capacity factor, Oahu", ylabel="Capacity factor", ylim=(0, 1.05),
            xticks=m.index, xticklabels=months)
    ax1.legend(loc="upper center", ncol=3, fontsize=9)
    ax2.plot(m.index, m.load_mw, color=INK2)
    ax2.set(title="Monthly mean demand (synthetic)", ylabel="MW", xticks=m.index, xticklabels=months,
            ylim=(0, m.load_mw.max() * 1.15))
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_monthly_resources.png", dpi=160)


def fig_dispatch():
    n = pypsa.Network(RESULTS / "network_renewable_100_wave5000.nc")
    gen = n.generators_t.p.T.groupby(n.generators.carrier).sum().T
    sto = n.storage_units_t.p.sum(axis=1)
    stack = pd.DataFrame({
        "Wave": -n.links_t.p1.sum(axis=1),          # delivered onshore, after cable losses
        "Waste": gen["waste"], "Wind": gen["wind"], "Solar": gen["solar"],
        "Biofuel": gen["biofuel"], "Battery discharge": sto.clip(lower=0),
    })
    load = n.loads_t.p_set.sum(axis=1)
    charge = sto.clip(upper=0)

    weeks = {"Winter week (Jan 14–21)": ("2019-01-14", "2019-01-21"),
             "Summer week (Jul 15–22)": ("2019-07-15", "2019-07-22")}
    colors = [SERIES[0], SERIES[6], SERIES[2], SERIES[3], SERIES[1], SERIES[4]]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), sharey=True)
    for ax, (title, (a, b)) in zip(axes, weeks.items()):
        s, l, c = stack.loc[a:b], load.loc[a:b], charge.loc[a:b]
        ax.stackplot(s.index, s.T.values, colors=colors, labels=s.columns,
                     edgecolor=SURFACE, linewidth=0.6, step="post")
        ax.fill_between(c.index, c.values, 0, color=SERIES[4], alpha=0.45, step="post",
                        lw=0, label="Battery charging")
        ax.step(l.index, l.values, where="post", color=INK, lw=1.6, label="Demand")
        ax.set(title=title, ylabel="MW")
        ax.tick_params(axis="x", rotation=0)
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=8, fontsize=9)
    fig.suptitle("Dispatch, 100% renewable case, wave at $5,000/kW (UTC)", x=0.01, ha="left",
                 fontweight="bold")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(RESULTS / "fig_dispatch_weeks.png", dpi=160)


if __name__ == "__main__":
    fig_breakeven()
    fig_monthly()
    fig_dispatch()
