"""
vtol_data_cleaning.py
=====================
Phase 7 — VTOL Data Validation & Cleaning

Shared cleaning framework with DJI but VTOL-specific rules:
- Does NOT remove low-speed segments (VTOL has valid vertical takeoff/transition)
- Does NOT apply hover removal or route exclusion (no hover test flights)
- Applies: missing checks, duplicate checks, invalid battery, timestamp order,
  sensor NaN checks, outlier reporting

Output: data/processed/vtol_cleaned_flight_data.csv
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from pathlib import Path
import sys
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import (
    DATA_INTERMEDIATE_DIR, DATA_PROCESSED_DIR,
    OUT_FIG_QUALITY, ensure_dirs,
)

# ============================================================
# Setup
# ============================================================
ensure_dirs()

print("=" * 70)
print("VTOL DATA VALIDATION & CLEANING")
print("=" * 70)

# ============================================================
# Load VTOL intermediate data
# ============================================================
in_path = Path(DATA_INTERMEDIATE_DIR) / "vtol_flight_with_weather.csv"
df = pd.read_csv(in_path)
print(f"\n[1] VTOL raw dataset loaded: {df.shape[0]:,} rows × {df.shape[1]} columns")
print(f"    Unique flights: {df['flight'].nunique()}")
print(f"    Flight ID range: {df['flight'].min()} – {df['flight'].max()}")
if "pattern" in df.columns:
    print(f"    Patterns: {sorted(df['pattern'].unique())}")
print(f"    Unique dates: {df['date'].nunique()}")

# ============================================================
# Missing Values Analysis
# ============================================================
print(f"\n[2] MISSING VALUES ANALYSIS")
print("-" * 50)
missing = df.isnull().sum()
missing_pct = (missing / len(df) * 100).round(2)
missing_df = pd.DataFrame({
    "Missing Count": missing[missing > 0],
    "Missing %": missing_pct[missing > 0]
})
if len(missing_df) > 0:
    print(missing_df.to_string())
    flights_with_missing = df[df.isnull().any(axis=1)]["flight"].unique()
    print(f"\n    Flights with any missing data: {sorted(flights_with_missing)}")
else:
    print("    No missing values found.")

# ============================================================
# Remove problematic flights (VTOL-specific rules)
# ============================================================
flights_to_remove = set()
removal_reasons = {}

# Flights with excessive missing data (>50% of rows in a flight)
for fid in df["flight"].unique():
    fdata = df[df["flight"] == fid]
    missing_pct_flight = fdata.isnull().any(axis=1).sum() / len(fdata) * 100
    if missing_pct_flight > 50:
        flights_to_remove.add(fid)
        removal_reasons[fid] = f"excessive_missing ({missing_pct_flight:.1f}%)"

# Flights with no battery data
for fid in df["flight"].unique():
    fdata = df[df["flight"] == fid]
    if fdata["battery_voltage"].isnull().all() or fdata["battery_current"].isnull().all():
        flights_to_remove.add(fid)
        removal_reasons[fid] = "no_battery_data"

# Flights with very few rows (< 10 telemetry points)
for fid in df["flight"].unique():
    fdata = df[df["flight"] == fid]
    if len(fdata) < 10:
        flights_to_remove.add(fid)
        removal_reasons[fid] = f"too_few_rows ({len(fdata)})"

# Flights with impossible timestamp ordering
for fid in df["flight"].unique():
    fdata = df[df["flight"] == fid].sort_values("time")
    if len(fdata) > 1 and (fdata["time"].diff().dropna() < 0).any():
        flights_to_remove.add(fid)
        removal_reasons[fid] = "negative_time_deltas"

print(f"\n[3] REMOVING PROBLEMATIC FLIGHTS")
print("-" * 50)
if flights_to_remove:
    for fid in sorted(flights_to_remove):
        print(f"    Flight {fid}: {removal_reasons[fid]}")
else:
    print("    No flights to remove.")

n_before_flights = df["flight"].nunique()
n_before_rows = len(df)
df_clean = df[~df["flight"].isin(flights_to_remove)].copy()

print(f"    Before: {n_before_rows:,} rows, {n_before_flights} flights")
print(f"    After:  {len(df_clean):,} rows, {df_clean['flight'].nunique()} flights")
print(f"    Removed: {len(flights_to_remove)} flights")

# ============================================================
# Duplicate Check
# ============================================================
print(f"\n[4] DUPLICATE CHECK")
print("-" * 50)
n_dup = df_clean.duplicated().sum()
print(f"    Exact duplicate rows: {n_dup}")
if n_dup > 0:
    df_clean = df_clean.drop_duplicates()
    print(f"    Removed {n_dup} duplicates. New shape: {df_clean.shape}")
else:
    print("    No duplicates found.")

# ============================================================
# Outlier Detection (report only — keep for VTOL)
# ============================================================
print(f"\n[5] OUTLIER DETECTION (Z-score + IQR)")
print("-" * 50)
outlier_cols = ["battery_voltage", "battery_current", "position_z"]

for col in outlier_cols:
    data = df_clean[col].dropna()
    if len(data) == 0:
        print(f"    {col}: no data")
        continue
    z_scores = np.abs(stats.zscore(data))
    z_outliers = (z_scores > 3).sum()
    Q1 = data.quantile(0.25)
    Q3 = data.quantile(0.75)
    IQR = Q3 - Q1
    iqr_lower = Q1 - 1.5 * IQR
    iqr_upper = Q3 + 1.5 * IQR
    iqr_outliers = ((data < iqr_lower) | (data > iqr_upper)).sum()
    print(f"    {col}:")
    print(f"      Range: [{data.min():.4f}, {data.max():.4f}]")
    print(f"      Z-score outliers (|z|>3): {z_outliers} ({z_outliers/len(data)*100:.2f}%)")
    print(f"      IQR outliers: {iqr_outliers} ({iqr_outliers/len(data)*100:.2f}%)")

    print(f"\n    DECISION: Keep all outliers (physically valid for VTOL flight data)")
print(f"       VTOL has valid low-speed segments (vertical takeoff/transition)")

# ============================================================
# Sanitize Extreme Velocity Outliers (VTOL-specific sanity mask)
# ============================================================
print(f"\n[5.5] SANITIZE EXTREME VELOCITY OUTLIERS")
print("-" * 50)
vel_cols = ["velocity_x", "velocity_y", "velocity_z"]
VEL_THRESHOLD = 100.0  # m/s

for col in vel_cols:
    if col in df_clean.columns:
        outliers = np.abs(df_clean[col]) > VEL_THRESHOLD
        n_out = outliers.sum()
        if n_out > 0:
            df_clean.loc[outliers, col] = np.nan
            print(f"    {col}: Masked {n_out} values exceeding |v| > {VEL_THRESHOLD} m/s to NaN")
        else:
            print(f"    {col}: No extreme outliers")

# ============================================================
# Consistency Checks
# ============================================================
print(f"\n[6] CONSISTENCY CHECKS")
print("-" * 50)
v_min, v_max = df_clean["battery_voltage"].min(), df_clean["battery_voltage"].max()
print(f"    Battery voltage: {v_min:.2f}V – {v_max:.2f}V")

payloads = sorted(df_clean["payload"].unique())
print(f"    Payload values: {payloads}")

altitudes = sorted(df_clean["altitude"].unique())
print(f"    Altitude values: {altitudes}")

speeds = sorted(df_clean["speed"].unique())
print(f"    Speed values: {speeds} m/s")

if "pattern" in df_clean.columns:
    patterns = sorted(df_clean["pattern"].unique())
    print(f"    Patterns: {patterns}")

neg_current = (df_clean["battery_current"] < 0).sum()
total = len(df_clean)
print(f"    Negative current rows: {neg_current} ({neg_current/total*100:.3f}%)")

# ============================================================
# Final Summary
# ============================================================
print(f"\n[7] FINAL DATA QUALITY SUMMARY")
print("-" * 50)
print(f"    Total rows: {df_clean.shape[0]:,}")
print(f"    Total columns: {df_clean.shape[1]}")
print(f"    Unique flights: {df_clean['flight'].nunique()}")
print(f"    Date range: {df_clean['date'].min()} to {df_clean['date'].max()}")
print(f"    Missing values: {df_clean.isnull().sum().sum()}")

remaining_missing = df_clean.isnull().sum()
if remaining_missing.sum() > 0:
    print(f"\n    Remaining missing values:")
    for col, cnt in remaining_missing[remaining_missing > 0].items():
        print(f"      {col}: {cnt}")

if "pattern" in df_clean.columns:
    print(f"\n    Flights per pattern:")
    for pat, cnt in df_clean.groupby("pattern")["flight"].nunique().items():
        print(f"      {pat}: {cnt} flights")

# ============================================================
# Generate VTOL Quality Report Figure
# ============================================================
print(f"\n[8] Generating VTOL data quality report figure...")

fig, axes = plt.subplots(2, 3, figsize=(18, 12))
fig.suptitle("Data Quality Report — VTOL Package Delivery Drone", fontsize=16, fontweight="bold")

# Battery voltage distribution
ax = axes[0, 0]
ax.hist(df_clean["battery_voltage"].dropna(), bins=50, color="#2196F3", edgecolor="white", alpha=0.8)
ax.set_title("Battery Voltage Distribution")
ax.set_xlabel("Voltage (V)")
ax.set_ylabel("Count")

# Battery current distribution
ax = axes[0, 1]
ax.hist(df_clean["battery_current"].dropna(), bins=50, color="#4CAF50", edgecolor="white", alpha=0.8)
ax.set_title("Battery Current Distribution")
ax.set_xlabel("Current (A)")
ax.set_ylabel("Count")
ax.axvline(x=0, color="red", linestyle="--", alpha=0.7, label="Zero line")
ax.legend()

# Flights per payload
ax = axes[0, 2]
payload_counts = df_clean.groupby("payload")["flight"].nunique()
ax.bar(payload_counts.index.astype(str), payload_counts.values, color="#9C27B0", edgecolor="white")
ax.set_title("Flights per Payload Level")
ax.set_xlabel("Payload (g)")
ax.set_ylabel("Number of Flights")

# Flights per speed
ax = axes[1, 0]
speed_counts = df_clean.groupby("speed")["flight"].nunique()
ax.bar(speed_counts.index.astype(str), speed_counts.values, color="#00BCD4", edgecolor="white")
ax.set_title("Flights per Speed Level")
ax.set_xlabel("Speed (m/s)")
ax.set_ylabel("Number of Flights")

# Flights per pattern
ax = axes[1, 1]
if "pattern" in df_clean.columns:
    pat_counts = df_clean.groupby("pattern")["flight"].nunique()
    ax.bar(pat_counts.index, pat_counts.values, color="#FF9800", edgecolor="white")
    ax.set_title("Flights per Pattern")
    ax.set_xlabel("Pattern")
    ax.set_ylabel("Number of Flights")
else:
    ax.set_visible(False)

# Flights per altitude
ax = axes[1, 2]
alt_counts = df_clean.groupby("altitude")["flight"].nunique()
ax.bar(alt_counts.index.astype(str), alt_counts.values, color="#E91E63", edgecolor="white")
ax.set_title("Flights per Altitude Level")
ax.set_xlabel("Altitude (m)")
ax.set_ylabel("Number of Flights")

plt.tight_layout()
out_fig = Path(OUT_FIG_QUALITY) / "vtol_data_quality_report.png"
plt.savefig(out_fig, dpi=150, bbox_inches="tight")
plt.close()
print(f"    Saved: {out_fig}")

# ============================================================
# Save Cleaned Data
# ============================================================
out_path = Path(DATA_PROCESSED_DIR) / "vtol_cleaned_flight_data.csv"
df_clean.to_csv(out_path, index=False)
print(f"\n[9] Saved: {out_path} ({df_clean.shape[0]:,} rows × {df_clean.shape[1]} cols)")

# Cleaning log
print(f"\n    CLEANING LOG:")
print(f"    Flights before: {n_before_flights}")
print(f"    Flights after:  {df_clean['flight'].nunique()}")
print(f"    Flights removed: {len(flights_to_remove)}")
print(f"    Rows before: {n_before_rows:,}")
print(f"    Rows after:  {len(df_clean):,}")
for fid in sorted(flights_to_remove):
    print(f"      Flight {fid} removed: {removal_reasons[fid]}")

print(f"\n{'=' * 70}")
print("VTOL DATA VALIDATION & CLEANING COMPLETE")
print(f"{'=' * 70}")
