"""One-at-a-time sensitivities on the 2045, 100%-RPS result: no-wave and moderate-wave RPS pathways rerun
with one assumption changed. Writes results/oahu/sensitivity.csv. Run after run_oahu.py."""
import importlib
import logging

import pandas as pd

import oahu_assumptions as A
import run_oahu as R


def solar_wind_land(): A.TECH["solar"]["max_mw"], A.TECH["onwind"]["max_mw"] = 3000, 400
def rate_5pct(): A.DISCOUNT_RATE = 0.05
def no_margin(): A.ERM_MARGIN = 0.0
def biodiesel_cheap(): A.FUEL_PRICE["biodiesel"] = 25.0
def keep_oil_backup(): A.FOSSIL_RETIRE_AT_FULL_RPS = False


CASES = {  # name: (description, assumption change, wave output scale)
    "base": ("assumptions as in oahu_assumptions.py", None, 1.0),
    "wave_output_x0.75": ("wave output 25% lower (real-year variability / lower device CF)", None, 0.75),
    "land_3000_solar_400_wind": ("new-solar cap 1,500 -> 3,000 MW, new-wind cap 200 -> 400 MW", solar_wind_land, 1.0),
    "discount_5pct": ("discount rate 7% -> 5%", rate_5pct, 1.0),
    "no_reserve_margin": ("energy reserve margin 30% -> 0%", no_margin, 1.0),
    "biodiesel_25": ("biodiesel $40 -> $25/MMBtu", biodiesel_cheap, 1.0),
    "oil_kept_as_backup": ("oil units kept (idle, count toward the margin) at 100% RPS", keep_oil_backup, 1.0),
}


def main():
    for name in ["pypsa", "linopy", "gurobipy"]:
        logging.getLogger(name).setLevel(logging.ERROR)
    solver = R.pick_solver("auto")
    ts0 = pd.read_csv(R.INPUTS / "timeseries.csv", index_col=0, parse_dates=True)
    sites = pd.read_csv(R.INPUTS / "wave_sites.csv", index_col=0)
    fleet = pd.read_csv(R.INPUTS / "fleet.csv", index_col=0)
    R.NETS = R.RESULTS / "sensitivity_networks"
    rows = []
    for case, (desc, change, wave_scale) in CASES.items():
        importlib.reload(A)
        if change:
            change()
        ts = ts0.copy()
        wave_cols = ts.columns[ts.columns.str.startswith("wave_cf_")]
        ts[wave_cols] *= wave_scale
        out = {}
        for wave in [None, "moderate"]:
            r, _, _ = R.run_pathway(ts, sites, fleet, "rps", wave, solver)
            out[wave or "none"] = r[-1]
        rows.append(dict(case=case, description=desc,
                         cost_no_wave_musd=out["none"]["system_cost_musd"],
                         cost_moderate_wave_musd=out["moderate"]["system_cost_musd"],
                         saving_musd=out["none"]["system_cost_musd"] - out["moderate"]["system_cost_musd"],
                         wave_mw_2045=out["moderate"]["wave_mw"], wave_share_pct=100 * out["moderate"]["wave_gwh"]
                         / out["moderate"]["load_twh"] / 1e3, erm_price_no_wave=out["none"]["erm_price_kw_yr"]))
        print(rows[-1])
    importlib.reload(A)
    pd.DataFrame(rows).round(1).to_csv(R.RESULTS / "sensitivity.csv", index=False)


if __name__ == "__main__":
    main()
