"""
Phase 4 - Step 1: Train Models & Hyperparameter Tuning
Builds Scikit-Learn Pipelines (Scaling + Model) to prevent data leakage (Level 5).
Uses GroupKFold for Cross-Validation (defined by split_manifest.json).
Performs GridSearchCV.
Generates Out-Of-Fold (OOF) predictions for model diagnostics.
Saves best estimators (pipelines) for each algorithm.
"""

import pandas as pd
import numpy as np
import time
import joblib
import json
from pathlib import Path

from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.base import clone
from sklearn.model_selection import GroupKFold, GridSearchCV, cross_val_predict

from config.paths import OUT_DATA_SPLITS, OUT_MODELS_TRAINED, OUT_METADATA, OUT_RES_EVAL
from config.dataset_config import ALL_FEATURES, TARGET
from config.model_config import MODEL_NAMES, LINEAR_MODELS, PARAM_GRIDS
from src.utils.logging_utils import get_logger

# Import models
from sklearn.linear_model import LinearRegression, Ridge, Lasso, ElasticNet
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from xgboost import XGBRegressor
from lightgbm import LGBMRegressor
from catboost import CatBoostRegressor

logger = get_logger("TrainModels")

def get_base_models():
    """Return instantiated base models."""
    return {
        "Linear Regression": LinearRegression(),
        "Ridge": Ridge(),
        "Lasso": Lasso(max_iter=10000),
        "ElasticNet": ElasticNet(max_iter=10000),
        "Decision Tree": DecisionTreeRegressor(random_state=42),
        "ExtraTrees": ExtraTreesRegressor(random_state=42, n_jobs=-1),
        "Random Forest": RandomForestRegressor(random_state=42, n_jobs=-1),
        "XGBoost": XGBRegressor(random_state=42, n_jobs=-1, verbosity=0),
        "LightGBM": LGBMRegressor(random_state=42, n_jobs=-1, verbosity=-1),
        "CatBoost": CatBoostRegressor(random_state=42, verbose=0)
    }

def build_pipeline(model_name, model):
    """Build a Pipeline with optional scaling based on model type."""
    if model_name in LINEAR_MODELS:
        # Linear models need scaled features
        return Pipeline([
            ('scaler', StandardScaler()),
            ('model', model)
        ])
    else:
        # Tree models don't need scaling (using passthrough or just omitting scaler)
        return Pipeline([
            ('model', model)
        ])

def get_group_column():
    """Read the group column used for splitting."""
    manifest_path = OUT_METADATA / "split_manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
            return manifest.get("group_column", "date")
    return "date"

def main():
    logger.info("="*60)
    logger.info("START: Train Models")
    
    in_train_path = OUT_DATA_SPLITS / "train.csv"
    if not in_train_path.exists():
        logger.error(f"Training data not found at {in_train_path}")
        return
        
    train_df = pd.read_csv(in_train_path)
    group_col = get_group_column()
    
    X = train_df[ALL_FEATURES].values
    y = train_df[TARGET].values
    
    if group_col in train_df.columns:
        groups = train_df[group_col].values
    else:
        logger.warning(f"Group column '{group_col}' not found. Defaulting to index.")
        groups = train_df.index.values

    logger.info(f"Training on {len(X):,} samples. Grouping by {group_col} ({len(np.unique(groups))} groups).")
    
    cv = GroupKFold(n_splits=5)
    base_models = get_base_models()
    
    # We will store OOF predictions here
    oof_predictions = train_df[["flight", group_col, TARGET]].copy() if "flight" in train_df.columns else train_df[[group_col, TARGET]].copy()
    
    tuning_results = []

    for name in MODEL_NAMES:
        if name not in base_models:
            continue
            
        logger.info(f"Training {name}...")
        
        # 1. Build Pipeline
        pipeline = build_pipeline(name, base_models[name])
        param_grid = PARAM_GRIDS.get(name, {})
        
        start_time = time.time()
        
        # 2. Hyperparameter Tuning using GridSearchCV
        if param_grid:
            grid = GridSearchCV(
                pipeline, 
                param_grid=param_grid, 
                cv=cv, 
                scoring="r2", 
                n_jobs=-1,
                return_train_score=True
            )
            grid.fit(X, y, groups=groups)
            best_pipeline = grid.best_estimator_
            best_params = grid.best_params_
            best_score = grid.best_score_
        else:
            best_pipeline = pipeline.fit(X, y) # Fit full for saving
            best_params = {}
            # Need a score for logging
            from sklearn.model_selection import cross_val_score
            scores = cross_val_score(best_pipeline, X, y, groups=groups, cv=cv, scoring="r2", n_jobs=-1)
            best_score = scores.mean()
            
        elapsed = time.time() - start_time
        logger.info(f"  Best CV R2: {best_score:.4f} | Time: {elapsed:.1f}s")
        
        # 3. Generate Out-Of-Fold (OOF) predictions with the best pipeline
        # Cross_val_predict guarantees Leakage Level 5 (no overlap of scaler/model on val set)
        oof_preds = cross_val_predict(best_pipeline, X, y, groups=groups, cv=cv, n_jobs=-1)
        oof_predictions[f"{name}_oof_pred"] = oof_preds
        
        # 4. Save trained pipeline
        model_filename = f"{name.lower().replace(' ', '_')}.pkl"
        joblib.dump(best_pipeline, OUT_MODELS_TRAINED / model_filename)
        
        tuning_results.append({
            "model_name": name,
            "best_cv_r2": best_score,
            "best_params": json.dumps(best_params),
            "training_time_s": elapsed
        })
        
    # Save OOF predictions
    oof_predictions.to_csv(OUT_RES_EVAL / "oof_predictions.csv", index=False)
    logger.info("Saved OOF predictions for all models.")
    
    # Save Tuning results
    pd.DataFrame(tuning_results).to_csv(OUT_RES_EVAL / "tuning_results.csv", index=False)
    
    logger.info("END: Train Models")
    logger.info("="*60)

if __name__ == "__main__":
    main()
