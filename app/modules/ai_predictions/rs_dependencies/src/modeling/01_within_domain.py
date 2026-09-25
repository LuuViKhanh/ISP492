"""D07 — Within-domain evaluation for DJI and VTOL.

Uses date-grouped CV and a selectable pre-flight feature set. Preprocessing is
fit inside each training fold to avoid leakage.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.paths import OUT_DATA_FEATURES
from config.multiuav_config import (
    DEFAULT_FEATURE_SET, FEATURE_SETS, PRIMARY_TARGET, get_feature_set,
    AERO_FEATURE_SETS_REQUIRING_PLANNED_GEOMETRY,
)

OUT_DIR = PROJECT_ROOT / "output" / "results" / "multiuav"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def get_models():
    return {
        "LinearRegression": LinearRegression(),
        "Ridge": Ridge(alpha=1.0),
        "ExtraTrees": ExtraTreesRegressor(n_estimators=100, random_state=42),
        "XGBoost": XGBRegressor(
            n_estimators=100, learning_rate=0.1, random_state=42,
            objective="reg:squarederror", n_jobs=-1,
        ),
        "CatBoost": CatBoostRegressor(
            n_estimators=100, learning_rate=0.1, random_state=42, verbose=0,
        ),
    }


def make_pipeline(model_name, model):
    steps = [("imputer", SimpleImputer(strategy="median"))]
    if model_name in {"LinearRegression", "Ridge"}:
        steps.append(("scaler", StandardScaler()))
    steps.append(("model", model))
    return Pipeline(steps)


def wmape(y_true, y_pred):
    denom = np.sum(np.abs(y_true))
    return np.sum(np.abs(y_true - y_pred)) / denom if denom > 0 else np.nan


def evaluate_within_domain(df, dataset_name, features, feature_set_name):
    print(f"\nEvaluating {dataset_name} ({len(df)} samples) — {feature_set_name}")
    missing_cols = [c for c in features if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing features for {feature_set_name}: {missing_cols}")

    X = df[features].copy()
    y = df[PRIMARY_TARGET].to_numpy(dtype=float)
    groups = df["date"].astype(str).to_numpy()
    n_groups = len(np.unique(groups))
    print(f"Features ({len(features)}): {features}")
    print(f"Target: {PRIMARY_TARGET}; unique date groups: {n_groups}")

    if n_groups >= 2:
        cv = GroupKFold(n_splits=min(5, n_groups))
        splits = cv.split(X, y, groups=groups)
    else:
        if len(df) < 5:
            raise ValueError("Not enough samples/groups for reliable CV")
        cv = KFold(n_splits=5, shuffle=True, random_state=42)
        splits = cv.split(X, y)

    # materialize once because generator is consumed per model
    splits = list(splits)
    results = []

    for model_name, model in get_models().items():
        print(f"  Training {model_name}...")
        all_true, all_pred = [], []
        for train_idx, test_idx in splits:
            pipe = make_pipeline(model_name, model)
            pipe.fit(X.iloc[train_idx], y[train_idx])
            pred = pipe.predict(X.iloc[test_idx])
            all_true.extend(y[test_idx])
            all_pred.extend(pred)

        y_true = np.asarray(all_true)
        y_pred = np.asarray(all_pred)
        results.append({
            "Dataset": dataset_name,
            "FeatureSet": feature_set_name,
            "NFeatures": len(features),
            "Target": PRIMARY_TARGET,
            "Model": model_name,
            "MAE": mean_absolute_error(y_true, y_pred),
            "RMSE": np.sqrt(mean_squared_error(y_true, y_pred)),
            "R2": r2_score(y_true, y_pred),
            "WMAPE": wmape(y_true, y_pred),
            "Bias": float(np.mean(y_pred - y_true)),
            "MedAE": float(np.median(np.abs(y_true - y_pred))),
        })

    return pd.DataFrame(results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set", default=DEFAULT_FEATURE_SET, choices=list(FEATURE_SETS))
    parser.add_argument("--allow-endpoint-proxy", action="store_true", help="Diagnostic only: allow aerodynamic features derived from observed flight endpoints")
    args = parser.parse_args()

    features = get_feature_set(args.feature_set)
    print("=" * 70)
    print(f"D07 — WITHIN-DOMAIN EVALUATION [{args.feature_set}]")
    print("=" * 70)

    data_path = Path(OUT_DATA_FEATURES) / "multiuav_flight_level_features.csv"
    df = pd.read_csv(data_path)
    if args.feature_set in AERO_FEATURE_SETS_REQUIRING_PLANNED_GEOMETRY:
        sources = set(df.get("route_geometry_source", pd.Series(dtype=str)).dropna().astype(str).unique())
        if sources != {"PLANNED_ROUTE"} and not args.allow_endpoint_proxy:
            ratio = df.get("route_endpoint_to_path_ratio", pd.Series(dtype=float))
            med = float(pd.to_numeric(ratio, errors="coerce").median()) if len(ratio) else float("nan")
            raise ValueError(
                f"{args.feature_set} requires true planned route geometry for final pre-flight use. "
                f"Current geometry sources={sources or {'MISSING'}}, median endpoint/path ratio={med:.4f}. "
                "Use --allow-endpoint-proxy only for a diagnostic ablation."
            )
    df = df[np.isfinite(pd.to_numeric(df[PRIMARY_TARGET], errors="coerce"))].copy()
    df = df[df[PRIMARY_TARGET] > 0].copy()

    dji = evaluate_within_domain(
        df[df["dataset_id"] == "DJI_M100"].copy(), "DJI_M100", features, args.feature_set
    )
    vtol = evaluate_within_domain(
        df[df["dataset_id"] == "VTOL"].copy(), "VTOL", features, args.feature_set
    )
    out = pd.concat([dji, vtol], ignore_index=True)

    tagged = OUT_DIR / f"D07_within_domain_summary_{args.feature_set}.csv"
    out.to_csv(tagged, index=False)
    if args.feature_set == DEFAULT_FEATURE_SET:
        out.to_csv(OUT_DIR / "D07_within_domain_summary.csv", index=False)

    print("\nRESULTS SUMMARY")
    print(out.to_string(index=False))
    print(f"\nSaved: {tagged}")


if __name__ == "__main__":
    main()
