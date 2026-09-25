"""
02_train_test_split.py
======================
Phase 1 - Step 3: Train/Test Split

Splits the aggregated flight-level features using GroupShuffleSplit by flight date.
- 75% Training / 25% Testing
- Groups by date to prevent data leakage (same-day flights stay together)
- Applies StandardScaler (fit on train, transform both)

Output:
  - output/train.csv
  - output/test.csv
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler
import os
import warnings
warnings.filterwarnings('ignore')

os.makedirs("output", exist_ok=True)

print("=" * 70)
print("STEP 02: TRAIN/TEST SPLIT (GroupShuffleSplit by Date)")
print("=" * 70)

# ============================================================
# 1. Load features
# ============================================================
df = pd.read_csv("output/flight_level_features.csv")
print(f"\n[1] Loaded: {df.shape[0]} flights × {df.shape[1]} columns")

# ============================================================
# 2. Define features and target
# ============================================================
FLIGHT_FEATURES = ["speed", "altitude", "payload", "flight_duration", "distance_3d"]
WEATHER_FEATURES = ["temperature_c", "humidity_pct", "avg_wind_speed", "wind_gust_ms",
                     "wind_dir_deg", "pressure_hpa", "cloud_cover_pct"]
ENGINEERED_FEATURES = ["relative_wind_angle"]
ALL_FEATURES = FLIGHT_FEATURES + WEATHER_FEATURES + ENGINEERED_FEATURES
TARGET = "energy_efficiency"
METADATA = ["flight", "route", "date"]

print(f"\n[2] Feature Configuration:")
print(f"    Flight features ({len(FLIGHT_FEATURES)}): {FLIGHT_FEATURES}")
print(f"    Weather features ({len(WEATHER_FEATURES)}): {WEATHER_FEATURES}")
print(f"    Engineered features ({len(ENGINEERED_FEATURES)}): {ENGINEERED_FEATURES}")
print(f"    Total input features: {len(ALL_FEATURES)}")
print(f"    Target: {TARGET}")

# ============================================================
# 3. GroupShuffleSplit by date
# ============================================================
groups = df["date"]
gss = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=42)

train_idx, test_idx = next(gss.split(df, groups=groups))

train_df = df.iloc[train_idx].copy()
test_df = df.iloc[test_idx].copy()

print(f"\n[3] SPLIT RESULTS (GroupShuffleSplit by date)")
print("-" * 50)
print(f"    Training set: {len(train_df)} flights ({len(train_df)/len(df)*100:.1f}%)")
print(f"    Test set:     {len(test_df)} flights ({len(test_df)/len(df)*100:.1f}%)")

print(f"\n    Training dates ({train_df['date'].nunique()}):")
for d in sorted(train_df["date"].unique()):
    n = (train_df["date"] == d).sum()
    print(f"      {d}: {n} flights")

print(f"\n    Test dates ({test_df['date'].nunique()}):")
for d in sorted(test_df["date"].unique()):
    n = (test_df["date"] == d).sum()
    print(f"      {d}: {n} flights")

# Check no date overlap
train_dates = set(train_df["date"].unique())
test_dates = set(test_df["date"].unique())
overlap = train_dates & test_dates
print(f"\n    Date overlap check: {len(overlap)} overlapping dates {'[OK]' if len(overlap) == 0 else '[WARN] OVERLAP!'}")

# ============================================================
# 4. Target statistics comparison
# ============================================================
print(f"\n[4] TARGET STATISTICS")
print("-" * 50)
print(f"    {'':20s} {'Train':>10s} {'Test':>10s}")
print(f"    {'Mean':20s} {train_df[TARGET].mean():10.4f} {test_df[TARGET].mean():10.4f}")
print(f"    {'Std':20s} {train_df[TARGET].std():10.4f} {test_df[TARGET].std():10.4f}")
print(f"    {'Min':20s} {train_df[TARGET].min():10.4f} {test_df[TARGET].min():10.4f}")
print(f"    {'Max':20s} {train_df[TARGET].max():10.4f} {test_df[TARGET].max():10.4f}")
print(f"    {'Median':20s} {train_df[TARGET].median():10.4f} {test_df[TARGET].median():10.4f}")

# ============================================================
# 5. Feature scaling
# ============================================================
print(f"\n[5] FEATURE SCALING (StandardScaler)")
print("-" * 50)

scaler = StandardScaler()
train_df[ALL_FEATURES] = scaler.fit_transform(train_df[ALL_FEATURES])
test_df[ALL_FEATURES] = scaler.transform(test_df[ALL_FEATURES])

# Save scaler params for reference
scaler_params = pd.DataFrame({
    "feature": ALL_FEATURES,
    "mean": scaler.mean_,
    "std": scaler.scale_
})
scaler_params.to_csv("output/scaler_params.csv", index=False)
print(f"    [OK] Scaler fitted on training data and applied to both sets")
print(f"    [OK] Saved: output/scaler_params.csv")

# Verify scaling
print(f"\n    Post-scaling train feature stats (should be ~0 mean, ~1 std):")
for feat in ALL_FEATURES[:3]:
    print(f"      {feat:25s}: mean={train_df[feat].mean():.4f}, std={train_df[feat].std():.4f}")
print(f"      ... (showing first 3 of {len(ALL_FEATURES)})")

# ============================================================
# 6. Save train and test sets
# ============================================================
# Save all columns (metadata + features + target + battery_consumed_wh for reference)
train_df.to_csv("output/train.csv", index=False)
test_df.to_csv("output/test.csv", index=False)

print(f"\n[6] [OK] Saved: output/train.csv ({train_df.shape[0]} rows × {train_df.shape[1]} cols)")
print(f"    [OK] Saved: output/test.csv ({test_df.shape[0]} rows × {test_df.shape[1]} cols)")

# ============================================================
# 7. Save feature config for downstream scripts
# ============================================================
import json
config = {
    "FLIGHT_FEATURES": FLIGHT_FEATURES,
    "WEATHER_FEATURES": WEATHER_FEATURES,
    "ENGINEERED_FEATURES": ENGINEERED_FEATURES,
    "ALL_FEATURES": ALL_FEATURES,
    "TARGET": TARGET,
    "METADATA": METADATA
}
with open("output/feature_config.json", "w") as f:
    json.dump(config, f, indent=2)
print(f"    [OK] Saved: output/feature_config.json")

print(f"\n{'=' * 70}")
print(f"STEP 02 COMPLETE")
print(f"{'=' * 70}")
