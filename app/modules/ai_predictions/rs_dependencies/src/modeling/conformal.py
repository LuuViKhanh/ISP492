"""
conformal.py
============
Group-aware conformal prediction logic.
Computes prediction intervals while avoiding data leakage.
"""
import numpy as np
import pandas as pd

def compute_conformal_quantiles(calibration_residuals: np.ndarray, alpha: float = 0.10) -> float:
    """
    Computes the conformal quantile 'q' given absolute residuals of a separate CALIBRATION set.
    """
    n = len(calibration_residuals)
    # finite sample correction: ceil((n + 1) * (1 - alpha)) / n
    val = np.ceil((n + 1) * (1 - alpha)) / n
    # If val > 1, we can't guarantee coverage, just return max or fallback
    val = min(val, 1.0)
    q = np.quantile(calibration_residuals, val)
    return float(q)

def apply_conformal_interval(point_predictions: pd.Series, q: float) -> pd.DataFrame:
    """
    Applies the computed quantile 'q' to point predictions.
    """
    lower = point_predictions - q
    upper = point_predictions + q
    return pd.DataFrame({
        "pred_lower": lower,
        "pred_upper": upper,
        "interval_width": upper - lower
    })

def transform_energy_interval_to_ee(energy_intervals: pd.DataFrame, distance: pd.Series) -> pd.DataFrame:
    """
    Transforms [L_E, U_E] to [EE_lower, EE_upper].
    Because EE = Distance / Energy, and it's inverse monotonic:
    EE_lower = Distance / U_E
    EE_upper = Distance / L_E
    """
    # Guard against negative/zero bounds
    # If U_E <= 0, EE_lower becomes invalid. If L_E <= 0, EE_upper becomes invalid.
    
    with np.errstate(divide='ignore', invalid='ignore'):
        ee_lower = distance / energy_intervals["pred_upper"]
        ee_upper = distance / energy_intervals["pred_lower"]
        
    return pd.DataFrame({
        "pred_lower": ee_lower,
        "pred_upper": ee_upper,
        "interval_width": ee_upper - ee_lower
    })
