"""G10 — Build request-conditioned graph-edge features for Energy prediction.

Each request supplies operational/weather conditions. Each graph edge supplies
known geometry. ``route_bearing`` is used only internally to derive
``relative_wind_angle`` and is never passed to the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.multiuav_config import (
    BASE_PREFLIGHT_FEATURES,
    get_segment_energy_features,
)
from config.paths import OUTPUT_DIR
from src.data_processing.target_calculation import compute_relative_wind_features


def build_edge_request_features() -> pd.DataFrame:
    print("=" * 70)
    print("G10 — BUILD EDGE-REQUEST ENERGY FEATURES")
    print("=" * 70)

    data_dir = Path(OUTPUT_DIR) / "data" / "multihub"
    requests_df = pd.read_csv(data_dir / "route_requests.csv")
    edges_df = pd.read_csv(data_dir / "graph_edges.csv")

    edge_cols = [
        "edge_id",
        "segment_distance",
        "start_x",
        "start_y",
        "end_x",
        "end_y",
    ]
    missing_edge = [c for c in edge_cols if c not in edges_df.columns]
    if missing_edge:
        raise ValueError(f"graph_edges.csv missing: {missing_edge}; rerun G08")

    request_cols = ["request_id"] + list(BASE_PREFLIGHT_FEATURES)
    missing_req = [c for c in request_cols if c not in requests_df.columns]
    if missing_req:
        raise ValueError(f"route_requests.csv missing request features: {missing_req}")

    req = requests_df[request_cols].copy()
    edge = edges_df[edge_cols].copy()
    req["_join"] = 1
    edge["_join"] = 1
    cross = req.merge(edge, on="_join").drop(columns="_join")

    wind = cross.apply(
        lambda r: compute_relative_wind_features(
            r["start_x"],
            r["start_y"],
            r["end_x"],
            r["end_y"],
            r["wind_dir_deg"],
            r["wind_speed_ms"],
            coordinate_mode="GEOGRAPHIC",
        ),
        axis=1,
        result_type="expand",
    )
    wind.columns = [
        "relative_wind_angle",
        "headwind_component",
        "crosswind_component",
    ]
    cross[wind.columns] = wind

    model_features = get_segment_energy_features()
    output_cols = ["request_id", "edge_id"] + model_features
    missing_model = [c for c in output_cols if c not in cross.columns]
    if missing_model:
        raise ValueError(f"Cannot construct segment-model schema: {missing_model}")

    output = cross[output_cols].copy()
    output.to_csv(data_dir / "edge_request_features.csv", index=False)

    print(f"Created {len(output)} request-edge vectors")
    print(f"Model features ({len(model_features)}): {model_features}")
    print("route_bearing is not a model feature.")
    return output


if __name__ == "__main__":
    build_edge_request_features()
