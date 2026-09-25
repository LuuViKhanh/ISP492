import pandas as pd
import numpy as np
from pathlib import Path
import requests
import time
from datetime import datetime
import argparse
import os

from config.paths import DATA_RAW_DIR, DATA_PROCESSED_DIR, DATA_INTERMEDIATE_DIR
from src.data_processing.rosbag_extractor import extract_rosbag, reorder_columns

def process_bag(bag_path, flight_id):
    """
    Process raw ROS bag file to extract wind, battery, IMU, and odometry data.
    Delegates to shared rosbag_extractor module.
    """
    return extract_rosbag(bag_path, flight_id)

def fetch_day(date_str, lat, lon):
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
            "timezone": "America/New_York",
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

def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    print("="*60)
    print("Read Raw Bag Files")
    print("="*60)
    
    all_flights = []
    dji_root = Path(DATA_RAW_DIR) / "dji"
    raw_root = dji_root / "rosbag"
    
    if not raw_root.exists():
        print(f"Error: {raw_root} does not exist.")
        return

    # Sắp xếp các thư mục theo số thứ tự thay vì chuỗi ký tự
    folders = [f for f in raw_root.iterdir() if f.is_dir()]
    folders = sorted(folders, key=lambda x: int(x.name) if x.name.isdigit() else 9999)

    for folder in folders:
        if folder.name.isdigit():
            try:
                flight_id = int(folder.name)
                bag_path = folder / "raw.bag"
                
                if bag_path.exists():
                    print(f"Processing Flight {flight_id}")
                    df = process_bag(bag_path, flight_id)
                    all_flights.append(df)
                else:
                    print(f"Skipped Flight {flight_id}: raw.bag not found")
            except Exception as e:
                print(f"Error Flight {folder.name}: {e}")

    if not all_flights:
        print("No flight data processed. Exiting.")
        return

    final_df = pd.concat(all_flights, ignore_index=True)
    print(f"\nExtracted {len(final_df):,} rows from {len(all_flights)} flights.")

    print("\n"+"="*60)
    print("Merge Parameters")
    print("="*60)
    
    # Kết hợp với thông số của chuyến bay
    params_path = dji_root / "parameters.csv"
    params = pd.read_csv(params_path)
    final_df = final_df.merge(params, on="flight", how="left")
    
    # Chuẩn hóa ngày tháng về định dạng YYYY-MM-DD
    if "date" in final_df.columns:
        final_df["date"] = pd.to_datetime(final_df["date"]).dt.strftime("%Y-%m-%d")

    # Sắp xếp lại thứ tự các cột
    base_cols = [
        "flight", "time", "wind_speed", "wind_angle", "battery_voltage", "battery_current",
        "position_x", "position_y", "position_z", "orientation_x", "orientation_y", 
        "orientation_z", "orientation_w", "velocity_x", "velocity_y", "velocity_z",
        "angular_x", "angular_y", "angular_z", "linear_acceleration_x", 
        "linear_acceleration_y", "linear_acceleration_z", "speed", "payload", 
        "altitude", "date", "time_day", "route"
    ]
    base_cols = [c for c in base_cols if c in final_df.columns]
    final_df = final_df[base_cols]

    print("\n"+"="*60)
    print("Add Weather Data")
    print("="*60)

    weather_cols = [
        "temperature_c", "apparent_temp_c", "dew_point_c", "humidity_pct",
        "wind_speed_ms", "wind_gust_ms", "wind_dir_deg",
        "precipitation_mm", "pressure_hpa", "cloud_cover_pct",
    ]

    # ── Bước 1: Tính thời gian thực tế từng dòng ─────────────────────────────
    # date = "2019-04-07", time_day = "10:13", time = giây kể từ đầu chuyến bay
    print("Tinh thoi gian thuc tung dong...")
    
    base_datetime = pd.to_datetime(
        final_df["date"].astype(str) + " " + final_df["time_day"].astype(str),
        format="%Y-%m-%d %H:%M"
    )

    # Thời gian thực tế = thời điểm bắt đầu chuyến bay + thời gian đã bay (giây)
    final_df["actual_dt"] = (
        base_datetime
        + pd.to_timedelta(final_df["time"].astype(float), unit="s")
    )

    final_df["actual_date"] = final_df["actual_dt"].dt.strftime("%Y-%m-%d")
    final_df["actual_hour"] = final_df["actual_dt"].dt.hour

    final_df["lat_r"] = final_df["position_y"].round(2)
    final_df["lon_r"] = final_df["position_x"].round(2)

    print("\nCHECK ACTUAL TIME:")
    print(
    final_df[
        ["flight", "date", "time_day", "time",
         "actual_dt", "actual_date", "actual_hour",
         "lat_r", "lon_r"]
    ].head(10).to_string(index=False)
)


    # ── Bước 2: Xác định các cặp (actual_date, lat_r, lon_r) unique để gọi API ─
    keys = final_df.groupby(["actual_date", "lat_r", "lon_r"]).size().reset_index()[["actual_date", "lat_r", "lon_r"]]
    # Bỏ tọa độ bất thường (0, 0)
    keys = keys[(keys["lat_r"] != 0) & (keys["lon_r"] != 0)].reset_index(drop=True)
    print(f"So lan goi API: {len(keys)}")

    # ── Bước 3: Gọi API và lưu vào cache dict ────────────────────────────────
    # cache[(actual_date, lat_r, lon_r)] = {0: {...}, 1: {...}, ..., 23: {...}}
    api_cache = {}
    for i, row in keys.iterrows():
        key = (row["actual_date"], row["lat_r"], row["lon_r"])
        try:
            api_cache[key] = fetch_day(*key)
            print(f"  [{i+1}/{len(keys)}] {key} OK")
        except Exception as e:
            print(f"  [{i+1}/{len(keys)}] {key} LOI: {e}")
            api_cache[key] = {}
        time.sleep(0.3)

    # ── Bước 4: Gán thời tiết cho từng dòng theo actual_hour thực tế ─────────
    print(f"\nGan thoi tiet vao {len(final_df):,} dong...")
    
    weather_rows = []
    for (actual_date, lat_r, lon_r), hourly_data in api_cache.items():
        for hour, weather_data in hourly_data.items():
            row = {
                "actual_date": actual_date,
                "lat_r": lat_r,
                "lon_r": lon_r,
                "actual_hour": hour,
            }
            row.update(weather_data)
            weather_rows.append(row)

    weather_df = pd.DataFrame(weather_rows)

    if not weather_df.empty:
        final_df = final_df.merge(
            weather_df,
            on=["actual_date", "lat_r", "lon_r", "actual_hour"],
            how="left"
        )
    else:
        for c in weather_cols:
            final_df[c] = None

    # ── Bước 5: Bỏ các cột tạm ───────────────────────────────────────────────
    final_df.drop(
    columns=["actual_dt", "actual_date", "actual_hour", "lat_r", "lon_r"],
    inplace=True
)
    
    print("\n"+"="*60)
    print("Save Raw Dataset")
    print("="*60)
    
    out_path = Path(DATA_INTERMEDIATE_DIR) / "flight_with_weather.csv"
    
    print("Final shape:", final_df.shape)
    print("Missing weather rows:", final_df["temperature_c"].isnull().sum())
    
    final_df.to_csv(out_path, index=False)
    print(f"Saved raw dataset to {out_path}!")

if __name__ == "__main__":
    main()
