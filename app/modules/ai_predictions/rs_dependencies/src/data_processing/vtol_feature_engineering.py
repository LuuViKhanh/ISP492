"""
vtol_feature_engineering.py
===========================
Phase 9 — VTOL Flight-Level Feature Engineering

Aggregates per-timestep VTOL data to per-flight features.
Uses shared target_calculation.py for consistent target definitions.

Input:  data/processed/vtol_cleaned_flight_data.csv
Output: output/data/features/vtol_flight_level_features.csv
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import warnings
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import DATA_PROCESSED_DIR, OUT_DATA_FEATURES, OUT_FIG_FEATURE
from config.paths import ensure_dirs
from src.data_processing.target_calculation import (
    calculate_flight_duration,
    calculate_battery_energy,
    calculate_distance_haversine,
    calculate_distance_euclidean,
    calculate_energy_efficiency,
    circular_mean,
    compute_relative_wind_angle,
    compute_planned_wind_features,
)

warnings.filterwarnings('ignore')

# ============================================================
# Setup
# ============================================================
ensure_dirs()

print("=" * 70)
print("VTOL FEATURE ENGINEERING & FLIGHT-LEVEL AGGREGATION")
print("=" * 70)

# ============================================================
# Load Cleaned VTOL Data
# ============================================================
in_path = Path(DATA_PROCESSED_DIR) / "vtol_cleaned_flight_data.csv"
df = pd.read_csv(in_path)
print(f"\nLoaded VTOL cleaned data: {df.shape[0]:,} rows × {df.shape[1]} cols")
print(f"Unique flights: {df['flight'].nunique()}")

# ============================================================
# Determine Position Convention
# ============================================================
# Check if positions are geographic (lon/lat) or local (meters)
x_range = df["position_x"].max() - df["position_x"].min()
y_range = df["position_y"].max() - df["position_y"].min()
x_min = df["position_x"].min()
x_max = df["position_x"].max()
y_min = df["position_y"].min()
y_max = df["position_y"].max()

# Geographic: lon in [-180,180], lat in [-90,90], range < 1 degree
is_geographic = (
    -180 <= x_min and x_max <= 180
    and -90 <= y_min and y_max <= 90
    and x_range < 1 and y_range < 1
)

if is_geographic:
    POSITION_CONVENTION = "GEOGRAPHIC"
    print(f"\nPosition convention: GEOGRAPHIC (3D Haversine distance)")
    print(f"  X(lon): [{x_min:.6f}, {x_max:.6f}], range={x_range:.6f} deg")
    print(f"  Y(lat): [{y_min:.6f}, {y_max:.6f}], range={y_range:.6f} deg")
else:
    POSITION_CONVENTION = "LOCAL_XYZ"
    print(f"\nPosition convention: LOCAL_XYZ (Euclidean 3D distance)")
    print(f"  X: [{x_min:.2f}, {x_max:.2f}], range={x_range:.2f} m")
    print(f"  Y: [{y_min:.2f}, {y_max:.2f}], range={y_range:.2f} m")

# ============================================================
# Detect battery current sign convention
# ============================================================
pct_positive = (df["battery_current"] > 0).sum() / len(df) * 100
pct_negative = (df["battery_current"] < 0).sum() / len(df) * 100
NORMALIZE_CURRENT_SIGN = pct_negative > pct_positive
print(f"\nBattery current: {pct_positive:.1f}% positive, {pct_negative:.1f}% negative")
print(f"  Normalize sign (abs): {NORMALIZE_CURRENT_SIGN}")

# ============================================================
# Aggregate Per-Flight Features
# ============================================================
print(f"\nAggregating per-flight features...")
flight_features = []

for flight_id in sorted(df["flight"].unique()):
    fdata = df[df["flight"] == flight_id].sort_values("time").reset_index(drop=True)
    
    if len(fdata) < 2:
        continue
    
    # Metadata
    date = fdata["date"].iloc[0]
    
    # Pre-flight parameters
    speed = fdata["speed"].iloc[0]
    altitude = fdata["altitude"].iloc[0]
    payload = fdata["payload"].iloc[0]
    
    # VTOL-specific
    pattern = fdata["pattern"].iloc[0] if "pattern" in fdata.columns else np.nan
    battery_discharge_rate = fdata["battery_discharge_rate"].iloc[0] if "battery_discharge_rate" in fdata.columns else np.nan
    condition = fdata["condition"].iloc[0] if "condition" in fdata.columns else np.nan
    
    # ── Shared Target Calculations ──────────────────────────────
    flight_duration = calculate_flight_duration(fdata["time"].values)
    
    # Distance: auto-select based on position convention
    if POSITION_CONVENTION == "GEOGRAPHIC":
        distance = calculate_distance_haversine(
            fdata["position_y"].values,
            fdata["position_x"].values,
            fdata["position_z"].values,
            times=fdata["time"].values,
            max_gap_sec=30.0
        )
    else:
        distance = calculate_distance_euclidean(
            fdata["position_x"].values,
            fdata["position_y"].values,
            fdata["position_z"].values,
            times=fdata["time"].values,
            max_gap_sec=30.0
        )
    
    battery_consumed_wh = calculate_battery_energy(
        fdata["battery_voltage"].values,
        fdata["battery_current"].values,
        fdata["time"].values,
        normalize_sign=NORMALIZE_CURRENT_SIGN,
        max_gap_sec=30.0,
    )
    
    energy_efficiency = calculate_energy_efficiency(distance, battery_consumed_wh)
    
    # ── Weather Aggregations ────────────────────────────────────
    temperature_c = fdata["temperature_c"].mean() if "temperature_c" in fdata.columns else np.nan
    dew_point_c = fdata["dew_point_c"].mean() if "dew_point_c" in fdata.columns else np.nan
    humidity_pct = fdata["humidity_pct"].mean() if "humidity_pct" in fdata.columns else np.nan
    avg_wind_speed = fdata["wind_speed"].mean()
    wind_speed_ms = fdata["wind_speed_ms"].mean() if "wind_speed_ms" in fdata.columns else np.nan
    wind_gust_ms = fdata["wind_gust_ms"].max() if "wind_gust_ms" in fdata.columns else np.nan
    wind_dir_deg = circular_mean(fdata["wind_dir_deg"].dropna().values) if "wind_dir_deg" in fdata.columns and fdata["wind_dir_deg"].notna().any() else np.nan
    precipitation_mm = fdata["precipitation_mm"].mean() if "precipitation_mm" in fdata.columns else np.nan
    pressure_hpa = fdata["pressure_hpa"].mean() if "pressure_hpa" in fdata.columns else np.nan
    cloud_cover_pct = fdata["cloud_cover_pct"].mean() if "cloud_cover_pct" in fdata.columns else np.nan
    
    # Legacy observed relative wind angle: analysis only (uses actual trajectory).
    relative_wind_angle = compute_relative_wind_angle(fdata)

    # Route-direction proxy aerodynamic features. Historical endpoint coordinates
    # are used here for ablation; Multi-Hub must recompute from planned graph edges.
    start_x, start_y = fdata["position_x"].iloc[0], fdata["position_y"].iloc[0]
    end_x, end_y = fdata["position_x"].iloc[-1], fdata["position_y"].iloc[-1]
    planned_rel_wind, planned_headwind, planned_crosswind = compute_planned_wind_features(
        start_x, start_y, end_x, end_y, wind_dir_deg, wind_speed_ms,
        coordinate_mode=POSITION_CONVENTION,
    )
    if POSITION_CONVENTION == "GEOGRAPHIC":
        route_endpoint_displacement_m = calculate_distance_haversine(
            [start_y, end_y], [start_x, end_x],
            [fdata["position_z"].iloc[0], fdata["position_z"].iloc[-1]],
        )
    else:
        route_endpoint_displacement_m = calculate_distance_euclidean(
            [start_x, end_x], [start_y, end_y],
            [fdata["position_z"].iloc[0], fdata["position_z"].iloc[-1]],
        )
    route_endpoint_to_path_ratio = (
        route_endpoint_displacement_m / distance if distance > 0 else np.nan
    )
    route_geometry_source = "OBSERVED_ENDPOINT_PROXY"
    
    flight_features.append({
        "flight": flight_id,
        "route": np.nan,  # VTOL has no route concept — keep as NaN per plan
        "date": date,
        "speed": speed,
        "altitude": altitude,
        "payload": payload,
        "flight_duration": flight_duration,
        "distance": distance,
        "temperature_c": temperature_c,
        "dew_point_c": dew_point_c,
        "humidity_pct": humidity_pct,
        "avg_wind_speed": avg_wind_speed,
        "wind_speed_ms": wind_speed_ms,
        "wind_gust_ms": wind_gust_ms,
        "wind_dir_deg": wind_dir_deg,
        "precipitation_mm": precipitation_mm,
        "pressure_hpa": pressure_hpa,
        "cloud_cover_pct": cloud_cover_pct,
        "relative_wind_angle": relative_wind_angle,
        "planned_relative_wind_angle": planned_rel_wind,
        "planned_headwind_component": planned_headwind,
        "planned_crosswind_component": planned_crosswind,
        "route_endpoint_displacement_m": route_endpoint_displacement_m,
        "route_endpoint_to_path_ratio": route_endpoint_to_path_ratio,
        "route_geometry_source": route_geometry_source,
        "battery_consumed_wh": battery_consumed_wh,
        "energy_efficiency": energy_efficiency,
        # VTOL-specific
        "pattern": pattern,
        "battery_discharge_rate": battery_discharge_rate,
        "condition": condition,
    })

features_df = pd.DataFrame(flight_features)

# Remove flights with invalid energy efficiency
n_before = len(features_df)
features_df = features_df.dropna(subset=["energy_efficiency"])
n_after = len(features_df)
if n_before != n_after:
    print(f"Removed {n_before - n_after} flights with invalid energy efficiency")

# Remove flights with negative/zero energy (sanity)
n_before2 = len(features_df)
features_df = features_df[features_df["battery_consumed_wh"] > 0]
n_after2 = len(features_df)
if n_before2 != n_after2:
    print(f"Removed {n_before2 - n_after2} flights with non-positive battery energy")

print(f"\nAggregated: {len(features_df)} flights × {features_df.shape[1]} columns")

# ============================================================
# Print Summary Statistics
# ============================================================
print(f"\nVTOL FEATURE SUMMARY")
print("-" * 60)

summary_cols = [
    "speed", "altitude", "payload", "flight_duration", "distance",
    "temperature_c", "humidity_pct", "avg_wind_speed", "wind_gust_ms",
    "pressure_hpa", "battery_consumed_wh", "energy_efficiency",
]
for col in summary_cols:
    if col in features_df.columns and features_df[col].notna().any():
        vals = features_df[col].dropna()
        print(f"  {col:25s}: mean={vals.mean():.2f}, std={vals.std():.2f}, range=[{vals.min():.2f}, {vals.max():.2f}]")

# ============================================================
# Save VTOL Flight-Level Features
# ============================================================
out_feat_csv = Path(OUT_DATA_FEATURES) / "vtol_flight_level_features.csv"
features_df.to_csv(out_feat_csv, index=False)
print(f"\nSaved: {out_feat_csv} ({features_df.shape[0]} rows × {features_df.shape[1]} cols)")

print(f"\n{'=' * 70}")
print(f"VTOL FEATURE ENGINEERING COMPLETE")
print(f"{'=' * 70}")
