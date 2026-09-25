"""
Phase 2 - Step 6: Validate Processed Features
Final validation before modeling on flight-level data.
Checks for unexpected missing values (based on policy), infinite values.
Leakage Audit Level 2 (Target, Target-derived, Temporal, Identifier).
Saves feature Hash to data_manifest.json.
"""

import pandas as pd
import numpy as np
import yaml
from pathlib import Path

from config.paths import OUT_DATA_FEATURES, OUT_RES_QUALITY, OUT_METADATA, DECISIONS_DIR
from config.dataset_config import ALL_FEATURES, TARGET
from src.utils.logging_utils import get_logger
from src.utils.reproducibility import compute_dataframe_hash, save_manifest

logger = get_logger("ValidateProcessedData")

def load_decisions():
    decision_path = DECISIONS_DIR / "data_quality_decision.yaml"
    if not decision_path.exists():
        logger.warning(f"Decision file not found at {decision_path}. Leakage rules will fallback to defaults.")
        return {}
    with open(decision_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def run_leakage_audit_level_2(df, decisions):
    """Check for feature leakage (Target, Derived, Temporal, Identifier)."""
    logger.info("Running Leakage Audit Level 2 (Feature-level)...")
    issues = []
    
    leakage_rules = decisions.get("leakage_audit", {})
    forbidden = leakage_rules.get("forbidden_columns", [TARGET])
    target_derived = leakage_rules.get("target_derived_columns", [])
    allowed = leakage_rules.get("allowed_operational_features", [])
    
    # Check if target exists
    if TARGET not in df.columns:
        issues.append(f"ERROR: Target '{TARGET}' is missing from the dataset!")
        
    features = [c for c in df.columns if c != TARGET and c not in ["flight", "route", "date"]]
    
    for feat in features:
        # 1. Target Leakage
        if feat in forbidden:
            issues.append(f"ERROR: Target leakage detected. Forbidden column '{feat}' is present as a feature.")
            
        # 2. Target-Derived Leakage
        if feat in target_derived:
            issues.append(f"ERROR: Target-derived leakage detected. Column '{feat}' is present as a feature.")
            
        # 3. Temporal Leakage (Heuristics)
        temporal_keywords = ["total_", "final_", "max_", "min_"]
        if any(kw in feat.lower() for kw in temporal_keywords) and feat not in allowed:
            issues.append(f"WARNING: Temporal leakage suspected. Feature '{feat}' aggregates future/complete flight data.")
            
        # 4. Correlation Leakage
        if TARGET in df.columns and pd.api.types.is_numeric_dtype(df[feat]) and pd.api.types.is_numeric_dtype(df[TARGET]):
            # only compute if std > 0
            if df[feat].std() > 0:
                corr = df[TARGET].corr(df[feat])
                if abs(corr) > 0.99:
                    issues.append(f"WARNING: Feature '{feat}' is perfectly correlated with target (r={corr:.2f}). Possible leakage.")
                    
    # Identifier Leakage
    for ident in ["flight", "id", "index"]:
        if ident in features:
            issues.append(f"ERROR: Identifier leakage. '{ident}' should not be used as a predictive feature.")
            
    if not issues:
        logger.info("Leakage Audit Level 2: PASS")
    else:
        for issue in issues:
            if "ERROR" in issue:
                logger.error(issue)
            else:
                logger.warning(issue)
                
    return len([i for i in issues if "ERROR" in i]) == 0

def validate_processed_data(df, decisions):
    """Check for NaNs, Infs, and basic structure."""
    logger.info("Validating processed data structure...")
    
    # 1. Missing values
    missing = df.isnull().sum()
    missing = missing[missing > 0]
    if not missing.empty:
        logger.error(f"Found unexpected missing values after feature engineering: \n{missing}")
        return False
        
    # 2. Infinite values
    num_df = df.select_dtypes(include=[np.number])
    infs = np.isinf(num_df).sum()
    infs = infs[infs > 0]
    if not infs.empty:
        logger.error(f"Found infinite values: \n{infs}")
        return False
        
    # 3. Target valid
    if TARGET in df.columns:
        if pd.api.types.is_numeric_dtype(df[TARGET]) and df[TARGET].min() <= 0:
            logger.error(f"Target '{TARGET}' has non-positive values (min={df[TARGET].min()})")
            return False
            
    # Ensure removed flights are absent
    removed_flights = [
        item["flight"]
        for item in decisions.get("flight_handling", {}).get("remove", [])
    ]
    if removed_flights and "flight" in df.columns:
        remaining = df[df["flight"].isin(removed_flights)]
        if not remaining.empty:
            logger.error(
                f"Previously removed flights still present: "
                f"{remaining['flight'].unique().tolist()}"
            )
            return False
            
    # 4. Flight count check
    # Flight-level features should have 1 row per flight, meaning rows == flights
    expected = decisions.get("expected_final_dataset", {})
    expected_flights = expected.get("flights", "N/A")
    
    if expected_flights != "N/A":
        if df.shape[0] != expected_flights:
            logger.error(f"Flight count mismatch! Expected {expected_flights} flights, but got {df.shape[0]} rows (flights) in feature dataset.")
            enforce = decisions.get("validation", {}).get("enforce_expected_shape", True)
            if enforce:
                return False
            else:
                logger.warning("enforce_expected_shape is false, proceeding despite mismatch.")
        else:
            logger.info(f"Flight count validated: {df.shape[0]}")
            
    return True

def main():
    logger.info("="*60)
    logger.info("START: Validate Processed Features (Step 6)")
    
    input_path = OUT_DATA_FEATURES / "flight_level_features.csv"
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return
        
    df = pd.read_csv(input_path)
    logger.info(f"Loaded processed dataset: {df.shape[0]:,} rows, {df.shape[1]} columns")
    
    decisions = load_decisions()
    
    # Validation
    is_valid = validate_processed_data(df, decisions)
    leakage_pass = run_leakage_audit_level_2(df, decisions)
    
    if not is_valid or not leakage_pass:
        logger.error("Processed features validation failed. Do not proceed to modeling.")
        logger.info("FINAL STATUS: FAIL")
        return
        
    # Generate Hash
    features_hash = compute_dataframe_hash(df)
    logger.info(f"Features SHA256 Hash: {features_hash}")
    
    # Update Data Manifest
    manifest_path = OUT_METADATA / "data_manifest.json"
    if manifest_path.exists():
        import json
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    else:
        manifest = {}
        
    manifest["features_name"] = "flight_level_features.csv"
    manifest["features_rows"] = df.shape[0]
    manifest["features_columns"] = df.shape[1]
    manifest["features_sha256"] = features_hash
    
    save_manifest(manifest, manifest_path)
    
    logger.info("Processed Features Validation completed successfully.")
    logger.info("FINAL STATUS: PASS")
    logger.info("END: Validate Processed Features (Step 6)")
    logger.info("="*60)

if __name__ == "__main__":
    main()
