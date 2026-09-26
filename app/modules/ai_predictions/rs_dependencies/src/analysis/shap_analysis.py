"""
shap_analysis.py
================
Phase 3 - Step 2: SHAP Value Analysis

Uses SHAP (SHapley Additive exPlanations) to explain the best model's predictions.
Generates:
  - Summary plot (beeswarm)
  - Feature importance bar plot
  - Dependence plots for key features
"""

import pandas as pd
import numpy as np
import json
import joblib
import os
import warnings
import shap
import matplotlib.pyplot as plt
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

plt.rcParams.update({
    'font.size': 14,
    'axes.labelsize': 16,
    'xtick.labelsize': 14,
    'ytick.labelsize': 14,
    'legend.fontsize': 14
})

from pathlib import Path

from config.settings import (
    OUT_DATA_SPLITS, OUT_MODELS, OUT_BEST_MODEL, OUT_FIG_SHAP,
    ALL_FEATURES, TARGET, ensure_dirs
)

warnings.filterwarnings('ignore')
ensure_dirs()

print("=" * 70)
print("SHAP ANALYSIS")
print("=" * 70)

# ============================================================
# Load Data and Model
# ============================================================
in_train = Path(OUT_DATA_SPLITS) / "train.csv"
in_test = Path(OUT_DATA_SPLITS) / "test.csv"

train_df = pd.read_csv(in_train)
test_df = pd.read_csv(in_test)

X_train = train_df[ALL_FEATURES]
X_test = test_df[ALL_FEATURES]

best_model_path = Path(OUT_BEST_MODEL) / "best_model_name.txt"
with open(best_model_path, "r") as f:
    best_model_name = f.read().strip()

print(f"\nBest model: {best_model_name}")
model_file = Path(OUT_BEST_MODEL) / "final_model.pkl"
best_model = joblib.load(model_file)

# ============================================================
# Compute SHAP Values
# ============================================================
# Tính toán giá trị SHAP để xem mức độ đóng góp của từng đặc trưng vào kết quả dự đoán
print(f"\nComputing SHAP values...")
is_tree_model = best_model_name in ["Decision Tree", "ExtraTrees", "Random Forest", "XGBoost", "LightGBM", "CatBoost"]

if is_tree_model:
    tree_model = best_model.named_steps['model'] if hasattr(best_model, 'named_steps') else best_model
    explainer = shap.TreeExplainer(tree_model)
    shap_values = explainer.shap_values(X_test)
else:
    background = shap.sample(X_train, min(50, len(X_train)))
    explainer = shap.KernelExplainer(best_model.predict, background)
    shap_values = explainer.shap_values(X_test)

print(f"  SHAP values computed: {shap_values.shape}")

# ============================================================
# SHAP Summary Plot
# ============================================================
# Vẽ biểu đồ Summary (beeswarm) tổng quát
print(f"  Generating SHAP Summary Plot...")
fig, ax = plt.subplots(figsize=(12, 8))
shap.summary_plot(shap_values, X_test, feature_names=ALL_FEATURES, show=False)
plt.title(f"SHAP Summary — {best_model_name}", fontsize=14, fontweight="bold")
plt.tight_layout()
summary_path = Path(OUT_FIG_SHAP) / "shap_summary.pdf"
plt.savefig(summary_path, format='pdf', dpi=300, bbox_inches="tight")
plt.close()
print(f"  Saved: {summary_path}")

# ============================================================
# SHAP Bar Plot
# ============================================================
# Vẽ biểu đồ Bar (độ quan trọng trung bình tuyệt đối)
print(f"  Generating SHAP Bar Plot...")
fig, ax = plt.subplots(figsize=(12, 8))
shap.summary_plot(shap_values, X_test, feature_names=ALL_FEATURES, plot_type="bar", show=False)
plt.title(f"SHAP Feature Importance — {best_model_name}", fontsize=14, fontweight="bold")
plt.tight_layout()
bar_path = Path(OUT_FIG_SHAP) / "shap_bar.pdf"
plt.savefig(bar_path, format='pdf', dpi=300, bbox_inches="tight")
plt.close()
print(f"  Saved: {bar_path}")

# ============================================================
# SHAP Dependence Plots
# ============================================================
# Vẽ biểu đồ Dependence cho các đặc trưng quan trọng nhất để xem tác động chi tiết
print(f"\nGenerating SHAP Dependence Plots...")
mean_abs_shap = np.abs(shap_values).mean(axis=0)
top_features_idx = np.argsort(mean_abs_shap)[::-1][:4]
top_features = [ALL_FEATURES[i] for i in top_features_idx]

for feature in top_features:
    print(f"  Plotting dependence for: {feature}")
    fig, ax = plt.subplots(figsize=(8, 6))
    shap.dependence_plot(feature, shap_values, X_test, feature_names=ALL_FEATURES, ax=ax, show=False)
    plt.title(f"SHAP Dependence: {feature}", fontsize=14, fontweight="bold")
    plt.tight_layout()
    dep_path = Path(OUT_FIG_SHAP) / f"shap_dependence_{feature}.pdf"
    plt.savefig(dep_path, format='pdf', dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {dep_path}")

print(f"\n{'=' * 70}")
print(f"SHAP ANALYSIS COMPLETE")
print(f"{'=' * 70}")
