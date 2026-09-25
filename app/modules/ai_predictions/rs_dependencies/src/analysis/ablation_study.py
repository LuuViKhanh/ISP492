"""
ablation_study.py
=================
Phase 3 - Step 1: Ablation Study on Best Model

Evaluates contribution of feature groups and individual features by removing them.
Generates two separate experiments:
  1. Feature Group Ablation
  2. Individual Feature Ablation
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
from pathlib import Path

from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, cross_validate
from sklearn.base import clone

from config.settings import (
    OUT_DATA_SPLITS, OUT_RES_ABLATION, OUT_FIG_ABLATION, OUT_MODELS, OUT_BEST_MODEL,
    FLIGHT_FEATURES, WEATHER_FEATURES, ENGINEERED_FEATURES, ALL_FEATURES, TARGET,
    ensure_dirs
)

warnings.filterwarnings('ignore')
ensure_dirs()

print("=" * 70)
print("ABLATION STUDY")
print("=" * 70)

# ============================================================
# Load Data and Model
# ============================================================
in_train = Path(OUT_DATA_SPLITS) / "train.csv"
in_test = Path(OUT_DATA_SPLITS) / "test.csv"

train_df = pd.read_csv(in_train)
test_df = pd.read_csv(in_test)

y_train = train_df[TARGET].values
y_test = test_df[TARGET].values
groups_train = train_df["date"].values

best_model_path = Path(OUT_BEST_MODEL) / "best_model_name.txt"
with open(best_model_path, "r") as f:
    best_model_name = f.read().strip()

print(f"\nBest model: {best_model_name}")
model_file = Path(OUT_BEST_MODEL) / "final_model.pkl"
best_model = joblib.load(model_file)

FULL_MODEL_NAME = "Flight + Weather + Relative Wind Angle"

# ============================================================
# Reusable Engine Function
# ============================================================
# Hàm chạy thử nghiệm cắt tỉa đặc trưng (Ablation Study)
# Bỏ lần lượt từng nhóm đặc trưng hoặc từng đặc trưng để xem độ giảm của R²
gkf = GroupKFold(n_splits=5)

def run_ablation_experiment(scenarios, csv_name, plot_name, plot_title):
    print(f"\n[EXPERIMENT] {plot_title}")
    print("-" * 70)
    
    results = []
    
    print(f"  Running Baseline: {FULL_MODEL_NAME}...")
    full_features = scenarios[FULL_MODEL_NAME]
    X_train_full = train_df[full_features].values
    X_test_full = test_df[full_features].values
    
    model_full = clone(best_model)
    cv_full = cross_validate(
        model_full, X_train_full, y_train, groups=groups_train,
        cv=gkf, scoring="r2", return_train_score=False, n_jobs=-1
    )
    cv_mean_full = cv_full["test_score"].mean()
    cv_std_full = cv_full["test_score"].std()
    
    model_full.fit(X_train_full, y_train)
    y_pred_full = model_full.predict(X_test_full)
    test_r2_full = r2_score(y_test, y_pred_full)
    
    for scenario_name, features in scenarios.items():
        print(f"  Scenario: {scenario_name} ({len(features)} features)")
        
        X_train_ab = train_df[features].values
        X_test_ab = test_df[features].values
        
        model_clone = clone(best_model)
        
        cv_res = cross_validate(
            model_clone, X_train_ab, y_train, groups=groups_train,
            cv=gkf, scoring="r2", return_train_score=False, n_jobs=-1
        )
        cv_mean = cv_res["test_score"].mean()
        cv_std = cv_res["test_score"].std()
        
        model_clone.fit(X_train_ab, y_train)
        y_pred = model_clone.predict(X_test_ab)
        
        mae = mean_absolute_error(y_test, y_pred)
        rmse = np.sqrt(mean_squared_error(y_test, y_pred))
        r2 = r2_score(y_test, y_pred)
        
        n_samples = len(y_test)
        n_feats = len(features)
        adj_r2 = 1 - (1 - r2) * (n_samples - 1) / (n_samples - n_feats - 1)
        
        test_drop = test_r2_full - r2
        drop_pct = (test_drop / abs(test_r2_full) * 100) if test_r2_full != 0 else 0
        cv_drop = cv_mean_full - cv_mean
        
        results.append({
            "Scenario": scenario_name,
            "Features Used": len(features),
            "Test R²": round(r2, 4),
            "Adjusted R²": round(adj_r2, 4),
            "CV Mean": round(cv_mean, 4),
            "CV Std": round(cv_std, 4),
            "Test Drop": round(test_drop, 4),
            "Drop %": round(drop_pct, 1),
            "CV Drop": round(cv_drop, 4)
        })
        
    df_res = pd.DataFrame(results)
    res_path = Path(OUT_RES_ABLATION) / f"{csv_name}.csv"
    df_res.to_csv(res_path, index=False)
    print(f"\n  Saved: {res_path}")
    
    # Generate Plot
    fig, axes = plt.subplots(1, 2, figsize=(18, 8))
    
    ax = axes[0]
    colors = ["#4CAF50" if s == FULL_MODEL_NAME else "#FF9800" if "Without" in s else "#2196F3" 
              for s in df_res["Scenario"]]
    bars = ax.barh(df_res["Scenario"], df_res["Test R²"], color=colors, edgecolor="white")
    ax.set_xlabel("Test R²")
    ax.set_title(f"{plot_title} — Test R²", fontsize=13, fontweight="bold")
    ax.axvline(x=test_r2_full, color="red", linestyle="--", alpha=0.5, label=f"Full Model R²={test_r2_full:.4f}")
    ax.legend()
    for bar, val in zip(bars, df_res["Test R²"]):
        ax.text(val + 0.005, bar.get_y() + bar.get_height()/2, f"{val:.4f}", va="center", fontsize=10)
        
    ax = axes[1]
    drop_df = df_res[df_res["Scenario"] != FULL_MODEL_NAME]
    colors = ["#f44336" if d > 0 else "#4CAF50" for d in drop_df["CV Drop"]]
    bars = ax.barh(drop_df["Scenario"], drop_df["CV Drop"], color=colors, edgecolor="white")
    ax.set_xlabel("ΔCV (CV Drop from Full Model)")
    ax.set_title(f"{plot_title} — CV Drop", fontsize=13, fontweight="bold")
    ax.axvline(x=0, color="gray", linestyle="--", alpha=0.5)
    for bar, cv_d, test_d in zip(bars, drop_df["CV Drop"], drop_df["Test Drop"]):
        ax.text(cv_d + (0.002 if cv_d > 0 else -0.002), 
                bar.get_y() + bar.get_height()/2, 
                f"CV Drop: {cv_d:.4f}\n(Test Drop: {test_d:.4f})", 
                va="center", ha="left" if cv_d > 0 else "right", fontsize=9)
        
    plt.tight_layout()
    fig_path = Path(OUT_FIG_ABLATION) / f"{plot_name}.png"
    plt.savefig(fig_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved plot: {fig_path}")

# ============================================================
# Define Scenarios
# ============================================================

# Thử nghiệm 1: Nhóm đặc trưng
# feature_group_scenarios = {
#     "Weather Only": WEATHER_FEATURES,
#     "Flight Only": FLIGHT_FEATURES,
#     "Flight + Weather": FLIGHT_FEATURES + WEATHER_FEATURES,
#     FULL_MODEL_NAME: ALL_FEATURES,
#     "Without Weather": FLIGHT_FEATURES + ENGINEERED_FEATURES,
#     "Without Relative Wind Angle": FLIGHT_FEATURES + WEATHER_FEATURES,
#     "Without Flight": WEATHER_FEATURES + ENGINEERED_FEATURES
# }

feature_group_scenarios = {
    "Flight Only": FLIGHT_FEATURES,

    "Weather Only": WEATHER_FEATURES,

    "Flight + Weather": FLIGHT_FEATURES + WEATHER_FEATURES,

    FULL_MODEL_NAME: ALL_FEATURES
}

run_ablation_experiment(
    scenarios=feature_group_scenarios,
    csv_name="feature_group_ablation",
    plot_name="feature_group_ablation",
    plot_title="Feature Group Ablation"
)

# Thử nghiệm 2: Từng đặc trưng riêng lẻ
single_feature_scenarios = {
    FULL_MODEL_NAME: ALL_FEATURES
}
for feat in ALL_FEATURES:
    friendly_name = feat.replace('_', ' ').title()
    if feat == "distance": friendly_name = "Distance"
    elif feat == "temperature_c": friendly_name = "Temperature"
    elif feat == "humidity_pct": friendly_name = "Humidity"
    elif feat == "wind_gust_ms": friendly_name = "Wind Gust"
    elif feat == "pressure_hpa": friendly_name = "Pressure"
    elif feat == "cloud_cover_pct": friendly_name = "Cloud Cover"
    
    scenario_name = f"Without {friendly_name}"
    feat_list = [f for f in ALL_FEATURES if f != feat]
    single_feature_scenarios[scenario_name] = feat_list

run_ablation_experiment(
    scenarios=single_feature_scenarios,
    csv_name="individual_feature_ablation",
    plot_name="individual_feature_ablation",
    plot_title="Individual Feature Ablation"
)

print(f"\n{'=' * 70}")
print(f"ABLATION STUDY COMPLETE")
print(f"{'=' * 70}")
