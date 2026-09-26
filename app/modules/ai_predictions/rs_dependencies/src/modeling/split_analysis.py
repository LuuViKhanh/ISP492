"""
Phase 3 - Step 1: Split Analysis
Analyzes data structure to answer: "What constitutes an independent observation?"
Evaluates candidate split protocols (Random vs. GroupShuffleSplit vs. Time-based).
Outputs evidence for HUMAN GATE 2.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

from config.paths import OUT_DATA_FEATURES, OUT_RES_QUALITY, OUT_FIG_SPLITS
from config.dataset_config import TARGET
from src.utils.logging_utils import get_logger

logger = get_logger("SplitAnalysis")

def analyze_temporal_leakage(df):
    """Check if target varies significantly over time (risk of temporal leakage)."""
    if "date" not in df.columns:
        return
        
    df_sorted = df.sort_values("date")
    
    plt.figure(figsize=(12, 5))
    sns.lineplot(data=df_sorted, x="date", y=TARGET, errorbar="sd")
    plt.title("Target Variation Over Time")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(OUT_FIG_SPLITS / "target_over_time.png")
    plt.close()
    
    logger.info("Generated temporal leakage plot.")

def analyze_group_structure(df):
    """Evaluate independence of observations based on grouping variables."""
    group_vars = [c for c in ["date", "route"] if c in df.columns]
    
    report = []
    
    for var in group_vars:
        groups = df[var].nunique()
        obs_per_group = df.groupby(var).size()
        
        report.append({
            "Grouping Variable": var,
            "Total Groups": groups,
            "Min Obs per Group": obs_per_group.min(),
            "Max Obs per Group": obs_per_group.max(),
            "Mean Obs per Group": obs_per_group.mean()
        })
        
    if report:
        report_df = pd.DataFrame(report)
        report_df.to_csv(OUT_RES_QUALITY / "group_structure_analysis.csv", index=False)
        logger.info(f"Group structure analysis:\n{report_df.to_string()}")

def main():
    logger.info("="*60)
    logger.info("START: Split Analysis")
    
    input_path = OUT_DATA_FEATURES / "flight_level_features.csv"
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return
        
    df = pd.read_csv(input_path)
    
    # Analyze
    analyze_temporal_leakage(df)
    analyze_group_structure(df)
    
    logger.info("Split analysis completed.")
    logger.info("Please review output/results/data_quality/group_structure_analysis.csv and output/figures/splits/")
    logger.info("Then update decisions/split_decision.yaml")
    logger.info("END: Split Analysis")
    logger.info("="*60)

if __name__ == "__main__":
    main()
