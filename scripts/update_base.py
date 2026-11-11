"""Copy the Snakemake-built networks of one model year into base/ so the notebook runs without Snakemake.

Run after rebuilding the networks with Snakemake (see ASSUMPTIONS.md), from the repo root:

    pypsa-usa/.venv/bin/python scripts/update_base.py [YEAR]     # YEAR = 2035 (default) or 2040

base/ holds, per year:
    network_<YEAR>.nc            prepared (unsolved) 4-zone network, input of the solve
    config_<YEAR>.yaml           merged config Snakemake used for that network
    network_<YEAR>_hybrid25.nc   prepared 25-node network, if built
    config_<YEAR>_hybrid25.yaml  its merged config, read from the network
and, from the 2035 Snakemake solve only:
    snakemake_reference.yaml     objective and capacities of the Snakemake solve (reproduction check)
    geo/                         zone and state shapes for the network map
"""

import logging
import os
import shutil
import sys
from pathlib import Path

import pypsa
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("PYPSA_USA_DIR", ROOT / "pypsa-usa"))
WORKFLOW = REPO / "workflow"

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2035
RUN = f"california-{YEAR}"
NETWORK_NAME = "elec_s75_c4_ec_lv1.0_REM-ERM-3h_E"
CONFIG_NAME = "config.elec_s75_c4_lv1.0_REM-ERM-3h_E.yaml"

BASE = ROOT / "base"
(BASE / "geo").mkdir(parents=True, exist_ok=True)

logging.disable(logging.WARNING)


def without_api_keys(config):
    """API keys must stay out of git: they are only needed to build networks, from config.api.yaml."""
    return {**config, "api": dict.fromkeys(config["api"])}


n = pypsa.Network(WORKFLOW / f"resources/{RUN}/networks/western/{NETWORK_NAME}.nc")
n.meta = without_api_keys(n.meta)
n.export_to_netcdf(BASE / f"network_{YEAR}.nc")
solved_config = WORKFLOW / f"results/{RUN}/western/configs/{CONFIG_NAME}"
# Snakemake writes the merged config only when it solves; otherwise it is read from the network
config = yaml.safe_load(solved_config.read_text()) if solved_config.exists() else n.meta
(BASE / f"config_{YEAR}.yaml").write_text(yaml.safe_dump(without_api_keys(config), sort_keys=False))

if YEAR == 2035:  # the notebook's reproduction check and map shapes come from the 2035 Snakemake solve
    for shape in ["reeds_shapes.geojson", "state_boundaries.geojson"]:
        shutil.copy(WORKFLOW / f"resources/{RUN}/geospatial/western/{shape}", BASE / "geo" / shape)
    n = pypsa.Network(WORKFLOW / f"results/{RUN}/western/networks/{NETWORK_NAME}.nc")
    capacity = (
        n.generators.groupby("carrier").p_nom_opt.sum().add(n.storage_units.groupby("carrier").p_nom_opt.sum(), fill_value=0)
        / 1e3
    )
    reference = {
        "objective": float(n.objective),
        "capacity_GW": {k: round(float(v), 4) for k, v in capacity.items()},
    }
    (BASE / "snakemake_reference.yaml").write_text(yaml.safe_dump(reference, sort_keys=True))

hybrid = WORKFLOW / f"resources/{RUN}-county/networks/western/elec_scounty_c25_ec_lv1.0_REM-ERM-3h_E.nc"
if hybrid.exists():
    n = pypsa.Network(hybrid)
    n.meta = without_api_keys(n.meta)
    n.meta["wildcards"]["sector"] = "E"  # the network's config predates the add_sectors wildcard
    n.export_to_netcdf(BASE / f"network_{YEAR}_hybrid25.nc")
    (BASE / f"config_{YEAR}_hybrid25.yaml").write_text(yaml.safe_dump(n.meta, sort_keys=False))
print(f"Updated {BASE}")
