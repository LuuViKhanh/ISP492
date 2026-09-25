"""
vtol_bag_audit.py
=================
Phase 2 — VTOL Raw Bag Audit

Inspects a sample of VTOL raw bags to confirm:
1. Topic names and message types
2. Timestamp range and sampling rate
3. Battery current sign convention (discharge positive or negative?)
4. Position coordinate convention (geographic lon/lat vs local ENU/XYZ)
5. Wind angle convention (0° direction, FROM/TO, wrap 0-360)
6. Missing topics or corrupted bags

Run this BEFORE building the VTOL pipeline.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from rosbags.highlevel import AnyReader
import sys
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config.paths import DATA_RAW_VTOL


def audit_single_bag(bag_path, flight_id):
    """Audit a single VTOL raw.bag file and return findings."""
    findings = {"flight": flight_id, "bag_path": str(bag_path)}

    try:
        with AnyReader([bag_path]) as reader:
            # ── 1. Topic Inventory ──────────────────────────────────────
            topics = {}
            for conn in reader.connections:
                topics[conn.topic] = conn.msgtype
            findings["topics"] = topics
            findings["topic_count"] = len(topics)

            # ── 2. Anemometer (Wind) ────────────────────────────────────
            wind_conns = [c for c in reader.connections if c.topic == "/anemometer"]
            wind_ts = []
            wind_speeds = []
            wind_angles = []
            for conn, ts, rawdata in reader.messages(wind_conns):
                msg = reader.deserialize(rawdata, conn.msgtype)
                wind_ts.append(ts)
                wind_speeds.append(msg.speed)
                wind_angles.append(msg.angle)
            findings["wind_count"] = len(wind_ts)
            if wind_ts:
                findings["wind_ts_min"] = min(wind_ts)
                findings["wind_ts_max"] = max(wind_ts)
                findings["wind_speed_min"] = min(wind_speeds)
                findings["wind_speed_max"] = max(wind_speeds)
                findings["wind_speed_mean"] = np.mean(wind_speeds)
                findings["wind_angle_min"] = min(wind_angles)
                findings["wind_angle_max"] = max(wind_angles)
                findings["wind_angle_mean"] = np.mean(wind_angles)
                if len(wind_ts) > 1:
                    dts = np.diff(wind_ts) / 1e9  # to seconds
                    findings["wind_sample_rate_hz"] = 1.0 / np.median(dts)

            # ── 3. Battery ──────────────────────────────────────────────
            batt_conns = [c for c in reader.connections if c.topic == "/battery"]
            batt_voltages = []
            batt_currents = []
            batt_ts = []
            for conn, ts, rawdata in reader.messages(batt_conns):
                msg = reader.deserialize(rawdata, conn.msgtype)
                batt_ts.append(ts)
                batt_voltages.append(msg.voltage)
                batt_currents.append(msg.current)
            findings["battery_count"] = len(batt_ts)
            if batt_currents:
                currents = np.array(batt_currents)
                findings["battery_voltage_min"] = min(batt_voltages)
                findings["battery_voltage_max"] = max(batt_voltages)
                findings["battery_current_min"] = float(currents.min())
                findings["battery_current_max"] = float(currents.max())
                findings["battery_current_mean"] = float(currents.mean())
                findings["battery_current_std"] = float(currents.std())
                findings["battery_pct_negative"] = float((currents < 0).sum() / len(currents) * 100)
                findings["battery_pct_positive"] = float((currents > 0).sum() / len(currents) * 100)
                findings["battery_pct_zero"] = float((currents == 0).sum() / len(currents) * 100)
                # Check for long zero stretches
                zero_streak = 0
                max_zero_streak = 0
                for c in currents:
                    if c == 0:
                        zero_streak += 1
                        max_zero_streak = max(max_zero_streak, zero_streak)
                    else:
                        zero_streak = 0
                findings["battery_max_zero_streak"] = max_zero_streak
                if len(batt_ts) > 1:
                    dts = np.diff(batt_ts) / 1e9
                    findings["battery_sample_rate_hz"] = 1.0 / np.median(dts)

            # ── 4. IMU ──────────────────────────────────────────────────
            imu_conns = [c for c in reader.connections if c.topic == "/imu/data"]
            imu_count = 0
            imu_ts = []
            for conn, ts, rawdata in reader.messages(imu_conns):
                imu_count += 1
                imu_ts.append(ts)
            findings["imu_count"] = imu_count
            if len(imu_ts) > 1:
                dts = np.diff(imu_ts) / 1e9
                findings["imu_sample_rate_hz"] = 1.0 / np.median(dts)

            # ── 5. Odometry (Position) ──────────────────────────────────
            odom_conns = [c for c in reader.connections if c.topic == "/nav/odom"]
            pos_x = []
            pos_y = []
            pos_z = []
            odom_ts = []
            for conn, ts, rawdata in reader.messages(odom_conns):
                msg = reader.deserialize(rawdata, conn.msgtype)
                odom_ts.append(ts)
                pos_x.append(msg.pose.pose.position.x)
                pos_y.append(msg.pose.pose.position.y)
                pos_z.append(msg.pose.pose.position.z)
            findings["odom_count"] = len(odom_ts)
            if pos_x:
                findings["pos_x_min"] = min(pos_x)
                findings["pos_x_max"] = max(pos_x)
                findings["pos_y_min"] = min(pos_y)
                findings["pos_y_max"] = max(pos_y)
                findings["pos_z_min"] = min(pos_z)
                findings["pos_z_max"] = max(pos_z)
                findings["pos_x_range"] = max(pos_x) - min(pos_x)
                findings["pos_y_range"] = max(pos_y) - min(pos_y)
                findings["pos_z_range"] = max(pos_z) - min(pos_z)
                if len(odom_ts) > 1:
                    dts = np.diff(odom_ts) / 1e9
                    findings["odom_sample_rate_hz"] = 1.0 / np.median(dts)

                # Position convention heuristic:
                # Geographic lon/lat: x (lon) in [-180,180], y (lat) in [-90,90]
                # Local ENU/XYZ: values typically near 0 with range in meters
                
                # Check if values fall in valid geographic coordinate ranges
                is_geographic = (
                    -180 <= min(pos_x) and max(pos_x) <= 180
                    and -90 <= min(pos_y) and max(pos_y) <= 90
                    and findings["pos_x_range"] < 1  # geographic degree range < 1 degree
                    and findings["pos_y_range"] < 1
                )
                
                if is_geographic:
                    findings["position_convention"] = "GEOGRAPHIC_LONLAT"
                else:
                    findings["position_convention"] = "LOCAL_XYZ_METERS"

    except Exception as e:
        findings["error"] = str(e)

    return findings


def main():
    vtol_rosbag_dir = DATA_RAW_VTOL / "rosbag"
    params = pd.read_csv(DATA_RAW_VTOL / "parameters.csv")

    print("=" * 70)
    print("VTOL RAW BAG AUDIT")
    print("=" * 70)
    print(f"\nVTOL rosbag dir: {vtol_rosbag_dir}")
    print(f"Total flights in parameters.csv: {len(params)}")
    print(f"Payload values: {sorted(params['payload'].unique())}")
    print(f"Pattern values: {sorted(params['pattern'].unique())}")
    print(f"Speed values: {sorted(params['speed'].unique())}")
    print(f"Altitude values: {sorted(params['altitude'].unique())}")

    # Select representative bags: one per payload level, different patterns
    audit_flights = []
    for payload in [0, 400, 800]:
        subset = params[params["payload"] == payload]
        for pattern in ["A", "B"]:
            pat_sub = subset[subset["pattern"] == pattern]
            if not pat_sub.empty:
                audit_flights.append(pat_sub.iloc[0]["flight"])
                break
        else:
            if not subset.empty:
                audit_flights.append(subset.iloc[0]["flight"])

    print(f"\nAudit flights selected: {audit_flights}")

    all_findings = []
    for flight_id in audit_flights:
        bag_path = vtol_rosbag_dir / str(flight_id) / "raw.bag"
        if not bag_path.exists():
            print(f"\n  Flight {flight_id}: raw.bag NOT FOUND at {bag_path}")
            continue

        print(f"\n{'-' * 60}")
        print(f"  FLIGHT {flight_id}")
        print(f"{'-' * 60}")

        f = audit_single_bag(bag_path, flight_id)
        all_findings.append(f)

        if "error" in f:
            print(f"    ERROR: {f['error']}")
            continue

        # Topics
        print(f"  Topics ({f['topic_count']}):")
        for topic, msgtype in f.get("topics", {}).items():
            print(f"    {topic}: {msgtype}")

        # Wind
        print(f"\n  Wind (anemometer): {f.get('wind_count', 0)} messages")
        if f.get("wind_count", 0) > 0:
            print(f"    Speed:  min={f['wind_speed_min']:.2f}, max={f['wind_speed_max']:.2f}, mean={f['wind_speed_mean']:.2f}")
            print(f"    Angle:  min={f['wind_angle_min']:.2f}, max={f['wind_angle_max']:.2f}, mean={f['wind_angle_mean']:.2f}")
            print(f"    Rate:   {f.get('wind_sample_rate_hz', 'N/A'):.1f} Hz" if 'wind_sample_rate_hz' in f else "")

        # Battery
        print(f"\n  Battery: {f.get('battery_count', 0)} messages")
        if f.get("battery_count", 0) > 0:
            print(f"    Voltage: {f['battery_voltage_min']:.2f}V – {f['battery_voltage_max']:.2f}V")
            print(f"    Current: min={f['battery_current_min']:.4f}A, max={f['battery_current_max']:.4f}A, mean={f['battery_current_mean']:.4f}A")
            print(f"    Current distribution: {f['battery_pct_positive']:.1f}% positive, {f['battery_pct_negative']:.1f}% negative, {f['battery_pct_zero']:.1f}% zero")
            print(f"    Max zero streak: {f['battery_max_zero_streak']} consecutive")
            sign = "POSITIVE" if f['battery_pct_positive'] > f['battery_pct_negative'] else "NEGATIVE"
            print(f"    >>> DISCHARGE CURRENT SIGN: {sign}")

        # IMU
        print(f"\n  IMU: {f.get('imu_count', 0)} messages")
        if f.get("imu_sample_rate_hz"):
            print(f"    Rate: {f['imu_sample_rate_hz']:.1f} Hz")

        # Odometry / Position
        print(f"\n  Odometry: {f.get('odom_count', 0)} messages")
        if f.get("odom_count", 0) > 0:
            print(f"    X: [{f['pos_x_min']:.6f}, {f['pos_x_max']:.6f}] range={f['pos_x_range']:.6f}")
            print(f"    Y: [{f['pos_y_min']:.6f}, {f['pos_y_max']:.6f}] range={f['pos_y_range']:.6f}")
            print(f"    Z: [{f['pos_z_min']:.6f}, {f['pos_z_max']:.6f}] range={f['pos_z_range']:.6f}")
            print(f"    >>> POSITION CONVENTION: {f.get('position_convention', 'UNKNOWN')}")

    # ── Summary ─────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("AUDIT SUMMARY")
    print(f"{'=' * 70}")

    if all_findings:
        # Check consistency
        conventions = set(f.get("position_convention") for f in all_findings if "position_convention" in f)
        print(f"\n  Position conventions found: {conventions}")

        discharge_signs = []
        for f in all_findings:
            if f.get("battery_pct_positive", 0) > f.get("battery_pct_negative", 0):
                discharge_signs.append("POSITIVE")
            elif f.get("battery_count", 0) > 0:
                discharge_signs.append("NEGATIVE")
        print(f"  Discharge current signs: {set(discharge_signs)}")

        topic_sets = [set(f.get("topics", {}).keys()) for f in all_findings if "topics" in f]
        if topic_sets:
            common = topic_sets[0]
            for ts in topic_sets[1:]:
                common = common & ts
            print(f"  Common topics across all audited bags: {sorted(common)}")

        # Check for missing critical topics
        required = {"/anemometer", "/battery", "/imu/data", "/nav/odom"}
        for f in all_findings:
            bag_topics = set(f.get("topics", {}).keys())
            missing = required - bag_topics
            if missing:
                print(f"  WARNING: Flight {f['flight']} missing topics: {missing}")

    print("\n  AUDIT COMPLETE — Review findings above before proceeding to Phase 3.")


if __name__ == "__main__":
    main()
