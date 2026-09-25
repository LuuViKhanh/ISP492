"""Shared utilities for CP1/CP2/CP3 validation.

This module centralizes the scientifically important rules so they are not
silently reimplemented differently by each runner:

- Primary target is flight-level ``energy_efficiency`` (m/Wh).
- DJI_ONLY and VTOL_ONLY load their own feature tables directly.
- COMBINED loads only the explicitly harmonized combined table.
- An untouched group holdout is created BEFORE CP1 feature screening.
- Development CV is group-aware by date; COMBINED groups include dataset_id.
- Preprocessing is fit inside each fold to avoid leakage.
- Fixed/random seeds are reproducibility controls, not tuned hyperparameters.

The code deliberately avoids sklearn Pipeline for CatBoost compatibility across
versions; imputation/scaling is still fit on training data only.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Lasso, LinearRegression, Ridge
from sklearn.model_selection import GroupKFold, ParameterGrid
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor

from catboost import CatBoostRegressor
from lightgbm import LGBMRegressor
from xgboost import XGBRegressor

from config.experiment_config import CV_FOLDS, RANDOM_STATE, TEST_SIZE
from config.multiuav_config import (
    CASE_COMBINED,
    CASE_DJI_ONLY,
    CASE_VTOL_ONLY,
    PRIMARY_TARGET,
    VALIDATION_CASES,
)
from config.paths import OUT_DATA_FEATURES, OUT_RES_MULTIUAV
from src.modeling.metrics import compute_metrics


CASE_DATA_FILES = {
    CASE_DJI_ONLY: Path(OUT_DATA_FEATURES) / "flight_level_features.csv",
    CASE_VTOL_ONLY: Path(OUT_DATA_FEATURES) / "vtol_flight_level_features.csv",
    CASE_COMBINED: Path(OUT_DATA_FEATURES) / "multiuav_flight_level_features.csv",
}

CASE_DATASET_IDS = {
    CASE_DJI_ONLY: "DJI_M100",
    CASE_VTOL_ONLY: "VTOL",
}

CASE_UAV_TYPES = {
    CASE_DJI_ONLY: "QUADCOPTER",
    CASE_VTOL_ONLY: "VTOL_FIXED_WING",
}

LINEAR_MODEL_NAMES = {
    "LinearRegression",
    "Ridge",
    "Lasso",
    "ElasticNet",
}

TREE_MODEL_NAMES = {
    "DecisionTree",
    "ExtraTrees",
    "RandomForest",
    "XGBoost",
    "LightGBM",
    "CatBoost",
}


# Hyperparameter grids used only at CP2. random_state is never tuned.
MODEL_PARAM_GRIDS: Dict[str, List[dict]] = {
    "LinearRegression": [{}],
    "Ridge": list(ParameterGrid({"alpha": [0.1, 1.0, 10.0]})),
    "Lasso": list(ParameterGrid({"alpha": [0.01, 0.1, 1.0]})),
    "ElasticNet": list(
        ParameterGrid({"alpha": [0.01, 0.1, 1.0], "l1_ratio": [0.2, 0.5, 0.8]})
    ),
    "DecisionTree": list(
        ParameterGrid({"max_depth": [None, 5, 10, 20], "min_samples_split": [2, 5, 10]})
    ),
    "ExtraTrees": list(
        ParameterGrid({"n_estimators": [50, 100], "max_depth": [None, 10, 20]})
    ),
    "RandomForest": list(
        ParameterGrid({"n_estimators": [50, 100], "max_depth": [None, 10, 20]})
    ),
    "XGBoost": list(
        ParameterGrid(
            {
                "n_estimators": [50, 100],
                "learning_rate": [0.01, 0.1, 0.2],
                "max_depth": [3, 5, 7],
            }
        )
    ),
    "LightGBM": list(
        ParameterGrid(
            {
                "n_estimators": [50, 100],
                "learning_rate": [0.01, 0.1, 0.2],
                "num_leaves": [31, 50],
            }
        )
    ),
    "CatBoost": list(
        ParameterGrid(
            {
                "iterations": [50, 100],
                "learning_rate": [0.01, 0.1, 0.2],
                "depth": [4, 6, 8],
            }
        )
    ),
}


@dataclass
class ModelBundle:
    """Serializable preprocessing + estimator bundle."""

    model_name: str
    features: List[str]
    imputer: SimpleImputer
    scaler: StandardScaler | None
    model: object
    params: dict

    def transform(self, X: pd.DataFrame) -> pd.DataFrame | np.ndarray:
        X = X[self.features]
        arr = self.imputer.transform(X)
        if self.scaler is not None:
            return self.scaler.transform(arr)
        return pd.DataFrame(arr, columns=self.features, index=X.index)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model.predict(self.transform(X)), dtype=float)


def case_result_dir(case: str) -> Path:
    _validate_case(case)
    return Path(OUT_RES_MULTIUAV) / "validation" / case.lower()


def _validate_case(case: str) -> None:
    if case not in VALIDATION_CASES:
        raise ValueError(f"Unknown case '{case}'. Valid: {', '.join(VALIDATION_CASES)}")


def load_case_dataset(case: str) -> Tuple[pd.DataFrame, Path]:
    """Load a validation case from the correct independent source."""
    _validate_case(case)
    path = CASE_DATA_FILES[case]
    if not path.exists():
        if case == CASE_COMBINED:
            raise FileNotFoundError(
                f"Missing combined table: {path}. Run harmonize_uav_datasets.py first. "
                "The validation runner will not combine datasets implicitly."
            )
        raise FileNotFoundError(
            f"Missing {case} feature table: {path}. Run that platform's feature stage first."
        )

    df = pd.read_csv(path)

    if case in CASE_DATASET_IDS:
        dataset_id = CASE_DATASET_IDS[case]
        df["dataset_id"] = dataset_id
        if "uav_type" not in df.columns:
            df["uav_type"] = CASE_UAV_TYPES[case]
        if "flight_uid" not in df.columns:
            df["flight_uid"] = dataset_id + "_" + df["flight"].astype(str)

    required = [PRIMARY_TARGET, "date", "dataset_id", "flight_uid"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name} missing required columns: {missing}")

    df[PRIMARY_TARGET] = pd.to_numeric(df[PRIMARY_TARGET], errors="coerce")
    df = df[np.isfinite(df[PRIMARY_TARGET]) & (df[PRIMARY_TARGET] > 0)].copy()
    df["date"] = df["date"].astype(str)

    if case == CASE_COMBINED:
        ids = set(df["dataset_id"].dropna().astype(str).unique())
        expected = {"DJI_M100", "VTOL"}
        if not expected.issubset(ids):
            raise ValueError(
                f"Combined table must contain both {sorted(expected)}; found {sorted(ids)}"
            )
        # Platform identity is a derived pre-flight architecture feature for
        # COMBINED only. The harmonized CSV does not need to persist it.
        df["is_vtol"] = (df["dataset_id"].astype(str) == "VTOL").astype(int)

    return df.reset_index(drop=True), path


def group_labels(df: pd.DataFrame, case: str) -> pd.Series:
    """Return group labels used by both holdout splitting and development CV."""
    if case == CASE_COMBINED:
        return df["dataset_id"].astype(str) + "::" + df["date"].astype(str)
    return df["date"].astype(str)


def _platform_holdout_groups(
    sub: pd.DataFrame,
    rng: np.random.Generator,
    test_size: float,
) -> set[str]:
    groups = sorted(sub["date"].astype(str).unique())
    if len(groups) < 2:
        raise ValueError(
            "Need at least two date groups to create an untouched group holdout."
        )
    groups = list(groups)
    rng.shuffle(groups)
    n_test = max(1, int(round(len(groups) * test_size)))
    n_test = min(n_test, len(groups) - 1)
    return set(groups[:n_test])


def development_holdout_split(
    df: pd.DataFrame,
    case: str,
    test_size: float = TEST_SIZE,
    random_state: int = RANDOM_STATE,
) -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Create deterministic date-group holdout before CP1.

    COMBINED is split inside each platform so the untouched holdout represents
    both DJI and VTOL rather than being dominated by one platform's dates.
    """
    _validate_case(case)
    rng = np.random.default_rng(random_state)
    holdout_mask = pd.Series(False, index=df.index)
    holdout_groups: Dict[str, List[str]] = {}

    if case == CASE_COMBINED:
        for dataset_id, sub in df.groupby("dataset_id", sort=True):
            chosen = _platform_holdout_groups(sub, rng, test_size)
            holdout_groups[str(dataset_id)] = sorted(chosen)
            holdout_mask.loc[sub.index] = sub["date"].astype(str).isin(chosen)
    else:
        chosen = _platform_holdout_groups(df, rng, test_size)
        holdout_groups[str(df["dataset_id"].iloc[0])] = sorted(chosen)
        holdout_mask = df["date"].astype(str).isin(chosen)

    dev = df.loc[~holdout_mask].copy().reset_index(drop=True)
    holdout = df.loc[holdout_mask].copy().reset_index(drop=True)
    if dev.empty or holdout.empty:
        raise ValueError("Development/holdout split produced an empty partition.")

    manifest = {
        "case": case,
        "random_state": int(random_state),
        "test_size_requested": float(test_size),
        "n_total": int(len(df)),
        "n_development": int(len(dev)),
        "n_holdout": int(len(holdout)),
        "holdout_date_groups_by_dataset": holdout_groups,
    }
    return dev, holdout, manifest


def make_group_kfold(df: pd.DataFrame, case: str, requested_folds: int = CV_FOLDS):
    groups = group_labels(df, case)
    n_groups = int(groups.nunique())
    n_splits = min(int(requested_folds), n_groups)
    if n_splits < 2:
        raise ValueError(f"Need >=2 development groups for CV; found {n_groups}")
    return GroupKFold(n_splits=n_splits), groups


def build_model(model_name: str, params: Mapping | None = None, random_state: int = RANDOM_STATE):
    params = dict(params or {})
    if model_name == "LinearRegression":
        return LinearRegression(**params)
    if model_name == "Ridge":
        return Ridge(**params)
    if model_name == "Lasso":
        return Lasso(max_iter=20_000, **params)
    if model_name == "ElasticNet":
        return ElasticNet(max_iter=20_000, random_state=random_state, **params)
    if model_name == "DecisionTree":
        return DecisionTreeRegressor(random_state=random_state, **params)
    if model_name == "ExtraTrees":
        return ExtraTreesRegressor(random_state=random_state, n_jobs=-1, **params)
    if model_name == "RandomForest":
        return RandomForestRegressor(random_state=random_state, n_jobs=-1, **params)
    if model_name == "XGBoost":
        return XGBRegressor(
            objective="reg:squarederror",
            random_state=random_state,
            n_jobs=-1,
            verbosity=0,
            **params,
        )
    if model_name == "LightGBM":
        return LGBMRegressor(
            random_state=random_state,
            n_jobs=-1,
            verbosity=-1,
            **params,
        )
    if model_name == "CatBoost":
        return CatBoostRegressor(
            random_seed=random_state,
            verbose=0,
            **params,
        )
    raise KeyError(f"Unknown model '{model_name}'")


def fit_model_bundle(
    model_name: str,
    params: Mapping,
    X_train: pd.DataFrame,
    y_train: Sequence[float],
    features: Sequence[str],
) -> ModelBundle:
    features = list(features)
    imputer = SimpleImputer(strategy="median")
    arr = imputer.fit_transform(X_train[features])
    scaler = None
    if model_name in LINEAR_MODEL_NAMES:
        scaler = StandardScaler()
        transformed = scaler.fit_transform(arr)
    else:
        transformed = pd.DataFrame(arr, columns=features, index=X_train.index)

    model = build_model(model_name, params)
    model.fit(transformed, np.asarray(y_train, dtype=float))
    return ModelBundle(
        model_name=model_name,
        features=features,
        imputer=imputer,
        scaler=scaler,
        model=model,
        params=dict(params),
    )


def evaluate_cv_configuration(
    df: pd.DataFrame,
    case: str,
    features: Sequence[str],
    model_name: str,
    params: Mapping,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Group-aware development CV for one model/parameter configuration."""
    features = list(features)
    missing = [c for c in features + [PRIMARY_TARGET] if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns for CV: {missing}")

    cv, groups = make_group_kfold(df, case)
    fold_rows = []
    pred_rows = []

    X_all = df[features]
    y_all = df[PRIMARY_TARGET].to_numpy(float)

    for fold_idx, (train_idx, val_idx) in enumerate(cv.split(X_all, y_all, groups), start=1):
        train_df = df.iloc[train_idx]
        val_df = df.iloc[val_idx]
        bundle = fit_model_bundle(
            model_name,
            params,
            train_df[features],
            train_df[PRIMARY_TARGET].to_numpy(float),
            features,
        )
        pred = bundle.predict(val_df[features])
        metrics = compute_metrics(val_df[PRIMARY_TARGET].to_numpy(float), pred)
        fold_rows.append(
            {
                "fold": fold_idx,
                "model": model_name,
                "params_json": json.dumps(dict(params), sort_keys=True),
                **metrics,
            }
        )
        for idx, y_true, y_pred in zip(val_df.index, val_df[PRIMARY_TARGET], pred):
            pred_rows.append(
                {
                    "row_index": int(idx),
                    "flight_uid": str(df.loc[idx, "flight_uid"]),
                    "dataset_id": str(df.loc[idx, "dataset_id"]),
                    "date": str(df.loc[idx, "date"]),
                    "y_true": float(y_true),
                    "y_pred": float(y_pred),
                    "fold": fold_idx,
                }
            )

    return pd.DataFrame(fold_rows), pd.DataFrame(pred_rows)


def summarize_fold_metrics(fold_df: pd.DataFrame) -> dict:
    out = {}
    for metric in ["R2", "MAE", "RMSE", "WMAPE", "Bias", "MedAE"]:
        vals = pd.to_numeric(fold_df[metric], errors="coerce")
        out[f"CV_{metric}_mean"] = float(vals.mean())
        out[f"CV_{metric}_std"] = float(vals.std(ddof=0))
    out["n_folds"] = int(len(fold_df))
    return out


def save_json(data: Mapping, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def load_best_params_for_model(cp2_evidence_path: Path, model_name: str) -> dict:
    df = pd.read_csv(cp2_evidence_path)
    row = df[df["Model"] == model_name]
    if row.empty:
        raise ValueError(f"Model '{model_name}' not present in {cp2_evidence_path}")
    raw = row.iloc[0]["Best_Params_JSON"]
    return json.loads(raw) if isinstance(raw, str) and raw.strip() else {}


def parse_model_list(raw: str | None, available: Iterable[str]) -> List[str]:
    available = list(available)
    if raw is None or raw.strip().lower() == "all":
        return available
    requested = [x.strip() for x in raw.split(",") if x.strip()]
    unknown = [x for x in requested if x not in available]
    if unknown:
        raise ValueError(f"Unknown models {unknown}; available={available}")
    return requested


def safe_pct_change(new: float, base: float) -> float:
    if not np.isfinite(new) or not np.isfinite(base) or base == 0:
        return float("nan")
    return float((new - base) / abs(base) * 100.0)
