"""All tunable assumptions for the West Coast (WA/OR/CA) wave model.

Units: MW, MWh, USD, $/kW for overnight capex, $/MMBtu for fuel.
Demand, hydro and the existing fleet come from EIA data (2023); everything here
is a placeholder meant to be replaced as better numbers arrive.
"""

YEAR = 2023                    # demand, hydro, fleet and weather year (wave curves are year-agnostic)
DISCOUNT_RATE = 0.07

# --- Zones ---------------------------------------------------------------
ZONES = ["PNW", "CA_N", "CA_S"]
ZONE_XY = {"PNW": (-121.5, 45.5), "CA_N": (-121.0, 38.5), "CA_S": (-117.8, 34.3)}  # map/plot anchors

# EIA-930 balancing authorities per zone (demand + hydro). CISO is split by sub-region.
ZONE_BAS = {
    "PNW": ["BPAT", "PACW", "PGE", "PSEI", "SCL", "TPWR", "AVA", "CHPD", "DOPD", "GCPD",
            "AVRN", "GRID"],
    "CA_N": ["BANC", "TIDC"],
    "CA_S": ["LDWP", "IID"],
}
CISO_SUBREGIONS = {"CA_N": ["PGAE"], "CA_S": ["SCE", "SDGE", "VEA"]}


def ciso_zone(lat, lon):
    """Rough PG&E (north) vs SCE/SDGE (south) split for CAISO plants."""
    south = (lat < 35.0) | (lon > -119.0) | ((lat < 35.5) & (lon > -120.0))
    return south.map({True: "CA_S", False: "CA_N"})


# --- Wave resource -------------------------------------------------------
WAVE_RATED_KW = 400
WAVE_AVAILABILITY = 0.95
WAVE_ARRAY_LOSS = 0.95

WAVE_CAP_DEFAULT_MW = 500      # 1,250 devices x 0.4 MW per site
WAVE_CAP_MW = {"PACWAVE": 20}  # PACWAVE South is permitted as a 20 MW test site

WAVE_CAPEX_PER_KW = 8000       # base case used for maps; the sweep overrides it
WAVE_FOM_FRAC = 0.04
WAVE_LIFETIME = 25
WAVE_CAPEX_SWEEP = [3000, 4000, 5000, 6000, 8000, 10000, 12000, 15000]

# --- Export cables (offshore hub -> onshore landing) ---------------------
CABLE_COST_PER_MW_KM = 4000
CABLE_LANDFALL_PER_MW = 150_000
CABLE_LIFETIME = 40
CABLE_EFFICIENCY = 0.98
CABLE_ROUTING_FACTOR = 1.2

# Approximate coastal landing points (lat, lon, zone). Each wave site uses the nearest.
LANDING_POINTS = {
    "Port Angeles":  (48.10, -123.43, "PNW"),
    "Aberdeen":      (46.97, -123.80, "PNW"),
    "Astoria":       (46.19, -123.83, "PNW"),
    "Newport OR":    (44.63, -124.05, "PNW"),
    "Gold Beach":    (42.41, -124.42, "PNW"),
    "Humboldt Bay":  (40.74, -124.21, "CA_N"),
    "Bodega Bay":    (38.33, -123.05, "CA_N"),
    "Half Moon Bay": (37.46, -122.43, "CA_N"),
    "Morro Bay":     (35.37, -120.85, "CA_N"),
    "Gaviota":       (34.47, -120.22, "CA_S"),
}

# --- Solar / wind -------------------------------------------------------
# Existing and new onshore solar/wind use 2023 EIA-930 reported output / EIA-860 nameplate.
# Offshore wind (no fleet yet) uses ERA5 100 m wind speed at these points via Open-Meteo.
OFFSHORE_WIND = {               # name: (lat, lon, zone, max new MW)
    "osw_oregon":   (43.2, -124.8, "PNW", 3000),
    "osw_humboldt": (40.9, -124.65, "CA_N", 1600),
    "osw_morro":    (35.6, -121.9, "CA_N", 3000),
}

# --- Inter-zone transmission (bidirectional, extendable) -----------------
# name: (bus0, bus1, existing MW, length km)
CORRIDORS = {
    "COI":     ("PNW", "CA_N", 4800, 650),    # California-Oregon Intertie (AC)
    "PDCI":    ("PNW", "CA_S", 3220, 1360),   # Pacific DC Intertie, Celilo-Sylmar
    "Path26":  ("CA_N", "CA_S", 4000, 400),
}
TRANSMISSION_COST_PER_MW_KM = 1000
TRANSMISSION_LIFETIME = 50

# --- Fuels ($/MMBtu) and emissions (t CO2 / MWh_thermal) -----------------
FUEL_PRICE = {"gas": 5.0, "coal": 2.0, "oil": 20.0, "h2": 20.0, "biomass": 3.0}
CO2_PER_MWH_TH = {"gas": 0.181, "coal": 0.34, "oil": 0.25, "h2": 0.0, "biomass": 0.0}
MMBTU_PER_MWH = 3.412

# Existing thermal: carrier -> (efficiency, fuel, VOM $/MWh, p_min_pu, p_max_pu)
THERMAL_PARAMS = {
    "gas_cc":     (0.50, "gas", 3.0, 0.0, 1.0),
    "gas_ct":     (0.33, "gas", 5.0, 0.0, 1.0),
    "coal":       (0.33, "coal", 5.0, 0.0, 1.0),
    "oil":        (0.30, "oil", 8.0, 0.0, 1.0),
    "biomass":    (0.25, "biomass", 10.0, 0.0, 0.7),
    "nuclear":    (0.33, None, 10.0, 0.9, 1.0),
    "geothermal": (1.0, None, 5.0, 0.7, 0.85),
}

# EIA-860 technology -> model carrier
TECH_MAP = {
    "Conventional Hydroelectric": "hydro",
    "Hydroelectric Pumped Storage": "phs",
    "Natural Gas Fired Combined Cycle": "gas_cc",
    "Natural Gas Fired Combustion Turbine": "gas_ct",
    "Natural Gas Internal Combustion Engine": "gas_ct",
    "Natural Gas Steam Turbine": "gas_ct",
    "Other Natural Gas": "gas_ct",
    "Other Gases": "gas_ct",
    "Natural Gas with Compressed Air Storage": "gas_ct",
    "Nuclear": "nuclear",
    "Conventional Steam Coal": "coal",
    "Coal Integrated Gasification Combined Cycle": "coal",
    "Petroleum Liquids": "oil",
    "Petroleum Coke": "oil",
    "Onshore Wind Turbine": "wind",
    "Offshore Wind Turbine": "wind",
    "Solar Photovoltaic": "solar",
    "Solar Thermal with Energy Storage": "solar",
    "Solar Thermal without Energy Storage": "solar",
    "Geothermal": "geothermal",
    "Wood/Wood Waste Biomass": "biomass",
    "Landfill Gas": "biomass",
    "Municipal Solid Waste": "biomass",
    "Other Waste Biomass": "biomass",
    "Batteries": "battery",
}

# --- Hydro and storage ---------------------------------------------------
HYDRO_MAX_HOURS = 168          # reservoir flexibility: one week at full output
HYDRO_MIN_FLOW_FRAC = 0.4      # run-of-river share: 40% of observed output released as it arrives
PHS_MAX_HOURS = 8
PHS_ONE_WAY_EFF = 0.88
EXISTING_BATTERY_HOURS = 4
BATTERY_ONE_WAY_EFF = 0.92

# --- New-build options: (capex $/kW, FOM frac/yr, lifetime, max new MW per zone) --
NEW_SOLAR = (1100, 0.02, 30, {"PNW": 20000, "CA_N": 30000, "CA_S": 60000})
NEW_WIND = (1500, 0.025, 25, {"PNW": 15000, "CA_N": 4000, "CA_S": 8000})
NEW_OFFSHORE_WIND = (5000, 0.02, 30)
NEW_BATTERY = (1200, 0.025, 15)      # 4-hour, capex per kW of power
NEW_BATTERY_HOURS = 4
NEW_CLEAN_FIRM = (1000, 0.02, 30)    # hydrogen-fired CT, eff 0.38
CLEAN_FIRM_EFF = 0.38

VOLL = 10_000
