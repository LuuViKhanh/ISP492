"""
build_vtol_raw_dataset.py
=========================
Phase 3-6 — Build VTOL Raw Dataset

Extracts VTOL ROS bags using shared extractor, merges parameters.csv,
fetches weather from Open-Meteo API (same as DJI pipeline), and outputs
intermediate dataset.

Output: data/intermediate/vtol_flight_with_weather.csv
"""

import pandas as pd
import numpy as np
from pathlib import Path
import requests
import time
import re
import argparse
import sys
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import DATA_RAW_VTOL, DATA_INTERMEDIATE_DIR
from src.data_processing.rosbag_extractor import extract_rosbag, reorder_columns

# ============================================================
# VTOL Site Configuration (Nardo Flight Test Field, PA)
# ============================================================
VTOL_LAT = 40.5834006
VTOL_LON = -79.8997747
VTOL_TIMEZONE = "America/New_York"


# ============================================================
# Weather API (same as DJI pipeline)
# ============================================================

def fetch_day(date_str, lat, lon, timezone="America/New_York"):
    """
    Gọi Open-Meteo Archive API cho một ngày tại tọa độ (lat, lon).
    Trả về dict {hour (0-23): {các biến thời tiết}} cho đủ 24 giờ.
    """
    r = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude":   lat,
            "longitude":  lon,
            "start_date": date_str,
            "end_date":   date_str,
            "hourly": ",".join([
                "temperature_2m",
                "apparent_temperature",
                "dew_point_2m",
                "relative_humidity_2m",
                "wind_speed_10m",
                "wind_gusts_10m",
                "wind_direction_10m",
                "precipitation",
                "surface_pressure",
                "cloud_cover",
            ]),
            "wind_speed_unit": "ms",
            "timezone": timezone,
        },
        timeout=15,
    )
    r.raise_for_status()
    h = r.json()["hourly"]
    return {
        i: {
            "temperature_c":    h["temperature_2m"][i],
            "apparent_temp_c":  h["apparent_temperature"][i],
            "dew_point_c":      h["dew_point_2m"][i],
            "humidity_pct":     h["relative_humidity_2m"][i],
            "wind_speed_ms":    h["wind_speed_10m"][i],
            "wind_gust_ms":     h["wind_gusts_10m"][i],
            "wind_dir_deg":     h["wind_direction_10m"][i],
            "precipitation_mm": h["precipitation"][i],
            "pressure_hpa":     h["surface_pressure"][i],
            "cloud_cover_pct":  h["cloud_cover"][i],
        }
        for i in range(24)
    }


# ============================================================
# VTOL Parameters Parser
# ============================================================

def parse_vtol_parameters(params_path):
    """
    Parse VTOL parameters.csv with station weather data.
    
    Normalizes:
    - date format: YYYY-MM-DD
    - local_time → time_day (HH:MM format)
    - Keeps VTOL-specific: pattern, battery_discharge_rate, Condition
    - Does NOT rename pattern → route
    """
    params = pd.read_csv(params_path)
    
    # Normalize date format
    params["date"] = pd.to_datetime(params["date"]).dt.strftime("%Y-%m-%d")
    
    # Normalize local_time to time_day
    # local_time is like "12:57" or "1:18" (single digit hour)
    def normalize_time(t):
        t = str(t).strip()
        parts = t.split(":")
        if len(parts) == 2:
            h = int(parts[0])
            m = int(parts[1])
            return f"{h}:{m:02d}"
        return t
    
    params["time_day"] = params["local_time"].apply(normalize_time)
    
    # Rename Condition to lowercase
    if "Condition" in params.columns:
        params.rename(columns={"Condition": "condition"}, inplace=True)
    
    # Select and return relevant columns
    keep_cols = [
        "flight", "speed", "payload", "altitude", "date", "time_day",
        "pattern", "battery_discharge_rate",
    ]
    if "condition" in params.columns:
        keep_cols.append("condition")
    
    return params[keep_cols].copy()


def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    print("=" * 60)
    print("VTOL — Read Raw Bag Files")
    print("=" * 60)
    
    all_flights = []
    raw_root = DATA_RAW_VTOL / "rosbag"
    
    if not raw_root.exists():
        print(f"Error: {raw_root} does not exist.")
        return

    # Sắp xếp theo số thứ tự
    folders = [f for f in raw_root.iterdir() if f.is_dir()]
    folders = sorted(folders, key=lambda x: int(x.name) if x.name.isdigit() else 9999)

    for folder in folders:
        if folder.name.isdigit():
            try:
                flight_id = int(folder.name)
                bag_path = folder / "raw.bag"
                
                if bag_path.exists():
                    print(f"Processing VTOL Flight {flight_id}")
                    df = extract_rosbag(bag_path, flight_id)
                    if not df.empty:
                        all_flights.append(df)
                    else:
                        print(f"  WARNING: Flight {flight_id} returned empty data")
                else:
                    print(f"Skipped Flight {flight_id}: raw.bag not found")
            except Exception as e:
                print(f"Error Flight {folder.name}: {e}")

    if not all_flights:
        print("No flight data processed. Exiting.")
        return

    final_df = pd.concat(all_flights, ignore_index=True)
    print(f"\nExtracted {len(final_df):,} rows from {len(all_flights)} flights.")

    # ============================================================
    # Merge VTOL Parameters
    # ============================================================
    print("\n" + "=" * 60)
    print("Merge VTOL Parameters")
    print("=" * 60)
    
    params = parse_vtol_parameters(DATA_RAW_VTOL / "parameters.csv")
    print(f"Parameters loaded: {len(params)} flights")
    print(f"  Speed values: {sorted(params['speed'].unique())}")
    print(f"  Payload values: {sorted(params['payload'].unique())}")
    print(f"  Altitude values: {sorted(params['altitude'].unique())}")
    print(f"  Pattern values: {sorted(params['pattern'].unique())}")
    
    final_df = final_df.merge(params, on="flight", how="left")

    # Normalize date format
    if "date" in final_df.columns:
        final_df["date"] = pd.to_datetime(final_df["date"]).dt.strftime("%Y-%m-%d")

    # Reorder columns: telemetry first, then parameters
    param_cols = ["speed", "payload", "altitude", "date", "time_day",
                  "pattern", "battery_discharge_rate"]
    if "condition" in final_df.columns:
        param_cols.append("condition")
    final_df = reorder_columns(final_df, extra_cols=param_cols)

    # ============================================================
    # Add Weather Data (Open-Meteo API — same source as DJI)
    # ============================================================
    print("\n" + "=" * 60)
    print("Add Weather Data (Open-Meteo API)")
    print("=" * 60)

    weather_cols = [
        "temperature_c", "apparent_temp_c", "dew_point_c", "humidity_pct",
        "wind_speed_ms", "wind_gust_ms", "wind_dir_deg",
        "precipitation_mm", "pressure_hpa", "cloud_cover_pct",
    ]

    # VTOL flights are all at Nardo — use fixed GPS coordinates
    # Compute actual_hour from time_day + relative time in bag
    print("Computing actual time per row...")
    
    base_datetime = pd.to_datetime(
        final_df["date"].astype(str) + " " + final_df["time_day"].astype(str),
        format="%Y-%m-%d %H:%M"
    )
    final_df["actual_dt"] = (
        base_datetime
        + pd.to_timedelta(final_df["time"].astype(float), unit="s")
    )
    final_df["actual_date"] = final_df["actual_dt"].dt.strftime("%Y-%m-%d")
    final_df["actual_hour"] = final_df["actual_dt"].dt.hour

    # Unique dates for API calls (all at same coordinates)
    unique_dates = final_df["actual_date"].unique()
    print(f"API calls needed: {len(unique_dates)} (unique dates at Nardo)")

    api_cache = {}
    for i, date_str in enumerate(sorted(unique_dates)):
        try:
            api_cache[date_str] = fetch_day(date_str, VTOL_LAT, VTOL_LON, VTOL_TIMEZONE)
            print(f"  [{i+1}/{len(unique_dates)}] {date_str} OK")
        except Exception as e:
            print(f"  [{i+1}/{len(unique_dates)}] {date_str} ERROR: {e}")
            api_cache[date_str] = {}
        time.sleep(0.3)

    # Assign weather to each row by actual_hour
    print(f"\nAssigning weather to {len(final_df):,} rows...")
    
    weather_rows = []
    for date_str, hourly_data in api_cache.items():
        for hour, weather_data in hourly_data.items():
            row = {"actual_date": date_str, "actual_hour": hour}
            row.update(weather_data)
            weather_rows.append(row)

    weather_df = pd.DataFrame(weather_rows)

    if not weather_df.empty:
        final_df = final_df.merge(
            weather_df,
            on=["actual_date", "actual_hour"],
            how="left"
        )
    else:
        for c in weather_cols:
            final_df[c] = None

    # Drop temp columns
    final_df.drop(
        columns=["actual_dt", "actual_date", "actual_hour"],
        inplace=True,
        errors="ignore"
    )

    # ============================================================
    # Save VTOL Raw Dataset
    # ============================================================
    print("\n" + "=" * 60)
    print("Save VTOL Raw Dataset")
    print("=" * 60)
    
    out_path = Path(DATA_INTERMEDIATE_DIR) / "vtol_flight_with_weather.csv"
    
    print("Final shape:", final_df.shape)
    print("Columns:", list(final_df.columns))
    print("Missing weather rows:", final_df["temperature_c"].isnull().sum())
    print(f"Unique flights: {final_df['flight'].nunique()}")
    
    final_df.to_csv(out_path, index=False)
    print(f"Saved VTOL raw dataset to {out_path}!")


if __name__ == "__main__":
    main()
