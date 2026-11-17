"""All tunable assumptions for the Oahu wave model, in one place.

Units: MW, MWh, USD (2024-ish), $/kW for overnight capex, $/MMBtu for fuel.
Every number here is a placeholder meant to be replaced as better data arrives.
"""

# --- Finance -------------------------------------------------------------
DISCOUNT_RATE = 0.07

# --- Wave resource -------------------------------------------------------
WAVE_RATED_KW = 400            # nameplate of the device behind the power curves
WAVE_AVAILABILITY = 0.95       # downtime for O&M / weather windows
WAVE_ARRAY_LOSS = 0.95         # array interaction + intra-array electrical losses

# Capacity limit per site (MW). 100 MW = 250 devices x 0.4 MW.
# Override individual sites here; anything not listed uses the default.
WAVE_CAP_DEFAULT_MW = 100
WAVE_CAP_MW = {
    # "WETS": 20,              # e.g. if the Navy test site stays small
}

WAVE_CAPEX_PER_KW = 8000       # base case; the sweep below overrides it
WAVE_FOM_FRAC = 0.04           # fixed O&M as fraction of capex per year
WAVE_LIFETIME = 25
WAVE_CAPEX_SWEEP = [2000, 3000, 4000, 5000, 5500, 6000, 8000, 10000, 12000, 15000, 20000]

# --- Export cables (offshore hub -> onshore landing) ---------------------
CABLE_COST_PER_MW_KM = 4000    # $/MW/km subsea cable, installed
CABLE_LANDFALL_PER_MW = 150_000  # $/MW onshore substation + landfall
CABLE_LIFETIME = 40
CABLE_EFFICIENCY = 0.98
CABLE_ROUTING_FACTOR = 1.2     # route length / straight-line distance

# Approximate onshore interconnection points (lat, lon). Each wave site is
# connected to the nearest one; only cable length depends on this.
LANDING_POINTS = {
    "Waimanalo": (21.340, -157.710),
    "Kaneohe":   (21.410, -157.800),
    "Kahuku":    (21.680, -157.970),
    "Waialua":   (21.575, -158.120),
    "Kahe":      (21.355, -158.130),
}

# --- Demand (synthetic placeholder: replace with HECO hourly data) -------
ANNUAL_DEMAND_TWH = 7.0
UTC_OFFSET_HOURS = -10         # Hawaii Standard Time, no DST

# --- Weather year for solar/wind (wave curves are year-agnostic) --------
WEATHER_YEAR = 2019

# --- Fuels ($/MMBtu) and emissions (t CO2 / MWh_thermal) -----------------
FUEL_PRICE = {"lsfo": 17.0, "diesel": 24.0, "biodiesel": 40.0}
CO2_PER_MWH_TH = {"lsfo": 0.27, "diesel": 0.25, "biodiesel": 0.0}
MMBTU_PER_MWH = 3.412

# --- Existing fleet (approximate aggregates; check against EIA-860 / HECO)
EXISTING_THERMAL = {
    # name: (MW, efficiency, fuel, VOM $/MWh)
    "oil_steam":  (1050, 0.31, "lsfo", 6.0),      # Kahe + Waiau steam units
    "oil_cc":     (210, 0.43, "lsfo", 5.0),       # Kalaeloa combined cycle
    "oil_ct":     (100, 0.28, "diesel", 8.0),     # Waiau CTs
    "biofuel_ct": (165, 0.30, "biodiesel", 8.0),  # CIP CT-1 + Schofield
}
WASTE_MW = 68                  # H-POWER, run as near-baseload
EXISTING_SOLAR_MW = 1000       # utility + rooftop
EXISTING_WIND_MW = 123
EXISTING_BATTERY_MW = 300
EXISTING_BATTERY_HOURS = 4

# --- New-build options: (capex $/kW, FOM frac/yr, lifetime, max new MW) --
NEW_SOLAR = (1800, 0.014, 30, 1500)
NEW_WIND = (2800, 0.016, 25, 200)
NEW_BATTERY = (1500, 0.025, 15, None)   # 4-hour, capex per kW of power
NEW_BATTERY_HOURS = 4
BATTERY_ONE_WAY_EFF = 0.92              # ~85% round trip
NEW_FIRM_BIOFUEL = (1300, 0.02, 30, None)  # new biodiesel CTs, eff 0.33

VOLL = 10_000                  # $/MWh, unserved energy penalty
