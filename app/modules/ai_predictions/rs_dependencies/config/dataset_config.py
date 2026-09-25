"""DJI flight-level analysis schema.

ALL_FEATURES is intended for EDA/correlation/VIF, not as the clean pre-flight
model input. Multi-UAV pre-flight modeling uses config.multiuav_config.
"""

FLIGHT_FEATURES = ["speed", "altitude", "payload", "flight_duration", "distance"]

WEATHER_FEATURES = [
    "temperature_c",
    "humidity_pct",
    "avg_wind_speed",
    "wind_gust_ms",
    "wind_dir_deg",
    "pressure_hpa",
    "cloud_cover_pct",
]

ENGINEERED_FEATURES = [
    "relative_wind_angle",                 # observed / post-flight analysis only
    "planned_relative_wind_angle",         # endpoint route-direction proxy in historical data
    "planned_headwind_component",          # +headwind, -tailwind
    "planned_crosswind_component",         # magnitude >= 0
]

ALL_FEATURES = FLIGHT_FEATURES + WEATHER_FEATURES + ENGINEERED_FEATURES
TARGET = "energy_efficiency"
METADATA = ["flight", "route", "date"]
RANDOM_STATE = 42
