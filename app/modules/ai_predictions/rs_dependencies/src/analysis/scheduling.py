"""
scheduling.py
=============
Phase 5: Delivery Scheduling Simulation

Simulates drone delivery scheduling over a day:
  - Drones start with full battery (99.9 Wh)
  - Uses ML model to predict energy consumption for candidate routes
  - Decision: Dispatch if (predicted < remaining * safety_margin), else return/charge
  - Tracks battery state across consecutive deliveries
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
from sklearn.preprocessing import StandardScaler
from pathlib import Path

from config.settings import (
    OUT_DATA_FEATURES, OUT_MODELS, OUT_BEST_MODEL, OUT_RES_SCHED, OUT_FIG_SCHED,
    ALL_FEATURES, OUT_DATA_SPLITS, ensure_dirs
)

warnings.filterwarnings('ignore')
ensure_dirs()

print("=" * 70)
print("DELIVERY SCHEDULING SIMULATION")
print("=" * 70)

# ============================================================
# Load Data and Model
# ============================================================
feat_path = Path(OUT_DATA_FEATURES) / "flight_level_features.csv"
features_df = pd.read_csv(feat_path)
best_model_path = Path(OUT_BEST_MODEL) / "best_model_name.txt"
with open(best_model_path, "r") as f:
    best_model_name = f.read().strip()

model_file = Path(OUT_BEST_MODEL) / "final_model.pkl"
best_model = joblib.load(model_file)

# Constants
MAX_BATTERY_WH = 99.9
SAFETY_MARGIN = 0.8  # Giới hạn sử dụng 80% dung lượng pin
NUM_DRONES = 3

# ============================================================
# Simulation Logic
# ============================================================
# Mô phỏng quá trình phân công đơn hàng cho các drone dựa trên dự đoán năng lượng
print(f"\nRunning Schedule Simulation (Model: {best_model_name})")

drones = {f"Drone-{i}": {"battery": MAX_BATTERY_WH, "deliveries": 0, "status": "Idle"} 
          for i in range(1, NUM_DRONES + 1)}

np.random.seed(123)
routes = features_df["route"].unique()
base_conditions = features_df[ALL_FEATURES].median().to_dict()

delivery_queue = []
for i in range(1, 21):
    r = np.random.choice(routes)
    dist = features_df[features_df["route"] == r]["distance"].median()
    delivery_queue.append({
        "Delivery_ID": f"DLV-{i:03d}",
        "Route": r,
        "Distance_3d": dist if not pd.isna(dist) else 1000,
        "Payload": np.random.choice([0, 250, 500, 750])
    })

schedule_log = []

for dlv in delivery_queue:
    assigned = False
    
    for d_name, d_state in drones.items():
        if d_state["status"] == "Charging":
            continue
            
        feat_vec = base_conditions.copy()
        feat_vec["route"] = dlv["Route"]
        feat_vec["distance"] = dlv["Distance_3d"]
        feat_vec["payload"] = dlv["Payload"]
        
        req_features = pd.DataFrame([feat_vec])
        X_req = req_features[ALL_FEATURES].values
        
        # Predict Efficiency
        pred_eff = best_model.predict(X_req)[0]
        
        pred_energy_wh = dlv["Distance_3d"] / pred_eff if pred_eff > 0 else 999
        
        safe_battery = d_state["battery"] * SAFETY_MARGIN
        
        if pred_energy_wh < safe_battery:
            start_batt = d_state["battery"]
            end_batt = start_batt - pred_energy_wh
            d_state["battery"] = end_batt
            d_state["deliveries"] += 1
            
            schedule_log.append({
                "Delivery_ID": dlv["Delivery_ID"],
                "Assigned_Drone": d_name,
                "Route": dlv["Route"],
                "Predicted_Eff_m_Wh": pred_eff,
                "Predicted_Energy_Wh": pred_energy_wh,
                "Start_Battery_Wh": start_batt,
                "End_Battery_Wh": end_batt,
                "Action": "Dispatch"
            })
            assigned = True
            break
    
    if not assigned:
        schedule_log.append({
            "Delivery_ID": dlv["Delivery_ID"],
            "Assigned_Drone": "None",
            "Route": dlv["Route"],
            "Predicted_Eff_m_Wh": None,
            "Predicted_Energy_Wh": None,
            "Start_Battery_Wh": None,
            "End_Battery_Wh": None,
            "Action": "Delayed - All Drones Charging"
        })
        for d in drones.values():
            d["battery"] = MAX_BATTERY_WH

sched_df = pd.DataFrame(schedule_log)
out_sched = Path(OUT_RES_SCHED) / "delivery_schedule.csv"
sched_df.to_csv(out_sched, index=False)
print(f"  Simulation complete. Saved to {out_sched}")

# ============================================================
# Plotting
# ============================================================
# Vẽ biểu đồ mức tiêu hao pin của từng drone qua các chuyến bay
print(f"\nGenerating Plot...")

fig, ax = plt.subplots(figsize=(12, 6))

for d_name in drones.keys():
    d_log = sched_df[(sched_df["Assigned_Drone"] == d_name) & (sched_df["Action"] == "Dispatch")]
    if len(d_log) > 0:
        x_points = range(len(d_log) + 1)
        y_points = [MAX_BATTERY_WH] + d_log["End_Battery_Wh"].tolist()
        
        ax.plot(x_points, y_points, marker='o', linewidth=2, label=d_name)
        
        for i, row in enumerate(d_log.iterrows()):
            _, r = row
            ax.text(i+1, r["End_Battery_Wh"] + 2, r["Delivery_ID"], fontsize=8, ha="center")

ax.axhline(MAX_BATTERY_WH * (1 - SAFETY_MARGIN), color='red', linestyle='--', label="Safety Limit (Recharge Req)")
ax.set_title(f"Drone Battery Depletion Across Deliveries (Safety Margin = {SAFETY_MARGIN*100}%)", fontsize=14, fontweight="bold")
ax.set_xlabel("Consecutive Deliveries")
ax.set_ylabel("Battery Level (Wh)")
ax.set_ylim(0, MAX_BATTERY_WH + 10)
ax.legend()
ax.grid(alpha=0.3)

plt.tight_layout()
fig_path = Path(OUT_FIG_SCHED) / "delivery_scheduling.png"
plt.savefig(fig_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved plot: {fig_path}")

print(f"\n{'=' * 70}")
print(f"DELIVERY SCHEDULING COMPLETE")
print(f"{'=' * 70}")
