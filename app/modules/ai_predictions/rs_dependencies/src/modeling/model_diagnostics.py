"""
Phase 4 - Step 3: Model Diagnostics (OOF)
Analyzes Out-Of-Fold residuals to evaluate model generalization.
Focuses on the top performing models.
Identifies bias in specific subgroups (e.g., specific routes, high temperatures).
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

from config.paths import OUT_RES_EVAL, OUT_FIG_DIAGNOSTICS, OUT_DATA_SPLITS
from config.dataset_config import TARGET
from src.utils.logging_utils import get_logger

logger = get_logger("ModelDiagnostics")

def analyze_residuals(oof_df, top_models):
    """Plot residuals for top models."""
    logger.info("Plotting residuals...")
    
    y_true = oof_df[TARGET]
    
    for model in top_models:
        pred_col = f"{model}_oof_pred"
        if pred_col not in oof_df.columns:
            continue
            
        y_pred = oof_df[pred_col]
        residuals = y_pred - y_true
        
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        
        # 1. Residuals vs True Values
        sns.scatterplot(x=y_true, y=residuals, ax=axes[0], alpha=0.5)
        axes[0].axhline(0, color='red', linestyle='--')
        axes[0].set_title(f"{model} - Residuals vs True")
        axes[0].set_xlabel("True Target")
        axes[0].set_ylabel("Residual (Predicted - True)")
        
        # 2. Residual Distribution
        sns.histplot(residuals, kde=True, ax=axes[1])
        axes[1].axvline(0, color='red', linestyle='--')
        axes[1].set_title(f"{model} - Residual Distribution")
        
        plt.tight_layout()
        plt.savefig(OUT_FIG_DIAGNOSTICS / f"{model.lower().replace(' ', '_')}_residuals.png")
        plt.close()

def main():
    logger.info("="*60)
    logger.info("START: Model Diagnostics")
    
    evidence_path = OUT_RES_EVAL / "model_evidence_table.csv"
    oof_path = OUT_RES_EVAL / "oof_predictions.csv"
    train_path = OUT_DATA_SPLITS / "train.csv"
    
    if not evidence_path.exists() or not oof_path.exists() or not train_path.exists():
        logger.error("Required files not found. Run evaluate_cv first.")
        return
        
    evidence_df = pd.read_csv(evidence_path)
    oof_df = pd.read_csv(oof_path)
    train_df = pd.read_csv(train_path)
    
    # Merge train data features with OOF predictions for subgroup analysis
    # Assuming order is preserved, or we can use index
    for col in train_df.columns:
        if col not in oof_df.columns:
            oof_df[col] = train_df[col]
            
    # Select top 3 models
    top_models = evidence_df.head(3)["Model"].tolist()
    logger.info(f"Analyzing diagnostics for top models: {top_models}")
    
    analyze_residuals(oof_df, top_models)
    
    # Additional subgroup analysis could be added here (e.g., residuals by Route if 'route' is in OOF)
    if "route" in oof_df.columns:
        for model in top_models:
            pred_col = f"{model}_oof_pred"
            if pred_col not in oof_df.columns: continue
            
            residuals = oof_df[pred_col] - oof_df[TARGET]
            
            plt.figure(figsize=(10, 5))
            sns.boxplot(x=oof_df["route"], y=residuals)
            plt.axhline(0, color='red', linestyle='--')
            plt.title(f"{model} - Residuals by Route")
            plt.xticks(rotation=45)
            plt.tight_layout()
            plt.savefig(OUT_FIG_DIAGNOSTICS / f"{model.lower().replace(' ', '_')}_residuals_by_route.png")
            plt.close()
    
    logger.info("Model Diagnostics completed. Generating evidence for HUMAN GATE 3.")
    logger.info("END: Model Diagnostics")
    logger.info("="*60)

if __name__ == "__main__":
    main()
