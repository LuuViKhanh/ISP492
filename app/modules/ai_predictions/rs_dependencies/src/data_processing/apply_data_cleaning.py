"""
Phase 2 - Step 4: Apply Data Cleaning
Reads decisions/data_quality_decision.yaml and applies the requested operations.
Generates cleaned_flight_data.csv and cleaning_decision_log.csv.
"""

import pandas as pd
import numpy as np
import yaml
from pathlib import Path

from config.paths import DATA_INTERMEDIATE_DIR, DATA_PROCESSED_DIR, OUT_RES_QUALITY, DECISIONS_DIR
from src.utils.logging_utils import get_logger

logger = get_logger("ApplyDataCleaning")

def load_decisions():
    decision_path = DECISIONS_DIR / "data_quality_decision.yaml"
    if not decision_path.exists():
        logger.error(f"Decision file not found at {decision_path}")
        return None
        
    with open(decision_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def verify_decisions(df, decisions):
    """Verify that flights requested for removal actually exist."""
    flight_handling = decisions.get("flight_handling", {})
    removals = flight_handling.get("remove", [])
    if not removals:
        return True
        
    requested_flights = [item["flight"] for item in removals if "flight" in item]
    actual_flights = set(df["flight"].unique())
    
    missing_flights = [f for f in requested_flights if f not in actual_flights]
    
    if missing_flights:
        logger.error(f"Decision file requests removal of flights that don't exist: {missing_flights}")
        return False
        
    return True

def remove_flights(df, flights, reason, decision_log):
    """Remove specific flights from the dataset."""
    if not flights:
        return df, 0, 0
        
    flights = list(set(flights))
    before_rows = len(df)
    before_flights = df["flight"].nunique()
    
    mask = df["flight"].isin(flights)
    removed_rows = int(mask.sum())
    
    df = df[~mask].copy()
    
    decision_log.append({
        "step": "flight_removal",
        "action": "remove_flight",
        "reason": reason,
        "rows_removed": removed_rows,
        "flights_removed": before_flights - df["flight"].nunique()
    })
    
    return df, removed_rows, before_flights - df["flight"].nunique()

def remove_exact_duplicates(df, decision_log):
    """Remove exact duplicate rows."""
    before = len(df)
    duplicate_count = int(df.duplicated().sum())
    
    if duplicate_count > 0:
        df = df.drop_duplicates().copy()
        
        decision_log.append({
            "step": "duplicate_handling",
            "action": "remove_exact_duplicates",
            "reason": "Duplicate telemetry",
            "rows_removed": duplicate_count,
            "flights_removed": 0
        })
        
    return df, duplicate_count

def handle_missing(df, missing_policy, decision_log):
    """Handle remaining missing values."""
    logger.info("Applying missing value policies...")
    
    remaining = df.isnull().sum().sum()
    if remaining == 0:
        decision_log.append({
            "step": "missing_handling",
            "action": "none_remaining",
            "reason": "No missing data left",
            "rows_removed": 0,
            "flights_removed": 0
        })
        return df
        
    action = missing_policy.get("remaining_missing", {}).get("action")
    if action == "error":
        raise ValueError(f"Found {remaining} unresolved missing values and policy is set to ERROR.")
        
    # Support imputation if added later
    logger.warning(f"Unresolved missing values exist, but action is {action}")
    return df

def handle_outliers(df, outlier_policy, decision_log):
    """Apply outlier policies."""
    logger.info("Applying outlier policies...")
    
    # Just logging for now as we don't automatically cap or remove based on the new logic.
    decision_log.append({
        "step": "outlier_handling",
        "action": "keep",
        "reason": "Keep physically valid",
        "rows_removed": 0,
        "flights_removed": 0
    })
    
    return df

def generate_markdown_report(decisions, rows_initial, flights_initial, df_final, decision_log):
    """Auto-generate DATA_QUALITY_SUMMARY.md."""
    report_path = OUT_RES_QUALITY / "DATA_QUALITY_SUMMARY.md"
    
    missing_cells = df_final.isnull().sum().sum()
    exact_duplicates = df_final.duplicated().sum()
    
    expected = decisions.get("expected_final_dataset", {})
    expected_rows = expected.get("rows", "N/A")
    expected_flights = expected.get("flights", "N/A")
    
    regression_status = "PASS"
    if expected_rows != "N/A" and len(df_final) != expected_rows:
        regression_status = "REVIEW REQUIRED"
    
    content = f"""# Data Quality Summary

## 1. Dataset
- Input: `flight_with_weather.csv`
- Initial rows: {rows_initial:,}
- Initial flights: {flights_initial}
- Initial columns: {df_final.shape[1]}

## 2. Cleaning Log
"""
    
    for log in decision_log:
        content += f"- **{log['step']}**: {log['action']} ({log['reason']}) -> Removed {log['rows_removed']} rows, {log['flights_removed']} flights.\n"
        
    content += f"""
## 3. Final Dataset
- Rows: {len(df_final):,}
- Flights: {df_final["flight"].nunique()}
- Columns: {df_final.shape[1]}
- Missing Cells: {missing_cells}
- Exact Duplicates: {exact_duplicates}

## 4. Regression Comparison
- Expected Rows: {expected_rows}
- Actual Rows: {len(df_final)}
- Expected Flights: {expected_flights}
- Actual Flights: {df_final["flight"].nunique()}
- **Status: {regression_status}**
"""
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)
        
    logger.info(f"Generated {report_path}")
    return regression_status

def validate_cleaned_data(df, decisions, regression_status):
    """Validation checks on the cleaned telemetry data."""
    logger.info("Validating cleaned dataset...")
    
    if regression_status == "REVIEW REQUIRED":
        enforce = decisions.get("validation", {}).get("enforce_expected_shape", True)
        if enforce:
            logger.error("Regression shape check failed and enforce_expected_shape is true.")
            return False
        else:
            logger.warning("Regression shape check failed but enforce_expected_shape is false. Proceeding.")
            
    if df.isnull().sum().sum() > 0:
        logger.error("Unresolved missing data remains in dataset.")
        return False
        
    return True

def main():
    logger.info("="*60)
    logger.info("START: Apply Data Cleaning")
    
    decisions = load_decisions()
    if not decisions:
        return
        
    if not decisions.get("approved", False):
        logger.error("Data quality decisions have NOT been approved! Pipeline halted.")
        return
        
    logger.info(f"Using decisions approved by: {decisions.get('approved_by')} on {decisions.get('date')}")
    
    input_path = DATA_INTERMEDIATE_DIR / "flight_with_weather.csv"
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return
        
    df = pd.read_csv(input_path)
    rows_initial = len(df)
    flights_initial = df["flight"].nunique() if "flight" in df.columns else 0
    decision_log = []
    
    if not verify_decisions(df, decisions):
        return
    
    # 1. Remove non-representative flights
    flight_handling = decisions.get("flight_handling", {})
    removals = flight_handling.get("remove", [])
    
    # Group flights by reason for logging
    flights_by_reason = {}
    if removals:
        for item in removals:
            reason = item.get("reason", "Unknown reason")
            flight = item.get("flight")
            if flight is not None:
                if reason not in flights_by_reason:
                    flights_by_reason[reason] = []
                flights_by_reason[reason].append(flight)
            
    for reason, flights in flights_by_reason.items():
        df, r_rows, r_flights = remove_flights(df, flights, reason, decision_log)
        
    # 2. Remove exact duplicates
    dup_policy = decisions.get("duplicate_handling", {})
    if dup_policy.get("exact_duplicates", {}).get("action") == "remove":
        df, r_dups = remove_exact_duplicates(df, decision_log)
        
    # 3. Handle remaining missing
    missing_policy = decisions.get("missing_handling", {})
    df = handle_missing(df, missing_policy, decision_log)
    
    # 4. Handle approved outliers
    outlier_policy = decisions.get("outlier_handling", {})
    df = handle_outliers(df, outlier_policy, decision_log)
    
    # Validation & Markdown Generation
    regression_status = generate_markdown_report(decisions, rows_initial, flights_initial, df, decision_log)
    if not validate_cleaned_data(df, decisions, regression_status):
        return
    
    # Save results
    out_path = DATA_PROCESSED_DIR / "cleaned_flight_data.csv"
    df.to_csv(out_path, index=False)
    
    log_df = pd.DataFrame(decision_log)
    log_df.to_csv(OUT_RES_QUALITY / "cleaning_summary.csv", index=False)
    
    logger.info(f"Cleaned dataset saved: {len(df):,} rows (Removed {rows_initial - len(df)}).")
    logger.info("END: Apply Data Cleaning")
    logger.info("="*60)

if __name__ == "__main__":
    main()
