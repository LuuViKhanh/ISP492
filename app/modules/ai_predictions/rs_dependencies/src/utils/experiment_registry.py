"""
experiment_registry.py
======================
Manages the Master Experiment Results and Prediction Registry for Research V2.
"""
import pandas as pd
import numpy as np
import os
from pathlib import Path
import datetime

RESEARCH_OUT_DIR = Path("output/research_v2")
RESULTS_DIR = RESEARCH_OUT_DIR / "results"
PREDICTIONS_DIR = RESEARCH_OUT_DIR / "predictions"
MANIFESTS_DIR = RESEARCH_OUT_DIR / "manifests"

MASTER_RESULTS_PATH = RESULTS_DIR / "MASTER_EXPERIMENT_RESULTS.csv"

# Ensure directories exist
for d in [RESULTS_DIR, PREDICTIONS_DIR, MANIFESTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

def log_experiment_result(
    experiment_id, protocol, feature_set, model_name, target, seed,
    train_n, eval_n, n_groups, n_features,
    r2, mae, medae, rmse, wmape, bias,
    training_time, inference_time,
    dataset_hash, feature_hash, config_hash, git_commit
):
    """
    Appends a single experiment evaluation result to the master CSV.
    """
    record = {
        "experiment_id": experiment_id,
        "protocol": protocol,
        "feature_set": feature_set,
        "model": model_name,
        "target": target,
        "seed": seed,
        "train_n": train_n,
        "eval_n": eval_n,
        "n_groups": n_groups,
        "n_features": n_features,
        "r2": r2,
        "mae": mae,
        "medae": medae,
        "rmse": rmse,
        "wmape": wmape,
        "bias": bias,
        "training_time": training_time,
        "inference_time": inference_time,
        "dataset_hash": dataset_hash,
        "feature_hash": feature_hash,
        "config_hash": config_hash,
        "git_commit": git_commit,
        "timestamp": datetime.datetime.now().isoformat()
    }
    
    df_record = pd.DataFrame([record])
    
    # Append to CSV
    if not MASTER_RESULTS_PATH.exists():
        df_record.to_csv(MASTER_RESULTS_PATH, index=False)
    else:
        df_record.to_csv(MASTER_RESULTS_PATH, mode='a', header=False, index=False)

def log_predictions(predictions_df: pd.DataFrame, experiment_id, protocol, feature_set, model_name, target):
    """
    Saves predictions to the prediction registry.
    Expected columns in predictions_df:
    flight, route, date, domain, fold, seed, actual, predicted, residual, absolute_error
    """
    # Ensure necessary metadata is attached
    df = predictions_df.copy()
    df["experiment_id"] = experiment_id
    df["protocol"] = protocol
    df["feature_set"] = feature_set
    df["model"] = model_name
    df["target"] = target
    
    # Reorder columns to match requested format approximately
    cols_order = [
        "experiment_id", "flight", "route", "date", 
        "protocol", "domain", "fold", "seed", 
        "actual", "predicted", "residual", "absolute_error",
        "feature_set", "model", "target"
    ]
    
    # Add missing columns with NaN if any (for safety)
    for c in cols_order:
        if c not in df.columns:
            df[c] = np.nan
            
    df = df[cols_order]
    
    # We append to a master predictions file, or save per experiment.
    # Appending to a single master file might get huge, but user requested a prediction registry.
    master_pred_path = PREDICTIONS_DIR / "MASTER_PREDICTIONS.csv"
    
    if not master_pred_path.exists():
        df.to_csv(master_pred_path, index=False)
    else:
        df.to_csv(master_pred_path, mode='a', header=False, index=False)
