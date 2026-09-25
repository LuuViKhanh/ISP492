"""
candidate_builder.py
====================
Builds route candidates for E12 multi-hub routing.
Strictly ensures only pre-flight features are used.
"""
import pandas as pd
import yaml
from pathlib import Path

# This would ideally be cached or imported, keeping it simple here
project_root = Path(__file__).resolve().parent.parent.parent

def load_feature_schema():
    schema_path = project_root / "config" / "feature_schema.yaml"
    with open(schema_path, 'r') as f:
        return yaml.safe_load(f)

ALLOWED_PREFLIGHT_TYPES = {"PRE_FLIGHT", "WEATHER_FORECAST_DERIVED", "ROUTE_PLAN_DERIVED"}

def validate_candidate_features(features: list, schema: dict):
    """
    Enforces that candidate builder only uses allowed pre-flight features.
    If actual_flight_duration, actual_speed, etc., are passed, FAIL FAST.
    """
    for f in features:
        if f not in schema:
            continue
        dep = schema[f].get("deployability")
        if dep not in ALLOWED_PREFLIGHT_TYPES:
            raise ValueError(f"FAIL FAST: Candidate builder received post-flight feature '{f}' with deployability '{dep}'.")

def build_hub_candidates(base_request: dict, hubs: list, schema: dict) -> pd.DataFrame:
    """
    Given a delivery request to a specific destination, builds feature vectors
    for each possible hub.
    """
    candidates = []
    
    # Check what features we are trying to build
    feature_names = list(base_request.keys())
    
    # 1. Enforce PRE-FLIGHT ONLY rule
    validate_candidate_features(feature_names, schema)
    
    for hub in hubs:
        # Clone base request
        cand = base_request.copy()
        cand["hub_id"] = hub["id"]
        
        # 2. Enforce unique physical calculations based on hub geometry
        # In a real system, we compute bearing from Hub(lat,lon) to Dest(lat,lon)
        # Here we just mock the assignment ensuring it's unique
        cand["planned_distance"] = hub["distance_to_dest"]
        cand["route_bearing"] = hub["bearing_to_dest"]
        
        # Calculate wind angles relative to this specific route bearing
        # assuming wind_dir_deg is in base_request
        if "wind_dir_deg" in cand:
            # relative angle = |wind_dir - bearing| % 360 ... simplified mapping
            angle = abs(cand["wind_dir_deg"] - cand["route_bearing"]) % 360
            if angle > 180:
                angle = 360 - angle
            cand["relative_wind_angle"] = angle
            
        candidates.append(cand)
        
    df_candidates = pd.DataFrame(candidates)
    
    # 3. Ensure uniqueness. Different hubs MUST result in different candidate vectors.
    if len(df_candidates) > 1:
        if df_candidates["planned_distance"].nunique() == 1 and df_candidates["route_bearing"].nunique() == 1:
            # This is a strict scientific guard to catch bugs where all hubs return identical route features
            raise ValueError("Candidate builder produced identical physical vectors for different hubs. Check logic.")
            
    return df_candidates
