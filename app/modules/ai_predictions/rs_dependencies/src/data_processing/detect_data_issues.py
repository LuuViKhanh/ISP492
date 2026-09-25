"""
Phase 2 - Step 3: Detect Data Issues
Identifies missing values, outliers, duplicates, and non-representative flights (static/hover candidates).
Classifies outliers into: Impossible, Suspicious, Extreme but valid.
Generates comprehensive quality reports.
Does NOT clean or modify the data. Outputs evidence for HUMAN GATE 1.
"""

import pandas as pd
import numpy as np
import yaml
from pathlib import Path
from scipy import stats

from config.paths import DATA_INTERMEDIATE_DIR, OUT_RES_QUALITY
from config.dataset_config import ALL_FEATURES
from src.utils.logging_utils import get_logger

logger = get_logger("DetectDataIssues")

def haversine_distance(lon1, lat1, lon2, lat2):
    """
    Calculate the great circle distance in meters between two points 
    on the earth (specified in decimal degrees).
    """
    R = 6371000  # Earth radius in meters
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2)**2
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c

def load_data_dictionary():
    dict_path = Path("config/data_dictionary.yaml")
    if dict_path.exists():
        with open(dict_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

def analyze_missing_by_flight(df):
    """Analyze missing values grouped by flight."""
    logger.info("Analyzing missing values by flight...")
    
    # Identify weather columns (often missing)
    weather_cols = [
        "temperature_c", "apparent_temp_c", "dew_point_c", "humidity_pct",
        "wind_speed_ms", "avg_wind_speed", "wind_gust_ms", "wind_dir_deg", 
        "precipitation_mm", "pressure_hpa", "cloud_cover_pct"
    ]
    
    available = [c for c in weather_cols if c in df.columns]
    
    if not available or "flight" not in df.columns:
        return pd.DataFrame()
        
    report = (
        df.groupby("flight")[available]
        .apply(lambda x: x.isna().sum().sum())
        .reset_index(name="missing_cells")
    )
    
    report["missing_rows"] = (
        df.groupby("flight")[available]
        .apply(lambda x: x.isna().any(axis=1).sum())
        .values
    )
    
    # Also calculate missing percentage
    total_rows = df.groupby("flight").size().values
    report["missing_pct"] = (report["missing_rows"] / total_rows) * 100
    
    return report

def analyze_duplicates(df):
    logger.info("Analyzing duplicates...")
    
    exact_dups = df.duplicated().sum()
    
    flight_time_dups = 0
    if "flight" in df.columns and "time" in df.columns:
        flight_time_dups = df.duplicated(subset=["flight", "time"], keep=False).sum()
        
    dup_df = pd.DataFrame([
        {"type": "exact_duplicate", "count": int(exact_dups)},
        {"type": "flight_time_duplicate", "count": int(flight_time_dups)}
    ])
    dup_df.to_csv(OUT_RES_QUALITY / "duplicate_report.csv", index=False)
    logger.info(f"Duplicates found: Exact={exact_dups}, Flight-Time={flight_time_dups}")

def build_flight_quality_report(df, data_dict):
    """Build a comprehensive flight quality report including static/hover candidates."""
    logger.info("Building flight quality report...")
    if "flight" not in df.columns:
        return
        
    # Get configuration rules
    rules = data_dict.get("flight_quality_rules", {})
    static_rules = rules.get("static", {"max_altitude_m": 1.0, "max_speed_ms": 0.1})
    hover_rules = rules.get("hover", {
        "routes": ["H"], 
        "max_horizontal_displacement_m": 20.0,
        "max_horizontal_path_length_m": 50.0,
        "max_mean_horizontal_velocity_ms": 0.5,
        "max_mean_altitude_m": 5.0
    })
    
    grouped = df.groupby("flight").agg(
        records=("flight", "size"),
        route=("route", "first") if "route" in df.columns else ("flight", "first"),
        min_speed=("speed", "min") if "speed" in df.columns else ("flight", "first"),
        mean_speed=("speed", "mean") if "speed" in df.columns else ("flight", "first"),
        max_speed=("speed", "max") if "speed" in df.columns else ("flight", "first"),
        min_altitude=("altitude", "min") if "altitude" in df.columns else ("flight", "first"),
        mean_altitude=("altitude", "mean") if "altitude" in df.columns else ("flight", "first"),
        max_altitude=("altitude", "max") if "altitude" in df.columns else ("flight", "first"),
        altitude_std=("altitude", "std") if "altitude" in df.columns else ("flight", "first")
    ).reset_index()
    
    # Merge missing data
    missing_report = analyze_missing_by_flight(df)
    if not missing_report.empty:
        grouped = pd.merge(grouped, missing_report, on="flight", how="left")
        grouped["missing_cells"] = grouped["missing_cells"].fillna(0)
        grouped["missing_rows"] = grouped["missing_rows"].fillna(0)
        grouped["missing_pct"] = grouped["missing_pct"].fillna(0)
    else:
        grouped["missing_cells"] = 0
        grouped["missing_rows"] = 0
        grouped["missing_pct"] = 0
        
    # Classifications
    grouped["classification"] = "Normal"
    grouped["candidate_reason"] = ""
    grouped["researcher_decision"] = ""
    grouped["decision_reason"] = ""
    
    # Calculate movement metrics to help verify Hover candidates
    if all(c in df.columns for c in ["position_x", "position_y", "velocity_x", "velocity_y"]):
        def calculate_movement_metrics(g):
            lons = g["position_x"].values
            lats = g["position_y"].values
            
            # Displacement from start
            lon0, lat0 = lons[0], lats[0]
            displacements = haversine_distance(lon0, lat0, lons, lats)
            max_disp = displacements.max()
            
            # Path length
            if len(lons) > 1:
                path_len = haversine_distance(lons[:-1], lats[:-1], lons[1:], lats[1:]).sum()
            else:
                path_len = 0.0
                
            # Mean horizontal velocity
            horiz_vel = np.sqrt(g["velocity_x"]**2 + g["velocity_y"]**2)
            mean_vel = horiz_vel.mean()
            
            return pd.Series({
                "max_horizontal_displacement_m": max_disp,
                "horizontal_path_length_m": path_len,
                "mean_horizontal_velocity_ms": mean_vel
            })
            
        metrics_df = df.groupby("flight").apply(calculate_movement_metrics, include_groups=False).reset_index()
        grouped = pd.merge(grouped, metrics_df, on="flight", how="left")
    else:
        grouped["max_horizontal_displacement_m"] = 0.0
        grouped["horizontal_path_length_m"] = 0.0
        grouped["mean_horizontal_velocity_ms"] = 0.0
        
    # Static detection
    if "altitude" in df.columns and "speed" in df.columns:
        static_mask = (grouped["max_altitude"] <= static_rules["max_altitude_m"]) & (grouped["max_speed"] <= static_rules["max_speed_ms"])
        grouped.loc[static_mask, "classification"] = "Static candidate"
        grouped.loc[static_mask, "candidate_reason"] = "Low altitude and speed"
        
    # Hover detection
    if "route" in df.columns:
        hover_mask = grouped["route"].isin(hover_rules["routes"]) & \
                     (grouped["max_horizontal_displacement_m"] <= hover_rules.get("max_horizontal_displacement_m", 20.0)) & \
                     (grouped["mean_horizontal_velocity_ms"] <= hover_rules.get("max_mean_horizontal_velocity_ms", 0.5)) & \
                     (grouped["horizontal_path_length_m"] <= hover_rules.get("max_horizontal_path_length_m", 50.0)) & \
                     (grouped["mean_altitude"] <= hover_rules.get("max_mean_altitude_m", 5.0))
        # Don't overwrite Static if already classified
        hover_mask = hover_mask & (grouped["classification"] == "Normal")
        grouped.loc[hover_mask, "classification"] = "Hover candidate"
        grouped.loc[hover_mask, "candidate_reason"] = "Route H; configured low altitude; very low horizontal movement; low actual horizontal velocity; short flight path"
        
    grouped.to_csv(OUT_RES_QUALITY / "flight_quality_report.csv", index=False)
    logger.info(f"Generated flight quality report for {len(grouped)} flights.")


def check_physical_validity(val, col, data_dict):
    """Check if value violates physical boundaries defined in data_dictionary."""
    if col not in data_dict:
        return "Unknown"
        
    expected_range = data_dict[col].get("expected_range", [None, None])
    min_val, max_val = expected_range
    
    if (min_val is not None and val < min_val) or (max_val is not None and val > max_val):
        return "Impossible"
    return "Valid"

def detect_outliers(df, data_dict):
    """Detect outliers using multiple methods (Z-score, IQR) and classify them."""
    logger.info("Detecting outliers...")
    
    outlier_candidates = []
    
    num_cols = [c for c in ALL_FEATURES if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]
    
    for col in num_cols:
        series = df[col].dropna()
        if series.empty:
            continue
            
        # Z-score method
        z_scores = np.abs(stats.zscore(series))
        z_outliers = series[z_scores > 3].index
        
        # IQR method
        Q1 = series.quantile(0.25)
        Q3 = series.quantile(0.75)
        IQR = Q3 - Q1
        iqr_outliers = series[(series < (Q1 - 1.5 * IQR)) | (series > (Q3 + 1.5 * IQR))].index
        
        # Combine
        for idx in set(list(z_outliers) + list(iqr_outliers)):
            val = df.loc[idx, col]
            detector_count = 0
            if idx in z_outliers: detector_count += 1
            if idx in iqr_outliers: detector_count += 1
            
            flight = df.loc[idx, "flight"] if "flight" in df.columns else "Unknown"
            route = df.loc[idx, "route"] if "route" in df.columns else "Unknown"
            speed = df.loc[idx, "speed"] if "speed" in df.columns else "Unknown"
            altitude = df.loc[idx, "altitude"] if "altitude" in df.columns else "Unknown"
            
            physical_validity = check_physical_validity(val, col, data_dict)
            
            classification = "Suspicious"
            if physical_validity == "Impossible":
                classification = "Impossible"
            elif detector_count < 2 and physical_validity == "Valid":
                classification = "Extreme but valid"
                
            outlier_candidates.append({
                "flight": flight,
                "route": route,
                "speed": speed,
                "altitude": altitude,
                "row_index": idx,
                "feature": col,
                "value": val,
                "detector_count": detector_count,
                "physical_validity": physical_validity,
                "auto_classification": classification,
                "researcher_decision": "", # To be filled by human
                "decision_reason": ""      # To be filled by human
            })
            
    if outlier_candidates:
        outliers_df = pd.DataFrame(outlier_candidates)
        outliers_df.to_csv(OUT_RES_QUALITY / "outlier_candidates.csv", index=False)
        logger.warning(f"Detected {len(outliers_df)} outlier candidates.")
    else:
        logger.info("No outliers detected.")

def detect_missing(df):
    """Detect and profile missing values at the column level."""
    logger.info("Detecting missing values by column...")
    missing = df.isnull().sum()
    missing = missing[missing > 0]
    
    if not missing.empty:
        missing_df = pd.DataFrame({
            "feature": missing.index,
            "missing_count": missing.values,
            "missing_pct": (missing.values / len(df)) * 100
        })
        missing_df.to_csv(OUT_RES_QUALITY / "missing_values_report.csv", index=False)
        logger.warning(f"Detected missing values in {len(missing_df)} columns.")
    else:
        logger.info("No missing values detected.")

def main():
    logger.info("="*60)
    logger.info("START: Detect Data Issues")
    
    input_path = DATA_INTERMEDIATE_DIR / "flight_with_weather.csv"
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return
        
    df = pd.read_csv(input_path)
    data_dict = load_data_dictionary()
    
    detect_missing(df)
    analyze_duplicates(df)
    build_flight_quality_report(df, data_dict)
    detect_outliers(df, data_dict)
    
    logger.info("Data issue detection completed. Waiting for HUMAN GATE 1.")
    logger.info("Please review output/results/data_quality/ and update decisions/data_quality_decision.yaml")
    logger.info("END: Detect Data Issues")
    logger.info("="*60)

if __name__ == "__main__":
    main()
