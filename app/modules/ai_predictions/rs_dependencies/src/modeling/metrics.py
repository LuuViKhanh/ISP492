"""
metrics.py
==========
Standardized metrics calculation for Research V2.
"""
import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score, median_absolute_error

def wmape(y_true, y_pred):
    """Weighted Mean Absolute Percentage Error."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    total_abs_true = np.sum(np.abs(y_true))
    if total_abs_true == 0:
        return np.nan
    return np.sum(np.abs(y_true - y_pred)) / total_abs_true

def compute_metrics(y_true, y_pred, include_r2=True):
    """
    Computes a standard dictionary of evaluation metrics.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    
    n = len(y_true)
    
    if n == 0:
        return {
            "MAE": np.nan,
            "RMSE": np.nan,
            "MedAE": np.nan,
            "Bias": np.nan,
            "WMAPE": np.nan,
            "R2": np.nan,
            "N": 0
        }
        
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    medae = median_absolute_error(y_true, y_pred)
    bias = np.mean(y_pred - y_true)  # Bias: avg prediction error. Positive means overpredicting.
    wmape_val = wmape(y_true, y_pred)
    
    metrics = {
        "MAE": mae,
        "RMSE": rmse,
        "MedAE": medae,
        "Bias": bias,
        "WMAPE": wmape_val,
        "N": n
    }
    
    if include_r2:
        # R2 might not be meaningful if variance is 0 or N is very small
        if n > 1 and np.var(y_true) > 1e-6:
            r2 = r2_score(y_true, y_pred)
        else:
            r2 = np.nan
        metrics["R2"] = r2
        
    return metrics
