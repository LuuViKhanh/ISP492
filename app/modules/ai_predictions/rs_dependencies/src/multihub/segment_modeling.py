"""Segment Energy model checkpoint utilities (G07).

This module replaces the old approved:true/false YAML gate.

Canonical flow
--------------
1. G00-G06 build the audited segment-level dataset.
2. ``benchmark_segment_models`` tunes hyperparameters *within each model*
   using GroupKFold grouped by ``flight_uid`` and writes evidence.
3. STOP for the explicit model-family decision.
4. ``fit_selected_segment_model`` fits exactly the chosen model on all valid
   G06 segments and freezes the artifact used by G11.

Model target
------------
    segment_energy_wh [Wh]

Canonical model features
------------------------
    Operational
    + Environmental
    + relative_wind_angle
    + segment_distance

The route bearing used to derive ``relative_wind_angle`` is an intermediate
geometric quantity and is never a model feature.

Why group by flight_uid?
------------------------
Segments from the same historical flight are strongly related. Putting
segments from one flight into both train and validation folds would create
segment-level leakage and overstate generalization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from config.experiment_config import CV_FOLDS
from config.multiuav_config import (
    MODEL_BENCHMARK_CANDIDATES,
    SEGMENT_TARGET,
    get_segment_energy_features,
)
from config.paths import OUT_RES_MULTIUAV, OUTPUT_DIR
from src.modeling.metrics import compute_metrics
from src.modeling.validation_common import (
    MODEL_PARAM_GRIDS,
    fit_model_bundle,
    parse_model_list,
    save_json,
    summarize_fold_metrics,
)


SEGMENT_DATA_PATH = Path(OUTPUT_DIR) / "data" / "multihub" / "segment_level_features.csv"
CHECKPOINT_DIR = Path(OUTPUT_DIR) / "results" / "multihub" / "segment_model_checkpoint"
MODEL_DIR = Path(OUTPUT_DIR) / "models" / "multihub"
MODEL_PATH = MODEL_DIR / "segment_energy_model.pkl"
CP3_SELECTION_PATH = Path(OUT_RES_MULTIUAV) / "validation" / "selected_configuration.json"


def _load_segment_dataset() -> pd.DataFrame:
    if not SEGMENT_DATA_PATH.exists():
        raise FileNotFoundError(
            f"Missing G06 dataset: {SEGMENT_DATA_PATH}\n"
            "Run: python run_multihub_g00_g07.py"
        )

    df = pd.read_csv(SEGMENT_DATA_PATH)
    features = get_segment_energy_features()

    required = [
        "segment_id",
        "flight_uid",
        "dataset_id",
        SEGMENT_TARGET,
        *features,
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            "G06 segment dataset does not match the canonical segment schema. "
            f"Missing columns: {missing}. Re-run G00-G06."
        )

    # Fail fast on quantities that would make Energy/EE meaningless.
    df[SEGMENT_TARGET] = pd.to_numeric(df[SEGMENT_TARGET], errors="coerce")
    df["segment_distance"] = pd.to_numeric(df["segment_distance"], errors="coerce")
    valid = (
        np.isfinite(df[SEGMENT_TARGET])
        & (df[SEGMENT_TARGET] > 0)
        & np.isfinite(df["segment_distance"])
        & (df["segment_distance"] > 0)
    )
    df = df.loc[valid].copy().reset_index(drop=True)

    if df.empty:
        raise ValueError("No valid segment rows remain for G07 modeling.")
    if df["flight_uid"].nunique() < 2:
        raise ValueError("Need at least two distinct flights for group-aware segment CV.")

    # route_bearing must never enter the model schema.
    if "route_bearing" in features:
        raise ValueError("route_bearing is forbidden as a segment model feature.")

    return df


def _selection_key(row: Mapping) -> tuple:
    """Select hyperparameters within one model, never the model family itself."""
    r2 = float(row.get("CV_R2_mean", np.nan))
    wmape = float(row.get("CV_WMAPE_mean", np.nan))
    r2_std = float(row.get("CV_R2_std", np.nan))
    return (
        -np.inf if not np.isfinite(r2) else r2,
        -np.inf if not np.isfinite(wmape) else -wmape,
        -np.inf if not np.isfinite(r2_std) else -r2_std,
    )


def _evaluate_segment_cv(
    df: pd.DataFrame,
    model_name: str,
    params: Mapping,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    features = get_segment_energy_features()
    groups = df["flight_uid"].astype(str)
    n_splits = min(int(CV_FOLDS), int(groups.nunique()))
    if n_splits < 2:
        raise ValueError("Need at least two flight groups for GroupKFold.")

    cv = GroupKFold(n_splits=n_splits)
    X = df[features]
    y = df[SEGMENT_TARGET].to_numpy(float)

    fold_rows = []
    pred_rows = []

    for fold_idx, (train_idx, val_idx) in enumerate(
        cv.split(X, y, groups), start=1
    ):
        train_df = df.iloc[train_idx]
        val_df = df.iloc[val_idx]

        bundle = fit_model_bundle(
            model_name=model_name,
            params=params,
            X_train=train_df[features],
            y_train=train_df[SEGMENT_TARGET].to_numpy(float),
            features=features,
        )
        pred = bundle.predict(val_df[features])
        metrics = compute_metrics(
            val_df[SEGMENT_TARGET].to_numpy(float),
            pred,
        )

        fold_rows.append(
            {
                "fold": fold_idx,
                "model": model_name,
                "params_json": json.dumps(dict(params), sort_keys=True),
                "n_train_segments": int(len(train_df)),
                "n_validation_segments": int(len(val_df)),
                "n_train_flights": int(train_df["flight_uid"].nunique()),
                "n_validation_flights": int(val_df["flight_uid"].nunique()),
                **metrics,
            }
        )

        for row_idx, (_, row), y_pred in zip(
            val_idx,
            val_df.iterrows(),
            pred,
        ):
            pred_rows.append(
                {
                    "row_index": int(row_idx),
                    "segment_id": row["segment_id"],
                    "flight_uid": str(row["flight_uid"]),
                    "dataset_id": str(row["dataset_id"]),
                    "y_true": float(row[SEGMENT_TARGET]),
                    "y_pred": float(y_pred),
                    "fold": fold_idx,
                }
            )

    return pd.DataFrame(fold_rows), pd.DataFrame(pred_rows)


def benchmark_segment_models(models: str | None = "all") -> pd.DataFrame:
    """Benchmark/tune segment Energy models and STOP at the G07 checkpoint."""
    df = _load_segment_dataset()
    features = get_segment_energy_features()
    model_names = parse_model_list(models, MODEL_BENCHMARK_CANDIDATES)

    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    # Remove stale frozen model so G11 cannot accidentally use an old schema
    # after the segment dataset/features have changed.
    if MODEL_PATH.exists():
        MODEL_PATH.unlink()

    config_rows = []
    evidence_rows = []
    best_fold_frames = []
    best_oof_frames = []

    print("=" * 78)
    print("G07 SEGMENT ENERGY MODEL BENCHMARK")
    print(f"Target: {SEGMENT_TARGET} [Wh]")
    print(f"Features ({len(features)}): {features}")
    print(f"Segments: {len(df)} | Flights: {df['flight_uid'].nunique()}")
    print(f"Datasets: {df['dataset_id'].value_counts().to_dict()}")
    print("CV grouping: flight_uid")
    print("=" * 78)

    for model_name in model_names:
        params_list = MODEL_PARAM_GRIDS[model_name]
        print(f"\n[{model_name}] {len(params_list)} hyperparameter configuration(s)")
        model_configs = []

        for config_id, params in enumerate(params_list, start=1):
            fold_df, _ = _evaluate_segment_cv(df, model_name, params)
            summary = summarize_fold_metrics(fold_df)
            row = {
                "Model": model_name,
                "Config_ID": config_id,
                "Params_JSON": json.dumps(params, sort_keys=True),
                **summary,
            }
            config_rows.append(row)
            model_configs.append(row)

        best = max(model_configs, key=_selection_key)
        best_params = json.loads(best["Params_JSON"])

        fold_df, oof_df = _evaluate_segment_cv(
            df,
            model_name,
            best_params,
        )
        fold_df.insert(0, "Model", model_name)
        oof_df.insert(0, "Model", model_name)
        best_fold_frames.append(fold_df)
        best_oof_frames.append(oof_df)

        overall = compute_metrics(
            oof_df["y_true"].to_numpy(float),
            oof_df["y_pred"].to_numpy(float),
        )

        evidence_rows.append(
            {
                "Model": model_name,
                "Best_Params_JSON": json.dumps(best_params, sort_keys=True),
                "CV_R2_mean": best["CV_R2_mean"],
                "CV_R2_std": best["CV_R2_std"],
                "CV_MAE_mean": best["CV_MAE_mean"],
                "CV_RMSE_mean": best["CV_RMSE_mean"],
                "CV_WMAPE_mean": best["CV_WMAPE_mean"],
                "CV_WMAPE_std": best["CV_WMAPE_std"],
                "OOF_R2": overall.get("R2", np.nan),
                "OOF_MAE": overall.get("MAE", np.nan),
                "OOF_RMSE": overall.get("RMSE", np.nan),
                "OOF_WMAPE": overall.get("WMAPE", np.nan),
                "OOF_Bias": overall.get("Bias", np.nan),
                "N_Segments": int(len(df)),
                "N_Flights": int(df["flight_uid"].nunique()),
            }
        )

        print(
            f"  best params={best_params} | "
            f"CV R2={best['CV_R2_mean']:.4f} | "
            f"CV WMAPE={best['CV_WMAPE_mean']*100:.2f}% | "
            f"OOF R2={overall.get('R2', np.nan):.4f}"
        )

    config_df = pd.DataFrame(config_rows)
    evidence_df = pd.DataFrame(evidence_rows)

    config_df.to_csv(
        CHECKPOINT_DIR / "hyperparameter_evidence.csv",
        index=False,
    )
    evidence_df.to_csv(
        CHECKPOINT_DIR / "segment_model_evidence.csv",
        index=False,
    )
    pd.concat(best_fold_frames, ignore_index=True).to_csv(
        CHECKPOINT_DIR / "best_config_fold_metrics.csv",
        index=False,
    )
    pd.concat(best_oof_frames, ignore_index=True).to_csv(
        CHECKPOINT_DIR / "best_config_oof_predictions.csv",
        index=False,
    )

    selected_case = None
    if CP3_SELECTION_PATH.exists():
        try:
            selected_case = json.loads(
                CP3_SELECTION_PATH.read_text(encoding="utf-8")
            ).get("selected_case")
        except Exception:
            selected_case = None

    save_json(
        {
            "checkpoint": "SEGMENT_MODEL_SELECTION",
            "selected_cp3_case": selected_case,
            "target": SEGMENT_TARGET,
            "target_unit": "Wh",
            "features": features,
            "n_features": len(features),
            "n_segments": int(len(df)),
            "n_flights": int(df["flight_uid"].nunique()),
            "datasets": df["dataset_id"].value_counts().to_dict(),
            "cv": "GroupKFold grouped by flight_uid",
            "models_evaluated": model_names,
            "automatic_model_family_selection": False,
            "route_bearing_is_model_feature": False,
            "relative_wind_angle_convention": (
                "0=headwind, 90=crosswind, 180=tailwind"
            ),
            "distance_definition": (
                "cumulative 3-D point distance: "
                "Haversine horizontal + altitude difference"
            ),
        },
        CHECKPOINT_DIR / "run_manifest.json",
    )

    (CHECKPOINT_DIR / "CHECKPOINT_SEGMENT_MODEL.txt").write_text(
        "SEGMENT ENERGY MODEL CHECKPOINT\n\n"
        "Evidence has been generated with flight-grouped CV.\n"
        "No model family was selected automatically.\n\n"
        "Review:\n"
        f"  {CHECKPOINT_DIR / 'segment_model_evidence.csv'}\n\n"
        "Then continue explicitly, for example:\n"
        "  python fit_segment_energy_model.py --model ExtraTrees\n",
        encoding="utf-8",
    )

    print("\n" + evidence_df.to_string(index=False))
    print("\nG07 COMPLETE — STOPPING FOR SEGMENT MODEL DECISION")
    return evidence_df


def fit_selected_segment_model(model_name: str) -> Path:
    """Fit the explicitly chosen G07 model on all valid segment rows."""
    if model_name not in MODEL_BENCHMARK_CANDIDATES:
        raise ValueError(
            f"Unknown model '{model_name}'. "
            f"Valid: {MODEL_BENCHMARK_CANDIDATES}"
        )

    evidence_path = CHECKPOINT_DIR / "segment_model_evidence.csv"
    if not evidence_path.exists():
        raise FileNotFoundError(
            f"Missing segment-model evidence: {evidence_path}\n"
            "Run benchmark_segment_energy_models.py first."
        )

    evidence = pd.read_csv(evidence_path)
    selected = evidence[evidence["Model"] == model_name]
    if selected.empty:
        raise ValueError(
            f"Model '{model_name}' was not benchmarked in {evidence_path}."
        )

    raw_params = selected.iloc[0]["Best_Params_JSON"]
    params = (
        json.loads(raw_params)
        if isinstance(raw_params, str) and raw_params.strip()
        else {}
    )

    df = _load_segment_dataset()
    features = get_segment_energy_features()

    bundle = fit_model_bundle(
        model_name=model_name,
        params=params,
        X_train=df[features],
        y_train=df[SEGMENT_TARGET].to_numpy(float),
        features=features,
    )

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifact = {
        "bundle": bundle,
        "features": features,
        "target": SEGMENT_TARGET,
        "target_unit": "Wh",
        "model_name": model_name,
        "params": params,
        "n_segments": int(len(df)),
        "n_flights": int(df["flight_uid"].nunique()),
        "cv_grouping_used_for_selection": "flight_uid",
        "distance_definition": (
            "cumulative 3-D point distance: "
            "Haversine horizontal + altitude difference"
        ),
        "relative_wind_angle_convention": (
            "0=headwind, 90=crosswind, 180=tailwind"
        ),
        "route_bearing_is_model_feature": False,
    }
    joblib.dump(artifact, MODEL_PATH)

    save_json(
        {
            "model_name": model_name,
            "best_params": params,
            "features": features,
            "target": SEGMENT_TARGET,
            "model_path": str(MODEL_PATH),
            "n_segments": int(len(df)),
            "n_flights": int(df["flight_uid"].nunique()),
            "decision_mechanism": (
                "explicit CLI selection after G07 evidence; no approved flag"
            ),
        },
        CHECKPOINT_DIR / "selected_segment_model.json",
    )

    print("=" * 78)
    print("SEGMENT ENERGY MODEL FROZEN")
    print(f"Model: {model_name}")
    print(f"Params: {params}")
    print(f"Features ({len(features)}): {features}")
    print(f"Target: {SEGMENT_TARGET} [Wh]")
    print(f"Saved: {MODEL_PATH}")
    return MODEL_PATH
