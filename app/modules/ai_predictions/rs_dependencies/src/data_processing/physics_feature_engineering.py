"""Physics-informed pre-flight weather feature engineering.

Convention used throughout the current Multi-UAV pipeline:
  planned_relative_wind_angle = 0 deg   -> tailwind
  planned_relative_wind_angle = 90 deg  -> crosswind
  planned_relative_wind_angle = 180 deg -> headwind

Therefore the headwind component is defined positive for headwind:
  H = -W * cos(theta)
and the crosswind magnitude is:
  C = |W * sin(theta)|
"""
import numpy as np
import pandas as pd

Rd = 287.05
Rv = 461.495
EPSILON = 1e-6


def compute_headwind(wind_speed: pd.Series, relative_wind_angle_deg: pd.Series) -> pd.Series:
    """Positive=headwind, negative=tailwind under the 0=tailwind convention."""
    theta_rad = np.radians(relative_wind_angle_deg)
    return -wind_speed * np.cos(theta_rad)


def compute_crosswind(wind_speed: pd.Series, relative_wind_angle_deg: pd.Series) -> pd.Series:
    """Unsigned crosswind magnitude in m/s."""
    theta_rad = np.radians(relative_wind_angle_deg)
    return np.abs(wind_speed * np.sin(theta_rad))


def compute_moist_air_density(temp_c: pd.Series, rh_pct: pd.Series, pressure_hpa: pd.Series) -> pd.Series:
    """Moist-air density rho, kg/m^3, using Tetens saturation vapor pressure."""
    temp_k = temp_c + 273.15
    es = 6.112 * np.exp((17.67 * temp_c) / (temp_c + 243.5))
    e_hpa = (rh_pct / 100.0) * es
    pd_hpa = pressure_hpa - e_hpa
    return (pd_hpa * 100.0 / (Rd * temp_k)) + (e_hpa * 100.0 / (Rv * temp_k))


def attach_physics_features(
    df: pd.DataFrame,
    speed_col="speed",
    wind_speed_col="wind_speed_ms",
    relative_angle_col="planned_relative_wind_angle",
) -> pd.DataFrame:
    """Attach deterministic pre-flight physics proxies.

    Defaults deliberately use forecast wind + route-known relative angle, not
    onboard avg_wind_speed or observed trajectory relative_wind_angle.
    """
    required = [
        speed_col, wind_speed_col, relative_angle_col, "wind_gust_ms",
        "temperature_c", "humidity_pct", "pressure_hpa",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns for physics feature engineering: {missing}")

    out = df.copy()
    out["headwind_component"] = compute_headwind(out[wind_speed_col], out[relative_angle_col])
    out["crosswind_component"] = compute_crosswind(out[wind_speed_col], out[relative_angle_col])
    out["gust_delta"] = out["wind_gust_ms"] - out[wind_speed_col]
    out["gust_ratio"] = out["wind_gust_ms"] / (out[wind_speed_col] + EPSILON)
    out["air_density"] = compute_moist_air_density(
        out["temperature_c"], out["humidity_pct"], out["pressure_hpa"]
    )
    out["effective_airspeed"] = np.sqrt(
        (out[speed_col] + out["headwind_component"]) ** 2
        + out["crosswind_component"] ** 2
    )
    out["aerodynamic_power_proxy"] = out["air_density"] * out["effective_airspeed"] ** 3
    return out
