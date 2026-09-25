"""
feature_engineering.py
======================
Phase 1 - Feature Engineering & Flight-Level Aggregation

Aggregates per-timestep data (~1253 rows/flight) to per-flight features (1 row/flight).
Computes:
  - 3D Haversine cumulative distance
  - Battery consumed (Wh) via trapezoidal power integration
  - Energy Efficiency = distance / battery_consumed_wh (TARGET)
  - Relative Wind Angle (headwind/tailwind indicator)
  - All flight and weather features
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import os
import warnings
from pathlib import Path

from config.paths import DATA_PROCESSED_DIR, OUT_DATA_FEATURES, OUT_FIG_FEATURE
from config.dataset_config import FLIGHT_FEATURES, WEATHER_FEATURES, ENGINEERED_FEATURES, ALL_FEATURES, TARGET
from src.utils.io import ensure_dirs
from src.data_processing.target_calculation import (
    calculate_distance_haversine,
    calculate_battery_energy,
    calculate_energy_efficiency,
    compute_relative_wind_angle,
    compute_planned_wind_features,
    circular_mean
)

warnings.filterwarnings('ignore')

# ============================================================
# Setup
# ============================================================
ensure_dirs()

print("=" * 70)
print("FEATURE ENGINEERING & FLIGHT-LEVEL AGGREGATION")
print("=" * 70)

# ============================================================
# Load Cleaned Data
# ============================================================
# Tải dữ liệu đã qua quá trình làm sạch
in_path = Path(DATA_PROCESSED_DIR) / "cleaned_flight_data.csv"
df = pd.read_csv(in_path)
print(f"\nLoaded cleaned data: {df.shape[0]:,} rows × {df.shape[1]} cols")
print(f"Unique flights: {df['flight'].nunique()}")

# Detect platform-level current sign convention. A small number of negative
# samples does not justify abs(); normalize only when negative discharge is
# clearly the dominant convention.
pct_positive = (df["battery_current"] > 0).mean() * 100
pct_negative = (df["battery_current"] < 0).mean() * 100
NORMALIZE_CURRENT_SIGN = pct_negative > pct_positive
print(f"Battery current: {pct_positive:.1f}% positive, {pct_negative:.1f}% negative")
print(f"Normalize sign (abs): {NORMALIZE_CURRENT_SIGN}")

# ============================================================
# Aggregate Per-Flight Features
# ============================================================
print(f"\nAggregating per-flight features...")
flight_features = []

for flight_id in sorted(df["flight"].unique()):
    fdata = df[df["flight"] == flight_id].sort_values("time").reset_index(drop=True)
    
    if len(fdata) < 2:
        continue
    
    route = fdata["route"].iloc[0]
    date = fdata["date"].iloc[0]
    
    speed = fdata["speed"].iloc[0]
    altitude = fdata["altitude"].iloc[0]
    payload = fdata["payload"].iloc[0]
    
    flight_duration = fdata["time"].max() - fdata["time"].min()
    
    distance = calculate_distance_haversine(
        fdata["position_y"].values,
        fdata["position_x"].values,
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
    
    temperature_c = fdata["temperature_c"].mean()
    dew_point_c = fdata["dew_point_c"].mean() if "dew_point_c" in fdata.columns and fdata["dew_point_c"].notna().any() else np.nan
    humidity_pct = fdata["humidity_pct"].mean()
    avg_wind_speed = fdata["wind_speed"].mean()
    wind_speed_ms = fdata["wind_speed_ms"].mean() if "wind_speed_ms" in fdata.columns and fdata["wind_speed_ms"].notna().any() else np.nan
    wind_gust_ms = fdata["wind_gust_ms"].max()
    wind_dir_deg = circular_mean(fdata["wind_dir_deg"].dropna().values) if fdata["wind_dir_deg"].notna().any() else np.nan
    precipitation_mm = fdata["precipitation_mm"].mean() if "precipitation_mm" in fdata.columns and fdata["precipitation_mm"].notna().any() else np.nan
    pressure_hpa = fdata["pressure_hpa"].mean()
    cloud_cover_pct = fdata["cloud_cover_pct"].mean()
    
    # Legacy observed relative wind angle: analysis only (uses actual trajectory).
    relative_wind_angle = compute_relative_wind_angle(fdata)

    # Route-direction proxy aerodynamic features. In historical flight-level data
    # the endpoints come from observed telemetry; in deployment/Multi-Hub the same
    # function must receive explicit planned edge endpoints.
    start_x, start_y = fdata["position_x"].iloc[0], fdata["position_y"].iloc[0]
    end_x, end_y = fdata["position_x"].iloc[-1], fdata["position_y"].iloc[-1]
    planned_rel_wind, planned_headwind, planned_crosswind = compute_planned_wind_features(
        start_x, start_y, end_x, end_y, wind_dir_deg, wind_speed_ms,
        coordinate_mode="GEOGRAPHIC",
    )
    route_endpoint_displacement_m = calculate_distance_haversine(
        [start_y, end_y], [start_x, end_x],
        [fdata["position_z"].iloc[0], fdata["position_z"].iloc[-1]],
    )
    route_endpoint_to_path_ratio = (
        route_endpoint_displacement_m / distance if distance > 0 else np.nan
    )
    route_geometry_source = "OBSERVED_ENDPOINT_PROXY"
    
    flight_features.append({
        "flight": flight_id,
        "route": route,
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
    })

features_df = pd.DataFrame(flight_features)

# Loại bỏ các chuyến bay có hiệu suất năng lượng bị lỗi (NaN)
n_before = len(features_df)
features_df = features_df.dropna(subset=["energy_efficiency"])
n_after = len(features_df)
if n_before != n_after:
    print(f"Removed {n_before - n_after} flights with invalid energy efficiency")

print(f"\nAggregated: {len(features_df)} flights × {features_df.shape[1]} columns")

# ============================================================
# Print Summary Statistics
# ============================================================
print(f"\nFEATURE SUMMARY")
print("-" * 60)

print(f"\nFlight Features ({len(FLIGHT_FEATURES)}):")
for f in FLIGHT_FEATURES:
    print(f"  {f:25s}: mean={features_df[f].mean():.2f}, std={features_df[f].std():.2f}, range=[{features_df[f].min():.2f}, {features_df[f].max():.2f}]")

print(f"\nWeather Features ({len(WEATHER_FEATURES)}):")
for f in WEATHER_FEATURES:
    print(f"  {f:25s}: mean={features_df[f].mean():.2f}, std={features_df[f].std():.2f}, range=[{features_df[f].min():.2f}, {features_df[f].max():.2f}]")

print(f"\nEngineered Features ({len(ENGINEERED_FEATURES)}):")
for f in ENGINEERED_FEATURES:
    print(f"  {f:25s}: mean={features_df[f].mean():.2f}, std={features_df[f].std():.2f}, range=[{features_df[f].min():.2f}, {features_df[f].max():.2f}]")

print(f"\nTarget: {TARGET}")
print(f"  mean={features_df[TARGET].mean():.4f}, std={features_df[TARGET].std():.4f}, range=[{features_df[TARGET].min():.4f}, {features_df[TARGET].max():.4f}]")

# ============================================================
# Correlation Heatmaps (Pearson + Spearman)
# ============================================================
# Tạo bản đồ nhiệt thể hiện sự tương quan giữa các đặc trưng
print(f"\nGenerating correlation heatmaps...")

corr_cols = ALL_FEATURES + [TARGET]
corr_data = features_df[corr_cols]

for method in ["pearson", "spearman"]:
    corr_matrix = corr_data.corr(method=method)
    
    fig, ax = plt.subplots(figsize=(14, 11))
    mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)
    sns.heatmap(corr_matrix, mask=mask, annot=True, fmt=".2f", cmap="RdBu_r",
                center=0, vmin=-1, vmax=1, square=True,
                linewidths=0.5, ax=ax,
                annot_kws={"size": 8})
    ax.set_title(f"{method.capitalize()} Correlation Heatmap\n(Features + Target: Energy Efficiency)",
                 fontsize=14, fontweight="bold")
    plt.tight_layout()
    out_fig = Path(OUT_FIG_FEATURE) / f"correlation_heatmap_{method}.png"
    plt.savefig(out_fig, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out_fig}")
    
    target_corr = corr_matrix[TARGET].drop(TARGET).abs().sort_values(ascending=False)
    print(f"\n  Top correlations with target ({method}):")
    for feat, val in target_corr.head(5).items():
        sign = "+" if corr_matrix.loc[feat, TARGET] > 0 else "-"
        print(f"    {feat:25s}: {sign}{val:.4f}")

# Tính hệ số VIF (Variance Inflation Factor) để kiểm tra hiện tượng đa cộng tuyến
print(f"\nCalculating Variance Inflation Factor (VIF)...")
from sklearn.linear_model import LinearRegression
X_vif = features_df[ALL_FEATURES]
vif_data = []
lr = LinearRegression()
for col in ALL_FEATURES:
    y = X_vif[col]
    X_other = X_vif.drop(columns=[col])
    lr.fit(X_other, y)
    r_squared = lr.score(X_other, y)
    vif = 1 / (1 - r_squared) if r_squared != 1 else float('inf')
    vif_data.append({'Feature': col, 'VIF': vif})

vif_df = pd.DataFrame(vif_data).sort_values(by='VIF', ascending=False)
out_vif = Path(OUT_DATA_FEATURES) / "vif_results.csv"
vif_df.to_csv(out_vif, index=False)
print(f"  Saved: {out_vif}")

# ============================================================
# Target Distribution
# ============================================================
# Vẽ biểu đồ phân bổ của mục tiêu dự đoán (Hiệu suất năng lượng)
print(f"\nGenerating target distribution plot...")

fig, axes = plt.subplots(1, 3, figsize=(18, 5))

ax = axes[0]
ax.hist(features_df[TARGET], bins=30, color="#2196F3", edgecolor="white", alpha=0.8)
ax.set_title("Energy Efficiency Distribution", fontsize=12, fontweight="bold")
ax.set_xlabel("Energy Efficiency (m/Wh)")
ax.set_ylabel("Count")
ax.axvline(features_df[TARGET].mean(), color="red", linestyle="--", label=f"Mean: {features_df[TARGET].mean():.2f}")
ax.axvline(features_df[TARGET].median(), color="orange", linestyle="--", label=f"Median: {features_df[TARGET].median():.2f}")
ax.legend()

ax = axes[1]
ax.boxplot(features_df[TARGET], vert=True)
ax.set_title("Energy Efficiency Box Plot", fontsize=12, fontweight="bold")
ax.set_ylabel("Energy Efficiency (m/Wh)")

ax = axes[2]
route_order = features_df.groupby("route")[TARGET].median().sort_values(ascending=False).index
features_df.boxplot(column=TARGET, by="route", ax=ax, 
                     positions=range(len(route_order)),
                     return_type="dict")
ax.set_xticklabels(route_order, rotation=45)
ax.set_title("Energy Efficiency by Route", fontsize=12, fontweight="bold")
ax.set_xlabel("Route")
ax.set_ylabel("Energy Efficiency (m/Wh)")
plt.suptitle("")

plt.tight_layout()
out_target = Path(OUT_FIG_FEATURE) / "target_distribution.png"
plt.savefig(out_target, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {out_target}")

# ============================================================
# Feature Distributions
# ============================================================
# Biểu đồ Histogram phân bổ của toàn bộ các đặc trưng
print(f"\nGenerating feature distribution plots...")

n_features = len(ALL_FEATURES)
n_cols = 4
n_rows = (n_features + n_cols - 1) // n_cols

fig, axes = plt.subplots(n_rows, n_cols, figsize=(20, 4 * n_rows))
axes = axes.flatten()

for i, feat in enumerate(ALL_FEATURES):
    ax = axes[i]
    ax.hist(features_df[feat], bins=25, color="#4CAF50", edgecolor="white", alpha=0.8)
    ax.set_title(feat, fontsize=11, fontweight="bold")
    ax.set_ylabel("Count")
    ax.axvline(features_df[feat].mean(), color="red", linestyle="--", alpha=0.7)

for j in range(i + 1, len(axes)):
    axes[j].set_visible(False)

fig.suptitle("Feature Distributions", fontsize=16, fontweight="bold", y=1.02)
plt.tight_layout()
out_dist = Path(OUT_FIG_FEATURE) / "feature_distributions.png"
plt.savefig(out_dist, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {out_dist}")

# ============================================================
# Spearman Scatter Plots (Top 6 Features)
# ============================================================
# Vẽ biểu đồ tương quan (Scatter Plot) cho 6 đặc trưng ảnh hưởng nhiều nhất đến mục tiêu
print("\nGenerating Spearman Scatter Plots...")
corr_matrix_sp = features_df[ALL_FEATURES + [TARGET]].corr(method='spearman')
target_corr_sp = corr_matrix_sp[TARGET].drop(TARGET).abs().sort_values(ascending=False)

top_6_features = target_corr_sp.head(6).index.tolist()

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
axes = axes.flatten()

for i, feature in enumerate(top_6_features):
    ax = axes[i]
    spearman_val = corr_matrix_sp.loc[feature, TARGET]
    
    sns.regplot(
        x=features_df[feature], 
        y=features_df[TARGET], 
        ax=ax, 
        scatter_kws={'alpha':0.5, 'color': '#2196F3'}, 
        line_kws={'color': '#f44336', 'linewidth': 2}
    )
    
    feature_name = feature.replace('_', ' ').title()
    ax.set_title(f"{feature_name} vs Target\n(Spearman ρ = {spearman_val:.3f})", fontweight='bold', fontsize=12)
    ax.set_xlabel(feature_name)
    ax.set_ylabel("Energy Efficiency (m/Wh)")
    ax.grid(True, linestyle='--', alpha=0.6)

plt.tight_layout()
out_scatter = Path(OUT_FIG_FEATURE) / "spearman_scatter_plots.png"
plt.savefig(out_scatter, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {out_scatter}")

# ============================================================
# Save Data
# ============================================================
out_feat_csv = Path(OUT_DATA_FEATURES) / "flight_level_features.csv"
features_df.to_csv(out_feat_csv, index=False)
print(f"\nSaved: {out_feat_csv} ({features_df.shape[0]} rows × {features_df.shape[1]} cols)")

print(f"\n{'=' * 70}")
print(f"FEATURE ENGINEERING COMPLETE")
print(f"{'=' * 70}")
