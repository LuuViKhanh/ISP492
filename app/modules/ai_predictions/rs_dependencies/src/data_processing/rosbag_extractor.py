"""
rosbag_extractor.py
===================
Phase 3 — Shared ROS Bag Extraction Module

Extracts telemetry from raw ROS bags for both DJI and VTOL platforms.
Unified output schema:
    flight, time, wind_speed, wind_angle, battery_voltage, battery_current,
    position_x, position_y, position_z, orientation_x, orientation_y,
    orientation_z, orientation_w, velocity_x, velocity_y, velocity_z,
    angular_x, angular_y, angular_z, linear_acceleration_x,
    linear_acceleration_y, linear_acceleration_z
"""

import pandas as pd
import numpy as np
from pathlib import Path
from rosbags.highlevel import AnyReader


# ============================================================
# Individual Sensor Extractors
# ============================================================

def extract_anemometer(reader, topic="/anemometer"):
    """Extract wind speed and angle from anemometer topic."""
    rows = []
    connections = [c for c in reader.connections if c.topic == topic]
    for conn, timestamp, rawdata in reader.messages(connections):
        msg = reader.deserialize(rawdata, conn.msgtype)
        rows.append({
            "time": timestamp,
            "wind_speed": msg.speed,
            "wind_angle": msg.angle,
        })
    return pd.DataFrame(rows)


def extract_battery(reader, topic="/battery"):
    """Extract battery voltage and current from battery topic."""
    rows = []
    connections = [c for c in reader.connections if c.topic == topic]
    for conn, timestamp, rawdata in reader.messages(connections):
        msg = reader.deserialize(rawdata, conn.msgtype)
        rows.append({
            "time": timestamp,
            "battery_voltage": msg.voltage,
            "battery_current": msg.current,
        })
    return pd.DataFrame(rows)


def extract_imu(reader, topic="/imu/data"):
    """Extract IMU orientation, angular velocity, and linear acceleration."""
    rows = []
    connections = [c for c in reader.connections if c.topic == topic]
    for conn, timestamp, rawdata in reader.messages(connections):
        msg = reader.deserialize(rawdata, conn.msgtype)
        rows.append({
            "time": timestamp,
            "orientation_x": msg.orientation.x,
            "orientation_y": msg.orientation.y,
            "orientation_z": msg.orientation.z,
            "orientation_w": msg.orientation.w,
            "angular_x": msg.angular_velocity.x,
            "angular_y": msg.angular_velocity.y,
            "angular_z": msg.angular_velocity.z,
            "linear_acceleration_x": msg.linear_acceleration.x,
            "linear_acceleration_y": msg.linear_acceleration.y,
            "linear_acceleration_z": msg.linear_acceleration.z,
        })
    return pd.DataFrame(rows)


def extract_odometry(reader, topic="/nav/odom"):
    """Extract position and velocity from odometry topic."""
    rows = []
    connections = [c for c in reader.connections if c.topic == topic]
    for conn, timestamp, rawdata in reader.messages(connections):
        msg = reader.deserialize(rawdata, conn.msgtype)
        rows.append({
            "time": timestamp,
            "position_x": msg.pose.pose.position.x,
            "position_y": msg.pose.pose.position.y,
            "position_z": msg.pose.pose.position.z,
            "velocity_x": msg.twist.twist.linear.x,
            "velocity_y": msg.twist.twist.linear.y,
            "velocity_z": msg.twist.twist.linear.z,
        })
    return pd.DataFrame(rows)


def extract_rosbag(bag_path, flight_id):
    """
    Extract all telemetry from a single raw.bag file.
    
    Returns a merged DataFrame with unified schema:
        flight, time (relative seconds), wind, battery, IMU, odometry columns.
    """
    with AnyReader([bag_path]) as reader:
        wind_df = extract_anemometer(reader)
        battery_df = extract_battery(reader)
        imu_df = extract_imu(reader)
        odom_df = extract_odometry(reader)

    # Handle empty dataframes
    dfs = {"wind": wind_df, "battery": battery_df, "imu": imu_df, "odom": odom_df}
    non_empty = {k: v for k, v in dfs.items() if not v.empty}

    if not non_empty:
        print(f"  WARNING: Flight {flight_id} has no data in any topic")
        return pd.DataFrame()

    # Find global start time from all available sensors
    all_times = []
    for df_temp in non_empty.values():
        all_times.append(df_temp["time"].min())
    start_time = min(all_times)

    # Convert timestamps to relative seconds
    for df_temp in non_empty.values():
        df_temp["time"] = (df_temp["time"] - start_time) / 1_000_000_000

    # Merge using nearest-time alignment (wind as base if available, else first non-empty)
    if "wind" in non_empty:
        base_df = non_empty.pop("wind").sort_values("time")
    else:
        first_key = next(iter(non_empty))
        base_df = non_empty.pop(first_key).sort_values("time")

    flight_df = base_df
    for name, df_temp in non_empty.items():
        flight_df = pd.merge_asof(
            flight_df.sort_values("time"),
            df_temp.sort_values("time"),
            on="time",
            direction="nearest",
        )

    flight_df["flight"] = flight_id
    return flight_df


# ============================================================
# Unified Column Order
# ============================================================

TELEMETRY_COLUMNS = [
    "flight", "time",
    "wind_speed", "wind_angle",
    "battery_voltage", "battery_current",
    "position_x", "position_y", "position_z",
    "orientation_x", "orientation_y", "orientation_z", "orientation_w",
    "velocity_x", "velocity_y", "velocity_z",
    "angular_x", "angular_y", "angular_z",
    "linear_acceleration_x", "linear_acceleration_y", "linear_acceleration_z",
]


def reorder_columns(df, extra_cols=None):
    """Reorder DataFrame columns to match unified telemetry schema."""
    cols = [c for c in TELEMETRY_COLUMNS if c in df.columns]
    if extra_cols:
        cols += [c for c in extra_cols if c in df.columns and c not in cols]
    # Add any remaining columns not yet included
    remaining = [c for c in df.columns if c not in cols]
    return df[cols + remaining]
