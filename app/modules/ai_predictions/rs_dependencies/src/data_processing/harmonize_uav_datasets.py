"""
harmonize_uav_datasets.py
=========================
Phase 12 — Harmonize DJI and VTOL Datasets

Combines two flight-level feature files into a single multi-UAV dataset.
Does NOT rename Dataset 1 (DJI).

Steps:
1. Select common columns
2. Align dtypes
3. Validate units
4. Add dataset_id and uav_type metadata
5. Output combined CSV

Input:
    output/data/features/flight_level_features.csv      (DJI)
    output/data/features/vtol_flight_level_features.csv  (VTOL)
    
Output:
    output/data/features/multiuav_flight_level_features.csv
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUT_DATA_FEATURES, ensure_dirs
from config.multiuav_config import (
    SITE_CONFIG,
    COMMON_PREFLIGHT_FEATURES,
    PRIMARY_TARGET,
    SECONDARY_TARGET,
    METADATA_COLUMNS,
    POST_FLIGHT_FEATURES,
    ROUTE_PROXY_AERO_FEATURES,
    ROUTE_PROXY_METADATA_COLUMNS,
)

# If there were VTOL specific features, they would be defined here
VTOL_SPECIFIC_FEATURES = []

ensure_dirs()

print("=" * 70)
print("HARMONIZE DJI + VTOL DATASETS")
print("=" * 70)

# ============================================================
# Load Both Datasets
# ============================================================
dji_path = Path(OUT_DATA_FEATURES) / "flight_level_features.csv"
vtol_path = Path(OUT_DATA_FEATURES) / "vtol_flight_level_features.csv"

dji_df = pd.read_csv(dji_path)
vtol_df = pd.read_csv(vtol_path)

print(f"\nDJI:  {dji_df.shape[0]} flights × {dji_df.shape[1]} cols")
print(f"VTOL: {vtol_df.shape[0]} flights × {vtol_df.shape[1]} cols")

print(f"\nDJI columns:  {sorted(dji_df.columns.tolist())}")
print(f"VTOL columns: {sorted(vtol_df.columns.tolist())}")

# ============================================================
# Add Dataset Identity
# ============================================================
dji_df["dataset_id"] = SITE_CONFIG["DJI"]["dataset_id"]
dji_df["uav_type"] = SITE_CONFIG["DJI"]["uav_type"]
dji_df["flight_uid"] = dji_df["dataset_id"] + "_" + dji_df["flight"].astype(str)

vtol_df["dataset_id"] = SITE_CONFIG["VTOL"]["dataset_id"]
vtol_df["uav_type"] = SITE_CONFIG["VTOL"]["uav_type"]
vtol_df["flight_uid"] = vtol_df["dataset_id"] + "_" + vtol_df["flight"].astype(str)

# ============================================================
# Identify Common Columns
# ============================================================
# Core columns that both datasets should have
core_columns = (
    ["flight_uid", "flight", "route", "date", "dataset_id", "uav_type"]
    + COMMON_PREFLIGHT_FEATURES
    + POST_FLIGHT_FEATURES
    + ROUTE_PROXY_AERO_FEATURES
    + ROUTE_PROXY_METADATA_COLUMNS
    + [PRIMARY_TARGET, SECONDARY_TARGET]
)

# Check which core columns exist in both
dji_cols = set(dji_df.columns)
vtol_cols = set(vtol_df.columns)
common_core = [c for c in core_columns if c in dji_cols and c in vtol_cols]
dji_only = [c for c in core_columns if c in dji_cols and c not in vtol_cols]
vtol_only = [c for c in core_columns if c not in dji_cols and c in vtol_cols]

print(f"\nCommon core columns ({len(common_core)}):")
for c in common_core:
    print(f"  [OK] {c}")

if dji_only:
    print(f"\nDJI-only columns:")
    for c in dji_only:
        print(f"  DJI: {c}")

if vtol_only:
    print(f"\nVTOL-only columns:")
    for c in vtol_only:
        print(f"  VTOL: {c}")

# ============================================================
# Align and Select Columns
# ============================================================
# For harmonized output: use common_core + VTOL-specific as optional
all_output_cols = common_core.copy()
for c in VTOL_SPECIFIC_FEATURES:
    if c not in all_output_cols:
        all_output_cols.append(c)

# Ensure both DataFrames have all output columns (fill missing with NaN)
for c in all_output_cols:
    if c not in dji_df.columns:
        dji_df[c] = np.nan
    if c not in vtol_df.columns:
        vtol_df[c] = np.nan

dji_selected = dji_df[all_output_cols].copy()
vtol_selected = vtol_df[all_output_cols].copy()

# ============================================================
# Align Dtypes
# ============================================================
numeric_cols = (
    COMMON_PREFLIGHT_FEATURES
    + POST_FLIGHT_FEATURES
    + ROUTE_PROXY_AERO_FEATURES
    + ["route_endpoint_displacement_m", "route_endpoint_to_path_ratio"]
    + [PRIMARY_TARGET, SECONDARY_TARGET]
)
for c in numeric_cols:
    if c in dji_selected.columns:
        dji_selected[c] = pd.to_numeric(dji_selected[c], errors="coerce")
    if c in vtol_selected.columns:
        vtol_selected[c] = pd.to_numeric(vtol_selected[c], errors="coerce")

# ============================================================
# Validate Units (sanity checks)
# ============================================================
print(f"\n{'-' * 60}")
print("UNIT VALIDATION")
print(f"{'-' * 60}")

unit_checks = {
    "speed": {"dji": (0, 15), "vtol": (15, 30), "unit": "m/s"},  # DJI: 4-12, VTOL: 18-22
    "altitude": {"dji": (0, 150), "vtol": (0, 150), "unit": "m"},
    "payload": {"dji": (0, 1000), "vtol": (0, 1000), "unit": "g"},
    "temperature_c": {"dji": (-20, 50), "vtol": (-20, 50), "unit": "°C"},
    "pressure_hpa": {"dji": (900, 1100), "vtol": (900, 1100), "unit": "hPa"},
    "battery_consumed_wh": {"dji": (0, 200), "vtol": (0, 500), "unit": "Wh"},
}

for col, config in unit_checks.items():
    if col in dji_selected.columns and col in vtol_selected.columns:
        dji_range = f"[{dji_selected[col].min():.1f}, {dji_selected[col].max():.1f}]"
        vtol_range = f"[{vtol_selected[col].min():.1f}, {vtol_selected[col].max():.1f}]"
        print(f"  {col:25s} ({config['unit']}): DJI {dji_range:20s} VTOL {vtol_range}")

# ============================================================
# Combine Datasets
# ============================================================
print(f"\n{'-' * 60}")
print("COMBINING DATASETS")
print(f"{'-' * 60}")

combined_df = pd.concat([dji_selected, vtol_selected], ignore_index=True)
print(f"Combined: {combined_df.shape[0]} flights × {combined_df.shape[1]} cols")
print(f"  DJI:  {(combined_df['dataset_id'] == 'DJI_M100').sum()} flights")
print(f"  VTOL: {(combined_df['dataset_id'] == 'VTOL').sum()} flights")

# ============================================================
# Check for flight ID conflicts
# ============================================================
# DJI and VTOL may have overlapping flight IDs
dji_flights = set(dji_selected["flight"])
vtol_flights = set(vtol_selected["flight"])
overlap = dji_flights & vtol_flights
if overlap:
    print(f"\n  WARNING: {len(overlap)} overlapping flight IDs between DJI and VTOL")
    print(f"  Flight IDs are unique WITHIN dataset but may overlap ACROSS datasets")
    print(f"  Use (dataset_id, flight) as composite key")

# ============================================================
# Save Harmonized Dataset
# ============================================================
out_path = Path(OUT_DATA_FEATURES) / "multiuav_flight_level_features.csv"
combined_df.to_csv(out_path, index=False)
print(f"\nSaved: {out_path}")
print(f"  Shape: {combined_df.shape[0]} rows × {combined_df.shape[1]} cols")

# Quick summary
print(f"\n{'-' * 60}")
print("DATASET SUMMARY")
print(f"{'-' * 60}")
for did in combined_df["dataset_id"].unique():
    subset = combined_df[combined_df["dataset_id"] == did]
    print(f"\n  {did}:")
    print(f"    Flights: {len(subset)}")
    print(f"    Dates: {subset['date'].nunique()}")
    print(f"    Speed range: [{subset['speed'].min():.1f}, {subset['speed'].max():.1f}] m/s")
    print(f"    Payload range: [{subset['payload'].min():.0f}, {subset['payload'].max():.0f}] g")
    print(f"    Altitude range: [{subset['altitude'].min():.0f}, {subset['altitude'].max():.0f}] m")
    print(f"    Energy range: [{subset['battery_consumed_wh'].min():.1f}, {subset['battery_consumed_wh'].max():.1f}] Wh")
    print(f"    EE range: [{subset['energy_efficiency'].min():.1f}, {subset['energy_efficiency'].max():.1f}] m/Wh")

print(f"\n{'=' * 70}")
print("HARMONIZATION COMPLETE")
print(f"{'=' * 70}")
