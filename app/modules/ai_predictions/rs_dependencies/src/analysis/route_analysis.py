"""
route_analysis.py
=================
Phase 3 - Step 3: Route Analysis & Ranking

Predicts energy efficiency for each flight using the best model.
Computes route ranking with:
  - Sample Size (number of flights)
  - Mean, Median, Std Efficiency
  - 95% Confidence Interval
  - Ranking
"""

import pandas as pd
import numpy as np
import joblib
import os
import warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path
from sklearn.preprocessing import StandardScaler
from matplotlib.patches import Patch

from config.settings import (
    OUT_DATA_FEATURES, OUT_DATA_SPLITS, OUT_MODELS, OUT_BEST_MODEL,
    OUT_RES_ROUTE, OUT_FIG_ROUTE,
    ALL_FEATURES, TARGET, ensure_dirs
)

warnings.filterwarnings('ignore')
ensure_dirs()

print("=" * 70)
print("ROUTE ANALYSIS & RANKING")
print("=" * 70)

# ============================================================
# Load Data and Best Model
# ============================================================
feat_path = Path(OUT_DATA_FEATURES) / "flight_level_features.csv"
features_df = pd.read_csv(feat_path)

train_path = Path(OUT_DATA_SPLITS) / "train.csv"
test_path = Path(OUT_DATA_SPLITS) / "test.csv"
train_df = pd.read_csv(train_path)
test_df = pd.read_csv(test_path)

best_model_path = Path(OUT_BEST_MODEL) / "best_model_name.txt"
with open(best_model_path, "r") as f:
    best_model_name = f.read().strip()

model_file = Path(OUT_BEST_MODEL) / "final_model.pkl"
best_model = joblib.load(model_file)
print(f"\nBest model: {best_model_name}")
print(f"Total flights: {len(features_df)}")

# ============================================================
# Predict
# ============================================================
# Chuẩn hóa lại toàn bộ dữ liệu đầu vào và sử dụng mô hình tốt nhất để dự đoán hiệu suất
X_all = features_df[ALL_FEATURES].copy()
predictions = best_model.predict(X_all.values)

pred_df = features_df[["flight", "route", "date"] + ALL_FEATURES].copy()
pred_df["actual_efficiency"] = features_df[TARGET]
pred_df["predicted_efficiency"] = predictions
pred_df["residual"] = pred_df["actual_efficiency"] - pred_df["predicted_efficiency"]
pred_df["abs_error"] = np.abs(pred_df["residual"])

out_pred = Path(OUT_RES_ROUTE) / "flight_predictions.csv"
pred_df.to_csv(out_pred, index=False)
print(f"\nSaved flight predictions: {out_pred} ({len(pred_df)} flights)")

# ============================================================
# Route Ranking
# ============================================================
# Tính toán và xếp hạng các tuyến đường dựa trên hiệu suất năng lượng dự đoán trung bình
print(f"\nROUTE RANKING")
print("=" * 70)

route_stats = pred_df.groupby("route").agg(
    Flights=("flight", "count"),
    Mean_Efficiency=("predicted_efficiency", "mean"),
    Median_Efficiency=("predicted_efficiency", "median"),
    Std_Efficiency=("predicted_efficiency", "std"),
    Min_Efficiency=("predicted_efficiency", "min"),
    Max_Efficiency=("predicted_efficiency", "max"),
    Mean_Actual=("actual_efficiency", "mean"),
).reset_index()

# Tính khoảng tin cậy 95% (95% Confidence Interval)
route_stats["SE"] = route_stats["Std_Efficiency"] / np.sqrt(route_stats["Flights"])
route_stats["CI_Lower"] = route_stats["Mean_Efficiency"] - 1.96 * route_stats["SE"]
route_stats["CI_Upper"] = route_stats["Mean_Efficiency"] + 1.96 * route_stats["SE"]
route_stats["CI_95"] = route_stats.apply(
    lambda r: f"[{r['CI_Lower']:.2f}, {r['CI_Upper']:.2f}]", axis=1
)

route_stats = route_stats.sort_values("Mean_Efficiency", ascending=False)
route_stats["Rank"] = range(1, len(route_stats) + 1)

# Phân loại độ tin cậy dựa trên số lượng mẫu dữ liệu
route_stats["Confidence"] = route_stats["Flights"].apply(
    lambda n: "High" if n >= 10 else "Medium" if n >= 5 else "Low (n<5)"
)

out_rank = Path(OUT_RES_ROUTE) / "route_ranking.csv"
route_stats.to_csv(out_rank, index=False)
print(f"\n{'Route':8s} {'Flights':>8s} {'Mean Eff':>10s} {'Median':>10s} {'Std':>8s} {'95% CI':>20s} {'Rank':>6s} {'Confidence':>12s}")
print("-" * 85)
for _, row in route_stats.iterrows():
    print(f"{row['route']:8s} {row['Flights']:8d} {row['Mean_Efficiency']:10.4f} "
          f"{row['Median_Efficiency']:10.4f} {row['Std_Efficiency']:8.4f} "
          f"{row['CI_95']:>20s} {row['Rank']:6d} {row['Confidence']:>12s}")
print(f"\nSaved route ranking: {out_rank}")

# ============================================================
# Route Ranking Bar Chart
# ============================================================
# Vẽ biểu đồ xếp hạng tuyến đường kèm theo khoảng tin cậy 95%
print(f"\nGenerating route ranking charts...")

fig, ax = plt.subplots(figsize=(12, 7))

route_plot = route_stats.sort_values("Mean_Efficiency", ascending=True)
colors = ["#4CAF50" if c == "High" else "#FF9800" if c == "Medium" else "#f44336" 
          for c in route_plot["Confidence"]]

bars = ax.barh(route_plot["route"], route_plot["Mean_Efficiency"], 
               xerr=1.96 * route_plot["SE"], color=colors, edgecolor="white",
               capsize=5, alpha=0.85)

for bar, (_, row) in zip(bars, route_plot.iterrows()):
    ax.text(row["Mean_Efficiency"] + 1.96 * row["SE"] + 0.3,
            bar.get_y() + bar.get_height()/2,
            f"n={row['Flights']} | {row['Mean_Efficiency']:.2f} m/Wh",
            va="center", fontsize=10, fontweight="bold")

ax.set_xlabel("Predicted Energy Efficiency (m/Wh)")
ax.set_title(f"Route Ranking — Mean Efficiency ± 95% CI ({best_model_name})", fontsize=14, fontweight="bold")

legend_elements = [
    Patch(facecolor="#4CAF50", label="High confidence (n≥10)"),
    Patch(facecolor="#FF9800", label="Medium confidence (5≤n<10)"),
    Patch(facecolor="#f44336", label="Low confidence (n<5)")
]
ax.legend(handles=legend_elements, loc="lower right")

plt.tight_layout()
fig_rank = Path(OUT_FIG_ROUTE) / "route_ranking.png"
plt.savefig(fig_rank, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {fig_rank}")

# ============================================================
# Predicted vs Route
# ============================================================
fig, ax = plt.subplots(figsize=(14, 7))

route_plot2 = route_stats.sort_values("Mean_Efficiency", ascending=False)
x = np.arange(len(route_plot2))

ax.bar(x, route_plot2["Mean_Efficiency"], yerr=1.96 * route_plot2["SE"],
       color="#2196F3", edgecolor="white", capsize=5, alpha=0.85, label="Mean ± 95% CI")
ax.scatter(x, route_plot2["Median_Efficiency"], color="red", zorder=5, s=60, label="Median")

for i, (_, row) in enumerate(route_plot2.iterrows()):
    ax.text(i, row["Mean_Efficiency"] + 1.96 * row["SE"] + 0.5,
            f"n={row['Flights']}", ha="center", fontsize=10, fontweight="bold")

ax.set_xticks(x)
ax.set_xticklabels(route_plot2["route"], fontsize=12)
ax.set_xlabel("Route")
ax.set_ylabel("Predicted Energy Efficiency (m/Wh)")
ax.set_title(f"Predicted Energy Efficiency per Route — Mean ± 95% CI ({best_model_name})", fontsize=14, fontweight="bold")
ax.legend()
ax.grid(axis="y", alpha=0.3)

plt.tight_layout()
fig_pred = Path(OUT_FIG_ROUTE) / "predicted_vs_route.png"
plt.savefig(fig_pred, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {fig_pred}")

# ============================================================
# Energy Efficiency Distribution
# ============================================================
fig, ax = plt.subplots(figsize=(14, 7))

route_order = route_stats.sort_values("Mean_Efficiency", ascending=False)["route"].values
data_for_box = [pred_df[pred_df["route"] == r]["predicted_efficiency"].values for r in route_order]

bp = ax.boxplot(data_for_box, patch_artist=True, 
                notch=True, showmeans=True,
                meanprops=dict(marker="D", markerfacecolor="red", markersize=8))
ax.set_xticklabels(route_order)

colors_box = plt.cm.viridis(np.linspace(0.2, 0.8, len(route_order)))
for patch, color in zip(bp["boxes"], colors_box):
    patch.set_facecolor(color)
    patch.set_alpha(0.7)

for i, route in enumerate(route_order):
    n = (pred_df["route"] == route).sum()
    ax.text(i + 1, ax.get_ylim()[0], f"n={n}", ha="center", fontsize=9, fontweight="bold")

ax.set_xlabel("Route")
ax.set_ylabel("Predicted Energy Efficiency (m/Wh)")
ax.set_title("Energy Efficiency Distribution per Route", fontsize=14, fontweight="bold")
ax.grid(axis="y", alpha=0.3)

plt.tight_layout()
fig_dist = Path(OUT_FIG_ROUTE) / "energy_efficiency_distribution.png"
plt.savefig(fig_dist, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {fig_dist}")

print(f"\n{'=' * 70}")
print(f"ROUTE ANALYSIS COMPLETE")
print(f"{'=' * 70}")
