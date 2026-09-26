"""
multihub_analysis.py
====================
Phase 4 - Step 1: Multi-hub Delivery Recommendation

Simulates a multi-hub delivery network:
  - Defines 3 hypothetical hubs based on data
  - Assigns routes to hubs based on dataset geometry
  - For each customer request, predicts energy efficiency from each hub
  - Recommends the hub with the highest predicted efficiency
"""

import pandas as pd
import numpy as np
import json
import joblib
import os
import warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.preprocessing import StandardScaler
from pathlib import Path

from config.settings import (
    OUT_DATA_FEATURES, OUT_MODELS, OUT_BEST_MODEL, OUT_RES_MULTIHUB, OUT_FIG_MULTIHUB,
    ALL_FEATURES, OUT_DATA_SPLITS, ensure_dirs
)

warnings.filterwarnings('ignore')
ensure_dirs()

print("=" * 70)
print("MULTI-HUB DELIVERY RECOMMENDATION")
print("=" * 70)

# ============================================================
# Load Data and Models
# ============================================================
feat_path = Path(OUT_DATA_FEATURES) / "flight_level_features.csv"
features_df = pd.read_csv(feat_path)
best_model_path = Path(OUT_BEST_MODEL) / "best_model_name.txt"
with open(best_model_path, "r") as f:
    best_model_name = f.read().strip()

model_file = Path(OUT_BEST_MODEL) / "final_model.pkl"
best_model = joblib.load(model_file)

# ============================================================
# Predict and Simulate Hubs
# ============================================================
X_all = features_df[ALL_FEATURES].copy()
predictions = best_model.predict(X_all.values)
# ============================================================
# Định nghĩa các trung tâm phân phối (hubs) giả định và gán tuyến đường (routes)
HUB_DEF = {
    "Hub North": ["R1", "R2", "R3"],
    "Hub Central": ["R4", "R5", "R6"],
    "Hub South": ["R7", "R8", "R9", "R10"]
}
route_to_hub = {}
for hub, routes in HUB_DEF.items():
    for r in routes:
        route_to_hub[r] = hub

print(f"\nDefined Multi-hub Network:")
for hub, routes in HUB_DEF.items():
    print(f"  {hub}: {routes}")

# ============================================================
# Generate Customer Requests
# ============================================================
# Tạo danh sách các yêu cầu giao hàng giả định để thử nghiệm (mô phỏng 50 đơn hàng)
print(f"\nGenerating Customer Delivery Requests...")
np.random.seed(42)

n_requests = 50
requests = []

base_conditions = features_df[ALL_FEATURES].median().to_dict()
destinations = features_df["route"].unique()

for req_id in range(1, n_requests + 1):
    customer_dest = np.random.choice(destinations)
    payload = np.random.choice([0, 250, 500, 750])
    
    wind_speed = base_conditions["avg_wind_speed"] * np.random.uniform(0.8, 1.5)
    temp = base_conditions["temperature_c"] + np.random.normal(0, 3)
    
    requests.append({
        "Request_ID": f"REQ-{req_id:03d}",
        "Destination": customer_dest,
        "Payload_g": payload,
        "Avg_Wind_Speed": wind_speed,
        "Temperature_C": temp
    })

req_df = pd.DataFrame(requests)

# ============================================================
# Predict and Recommend Hub
# ============================================================
# Dự đoán hiệu suất từ từng hub cho mỗi đơn hàng và đề xuất hub tốt nhất
print(f"\nPredicting and Recommending...")

recommendations = []

for _, req in req_df.iterrows():
    best_hub = None
    best_eff = -1
    hub_efficiencies = {}
    
    native_hub = route_to_hub.get(req["Destination"], "Hub Central")
    
    for hub in HUB_DEF.keys():
        feat_vec = base_conditions.copy()
        feat_vec["payload"] = req["Payload_g"]
        feat_vec["avg_wind_speed"] = req["Avg_Wind_Speed"]
        feat_vec["temperature_c"] = req["Temperature_C"]
        
        base_dist = features_df[features_df["route"] == req["Destination"]]["distance"].median()
        if pd.isna(base_dist):
            base_dist = 1000
            
        if hub != native_hub:
            # Giao hàng chéo hub sẽ bị phạt thêm quãng đường
            feat_vec["distance"] = base_dist + np.random.uniform(1000, 2000)
        else:
            feat_vec["distance"] = base_dist
            
        X_req = pd.DataFrame([feat_vec])[ALL_FEATURES]
        
        pred_eff = best_model.predict(X_req.values)[0]
        hub_efficiencies[hub] = pred_eff
        
        if pred_eff > best_eff:
            best_eff = pred_eff
            best_hub = hub
            
    rec_record = {
        "Request_ID": req["Request_ID"],
        "Destination": req["Destination"],
        "Payload_g": req["Payload_g"],
        "Recommended_Hub": best_hub,
        "Max_Predicted_Efficiency": best_eff
    }
    for h, eff in hub_efficiencies.items():
        rec_record[f"{h}_Efficiency"] = eff
        
    recommendations.append(rec_record)

rec_results = pd.DataFrame(recommendations)
out_rec = Path(OUT_RES_MULTIHUB) / "multihub_recommendation.csv"
rec_results.to_csv(out_rec, index=False)
print(f"  Generated {n_requests} recommendations")
print(f"  Saved: {out_rec}")

# ============================================================
# Summary and Plot
# ============================================================
# Tổng hợp kết quả và vẽ biểu đồ thể hiện phân bổ các khuyến nghị hub
hub_counts = rec_results["Recommended_Hub"].value_counts()
print(f"\nRecommendation Distribution:")
for hub, count in hub_counts.items():
    print(f"  {hub}: {count} requests ({count/n_requests*100:.1f}%)")

fig, axes = plt.subplots(1, 2, figsize=(14, 6))

ax = axes[0]
ax.pie(hub_counts.values, labels=hub_counts.index, autopct='%1.1f%%',
       colors=["#4CAF50", "#2196F3", "#FF9800"], startangle=90,
       wedgeprops={"edgecolor": "white", "linewidth": 2})
ax.set_title("Hub Recommendation Distribution")

ax = axes[1]
eff_cols = [f"{h}_Efficiency" for h in HUB_DEF.keys()]
avg_effs = rec_results[eff_cols].mean()
bars = ax.bar(HUB_DEF.keys(), avg_effs.values, color=["#4CAF50", "#2196F3", "#FF9800"], edgecolor="white")
ax.set_title("Average Predicted Efficiency by Dispatch Hub")
ax.set_ylabel("Energy Efficiency (m/Wh)")
for bar, val in zip(bars, avg_effs.values):
    ax.text(bar.get_x() + bar.get_width()/2, val - 2, f"{val:.1f}", 
            ha="center", color="white", fontweight="bold")

plt.suptitle("Multi-hub Delivery Simulation", fontsize=16, fontweight="bold")
plt.tight_layout()
fig_path = Path(OUT_FIG_MULTIHUB) / "multihub_recommendation.png"
plt.savefig(fig_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved plot: {fig_path}")

print(f"\n{'=' * 70}")
print(f"MULTI-HUB RECOMMENDATION COMPLETE")
print(f"{'=' * 70}")
