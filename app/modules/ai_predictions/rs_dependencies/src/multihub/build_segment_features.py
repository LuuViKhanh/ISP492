"""G06 — Build segment-level Energy model dataset.

Distance keeps the concise variable name ``segment_distance`` but is cumulative
3-D geographic travel: Haversine horizontal motion combined with altitude
change at each adjacent telemetry pair.

The segment Energy model uses:
    Operational + Environmental + relative_wind_angle + segment_distance

``route_bearing`` is calculated only internally as part of the relative-wind
geometry utility and is NOT a model feature.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.multihub_config import MIN_SEGMENT_DISTANCE_M
from config.multiuav_config import (
    ENVIRONMENTAL_FEATURES,
    OPERATIONAL_FEATURES,
    SEGMENT_TARGET,
    get_segment_energy_features,
)
from config.paths import OUTPUT_DIR
from src.data_processing.target_calculation import (
    calculate_battery_energy,
    calculate_distance_haversine,
    calculate_flight_duration,
    circular_mean,
    compute_relative_wind_features,
)


def _mean_numeric(group: pd.DataFrame, column: str) -> float:
    if column not in group.columns:
        return float("nan")
    return float(pd.to_numeric(group[column], errors="coerce").mean())


def build_segment_features(segments_df: pd.DataFrame) -> pd.DataFrame:
    print("=" * 70)
    print("G06 — BUILD SEGMENT ENERGY FEATURES")
    print("=" * 70)

    rows = []
    for segment_id, group in segments_df.groupby("segment_id", sort=False):
        group = group.sort_values("time").copy()
        if len(group) < 2:
            continue

        currents = pd.to_numeric(group["battery_current"], errors="coerce").to_numpy(float)
        normalize_current_sign = np.sum(currents < 0) > np.sum(currents > 0)

        segment_distance = calculate_distance_haversine(
            lats=group["position_y"].to_numpy(float),
            lons=group["position_x"].to_numpy(float),
            alts=group["position_z"].to_numpy(float),
            times=group["time"].to_numpy(float),
        )
        segment_duration = calculate_flight_duration(group["time"].to_numpy(float))
        segment_energy_wh = calculate_battery_energy(
            voltages=group["battery_voltage"].to_numpy(float),
            currents=currents,
            times=group["time"].to_numpy(float),
            normalize_sign=normalize_current_sign,
            max_gap_sec=30.0,
        )

        # Weather wind direction is circular; never use a normal arithmetic mean.
        wind_dir = (
            circular_mean(pd.to_numeric(group["wind_dir_deg"], errors="coerce").dropna().to_numpy(float))
            if "wind_dir_deg" in group.columns
            else float("nan")
        )
        wind_speed = _mean_numeric(group, "wind_speed_ms")
        relative_wind_angle, _, _ = compute_relative_wind_features(
            group["position_x"].iloc[0],
            group["position_y"].iloc[0],
            group["position_x"].iloc[-1],
            group["position_y"].iloc[-1],
            wind_dir,
            wind_speed,
            coordinate_mode="GEOGRAPHIC",
        )

        record = {
            "segment_id": segment_id,
            "source_flight": group["flight"].iloc[0] if "flight" in group.columns else np.nan,
            "flight_uid": group["flight_uid"].iloc[0],
            "dataset_id": group["dataset_id"].iloc[0],
            "uav_type": group["uav_type"].iloc[0],
            "start_node": group["start_node"].iloc[0],
            "end_node": group["end_node"].iloc[0],
            "date": group["date"].iloc[0] if "date" in group.columns else np.nan,
            "route": group["route"].iloc[0] if "route" in group.columns else np.nan,
            "segment_distance": float(segment_distance),
            "segment_duration": float(segment_duration),
            SEGMENT_TARGET: float(segment_energy_wh),
            "relative_wind_angle": float(relative_wind_angle),
        }

        for feature in OPERATIONAL_FEATURES:
            record[feature] = _mean_numeric(group, feature)
        for feature in ENVIRONMENTAL_FEATURES:
            record[feature] = wind_dir if feature == "wind_dir_deg" else _mean_numeric(group, feature)

        rows.append(record)

    all_features = pd.DataFrame(rows)
    if all_features.empty:
        raise ValueError("No segment features were generated.")

    model_features = get_segment_energy_features()
    missing_columns = [c for c in model_features + [SEGMENT_TARGET] if c not in all_features.columns]
    if missing_columns:
        raise ValueError(f"G06 missing required segment-model columns: {missing_columns}")

    all_features["exclusion_reason"] = None
    all_features.loc[
        ~np.isfinite(pd.to_numeric(all_features["segment_distance"], errors="coerce"))
        | (all_features["segment_distance"] <= MIN_SEGMENT_DISTANCE_M),
        "exclusion_reason",
    ] = f"segment_distance <= {MIN_SEGMENT_DISTANCE_M} m or invalid"

    invalid_energy = (
        ~np.isfinite(pd.to_numeric(all_features[SEGMENT_TARGET], errors="coerce"))
        | (all_features[SEGMENT_TARGET] <= 0)
    )
    all_features.loc[
        invalid_energy & all_features["exclusion_reason"].isna(),
        "exclusion_reason",
    ] = "segment_energy_wh <= 0 or invalid"

    missing_features = all_features[model_features].isna().any(axis=1)
    all_features.loc[
        missing_features & all_features["exclusion_reason"].isna(),
        "exclusion_reason",
    ] = "missing model feature"

    valid = all_features[all_features["exclusion_reason"].isna()].drop(
        columns=["exclusion_reason"]
    ).copy()
    excluded = all_features[all_features["exclusion_reason"].notna()].copy()

    out_dir = Path(OUTPUT_DIR) / "data" / "multihub"
    out_dir.mkdir(parents=True, exist_ok=True)
    all_features.to_csv(out_dir / "all_segment_features_unfiltered.csv", index=False)
    valid.to_csv(out_dir / "segment_level_features.csv", index=False)
    excluded.to_csv(out_dir / "excluded_segments.csv", index=False)

    print(f"Total segments: {len(all_features)}")
    print(f"Valid segments: {len(valid)}")
    print(f"Excluded segments: {len(excluded)}")
    print(f"Model features ({len(model_features)}): {model_features}")
    print(f"Target: {SEGMENT_TARGET} [Wh]")
    return valid


if __name__ == "__main__":
    path = Path(OUTPUT_DIR) / "data" / "multihub" / "segmented_telemetry.csv"
    build_segment_features(pd.read_csv(path))
