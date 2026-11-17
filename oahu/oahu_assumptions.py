"""O'ahu wave model v2: every tunable number, with where it comes from.

Source tags:
  [USER]        your inputs (wave curves, coordinates, wave cost table, lifetime, site cap)
  [EIA-860]     read from data/raw/eia860 (EIA-860 2023, already on disk)
  [HECO AOS]    Hawaiian Electric, Adequacy of Supply report (Jan 2026), Hawai'i PUC
  [HRS 269-92]  Hawai'i Revised Statutes §269-92, renewable portfolio standard
  [EPA]         EPA GHG Emission Factors Hub (CO2 per MMBtu of fuel)
  [PLACEHOLDER] my estimate. Replace it from the source named on the same line
                (README, "Data to download").

Units: MW, MWh, real USD, $/kW overnight capex, $/kW-yr fixed O&M, Btu/kWh heat rates, $/MMBtu fuel.
"""

# --- Finance ---------------------------------------------------------------------------------------
DISCOUNT_RATE = 0.07            # [PLACEHOLDER] real, same as the California runs. NREL ATB WACC is lower.

# --- Study years, time and policy --------------------------------------------------------------------
YEARS = [2030, 2040, 2045]      # myopic pathway: each year starts from the fleet built in earlier years
STEP_H = 3                      # wave curves are 3-hourly [USER]
WEATHER_YEAR = 2019             # solar, wind and load weather year. Wave curves are year-agnostic [USER].
UTC_OFFSET_HOURS = -10          # Hawai'i Standard Time, no daylight saving
RPS_TARGET = {2030: 0.40, 2040: 0.70, 2045: 1.00}   # [HRS 269-92] share of net generation
FOSSIL_RETIRE_AT_FULL_RPS = True  # at 100% RPS, oil-only units are deactivated (no capacity credit).
                                  # CIP CT-1 stays, on biodiesel. Set False to keep oil units as idle backup.
POLICIES = ["least_cost", "rps"]

# --- Demand ------------------------------------------------------------------------------------------
# Drop-in: if this file exists, it replaces the synthetic load. Format: 8760 rows, columns
# datetime_hst, load_mw (any single year; hour-of-year is mapped onto WEATHER_YEAR).
LOAD_FILE = "data/oahu/load_hourly.csv"
LOAD_FILE_BASIS = "net"         # "net" = utility system load (rooftop PV already subtracted) or "gross"
LOAD_FILE_ROOFTOP_MW = 700      # rooftop PV in the load file's year, added back when the basis is "net"

# Synthetic placeholder (used only without LOAD_FILE): weekday/weekend daily shape and a cooling response
# to daily ERA5 temperature, scaled so annual energy and peak match these two numbers.
GROSS_ENERGY_TWH = 7.6          # [PLACEHOLDER] system sales + losses + rooftop PV output. HECO Key
                                # Performance Metrics (sales by island) + HECO quarterly installed PV.
GROSS_PEAK_MW = 1200            # [PLACEHOLDER] evening peak (rooftop PV is ~0 then). HECO AOS / KPMs.
COOLING_SLOPE = 0.025           # [PLACEHOLDER] +2.5% load per °C of daily-mean temperature above mean
DEMAND_GROWTH = {2030: 1.00, 2040: 1.00, 2045: 1.00}   # [PLACEHOLDER] HECO IGP forecast (EVs, buildings)

ROOFTOP_PV_MW = {2030: 700, 2040: 700, 2045: 700}      # [PLACEHOLDER] HECO quarterly installed solar PV
ROOFTOP_PV_DERATE = 0.90        # rooftop orientation, shading, soiling vs. the utility-PV profile

# --- Existing fleet [EIA-860] -------------------------------------------------------------------------
FLEET_ONLINE_BY = 2025          # include EIA-860 "Proposed" units expected online by this year
EXCLUDE_PLANTS = ["Tesoro Hawaii", "Hawaii Cogen"]     # refinery self-generation, not utility supply
# Units deactivated before a study year, e.g. {"Waiau W3": 2028, "Waiau W4": 2028}. [PLACEHOLDER]
# HECO AOS lists planned deactivations. None are assumed, so every year starts with the 2025 fleet.
RETIREMENTS = {}
# EIA-860 2023 reports Waiawa Solar Hybrid's battery as 4.6 MWh. Assume 4 hours instead. [PLACEHOLDER]
BATTERY_MWH_OVERRIDE = {"Waiawa Solar Power Hybrid": 144.0}

# Heat rates (Btu/kWh), by plant or by "plant unit". [PLACEHOLDER] EIA-923 fuel use / net generation.
HEAT_RATE = {
    "Kahe": 10700, "Kahe K5": 10300, "Kahe K6": 10300,
    "Waiau": 12800, "Waiau W7": 11300, "Waiau W8": 11300, "Waiau W9": 13500, "Waiau W10": 13500,
    "Kalaeloa Cogen Plant": 8800,
    "Campbell Industrial Park": 11800,
    "Schofield Generating Station": 8900,
    "HNL Emergency Power Facility": 10000,
    "H Power": 18000,
}
# EIA energy source code -> model fuel
FUEL_OF = {"RFO": "lsfo", "DFO": "diesel", "OBL": "biodiesel", "MSW": "msw"}
FUEL_SWITCH = {"Campbell Industrial Park": ["diesel", "biodiesel"]}   # CIP CT-1 is biodiesel-capable
FUEL_PRICE = {"lsfo": 17.0, "diesel": 24.0, "biodiesel": 40.0, "msw": 0.0}   # [PLACEHOLDER] EIA-923
                                # Schedule 2 (delivered fuel cost, HI plants). MSW is paid by tipping fees.
CO2_T_PER_MMBTU = {"lsfo": 0.07510, "diesel": 0.07396, "biodiesel": 0.0, "msw": 0.0}   # [EPA] fuel oil
                                # No. 6 and No. 2. Biodiesel and MSW are reported as biogenic here.
VOM = {"steam": 6.0, "cc": 5.0, "ct": 8.0, "ice": 8.0, "msw": 10.0}       # [PLACEHOLDER] $/MWh

# Unit commitment (linearized). Minimum load comes from EIA-860 "Minimum Load (MW)".
UC_CLASSES = ["steam", "cc", "ct"]           # committable classes. ICE and MSW are not.
MIN_UP_H = {"steam": 12, "cc": 6, "ct": 3}   # [PLACEHOLDER] HECO unit data or NREL cycling-cost study
MIN_DOWN_H = {"steam": 9, "cc": 3, "ct": 3}
START_COST_PER_MW = {"steam": 120.0, "cc": 60.0, "ct": 25.0}   # [PLACEHOLDER] $/MW per start (NREL
                                # "Power Plant Cycling Costs", Kumar et al. 2012)
MSW_MIN_PU = 0.70               # H-POWER runs near baseload to dispose of waste
MSW_MAX_PU = 0.90

# --- Reliability ---------------------------------------------------------------------------------------
ERM_MARGIN = 0.30               # [HECO AOS] 30% energy reserve margin used for O'ahu. Checked every step:
                                # available capacity >= (1 + margin) x load. Variable resources count
                                # at their availability in that step, storage at what its charge can sustain.
ERM_STORAGE_HOURS = 3           # storage counts in the margin only if its charge can sustain it this long
ERM_FIRM_DERATE = 1.0           # firm units count at full capacity. The margin itself covers outages.
CONTINGENCY_MW = 135            # [PLACEHOLDER] spinning reserve every step, about the largest unit
                                # (Kahe 5/6, 128 MW summer). HECO operating reserve rules (PSIP / IGP).
QUICK_START_EXCLUDE = ["HNL Emergency Power Facility"]   # airport emergency units: margin only
RESERVE_STORAGE_HOURS = 1.0     # batteries must hold this many hours of energy behind their reserve
VOLL = 10_000                   # $/MWh unserved energy

# --- New-build options ---------------------------------------------------------------------------------
# Mainland overnight capex ($/kW) by year, interpolated between years, then x HAWAII_COST_MULTIPLIER.
# [PLACEHOLDER] all of these: NREL ATB 2024 moderate (atb.nrel.gov) and the EIA capital-cost report's
# Hawai'i location factor.
HAWAII_COST_MULTIPLIER = 1.35
TECH = {
    "solar":     dict(capex={2030: 1150, 2040: 950, 2045: 900}, fom=22, life=30, max_mw=1500),
    "onwind":    dict(capex={2030: 1500, 2040: 1350, 2045: 1300}, fom=30, life=25, max_mw=200),
    "offwind":   dict(capex={2030: 5000, 2040: 3800, 2045: 3500}, fom=100, life=25, max_mw=800),
    "battery":   dict(capex={2030: 1250, 2040: 1000, 2045: 950}, fom=30, life=15, max_mw=None),  # per kW, 4 h
    "biodiesel": dict(capex={2030: 1400, 2040: 1400, 2045: 1400}, fom=20, life=30, max_mw=None,
                      heat_rate=8800),       # new biodiesel reciprocating engines (quick start)
}
BATTERY_HOURS = 4
BATTERY_ONE_WAY_EFF = 0.92       # ~85% round trip
# Utility-scale solar land limit is TECH["solar"]["max_mw"] of new capacity. [PLACEHOLDER] HECO IGP
# resource potential (Workbook) or NREL Hawai'i land-availability studies.

# Floating offshore wind, BOEM O'ahu South call area (approximate centroid). [PLACEHOLDER] location.
OFFSHORE_WIND_SITE = dict(lat=21.07, lon=-157.85, landing="Honolulu")
OFFSHORE_WIND_LOSSES = 0.85      # wake, electrical, availability

# --- Wave --------------------------------------------------------------------------------------------
WAVE_COST_FILE = "data/wave_costs.csv"       # [USER] CapEx $M/MW and OpEx $M/MW-yr by year and scenario
WAVE_SCENARIOS = ["aggressive", "moderate", "conservative"]
WAVE_LIFETIME = 20               # [USER]
WAVE_MARGINAL_COST = 0.0         # [USER]
WAVE_CAP_DEFAULT_MW = 3800       # [USER] the 3.8 GW per-site cap you gave for California, applied here too
WAVE_CAP_MW = {}                 # per-site overrides, e.g. {"WETS": 20}
WAVE_RATED_KW = 400              # [USER] nameplate behind the power curves
WAVE_AVAILABILITY = 0.95         # [PLACEHOLDER] O&M and weather-window downtime
WAVE_ARRAY_LOSS = 0.95           # [PLACEHOLDER] array interaction + intra-array electrical losses

# CapEx sweep for the 2045 build curve ($M/MW). OpEx = 0.4% of CapEx, the ratio in your cost table.
WAVE_SWEEP_CAPEX = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0]
WAVE_SWEEP_OPEX_FRAC = 0.004
WAVE_CAPEX_PER_KW = 3000         # default sweep point shown on the map ($/kW)

# Export cables (offshore hub -> onshore landing)
CABLE_COST_PER_MW_KM = 4000      # [PLACEHOLDER] $/MW/km installed subsea cable
CABLE_LANDFALL_PER_MW = 150_000  # [PLACEHOLDER] $/MW onshore substation + landfall
CABLE_LIFETIME = 40
CABLE_EFFICIENCY = 0.98
CABLE_ROUTING_FACTOR = 1.2       # route length / straight-line distance

# Onshore interconnection points (lat, lon). Each site connects to the nearest one. [PLACEHOLDER]
LANDING_POINTS = {
    "Waimanalo": (21.340, -157.710),
    "Kaneohe":   (21.410, -157.800),
    "Kahuku":    (21.680, -157.970),
    "Waialua":   (21.575, -158.120),
    "Kahe":      (21.355, -158.130),
    "Honolulu":  (21.300, -157.870),
}
WAVE_LANDINGS = ["Waimanalo", "Kaneohe", "Kahuku", "Waialua", "Kahe"]
