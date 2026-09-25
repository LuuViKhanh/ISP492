"""G00 — Load normalized trajectory data for the CP3-selected validation case."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.multiuav_config import (
    CASE_COMBINED,
    CASE_DJI_ONLY,
    CASE_VTOL_ONLY,
    COMMON_PREFLIGHT_FEATURES,
    VALIDATION_CASES,
)
from config.paths import DATA_PROCESSED_DIR, OUT_RES_MULTIUAV


def _resolve_case(case: str | None) -> str:
    if case is not None:
        if case not in VALIDATION_CASES:
            raise ValueError(f"Unknown Multi-Hub case '{case}'")
        return case

    selected_path = Path(OUT_RES_MULTIUAV) / "validation" / "selected_configuration.json"
    if not selected_path.exists():
        raise FileNotFoundError(
            "No CP3 selected configuration found. Run select_validated_configuration.py "
            "or pass --case explicitly to run_multihub_g00_g07.py."
        )
    with selected_path.open("r", encoding="utf-8") as f:
        selected = json.load(f)
    selected_case = selected.get("selected_case")
    if selected_case not in VALIDATION_CASES:
        raise ValueError(f"Invalid selected_case in {selected_path}: {selected_case}")
    return selected_case


def _load_platform(path: Path, dataset_id: str, uav_type: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    df["dataset_id"] = dataset_id
    df["uav_type"] = uav_type
    return df


def load_and_normalize_data(case: str | None = None) -> pd.DataFrame:
    """Load only the trajectories permitted by the selected validation case."""
    case = _resolve_case(case)
    dji_path = Path(DATA_PROCESSED_DIR) / "cleaned_flight_data.csv"
    vtol_path = Path(DATA_PROCESSED_DIR) / "vtol_cleaned_flight_data.csv"

    frames = []
    if case in {CASE_DJI_ONLY, CASE_COMBINED}:
        frames.append(_load_platform(dji_path, "DJI_M100", "Quadrotor"))
    if case in {CASE_VTOL_ONLY, CASE_COMBINED}:
        frames.append(_load_platform(vtol_path, "VTOL", "Fixed-wing"))

    required = [
        "flight", "time", "position_x", "position_y", "position_z",
        "speed", "altitude", "payload", "dataset_id", "uav_type",
        "battery_voltage", "battery_current",
    ] + list(COMMON_PREFLIGHT_FEATURES)

    for idx, frame in enumerate(frames):
        missing = [c for c in required if c not in frame.columns]
        if missing:
            raise ValueError(f"Trajectory source {idx} missing required columns: {missing}")

    # Keep the union so platform-specific non-model metadata can survive, while
    # downstream model schemas remain explicit.
    df = pd.concat(frames, ignore_index=True, sort=False)
    df["flight_uid"] = df["dataset_id"].astype(str) + "_" + df["flight"].astype(str)

    if df["time"].isna().any():
        raise ValueError("Null timestamps found in selected trajectory data.")
    pos_valid = df[["position_x", "position_y", "position_z"]].notna().all(axis=1)
    dropped = int((~pos_valid).sum())
    if dropped:
        print(f"Dropping {dropped} rows with incomplete 3-D position.")
        df = df.loc[pos_valid].copy()

    print(f"G00 selected case: {case}")
    print(f"Loaded {len(df)} telemetry points from {df['flight_uid'].nunique()} flights")
    print(f"Platforms: {df['dataset_id'].value_counts().to_dict()}")
    return df.reset_index(drop=True)


if __name__ == "__main__":
    load_and_normalize_data()
