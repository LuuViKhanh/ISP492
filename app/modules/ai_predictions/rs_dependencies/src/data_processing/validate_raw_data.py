"""
Phase 2 - Step 1: Validate Raw Data
Performs structural checks, missing analysis, numerical validity (from data_dictionary.yaml),
physical consistency, and Data Leakage Audit (Level 1).
Saves Data Hash to data_manifest.json.
Does NOT modify or clean the data.
"""

import pandas as pd
import numpy as np
import yaml
from pathlib import Path

from config.paths import DATA_INTERMEDIATE_DIR, OUT_RES_QUALITY, OUT_METADATA, OUT_FIG_QUALITY
from src.utils.logging_utils import get_logger
from src.utils.reproducibility import compute_dataframe_hash, save_manifest

logger = get_logger("ValidateRawData")

def load_data_dictionary():
    dict_path = Path("config/data_dictionary.yaml")
    if dict_path.exists():
        with open(dict_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

def run_leakage_audit_level_1(df):
    """
    Data-level leakage and structural integrity check.

    Notes:
    - 'flight' is a group identifier, NOT a unique row identifier.
    - Multiple observations per flight are expected.
    - (flight, time) is expected to identify a telemetry observation.
    """
    logger.info("Running Leakage Audit Level 1 (Data-level)...")
    issues = []

    # ==========================================================
    # 1. Validate flight IDs
    # ==========================================================
    if "flight" not in df.columns:
        issues.append("ERROR: Missing required column 'flight'.")

    else:
        # Missing flight IDs
        missing_flight = df["flight"].isna().sum()

        if missing_flight > 0:
            issues.append(
                f"ERROR: Found {missing_flight} rows with missing flight IDs."
            )

        # Flight ID must be numeric
        if not pd.api.types.is_numeric_dtype(df["flight"]):
            issues.append("ERROR: Flight IDs must be numeric.")

        # Number of flights
        flight_count = df["flight"].nunique()
        logger.info(f"Unique flights: {flight_count}")

    # ==========================================================
    # 2. Duplicate (flight, time) observations
    # ==========================================================
    duplicate_flight_time = 0

    if "flight" in df.columns and "time" in df.columns:

        duplicate_flight_time = df.duplicated(
            subset=["flight", "time"]
        ).sum()

        logger.info(
            f"Duplicate (flight, time) observations: "
            f"{duplicate_flight_time}"
        )

        if duplicate_flight_time > 0:
            issues.append(
                f"WARNING: Found {duplicate_flight_time} duplicate "
                f"(flight, time) observations."
            )

# ==========================================================
#  Check time monotonicity
# ==========================================================
    df_sorted = df.sort_values(["flight", "time"])

    time_diff = (
    df_sorted
    .groupby("flight")["time"]
    .diff()
)

    zero_time = int((time_diff == 0).sum())
    negative_time = int((time_diff < 0).sum())

    # Report
    if zero_time > 0:
        issues.append(
            f"ERROR: {zero_time} observations have zero time difference "
            f"within flights (non-increasing time).")

    if negative_time > 0:
        issues.append(
            f"ERROR: {negative_time} observations have negative time "
            f"difference (time decreasing within flights).")

    # ==========================================================
    # 3. Exact duplicate rows
    # ==========================================================
    exact_duplicates = df.duplicated().sum()

    logger.info(
        f"Exact duplicate rows: {exact_duplicates}"
    )

    if exact_duplicates > 0:
        issues.append(
            f"WARNING: Found {exact_duplicates} exact duplicate rows."
        )

    # ==========================================================
    # 4. Feature == Target check
    # ==========================================================
    if "energy_efficiency" in df.columns:

        target = df["energy_efficiency"]

        for col in df.columns:

            if col == "energy_efficiency":
                continue

            if df[col].equals(target):

                issues.append(
                    f"ERROR: Column '{col}' is exactly identical "
                    f"to target 'energy_efficiency'!"
                )

    # ==========================================================
    # 5. Final result
    # ==========================================================
    has_errors = any(
        issue.startswith("ERROR:")
        for issue in issues
    )

    if not issues:
        logger.info("Leakage Audit Level 1: PASS")

    else:
        for issue in issues:

            if issue.startswith("ERROR:"):
                logger.error(issue)

            else:
                logger.warning(issue)

    if has_errors:
        return False

    logger.info(
        "Leakage Audit Level 1: PASS "
        "(warnings do not block validation)"
    )

    return True

def validate_data(df, data_dict):
    """Validate against data dictionary rules."""
    logger.info("Validating against data dictionary...")
    report = []
    
    for col in df.columns:
        if col not in data_dict:
            continue
            
        rules = data_dict[col]
        expected_range = rules.get("expected_range", [None, None])
        
        # Check numerical range
        if rules.get("type") == "continuous":
            min_val, max_val = expected_range
            
            if min_val is not None:
                violations = df[df[col] < min_val]
                if not violations.empty:
                    report.append({
                        "Feature": col,
                        "Issue": f"Below minimum allowed ({min_val})",
                        "Count": len(violations)
                    })
                    
            if max_val is not None:
                violations = df[df[col] > max_val]
                if not violations.empty:
                    report.append({
                        "Feature": col,
                        "Issue": f"Above maximum allowed ({max_val})",
                        "Count": len(violations)
                    })
                    
        # Check missing policy
        missing_policy = rules.get("missing_policy", {})
        if not missing_policy.get("allowed", True):
            missing_count = df[col].isnull().sum()
            if missing_count > 0:
                report.append({
                    "Feature": col,
                    "Issue": "Missing values found but not allowed by policy",
                    "Count": missing_count
                })
                
    if report:
        report_df = pd.DataFrame(report)
        out_path = OUT_RES_QUALITY / "raw_validation_report.csv"
        report_df.to_csv(out_path, index=False)
        logger.warning(f"Validation found {len(report)} types of issues. Saved to {out_path}")
    else:
        logger.info("No validation issues found.")

def main():
    logger.info("="*60)
    logger.info("START: Validate Raw Data")
    
    input_path = DATA_INTERMEDIATE_DIR / "flight_with_weather.csv"
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return
        
    df = pd.read_csv(input_path)
    logger.info(f"Loaded dataset: {df.shape[0]:,} rows, {df.shape[1]} columns")
    
    # Generate Hash
    dataset_hash = compute_dataframe_hash(df)
    logger.info(f"Dataset SHA256 Hash: {dataset_hash}")
    
    # Save Data Manifest
    manifest = {
    "dataset_name": "flight_with_weather.csv",
    "dataset_version": "v1.0",

    "rows": len(df),
    "columns": len(df.columns),
    "flights": df["flight"].nunique(),

    "sha256": dataset_hash,

    "missing_cells": int(df.isna().sum().sum()),
    "missing_rows": int(df.isna().any(axis=1).sum()),

    "exact_duplicates": int(df.duplicated().sum()),

    "flight_time_duplicates": int(
        df.duplicated(["flight", "time"]).sum()
    )
}
    save_manifest(manifest, OUT_METADATA / "data_manifest.json")
    
    # Audit & Validate
    data_dict = load_data_dictionary()
    
    leakage_pass = run_leakage_audit_level_1(df)
    validate_data(df, data_dict)
    
    if not leakage_pass:
        logger.error("Data failed Leakage Audit Level 1. Please fix raw data before proceeding.")
    else:
        logger.info("Raw Data Validation completed successfully.")
    
    logger.info("END: Validate Raw Data")
    logger.info("="*60)

if __name__ == "__main__":
    main()
