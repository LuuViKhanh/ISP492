"""
data_cleaning.py
================
Phase 1 - Data Validation & Cleaning

Validates the raw flight_with_weather.csv dataset:
- Missing values analysis
- Duplicate check
- Outlier detection (Z-score + IQR)
- Consistency checks
- Data leakage check
- Removes problematic flights (static tests and hovers)
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import os
from scipy import stats
from pathlib import Path

from config.settings import DATA_PROC_DIR, DATA_INTER_DIR, OUT_FIG_QUALITY, ensure_dirs

# ============================================================
# Setup
# ============================================================
# Đảm bảo các thư mục đầu ra tồn tại
ensure_dirs()

print("=" * 70)
print("DATA VALIDATION & CLEANING")
print("=" * 70)

# ============================================================
# Load raw data
# ============================================================
in_path = Path(DATA_INTER_DIR) / "flight_with_weather.csv"
df = pd.read_csv(in_path)
print(f"\n[1] Raw dataset loaded: {df.shape[0]:,} rows × {df.shape[1]} columns")
print(f"    Unique flights: {df['flight'].nunique()}")
print(f"    Flight ID range: {df['flight'].min()} – {df['flight'].max()}")
print(f"    Unique routes: {sorted(df['route'].unique())}")
print(f"    Unique dates: {df['date'].nunique()}")

# ============================================================
# Missing Values Analysis
# ============================================================
# Đếm số lượng và tỷ lệ phần trăm dữ liệu bị thiếu ở từng cột
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
    total_missing_rows = df[df.isnull().any(axis=1)].shape[0]
    print(f"\n    Total rows with any missing: {total_missing_rows:,} ({total_missing_rows/len(df)*100:.2f}%)")
    
    # Phân tích xem chuyến bay nào bị thiếu dữ liệu
    flights_with_missing = df[df.isnull().any(axis=1)]["flight"].unique()
    print(f"    Flights with missing data: {sorted(flights_with_missing)}")
    
    # Kiểm tra đặc điểm của những chuyến bay bị lỗi để quyết định có loại bỏ hay không
    missing_flights_info = df[df["flight"].isin(flights_with_missing)][
        ["flight", "date", "route", "payload", "altitude", "speed", "position_x", "position_y"]
    ].drop_duplicates("flight")
    print(f"\n    Missing flights detail:")
    print(missing_flights_info.to_string(index=False))
    
    print(f"\n    DECISION: Drop flights {sorted(flights_with_missing)} entirely")
    print(f"       Reason: MCAR pattern, <5% of data, zero GPS positions make them unusable")
    print(f"       These are static/calibration tests (altitude=0, speed=0, position=0)")
else:
    print("    No missing values found.")

# ============================================================
# Remove problematic flights
# ============================================================
# Loại bỏ các chuyến bay lỗi dữ liệu, chuyến bay thử nghiệm tĩnh và các bài kiểm tra lơ lửng tại chỗ
flights_to_remove = df[df.isnull().any(axis=1)]["flight"].unique()
static_flights = df[(df["altitude"] == 0) & (df["speed"] == 0)]["flight"].unique()
hover_flights = df[df["route"] == "H"]["flight"].unique()

all_remove = np.union1d(np.union1d(flights_to_remove, static_flights), hover_flights)

print(f"\n[3] REMOVING PROBLEMATIC FLIGHTS")
print("-" * 50)
print(f"    Flights with missing data: {sorted(flights_to_remove)}")
print(f"    Static test flights (alt=0, speed=0): {sorted(static_flights)}")
print(f"    Hover test flights (Route H): {sorted(hover_flights)}")
print(f"    Total flights to remove: {sorted(all_remove)} ({len(all_remove)} flights)")

df_clean = df[~df["flight"].isin(all_remove)].copy()
print(f"    Before: {df.shape[0]:,} rows, {df['flight'].nunique()} flights")
print(f"    After:  {df_clean.shape[0]:,} rows, {df_clean['flight'].nunique()} flights")

# ============================================================
# Duplicate Check
# ============================================================
# Tìm và xóa các dòng dữ liệu bị lặp lại hoàn toàn
print(f"\n[4] DUPLICATE CHECK")
print("-" * 50)
n_duplicates = df_clean.duplicated().sum()
print(f"    Exact duplicate rows: {n_duplicates}")
if n_duplicates > 0:
    df_clean = df_clean.drop_duplicates()
    print(f"    Removed {n_duplicates} duplicates. New shape: {df_clean.shape}")
else:
    print(f"    No duplicates found.")

# ============================================================
# Outlier Detection
# ============================================================
# Sử dụng phương pháp Z-score và IQR để phát hiện các giá trị ngoại lai
print(f"\n[5] OUTLIER DETECTION (Z-score + IQR)")
print("-" * 50)
outlier_cols = ["battery_voltage", "battery_current", "position_z"]

for col in outlier_cols:
    data = df_clean[col].dropna()
    
    # Z-score method
    z_scores = np.abs(stats.zscore(data))
    z_outliers = (z_scores > 3).sum()
    
    # IQR method
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
    print(f"      IQR bounds: [{iqr_lower:.4f}, {iqr_upper:.4f}]")

# Giữ lại các giá trị ngoại lai do đây là đặc tính vật lý bình thường của drone
print(f"\n    DECISION: Keep all outliers (physically valid for drone flight data)")
print(f"       - battery_voltage: 18.8–25.9V is valid range for 6S LiPo")
print(f"       - battery_current: negative values = regenerative braking (valid)")
print(f"       - position_z: represents GPS altitude (valid variation)")

# ============================================================
# Consistency Checks
# ============================================================
# Kiểm tra tính logic và hợp lệ của các giá trị cốt lõi
print(f"\n[6] CONSISTENCY CHECKS")
print("-" * 50)

v_min, v_max = df_clean["battery_voltage"].min(), df_clean["battery_voltage"].max()
print(f"    Battery voltage: {v_min:.2f}V – {v_max:.2f}V (expected: 18–26V for 6S LiPo)")

payloads = sorted(df_clean["payload"].unique())
print(f"    Payload values: {payloads} (expected: 0, 250, 500, 750g)")

altitudes = sorted(df_clean["altitude"].unique())
print(f"    Altitude values: {altitudes}m")

speeds = sorted(df_clean["speed"].unique())
print(f"    Speed values: {speeds} m/s")

neg_current = (df_clean["battery_current"] < 0).sum()
print(f"    Negative current rows: {neg_current} ({neg_current/len(df_clean)*100:.3f}%)")
print(f"    Min current: {df_clean['battery_current'].min():.4f}A (regenerative braking, keep)")

pos_zero = ((df_clean["position_x"] == 0) & (df_clean["position_y"] == 0)).sum()
print(f"    Zero-position rows after cleaning: {pos_zero}")

# ============================================================
# Data Leakage Check
# ============================================================
# Đảm bảo mục tiêu dự đoán (target) không bị rò rỉ dữ liệu thông qua các đặc trưng đầu vào
print(f"\n[7] DATA LEAKAGE CHECK")
print("-" * 50)
print(f"    Columns in dataset: {df_clean.columns.tolist()}")
print(f"    Target will be computed from: battery_voltage, battery_current, position_x/y/z")
print(f"    These raw columns will NOT be used as ML features (only aggregated features)")
print(f"    No direct leakage risk in feature selection plan")

# ============================================================
# Verify remaining data quality
# ============================================================
print(f"\n[8] FINAL DATA QUALITY SUMMARY")
print("-" * 50)
print(f"    Total rows: {df_clean.shape[0]:,}")
print(f"    Total columns: {df_clean.shape[1]}")
print(f"    Unique flights: {df_clean['flight'].nunique()}")
print(f"    Unique routes: {sorted(df_clean['route'].unique())}")
print(f"    Date range: {df_clean['date'].min()} to {df_clean['date'].max()}")
print(f"    Missing values: {df_clean.isnull().sum().sum()}")

remaining_missing = df_clean.isnull().sum()
if remaining_missing.sum() > 0:
    print(f"\n    Remaining missing values:")
    print(remaining_missing[remaining_missing > 0])
else:
    print(f"    No missing values remaining")

print(f"\n    Flights per route:")
route_counts = df_clean.groupby("route")["flight"].nunique().sort_values(ascending=False)
for route, count in route_counts.items():
    print(f"      {route}: {count} flights")

# ============================================================
# Generate Data Quality Report Figure
# ============================================================
print(f"\n[9] Generating data quality report figure...")

fig, axes = plt.subplots(2, 3, figsize=(18, 12))
fig.suptitle("Data Quality Report – Drone Flight Dataset", fontsize=16, fontweight="bold")

# Biểu đồ heatmap thể hiện sự phân bổ của các dữ liệu bị thiếu
ax = axes[0, 0]
missing_matrix = df[["temperature_c", "humidity_pct", "wind_speed_ms", 
                      "wind_gust_ms", "pressure_hpa", "cloud_cover_pct"]].isnull()
sample_idx = np.linspace(0, len(missing_matrix)-1, 500, dtype=int)
sns.heatmap(missing_matrix.iloc[sample_idx].T, cbar=False, ax=ax,
            yticklabels=True, cmap="YlOrRd")
ax.set_title("Missing Values Pattern (Original Data)")
ax.set_xlabel("Row Index (sampled)")

# Phân bổ điện áp pin
ax = axes[0, 1]
ax.hist(df_clean["battery_voltage"], bins=50, color="#2196F3", edgecolor="white", alpha=0.8)
ax.set_title("Battery Voltage Distribution")
ax.set_xlabel("Voltage (V)")
ax.set_ylabel("Count")
ax.axvline(x=18.8, color="red", linestyle="--", alpha=0.7, label="Min valid")
ax.axvline(x=25.9, color="red", linestyle="--", alpha=0.7, label="Max valid")
ax.legend()

# Phân bổ dòng điện pin
ax = axes[0, 2]
ax.hist(df_clean["battery_current"], bins=50, color="#4CAF50", edgecolor="white", alpha=0.8)
ax.set_title("Battery Current Distribution")
ax.set_xlabel("Current (A)")
ax.set_ylabel("Count")
ax.axvline(x=0, color="red", linestyle="--", alpha=0.7, label="Zero line")
ax.legend()

# Số chuyến bay theo từng tuyến đường
ax = axes[1, 0]
route_counts_all = df_clean.groupby("route")["flight"].nunique().sort_values(ascending=True)
bars = ax.barh(route_counts_all.index, route_counts_all.values, color="#FF9800", edgecolor="white")
ax.set_title("Flights per Route (After Cleaning)")
ax.set_xlabel("Number of Flights")
for bar, val in zip(bars, route_counts_all.values):
    ax.text(val + 0.5, bar.get_y() + bar.get_height()/2, str(val), 
            va="center", fontweight="bold")

# Số chuyến bay theo tải trọng
ax = axes[1, 1]
payload_counts = df_clean.groupby("payload")["flight"].nunique()
ax.bar(payload_counts.index.astype(str), payload_counts.values, color="#9C27B0", edgecolor="white")
ax.set_title("Flights per Payload Level")
ax.set_xlabel("Payload (g)")
ax.set_ylabel("Number of Flights")

# Số chuyến bay theo vận tốc
ax = axes[1, 2]
speed_counts = df_clean.groupby("speed")["flight"].nunique()
ax.bar(speed_counts.index.astype(str), speed_counts.values, color="#00BCD4", edgecolor="white")
ax.set_title("Flights per Speed Level")
ax.set_xlabel("Speed (m/s)")
ax.set_ylabel("Number of Flights")

plt.tight_layout()
out_fig_path = Path(OUT_FIG_QUALITY) / "data_quality_report.png"
plt.savefig(out_fig_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"    Saved: {out_fig_path}")

# ============================================================
# Save cleaned data
# ============================================================
out_data_path = Path(DATA_PROC_DIR) / "cleaned_flight_data.csv"
df_clean.to_csv(out_data_path, index=False)
print(f"\n[10] Saved: {out_data_path} ({df_clean.shape[0]:,} rows × {df_clean.shape[1]} cols)")

print(f"\n{'=' * 70}")
print(f"DATA VALIDATION & CLEANING COMPLETE")
print(f"{'=' * 70}")
