"""D08 — Cross-domain transfer: DJI->VTOL and VTOL->DJI.

Model selection/preprocessing uses source data only. Feature set is explicit so
transfer results can be compared across aerodynamic ablations.
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score, median_absolute_error
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.paths import OUT_DATA_FEATURES, OUT_RES_MULTIUAV
from config.multiuav_config import (
    DEFAULT_FEATURE_SET, FEATURE_SETS, PRIMARY_TARGET, get_feature_set,
    AERO_FEATURE_SETS_REQUIRING_PLANNED_GEOMETRY,
)


def wmape(y_true, y_pred):
    denom = np.sum(np.abs(y_true))
    return np.sum(np.abs(y_true - y_pred)) / denom if denom > 0 else np.nan


def calculate_metrics(y_true, y_pred):
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    return (
        mean_absolute_error(y_true, y_pred),
        np.sqrt(mean_squared_error(y_true, y_pred)),
        r2_score(y_true, y_pred) if len(y_true) > 1 and np.var(y_true) > 0 else np.nan,
        wmape(y_true, y_pred),
        float(np.mean(y_pred - y_true)),
        median_absolute_error(y_true, y_pred),
    )


def get_models():
    return {
        "LinearRegression": LinearRegression(),
        "Ridge": Ridge(alpha=1.0),
        "ExtraTrees": ExtraTreesRegressor(n_estimators=100, random_state=42),
        "XGBoost": XGBRegressor(n_estimators=100, learning_rate=0.1, random_state=42, n_jobs=-1),
        "CatBoost": CatBoostRegressor(n_estimators=100, learning_rate=0.1, random_state=42, verbose=0),
    }


def make_pipeline(name, model):
    steps = [("imputer", SimpleImputer(strategy="median"))]
    if name in {"LinearRegression", "Ridge"}:
        steps.append(("scaler", StandardScaler()))
    steps.append(("model", model))
    return Pipeline(steps)


def source_cv(X, y, groups, feature_set_name):
    n_groups = len(np.unique(groups))
    if n_groups < 2:
        raise ValueError("Source domain needs at least 2 date groups")
    splits = list(GroupKFold(n_splits=min(5, n_groups)).split(X, y, groups=groups))
    rows = []
    for name, model in get_models().items():
        fold_metrics = []
        for tr, va in splits:
            pipe = make_pipeline(name, model)
            pipe.fit(X.iloc[tr], y.iloc[tr])
            fold_metrics.append(calculate_metrics(y.iloc[va], pipe.predict(X.iloc[va])))
        avg = np.nanmean(fold_metrics, axis=0)
        rows.append({
            "FeatureSet": feature_set_name, "Model": name,
            "MAE": avg[0], "RMSE": avg[1], "R2": avg[2],
            "WMAPE": avg[3], "Bias": avg[4], "MedAE": avg[5],
        })
    return pd.DataFrame(rows)


def cross_domain_experiment(train_df, test_df, source_name, target_name, features, feature_set_name):
    print(f"\nEXPERIMENT: {source_name} -> {target_name} [{feature_set_name}]")
    missing = [c for c in features if c not in train_df.columns or c not in test_df.columns]
    if missing:
        raise ValueError(f"Missing cross-domain features: {missing}")

    X_train = train_df[features].copy()
    y_train = train_df[PRIMARY_TARGET].copy()
    groups = train_df["date"].astype(str).copy()
    X_test = test_df[features].copy()
    y_test = test_df[PRIMARY_TARGET].copy()

    cv_df = source_cv(X_train, y_train, groups, feature_set_name)
    cv_df["Source"] = source_name
    cv_df["Target"] = source_name + "_CV"

    cross_rows = []
    predictions = test_df[["dataset_id", "flight_uid", PRIMARY_TARGET]].copy()
    predictions.rename(columns={PRIMARY_TARGET: "y_true"}, inplace=True)

    for name, model in get_models().items():
        pipe = make_pipeline(name, model)
        pipe.fit(X_train, y_train)  # preprocessing fit on SOURCE only
        y_pred = pipe.predict(X_test)
        predictions[f"y_pred_{name}"] = y_pred
        m = calculate_metrics(y_test, y_pred)
        cross_rows.append({
            "FeatureSet": feature_set_name,
            "NFeatures": len(features),
            "TargetName": PRIMARY_TARGET,
            "Source": source_name,
            "Target": target_name,
            "Model": name,
            "MAE": m[0], "RMSE": m[1], "R2": m[2],
            "WMAPE": m[3], "Bias": m[4], "MedAE": m[5],
        })

    return cv_df, pd.DataFrame(cross_rows), predictions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set", default=DEFAULT_FEATURE_SET, choices=list(FEATURE_SETS))
    parser.add_argument("--allow-endpoint-proxy", action="store_true", help="Diagnostic only: allow aerodynamic features derived from observed flight endpoints")
    args = parser.parse_args()
    features = get_feature_set(args.feature_set)

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
    dji = df[df["dataset_id"] == "DJI_M100"].copy()
    vtol = df[df["dataset_id"] == "VTOL"].copy()

    out_dir = Path(OUT_RES_MULTIUAV)
    os.makedirs(out_dir, exist_ok=True)

    cv_dji, d2v, p_d2v = cross_domain_experiment(dji, vtol, "DJI_M100", "VTOL", features, args.feature_set)
    cv_vtol, v2d, p_v2d = cross_domain_experiment(vtol, dji, "VTOL", "DJI_M100", features, args.feature_set)
    all_cv = pd.concat([cv_dji, cv_vtol], ignore_index=True)
    all_cross = pd.concat([d2v, v2d], ignore_index=True)

    suffix = args.feature_set
    all_cv.to_csv(out_dir / f"D08_source_cv_results_{suffix}.csv", index=False)
    all_cross.to_csv(out_dir / f"D08_cross_domain_summary_{suffix}.csv", index=False)
    p_d2v.to_csv(out_dir / f"D08_predictions_DJI_to_VTOL_{suffix}.csv", index=False)
    p_v2d.to_csv(out_dir / f"D08_predictions_VTOL_to_DJI_{suffix}.csv", index=False)
    if args.feature_set == DEFAULT_FEATURE_SET:
        all_cv.to_csv(out_dir / "D08_source_cv_results.csv", index=False)
        all_cross.to_csv(out_dir / "D08_cross_domain_summary.csv", index=False)

    # Compare against matching D07 feature set only.
    d07_path = out_dir / f"D07_within_domain_summary_{suffix}.csv"
    if d07_path.exists():
        d07 = pd.read_csv(d07_path)
        deg = []
        for model in d07["Model"].unique():
            try:
                wd = d07[(d07.Dataset == "DJI_M100") & (d07.Model == model)].WMAPE.iloc[0]
                wv = d07[(d07.Dataset == "VTOL") & (d07.Model == model)].WMAPE.iloc[0]
                dv = all_cross[(all_cross.Source == "DJI_M100") & (all_cross.Target == "VTOL") & (all_cross.Model == model)].WMAPE.iloc[0]
                vd = all_cross[(all_cross.Source == "VTOL") & (all_cross.Target == "DJI_M100") & (all_cross.Model == model)].WMAPE.iloc[0]
                deg += [
                    {"FeatureSet": suffix, "Transfer": "DJI_M100 -> VTOL", "Model": model,
                     "Source_Native_WMAPE": wd, "Cross_Domain_WMAPE": dv,
                     "Target_Native_WMAPE": wv, "Source_Transfer_Penalty": dv-wd,
                     "Target_Native_Gap": dv-wv},
                    {"FeatureSet": suffix, "Transfer": "VTOL -> DJI_M100", "Model": model,
                     "Source_Native_WMAPE": wv, "Cross_Domain_WMAPE": vd,
                     "Target_Native_WMAPE": wd, "Source_Transfer_Penalty": vd-wv,
                     "Target_Native_Gap": vd-wd},
                ]
            except (IndexError, KeyError):
                pass
        if deg:
            pd.DataFrame(deg).to_csv(out_dir / f"D08_degradation_analysis_{suffix}.csv", index=False)

    print("\nD08 SUMMARY")
    print(all_cross.to_string(index=False))


if __name__ == "__main__":
    main()
