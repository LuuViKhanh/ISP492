"""
validate_multiuav_data.py
=========================
Phase 13 — Validate Harmonized Multi-UAV Data

Performs 10 validation checks on the combined dataset:
1. Same units (range-based sanity)
2. Target > 0
3. Missing %
4. Duplicate flight IDs within dataset
5. Range comparison
6. Date parse
7. Invalid weather
8. Impossible battery values
9. Distance sanity
10. Energy sanity

Output: Console report + CSV report
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUT_DATA_FEATURES, OUT_RES_MULTIUAV, ensure_dirs
from config.multiuav_config import (
    COMMON_PREFLIGHT_FEATURES, PRIMARY_TARGET, SECONDARY_TARGET, ROUTE_PROXY_AERO_FEATURES
)

ensure_dirs()

print("=" * 70)
print("VALIDATE HARMONIZED MULTI-UAV DATA")
print("=" * 70)

# ============================================================
# Load Data
# ============================================================
in_path = Path(OUT_DATA_FEATURES) / "multiuav_flight_level_features.csv"
df = pd.read_csv(in_path)
print(f"\nLoaded: {df.shape[0]} flights × {df.shape[1]} cols")
print(f"Datasets: {df['dataset_id'].value_counts().to_dict()}")

issues = []

# ============================================================
# Check 1: Target > 0
# ============================================================
print(f"\n[1] TARGET VALIDITY")
print("-" * 50)
for target in [PRIMARY_TARGET, SECONDARY_TARGET]:
    if target not in df.columns:
        issues.append(f"Missing target column: {target}")
        print(f"  [X] {target}: MISSING COLUMN")
        continue
    
    n_neg = (df[target] <= 0).sum()
    n_nan = df[target].isnull().sum()
    if n_neg > 0:
        issues.append(f"{target}: {n_neg} flights with value <= 0")
        print(f"  [X] {target}: {n_neg} flights with value ≤ 0")
    elif n_nan > 0:
        print(f"  [!] {target}: {n_nan} NaN values")
    else:
        print(f"  [OK] {target}: all values > 0")

# ============================================================
# Check 2: Missing % per Feature per Dataset
# ============================================================
print(f"\n[2] MISSING VALUES BY DATASET")
print("-" * 50)

check_cols = COMMON_PREFLIGHT_FEATURES + ROUTE_PROXY_AERO_FEATURES + [PRIMARY_TARGET, SECONDARY_TARGET, "flight_duration", "distance"]
missing_report = []

for col in check_cols:
    if col not in df.columns:
        continue
    for did in df["dataset_id"].unique():
        subset = df[df["dataset_id"] == did]
        n_miss = subset[col].isnull().sum()
        pct = n_miss / len(subset) * 100
        missing_report.append({
            "feature": col,
            "dataset_id": did,
            "missing_count": n_miss,
            "missing_pct": round(pct, 2),
            "total": len(subset),
        })
        if pct > 0:
            status = "[!]" if pct < 10 else "[X]"
            print(f"  {status} {col:25s} [{did}]: {n_miss}/{len(subset)} ({pct:.1f}%)")
            if pct > 50:
                issues.append(f"{col} [{did}]: {pct:.1f}% missing")

if not any(r["missing_count"] > 0 for r in missing_report):
    print("  [OK] No missing values in any feature")

# ============================================================
# Check 3: Duplicate Flight IDs Within Dataset
# ============================================================
print(f"\n[3] DUPLICATE FLIGHT IDs WITHIN DATASET")
print("-" * 50)
for did in df["dataset_id"].unique():
    subset = df[df["dataset_id"] == did]
    n_dup = subset["flight"].duplicated().sum()
    if n_dup > 0:
        issues.append(f"Duplicate flight IDs in {did}: {n_dup}")
        print(f"  [X] {did}: {n_dup} duplicate flight IDs")
    else:
        print(f"  [OK] {did}: all flight IDs unique")

# ============================================================
# Check 4: Range Comparison
# ============================================================
print(f"\n[4] FEATURE RANGE COMPARISON")
print("-" * 50)

range_report = []
compare_cols = COMMON_PREFLIGHT_FEATURES + ROUTE_PROXY_AERO_FEATURES + [PRIMARY_TARGET, SECONDARY_TARGET, "flight_duration", "distance"]

for col in compare_cols:
    if col not in df.columns:
        continue
    row = {"feature": col}
    for did in sorted(df["dataset_id"].unique()):
        subset = df[df["dataset_id"] == did][col].dropna()
        if len(subset) > 0:
            row[f"{did}_min"] = round(subset.min(), 4)
            row[f"{did}_max"] = round(subset.max(), 4)
            row[f"{did}_mean"] = round(subset.mean(), 4)
        else:
            row[f"{did}_min"] = np.nan
            row[f"{did}_max"] = np.nan
            row[f"{did}_mean"] = np.nan
    
    # Check overlap
    datasets = sorted(df["dataset_id"].unique())
    if len(datasets) == 2:
        d1, d2 = datasets
        min1, max1 = row.get(f"{d1}_min", np.nan), row.get(f"{d1}_max", np.nan)
        min2, max2 = row.get(f"{d2}_min", np.nan), row.get(f"{d2}_max", np.nan)
        if not (np.isnan(min1) or np.isnan(min2)):
            overlap_start = max(min1, min2)
            overlap_end = min(max1, max2)
            total_range = max(max1, max2) - min(min1, min2)
            if total_range > 0 and overlap_end > overlap_start:
                row["overlap_pct"] = round((overlap_end - overlap_start) / total_range * 100, 1)
            else:
                row["overlap_pct"] = 0.0
        else:
            row["overlap_pct"] = np.nan
    
    range_report.append(row)

range_df = pd.DataFrame(range_report)
print(range_df.to_string(index=False))

# ============================================================
# Check 5: Date Parse
# ============================================================
print(f"\n[5] DATE VALIDATION")
print("-" * 50)
try:
    dates = pd.to_datetime(df["date"], errors="coerce")
    n_bad = dates.isnull().sum()
    if n_bad > 0:
        issues.append(f"Unparseable dates: {n_bad}")
        print(f"  [X] {n_bad} unparseable dates")
    else:
        print(f"  [OK] All dates parse correctly")
        for did in df["dataset_id"].unique():
            subset_dates = dates[df["dataset_id"] == did]
            print(f"    {did}: {subset_dates.min().date()} to {subset_dates.max().date()}")
except Exception as e:
    issues.append(f"Date parse error: {e}")
    print(f"  [X] Date parse error: {e}")

# ============================================================
# Check 6: Invalid Weather Values
# ============================================================
print(f"\n[6] WEATHER SANITY")
print("-" * 50)
weather_checks = {
    "temperature_c": (-50, 60),
    "humidity_pct": (0, 100),
    "pressure_hpa": (800, 1100),
    "wind_speed_ms": (0, 50),
    "wind_gust_ms": (0, 80),
    "wind_dir_deg": (0, 360),
}
for col, (vmin, vmax) in weather_checks.items():
    if col not in df.columns:
        continue
    vals = df[col].dropna()
    n_out = ((vals < vmin) | (vals > vmax)).sum()
    if n_out > 0:
        issues.append(f"{col}: {n_out} values outside [{vmin}, {vmax}]")
        print(f"  [!] {col}: {n_out} values outside [{vmin}, {vmax}]")
    else:
        print(f"  [OK] {col}: all values within [{vmin}, {vmax}]")

# Route-proxy aerodynamic sanity. These are candidate features, not automatically
# selected final inputs.
if "planned_relative_wind_angle" in df.columns:
    vals = df["planned_relative_wind_angle"].dropna()
    n_out = ((vals < 0) | (vals > 180)).sum()
    if n_out:
        issues.append(f"planned_relative_wind_angle: {n_out} values outside [0, 180]")
    else:
        print("  [OK] planned_relative_wind_angle: all values within [0, 180]")
if "planned_crosswind_component" in df.columns:
    vals = df["planned_crosswind_component"].dropna()
    n_out = (vals < 0).sum()
    if n_out:
        issues.append(f"planned_crosswind_component: {n_out} negative values")
    else:
        print("  [OK] planned_crosswind_component: non-negative magnitude")

# ============================================================
# Check 7: Impossible Battery Values
# ============================================================
print(f"\n[7] BATTERY ENERGY SANITY")
print("-" * 50)
if "battery_consumed_wh" in df.columns:
    n_neg_energy = (df["battery_consumed_wh"] < 0).sum()
    n_extreme = (df["battery_consumed_wh"] > 1000).sum()  # > 1kWh seems extreme for UAV
    if n_neg_energy > 0:
        issues.append(f"Negative battery energy: {n_neg_energy}")
        print(f"  [X] {n_neg_energy} flights with negative energy")
    elif n_extreme > 0:
        print(f"  [!] {n_extreme} flights with energy > 1000 Wh (check if valid)")
    else:
        print(f"  [OK] All battery energy values look reasonable")

# ============================================================
# Check 8: Distance Sanity
# ============================================================
print(f"\n[8] DISTANCE SANITY")
print("-" * 50)
if "distance" in df.columns:
    n_zero = (df["distance"] == 0).sum()
    n_neg = (df["distance"] < 0).sum()
    n_extreme = (df["distance"] > 100000).sum()  # > 100 km
    if n_neg > 0:
        issues.append(f"Negative distance: {n_neg}")
        print(f"  [X] {n_neg} flights with negative distance")
    elif n_zero > 0:
        print(f"  [!] {n_zero} flights with zero distance")
    elif n_extreme > 0:
        print(f"  [!] {n_extreme} flights with distance > 100 km (check if valid)")
    else:
        print(f"  [OK] All distance values look reasonable")

# ============================================================
# Check 9: Energy Efficiency Sanity
# ============================================================
print(f"\n[9] ENERGY EFFICIENCY SANITY")
print("-" * 50)
if "energy_efficiency" in df.columns:
    for did in df["dataset_id"].unique():
        subset = df[df["dataset_id"] == did]["energy_efficiency"].dropna()
        print(f"  {did}: mean={subset.mean():.2f}, median={subset.median():.2f}, std={subset.std():.2f} m/Wh")

# ============================================================
# Check 10: Cross-Dataset Flight Duration Comparison
# ============================================================
print(f"\n[10] FLIGHT DURATION COMPARISON")
print("-" * 50)
if "flight_duration" in df.columns:
    for did in df["dataset_id"].unique():
        subset = df[df["dataset_id"] == did]["flight_duration"].dropna()
        print(f"  {did}: mean={subset.mean():.1f}s, range=[{subset.min():.1f}, {subset.max():.1f}]s")

# ============================================================
# Summary
# ============================================================
print(f"\n{'=' * 70}")
print("VALIDATION SUMMARY")
print(f"{'=' * 70}")

if issues:
    print(f"\n  [!] {len(issues)} issue(s) found:")
    for i, issue in enumerate(issues, 1):
        print(f"    {i}. {issue}")
else:
    print(f"\n  [OK] All validation checks passed!")

# Save reports
out_range = Path(OUT_RES_MULTIUAV) / "feature_range_comparison.csv"
range_df.to_csv(out_range, index=False)
print(f"\n  Saved range report: {out_range}")

missing_report_df = pd.DataFrame(missing_report)
out_missing = Path(OUT_RES_MULTIUAV) / "missing_values_report.csv"
missing_report_df.to_csv(out_missing, index=False)
print(f"  Saved missing report: {out_missing}")

print(f"\n{'=' * 70}")
print("VALIDATION COMPLETE")
print(f"{'=' * 70}")
