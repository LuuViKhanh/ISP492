"""G11 — Predict request-conditioned edge Energy with the selected segment model."""

from __future__ import annotations

import sys
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.paths import OUTPUT_DIR
from config.multiuav_config import get_segment_energy_features


def predict_edge_energy() -> pd.DataFrame:
    print("=" * 70)
    print("G11 — PREDICT EDGE ENERGY")
    print("=" * 70)

    data_dir = Path(OUTPUT_DIR) / "data" / "multihub"
    model_path = Path(
        os.environ.get(
            "RS_SEGMENT_MODEL_PATH",
            str(Path(OUTPUT_DIR) / "models" / "multihub" / "segment_energy_model.pkl"),
        )
    ).resolve()
    feature_path = data_dir / "edge_request_features.csv"

    if not model_path.exists():
        raise FileNotFoundError(
            f"Missing selected segment Energy model: {model_path}\n"
            "Run benchmark_segment_energy_models.py, choose a model, then run fit_segment_energy_model.py."
        )

    df = pd.read_csv(feature_path)
    artifact = joblib.load(model_path)
    features = list(artifact.get("features", []))
    expected = get_segment_energy_features()
    if features != expected:
        raise ValueError(
            "Stale/incompatible segment model feature schema.\n"
            f"Model features: {features}\nExpected now: {expected}\n"
            "Re-run G06, benchmark_segment_energy_models.py and fit_segment_energy_model.py."
        )

    bundle = artifact.get("bundle")
    if bundle is None:
        raise ValueError(
            "Legacy segment model artifact detected (no 'bundle'). Refit with fit_segment_energy_model.py."
        )

    missing = [c for c in features if c not in df.columns]
    if missing:
        raise ValueError(f"edge_request_features.csv missing model features: {missing}")

    pred = bundle.predict(df[features])
    out = df[["request_id", "edge_id"]].copy()
    out["predicted_segment_energy_wh"] = pred
    out["prediction_is_valid"] = (
        np.isfinite(out["predicted_segment_energy_wh"])
        & (out["predicted_segment_energy_wh"] > 0)
    )
    out.to_csv(data_dir / "edge_predictions.csv", index=False)

    invalid = int((~out["prediction_is_valid"]).sum())
    print(f"Predictions: {len(out)} | invalid: {invalid}")
    print(f"Saved: {data_dir / 'edge_predictions.csv'}")
    return out


if __name__ == "__main__":
    predict_edge_energy()
