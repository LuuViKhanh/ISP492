"""
environmental_impact.py
=======================
Phase 3.5: Environmental Impact Analysis

Computes:
  - Energy Saving (%) — best vs worst route
  - CO₂ Reduction (g) — based on emission factor
  - Relative Battery Saving (%)
  - Flights per Charge
  - Route Efficiency Comparison
"""

import pandas as pd
import numpy as np
import os
import warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

from config.settings import (
    OUT_RES_ROUTE, OUT_RES_ENV, OUT_FIG_ENV, OUT_DATA_FEATURES, OUT_BEST_MODEL,
    ensure_dirs
)

warnings.filterwarnings('ignore')
ensure_dirs()

print("=" * 70)
print("ENVIRONMENTAL IMPACT ANALYSIS")
print("=" * 70)

# ============================================================
# Load Data
# ============================================================
pred_path = Path(OUT_RES_ROUTE) / "flight_predictions.csv"
rank_path = Path(OUT_RES_ROUTE) / "route_ranking.csv"
feat_path = Path(OUT_DATA_FEATURES) / "flight_level_features.csv"

pred_df = pd.read_csv(pred_path)
route_ranking = pd.read_csv(rank_path)
features_df = pd.read_csv(feat_path)

best_model_path = Path(OUT_BEST_MODEL) / "best_model_name.txt"
with open(best_model_path, "r") as f:
    best_model_name = f.read().strip()

print(f"\nBest model: {best_model_name}")

# Constants
BATTERY_CAPACITY_WH = 99.9  # DJI Matrice 100: 4500mAh × 22.2V ≈ 99.9 Wh
CO2_EMISSION_FACTOR = 0.42  # kg CO₂ per kWh (US average grid)
SAFETY_MARGIN = 0.80  # 80% usable battery

# ============================================================
# Per-Flight Environmental Metrics
# ============================================================
# Tính toán lượng điện năng tiêu thụ, lượng CO2 tiết kiệm và số chuyến bay mỗi lần sạc
print(f"\nCOMPUTING ENVIRONMENTAL METRICS")
print("=" * 70)

env_df = features_df[["flight", "route", "battery_consumed_wh", "distance", "energy_efficiency"]].copy()

env_df["battery_saving_pct"] = (1 - env_df["battery_consumed_wh"] / BATTERY_CAPACITY_WH) * 100
env_df["co2_grams"] = env_df["battery_consumed_wh"] / 1000 * CO2_EMISSION_FACTOR * 1000
env_df["flights_per_charge"] = (BATTERY_CAPACITY_WH * SAFETY_MARGIN) / env_df["battery_consumed_wh"]

# ============================================================
# Route-Level Environmental Summary
# ============================================================
# Tổng hợp các chỉ số theo từng tuyến đường
route_env = env_df.groupby("route").agg(
    Flights=("flight", "count"),
    Avg_Consumption_Wh=("battery_consumed_wh", "mean"),
    Avg_Distance_m=("distance", "mean"),
    Avg_Efficiency=("energy_efficiency", "mean"),
    Avg_Battery_Saving_Pct=("battery_saving_pct", "mean"),
    Avg_CO2_g=("co2_grams", "mean"),
    Avg_Flights_Per_Charge=("flights_per_charge", "mean"),
).reset_index()

worst_consumption = route_env["Avg_Consumption_Wh"].max()
route_env["Energy_Saving_Pct"] = (1 - route_env["Avg_Consumption_Wh"] / worst_consumption) * 100

worst_co2 = route_env["Avg_CO2_g"].max()
route_env["CO2_Reduction_g"] = worst_co2 - route_env["Avg_CO2_g"]

route_env = route_env.sort_values("Avg_Efficiency", ascending=False)

print(f"\n{'Route':8s} {'Flights':>8s} {'Avg Wh':>8s} {'Eff':>8s} {'Battery%':>10s} {'CO2(g)':>8s} "
      f"{'Flt/Chg':>8s} {'Save%':>8s} {'CO2 Red':>8s}")
print("-" * 85)
for _, row in route_env.iterrows():
    print(f"{row['route']:8s} {row['Flights']:8.0f} {row['Avg_Consumption_Wh']:8.2f} "
          f"{row['Avg_Efficiency']:8.4f} {row['Avg_Battery_Saving_Pct']:9.1f}% "
          f"{row['Avg_CO2_g']:8.2f} {row['Avg_Flights_Per_Charge']:8.2f} "
          f"{row['Energy_Saving_Pct']:7.1f}% {row['CO2_Reduction_g']:8.2f}")

out_env = Path(OUT_RES_ENV) / "environmental_impact.csv"
route_env.to_csv(out_env, index=False)
print(f"\nSaved environmental impact data: {out_env}")

# ============================================================
# Overall Summary Statistics
# ============================================================
print(f"\nOVERALL ENVIRONMENTAL SUMMARY")
print("-" * 50)
print(f"  Battery Capacity: {BATTERY_CAPACITY_WH} Wh (DJI Matrice 100)")
print(f"  CO2 Emission Factor: {CO2_EMISSION_FACTOR} kg/kWh")
print(f"  Safety Margin: {SAFETY_MARGIN*100}%")
print(f"\n  Average consumption: {env_df['battery_consumed_wh'].mean():.2f} Wh/flight")
print(f"  Average CO2 emission: {env_df['co2_grams'].mean():.2f} g/flight")
print(f"  Average flights per charge: {env_df['flights_per_charge'].mean():.2f}")
print(f"  Best route efficiency: {route_env.iloc[0]['route']} ({route_env.iloc[0]['Avg_Efficiency']:.4f} m/Wh)")
print(f"  Worst route efficiency: {route_env.iloc[-1]['route']} ({route_env.iloc[-1]['Avg_Efficiency']:.4f} m/Wh)")
print(f"  Max energy saving: {route_env['Energy_Saving_Pct'].max():.1f}%")
print(f"  Max CO2 reduction: {route_env['CO2_Reduction_g'].max():.2f} g/flight")

# ============================================================
# Environmental Impact Visualization
# ============================================================
# Vẽ biểu đồ hiển thị các chỉ số môi trường cho từng tuyến đường
print(f"\nGenerating environmental impact charts...")

fig, axes = plt.subplots(2, 2, figsize=(18, 14))
fig.suptitle("Environmental Impact Analysis — Drone Delivery Network", fontsize=16, fontweight="bold")

route_plot = route_env.sort_values("Avg_Efficiency", ascending=True)

ax = axes[0, 0]
colors = plt.cm.Greens(np.linspace(0.3, 0.9, len(route_plot)))
bars = ax.barh(route_plot["route"], route_plot["Energy_Saving_Pct"], color=colors, edgecolor="white")
ax.set_xlabel("Energy Saving (%)")
ax.set_title("Energy Saving vs Worst Route")
for bar, val in zip(bars, route_plot["Energy_Saving_Pct"]):
    ax.text(val + 0.5, bar.get_y() + bar.get_height()/2, f"{val:.1f}%", va="center", fontsize=10)

ax = axes[0, 1]
colors = plt.cm.Blues(np.linspace(0.3, 0.9, len(route_plot)))
bars = ax.barh(route_plot["route"], route_plot["CO2_Reduction_g"], color=colors, edgecolor="white")
ax.set_xlabel("CO₂ Reduction (g/flight)")
ax.set_title("CO₂ Reduction vs Worst Route")
for bar, val in zip(bars, route_plot["CO2_Reduction_g"]):
    ax.text(val + 0.1, bar.get_y() + bar.get_height()/2, f"{val:.2f}g", va="center", fontsize=10)

ax = axes[1, 0]
colors = plt.cm.Oranges(np.linspace(0.3, 0.9, len(route_plot)))
bars = ax.barh(route_plot["route"], route_plot["Avg_Battery_Saving_Pct"], color=colors, edgecolor="white")
ax.set_xlabel("Battery Remaining (%)")
ax.set_title("Average Battery Remaining After Flight")
for bar, val in zip(bars, route_plot["Avg_Battery_Saving_Pct"]):
    ax.text(val + 0.5, bar.get_y() + bar.get_height()/2, f"{val:.1f}%", va="center", fontsize=10)

ax = axes[1, 1]
colors = plt.cm.Purples(np.linspace(0.3, 0.9, len(route_plot)))
bars = ax.barh(route_plot["route"], route_plot["Avg_Flights_Per_Charge"], color=colors, edgecolor="white")
ax.set_xlabel("Estimated Flights per Full Charge")
ax.set_title("Flights per Charge (80% usable)")
for bar, val in zip(bars, route_plot["Avg_Flights_Per_Charge"]):
    ax.text(val + 0.05, bar.get_y() + bar.get_height()/2, f"{val:.2f}", va="center", fontsize=10)

plt.tight_layout()
fig_env = Path(OUT_FIG_ENV) / "environmental_impact.png"
plt.savefig(fig_env, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved plot: {fig_env}")

print(f"\n{'=' * 70}")
print(f"ENVIRONMENTAL IMPACT ANALYSIS COMPLETE")
print(f"{'=' * 70}")
