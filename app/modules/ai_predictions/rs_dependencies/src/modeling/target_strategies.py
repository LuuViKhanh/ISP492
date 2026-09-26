"""
target_strategies.py
====================
Target transformation strategies for E07 (Direct EE vs Energy-First).
Provides rigorous physical validity checks and explicit distance contexts.
"""
import numpy as np
import pandas as pd
from enum import Enum

class DistanceContext(Enum):
    ACTUAL_DISTANCE = "actual_distance"
    PLANNED_DISTANCE = "planned_distance"

def derive_efficiency(predicted_energy: pd.Series, distance: pd.Series, distance_type: DistanceContext) -> pd.DataFrame:
    """
    Derives Energy Efficiency (EE) from predicted Energy and Distance.
    
    Returns a DataFrame containing:
    - 'derived_ee': The computed energy efficiency
    - 'physical_invalid_prediction': Boolean flag if energy <= 0 or non-finite
    
    Constraint: DOES NOT silently clip energy to 0.01. Flags it instead.
    """
    if not isinstance(distance_type, DistanceContext):
        raise ValueError("distance_type must be an instance of DistanceContext enum.")
        
    invalid_flag = (predicted_energy <= 0) | (~np.isfinite(predicted_energy))
    
    # Calculate EE. For invalid energy, this will result in Inf/NaN, 
    # but we will flag it so downstream evaluation handles it gracefully.
    with np.errstate(divide='ignore', invalid='ignore'):
        derived_ee = distance / predicted_energy
        
    invalid_ee_flag = ~np.isfinite(derived_ee)
    final_invalid = invalid_flag | invalid_ee_flag
    
    return pd.DataFrame({
        'derived_ee': derived_ee,
        'physical_invalid_prediction': final_invalid
    })
