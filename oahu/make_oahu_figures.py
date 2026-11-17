"""O'ahu v2 figures from inputs/oahu/ and results/oahu/. Run after run_oahu.py."""
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa

import oahu_assumptions as A
from plot_style import INK, INK2, SERIES, SURFACE

ROOT = Path(__file__).parent
INPUTS, RESULTS = ROOT / "inputs" / "oahu", ROOT / "results" / "oahu"
MONTHS = list("JFMAMJJASOND")
WAVE_LABEL = {"none": "No wave", "aggressive": "Wave: aggressive", "moderate": "Wave: moderate",
              "conservative": "Wave: conservative"}
POLICY_LABEL = {"least_cost": "Least cost (no RPS)", "rps": "Hawai'i RPS (40% 2030, 70% 2040, 100% 2045)"}

# One fixed palette slot per technology; stacks follow slot order so adjacent fills are validated pairs.
TECH = [  # (label, color, capacity columns, generation columns)
    ("Wave", SERIES[0], ["cap_wave_mw"], ["gen_wave_gwh"]),
    ("Waste + biodiesel", SERIES[1], ["cap_msw_mw", "cap_biodiesel_mw"], ["gen_msw_gwh", "gen_biodiesel_gwh"]),
    ("Onshore wind", SERIES[2], ["cap_onwind_mw"], ["gen_onwind_gwh"]),
    ("Solar (utility + rooftop)", SERIES[3], ["cap_solar_mw", "cap_rooftop_solar_mw"],
     ["gen_solar_gwh", "gen_rooftop_solar_gwh"]),
    ("Battery", SERIES[4], ["cap_battery_mw"], []),
    ("Offshore wind", SERIES[5], ["cap_offwind_mw"], ["gen_offwind_gwh"]),
    ("Oil (LSFO + diesel)", SERIES[6], ["cap_lsfo_mw", "cap_diesel_mw"], ["gen_lsfo_gwh", "gen_diesel_gwh"]),
]
COST_COLOR = {"aggressive": SERIES[0], "moderate": SERIES[1], "conservative": SERIES[2]}


def col_sum(df, cols):
    return sum(df[c].fillna(0.0) if c in df else 0.0 for c in cols)


def fig_pathways(df):
    """RPS pathway: capacity and generation mix by year, one column per wave cost case."""
    d = df[df.policy == "rps"]
    cases = ["none"] + A.WAVE_SCENARIOS
    fig, axes = plt.subplots(2, 4, figsize=(13.5, 7.2), sharey="row")
    x = np.arange(len(A.YEARS))
    for j, case in enumerate(cases):
        g = d[d.wave == case].set_index("year").reindex(A.YEARS)
        for i, (kind, scale, unit) in enumerate([("cap", 1e3, "GW"), ("gen", 1e3, "TWh")]):
            ax = axes[i, j]
            bottom = np.zeros(len(x))
            for label, color, caps, gens in TECH:
                cols = caps if kind == "cap" else gens
                if not cols:
                    continue
                v = col_sum(g, cols).to_numpy() / scale
                ax.bar(x, v, 0.62, bottom=bottom, color=color, edgecolor=SURFACE, linewidth=1.5,
                       label=label if (i, j) == (0, 0) else None)
                bottom += v
            if kind == "gen":
                ax.plot(x, g.load_twh, "_", color=INK, markersize=26, markeredgewidth=2,
                        label="Demand" if j == 0 else None)
            ax.set_xticks(x, A.YEARS)
            ax.set_title(WAVE_LABEL[case] if i == 0 else "")
            if j == 0:
                ax.set_ylabel(("Installed capacity" if kind == "cap" else "Annual generation") + f" ({unit})")
            ax.grid(axis="x", visible=False)
            if kind == "cap":
                for xi, w in zip(x, g.cap_wave_mw):
                    if w > 5:
                        ax.text(xi, w / 1e3 / 2, f"{w:,.0f}\nMW", ha="center", va="center", fontsize=7.5,
                                color=SURFACE, fontweight="bold")
    h1, l1 = axes[0, 0].get_legend_handles_labels()
    h2, l2 = axes[1, 0].get_legend_handles_labels()
    fig.legend(h1 + h2[-1:], l1 + l2[-1:], loc="lower center", ncol=8, fontsize=8.5)
    fig.suptitle("O'ahu pathway under the Hawai'i RPS: capacity and generation by wave cost case",
                 x=0.01, ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(RESULTS / "fig_pathway_mix.png", dpi=160)


def fig_wave_by_year(df):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0), sharey=True)
    for ax, policy in zip(axes, A.POLICIES):
        d = df[df.policy == policy]
        for scn in A.WAVE_SCENARIOS:
            g = d[d.wave == scn].sort_values("year")
            ax.plot(g.year, g.wave_mw, color=COST_COLOR[scn], marker="o", markersize=8, markeredgecolor=SURFACE,
                    markeredgewidth=2, label=scn.capitalize())
            last = g.iloc[-1]
            ax.annotate(f"{last.wave_mw:,.0f} MW", (last.year, last.wave_mw), xytext=(6, 0),
                        textcoords="offset points", va="center", fontsize=8.5, color=INK2)
        ax.set(title=POLICY_LABEL[policy], xlabel="Year", xticks=A.YEARS, xlim=(2028, 2048))
        ax.set_ylabel("Wave capacity in service (MW)")
    axes[0].legend(title="Wave cost case", loc="upper left", fontsize=8.5, title_fontsize=8.5)
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_wave_by_year.png", dpi=160)


def fig_buildcurve():
    s = pd.read_csv(RESULTS / "sweep_summary.csv")
    ref = s[s.wave_capex_per_kw.isna()].iloc[0]
    s = s.dropna(subset=["wave_capex_per_kw"]).sort_values("wave_capex_per_kw")
    costs = pd.read_csv(ROOT / A.WAVE_COST_FILE)
    marks = {scn: np.interp(2045, g.year, g.capex_musd_per_mw) for scn, g in costs.groupby("scenario")}
    x = s.wave_capex_per_kw / 1e3
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    style = dict(color=SERIES[0], marker="o", markersize=8, markeredgecolor=SURFACE, markeredgewidth=2)
    ax1.plot(x, s.wave_mw, **style)
    ax2.plot(x, ref.system_cost_musd - s.system_cost_musd, **style)
    for ax in (ax1, ax2):
        for k, (scn, v) in enumerate(sorted(marks.items(), key=lambda kv: kv[1])):
            ax.axvline(v, color=INK2, lw=1, ls=":")
            ax.text(v, 0.97 - 0.09 * k, f" {scn} ${v:.2f}k", transform=ax.get_xaxis_transform(), fontsize=7.5,
                    color=INK2, va="top", ha="left")
    ax1.set(title="Wave built in 2045 (100% RPS)", xlabel="Wave capex ($ thousand / kW)", ylabel="MW", ylim=(0, None))
    ax2.set(title="Annual system cost saving vs. no wave", xlabel="Wave capex ($ thousand / kW)",
            ylabel="$ million / yr")
    ax2.axhline(0, color=INK2, lw=1)
    fig.suptitle("2045 build curve: fleet from the no-wave RPS pathway through 2040, wave capex varied in 2045;"
                 " dotted lines = your 2045 costs", x=0.01, ha="left", fontsize=9.5, color=INK2)
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_wave_buildcurve.png", dpi=160)


def fig_wave_value():
    """First-MW value per site in the no-wave RPS 2045 case vs. the annual cost of a MW at your 2045 costs."""
    v = pd.read_csv(RESULTS / "wave_value.csv")
    v = v[(v.run == "rps_none_2045")].set_index("site").sort_values("cf", ascending=False)
    costs = pd.read_csv(ROOT / A.WAVE_COST_FILE)
    ann = A.DISCOUNT_RATE / (1 - (1 + A.DISCOUNT_RATE) ** -A.WAVE_LIFETIME)
    fig, ax = plt.subplots(figsize=(9.5, 4.4))
    x = np.arange(len(v))
    parts = [("Energy (price x output)", "energy_k", SERIES[0]), ("Reserve-margin credit", "erm_k", SERIES[1])]
    bottom = np.zeros(len(v))
    for label, c, color in parts:
        ax.bar(x, v[c], 0.55, bottom=bottom, color=color, edgecolor=SURFACE, linewidth=1.5, label=label)
        bottom += v[c].to_numpy()
    for scn, g in costs.groupby("scenario"):
        capex = np.interp(2045, g.year, g.capex_musd_per_mw) * 1e3
        opex = np.interp(2045, g.year, g.opex_musd_per_mw_yr) * 1e3
        y = ann * capex + opex
        ax.axhline(y, color=INK2, lw=1, ls=":")
        ax.text(len(v) - 0.45, y, f"{scn} cost", fontsize=8, color=INK2, va="bottom", ha="right")
    ax.set_xticks(x, [f"{s}\nCF {cf:.2f}" for s, cf in zip(v.index, v.cf)])
    ax.set(ylabel="$ thousand per MW-yr", title="Value of the first MW of wave, 2045 under 100% RPS "
           "(no-wave system) vs. annual cost + cable")
    ax.legend(loc="upper right", fontsize=8.5)
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_wave_value.png", dpi=160)


def fig_monthly():
    ts = pd.read_csv(INPUTS / "timeseries.csv", index_col=0, parse_dates=True)
    m = ts.groupby(ts.index.month).mean()
    wave = m.filter(like="wave_cf_")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.2), gridspec_kw={"width_ratios": [3, 2]})
    ax1.fill_between(m.index, wave.min(axis=1), wave.max(axis=1), color=SERIES[0], alpha=0.18, lw=0)
    ax1.plot(m.index, wave.mean(axis=1), color=SERIES[0], label="Wave (mean of 6 sites, band = range)")
    ax1.plot(m.index, m.onwind_cf if "onwind_cf" in m else m.wind_cf, color=SERIES[2], label="Onshore wind (Kahuku)")
    ax1.plot(m.index, m.solar_cf, color=SERIES[3], label="Solar")
    ax1.plot(m.index, m.offwind_cf, color=SERIES[5], label="Offshore wind (O'ahu South)")
    ax1.set(title="Monthly mean capacity factor, O'ahu (2019 weather; wave year-agnostic)", ylabel="Capacity factor",
            ylim=(0, 1.0), xticks=m.index, xticklabels=MONTHS)
    ax1.legend(loc="upper center", ncol=2, fontsize=8.5)
    ax2.plot(m.index, m.load_mw, color=INK2)
    ax2.set(title="Monthly mean gross demand (placeholder)", ylabel="MW", xticks=m.index, xticklabels=MONTHS,
            ylim=(0, m.load_mw.max() * 1.15))
    fig.tight_layout()
    fig.savefig(RESULTS / "fig_monthly_resources.png", dpi=160)


def fig_dispatch(tag="rps_moderate_2045"):
    n = pypsa.Network(RESULTS / "networks" / f"{tag}.nc")
    g = n.generators_t.p.T.groupby(n.generators.carrier).sum().T
    get = lambda c: g[c] if c in g else 0.0
    sto = n.storage_units_t.p.sum(axis=1)
    stack = pd.DataFrame({
        "Wave": -n.links_t.p1.filter(like="cable_").sum(axis=1),      # delivered onshore
        "Waste + biodiesel": get("msw") + get("biodiesel"),
        "Onshore wind": get("onwind"), "Solar (utility + rooftop)": get("solar") + get("rooftop_solar"),
        "Battery discharge": sto.clip(lower=0), "Offshore wind": get("offwind"),
        "Oil (LSFO + diesel)": get("lsfo") + get("diesel"),
    })
    stack = stack.loc[:, stack.abs().sum() > 1]
    color = {lab: c for lab, c, _, _ in TECH} | {"Battery discharge": SERIES[4]}
    load = n.loads_t.p_set.sum(axis=1)
    charge = sto.clip(upper=0)
    weeks = {"Winter week (Jan 14–21)": ("2019-01-14", "2019-01-21"),
             "Summer week (Jul 15–22)": ("2019-07-15", "2019-07-22")}
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6), sharey=True)
    for ax, (title, (a, b)) in zip(axes, weeks.items()):
        s = stack.loc[a:b]
        ax.stackplot(s.index, s.T.values, colors=[color[c] for c in s.columns], labels=s.columns,
                     edgecolor=SURFACE, linewidth=0.6, step="post")
        ax.fill_between(charge.loc[a:b].index, charge.loc[a:b].values, 0, color=SERIES[4], alpha=0.4, step="post",
                        lw=0, label="Battery charging")
        ax.step(load.loc[a:b].index, load.loc[a:b].values, where="post", color=INK, lw=1.6, label="Demand")
        ax.set(title=title, ylabel="MW")
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=8, fontsize=8.5)
    fig.suptitle(f"O'ahu dispatch, {tag.replace('_', ' ')} (UTC; HST = UTC - 10)", x=0.01, ha="left",
                 fontweight="bold")
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(RESULTS / "fig_dispatch_weeks.png", dpi=160)


if __name__ == "__main__":
    df = pd.read_csv(RESULTS / "pathways.csv")
    fig_pathways(df)
    fig_wave_by_year(df)
    fig_buildcurve()
    fig_wave_value()
    fig_monthly()
    fig_dispatch()
