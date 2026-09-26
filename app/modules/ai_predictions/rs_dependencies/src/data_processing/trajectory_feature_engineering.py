"""
trajectory_feature_engineering.py
=================================
Deterministic feature engineering for Trajectory/Geometry (F3A / F3B).
F3A = Observed Trajectory Geometry
F3B = Planned Route Geometry (If unavailable, explicitly noted)
"""
import pandas as pd
import warnings

def attach_f3a_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attaches F3A (Observed Trajectory) features.
    These are explanatory features based on actual telemetry.
    """
    # Assuming these might be pre-calculated in the raw data or we would calculate them from time-series telemetry.
    # For now, we expect them to be provided in the dataframe or we warn.
    expected_f3a = [
        "actual_3d_path_length", "actual_tortuosity", "actual_total_ascent",
        "actual_total_descent", "actual_heading_change", "actual_hover_ratio",
        "acceleration_rms"
    ]
    
    df_out = df.copy()
    missing = [c for c in expected_f3a if c not in df_out.columns]
    
    if missing:
        warnings.warn(f"Missing expected F3A features: {missing}. These will not be attached.")
        
    return df_out

def attach_f3b_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attaches F3B (Planned Route) features.
    Used for pre-flight prediction.
    """
    expected_f3b = [
        "planned_distance", "planned_turn_count", "planned_heading_change",
        "planned_ascent", "planned_descent", "planned_tortuosity", "route_bearing"
    ]
    
    df_out = df.copy()
    missing = [c for c in expected_f3b if c not in df_out.columns]
    
    if len(missing) == len(expected_f3b):
        warnings.warn("F3B features are NOT AVAILABLE in the current dataset. Do not use actual trajectory as proxy.")
    elif missing:
        warnings.warn(f"Missing some F3B features: {missing}")
        
    return df_out

def attach_trajectory_features(df: pd.DataFrame) -> pd.DataFrame:
    df = attach_f3a_features(df)
    df = attach_f3b_features(df)
    return df
