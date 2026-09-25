"""
Phase 3 - Step 2: Train/Test Split
Reads decisions/split_decision.yaml to determine the split protocol.
Splits the dataset and performs Leakage Audit Level 3 (No Overlap).
Generates split_manifest.json with the split configuration and hash.
DOES NOT perform scaling (handled in modeling Pipeline).
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.model_selection import GroupShuffleSplit, train_test_split, TimeSeriesSplit
import yaml
from pathlib import Path

from config.paths import OUT_DATA_FEATURES, OUT_DATA_SPLITS, OUT_FIG_SPLITS, DECISIONS_DIR, OUT_METADATA
from config.dataset_config import TARGET, RANDOM_STATE
from src.utils.logging_utils import get_logger
from src.utils.reproducibility import save_manifest

logger = get_logger("TrainTestSplit")

def load_split_decision():
    decision_path = DECISIONS_DIR / "split_decision.yaml"
    if not decision_path.exists():
        logger.error(f"Decision file not found at {decision_path}")
        return None
        
    with open(decision_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def run_leakage_audit_level_3(train_df, test_df, protocol, group_column):
    """Check for data leakage between train and test sets."""
    logger.info("Running Leakage Audit Level 3 (Train/Test Overlap)...")
    issues = []
    
    # 1. Row overlap (Index/Flight overlap)
    if "flight" in train_df.columns:
        train_flights = set(train_df["flight"])
        test_flights = set(test_df["flight"])
        overlap = train_flights & test_flights
        if len(overlap) > 0:
            issues.append(f"ERROR: Found {len(overlap)} flights present in BOTH train and test sets!")
            
    # 2. Group overlap (if applicable)
    if protocol == "group" and group_column in train_df.columns:
        train_groups = set(train_df[group_column])
        test_groups = set(test_df[group_column])
        overlap = train_groups & test_groups
        if len(overlap) > 0:
            issues.append(f"ERROR: Found {len(overlap)} groups ({group_column}) present in BOTH train and test sets! (Group leakage)")
            
    # 3. Temporal leakage (if applicable)
    if protocol == "time" and "date" in train_df.columns:
        train_max_date = train_df["date"].max()
        test_min_date = test_df["date"].min()
        if train_max_date > test_min_date:
            issues.append(f"ERROR: Temporal leakage! Train set contains date ({train_max_date}) AFTER test set start date ({test_min_date}).")
            
    if not issues:
        logger.info("Leakage Audit Level 3: PASS")
    else:
        for issue in issues:
            if "ERROR" in issue:
                logger.error(issue)
            else:
                logger.warning(issue)
                
    return len([i for i in issues if "ERROR" in i]) == 0

def plot_split_distribution(train_df, test_df):
    """Plot target distribution for Train and Test sets."""
    logger.info("Generating split distribution plot...")
    
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # KDE Plot
    sns.kdeplot(train_df[TARGET], ax=axes[0], label='Train', color='#2196F3', fill=True, alpha=0.3)
    sns.kdeplot(test_df[TARGET], ax=axes[0], label='Test', color='#FF9800', fill=True, alpha=0.3)
    axes[0].set_title("Target Distribution (Train vs Test)", fontweight='bold')
    axes[0].set_xlabel(TARGET)
    axes[0].legend()

    # Box Plot
    combined_df = pd.concat([
        train_df[[TARGET]].assign(Set='Train'),
        test_df[[TARGET]].assign(Set='Test')
    ])
    sns.boxplot(x='Set', y=TARGET, data=combined_df, ax=axes[1], palette=['#2196F3', '#FF9800'])
    axes[1].set_title("Target Box Plot", fontweight='bold')
    axes[1].set_ylabel(TARGET)

    plt.tight_layout()
    dist_fig_path = OUT_FIG_SPLITS / "target_distribution_split.png"
    plt.savefig(dist_fig_path, dpi=150, bbox_inches="tight")
    plt.close()

def perform_split(df, decision):
    protocol = decision.get("protocol", "random")
    test_size = decision.get("test_size", 0.25)
    
    logger.info(f"Using protocol: {protocol}")
    
    if protocol == "group":
        group_col = decision.get("group_column")
        if group_col not in df.columns:
            logger.error(f"Group column '{group_col}' not found in dataset!")
            return None, None
            
        logger.info(f"Grouping by: {group_col}")
        gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=RANDOM_STATE)
        train_idx, test_idx = next(gss.split(df, groups=df[group_col]))
        
        train_df = df.iloc[train_idx].copy()
        test_df = df.iloc[test_idx].copy()
        
    elif protocol == "time":
        time_col = decision.get("group_column", "date") # usually time column
        df = df.sort_values(time_col).reset_index(drop=True)
        split_idx = int(len(df) * (1 - test_size))
        train_df = df.iloc[:split_idx].copy()
        test_df = df.iloc[split_idx:].copy()
        
    else: # random
        train_df, test_df = train_test_split(df, test_size=test_size, random_state=RANDOM_STATE)
        
    return train_df, test_df

def main():
    logger.info("="*60)
    logger.info("START: Train/Test Split")
    
    decision = load_split_decision()
    if not decision:
        return
        
    if not decision.get("approved", False):
        logger.error("Split decisions have NOT been approved! Pipeline halted.")
        return
        
    logger.info(f"Using decisions approved by: {decision.get('approved_by')} on {decision.get('date')}")
    
    input_path = OUT_DATA_FEATURES / "flight_level_features.csv"
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return
        
    df = pd.read_csv(input_path)
    
    # Split
    train_df, test_df = perform_split(df, decision)
    if train_df is None:
        return
        
    logger.info(f"Split completed. Train: {len(train_df)} ({len(train_df)/len(df):.0%}), Test: {len(test_df)} ({len(test_df)/len(df):.0%})")
    
    # Leakage Audit
    protocol = decision.get("protocol")
    group_col = decision.get("group_column")
    leakage_pass = run_leakage_audit_level_3(train_df, test_df, protocol, group_col)
    
    if not leakage_pass:
        logger.error("Data failed Leakage Audit Level 3. Please revise split protocol.")
        return
        
    # Plot & Save
    plot_split_distribution(train_df, test_df)
    
    train_df.to_csv(OUT_DATA_SPLITS / "train.csv", index=False)
    test_df.to_csv(OUT_DATA_SPLITS / "test.csv", index=False)
    
    # Save Manifest
    manifest = {
        "protocol": protocol,
        "group_column": group_col,
        "test_size": decision.get("test_size"),
        "random_state": RANDOM_STATE,
        "decision_reason": decision.get("decision_reason"),
        "train_rows": len(train_df),
        "test_rows": len(test_df)
    }
    save_manifest(manifest, OUT_METADATA / "split_manifest.json")
    
    logger.info("Train/Test sets saved successfully.")
    logger.info("END: Train/Test Split")
    logger.info("="*60)

if __name__ == "__main__":
    main()
