"""
Phase 4 - Step 2: Evaluate Cross-Validation Results
Uses Out-Of-Fold (OOF) predictions to compute a multi-criteria evidence table.
Calculates R2, MAE, RMSE, and MAPE across the entire OOF set.
"""

import pandas as pd
import numpy as np
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
from pathlib import Path

from config.paths import OUT_RES_EVAL
from config.dataset_config import TARGET
from src.utils.logging_utils import get_logger

logger = get_logger("EvaluateCV")

def mean_absolute_percentage_error(y_true, y_pred): 
    # Handle division by zero
    mask = y_true != 0
    return np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100

def main():
    logger.info("="*60)
    logger.info("START: Evaluate CV Results")
    
    tuning_path = OUT_RES_EVAL / "tuning_results.csv"
    oof_path = OUT_RES_EVAL / "oof_predictions.csv"
    
    if not tuning_path.exists() or not oof_path.exists():
        logger.error("Required files (tuning_results.csv or oof_predictions.csv) not found.")
        return
        
    tuning_df = pd.read_csv(tuning_path)
    oof_df = pd.read_csv(oof_path)
    
    if TARGET not in oof_df.columns:
        logger.error(f"Target '{TARGET}' not found in OOF predictions.")
        return
        
    y_true = oof_df[TARGET].values
    
    evidence = []
    
    for _, row in tuning_df.iterrows():
        model_name = row["model_name"]
        pred_col = f"{model_name}_oof_pred"
        
        if pred_col not in oof_df.columns:
            logger.warning(f"OOF predictions for {model_name} not found.")
            continue
            
        y_pred = oof_df[pred_col].values
        
        r2 = r2_score(y_true, y_pred)
        mae = mean_absolute_error(y_true, y_pred)
        rmse = np.sqrt(mean_squared_error(y_true, y_pred))
        mape = mean_absolute_percentage_error(y_true, y_pred)
        
        evidence.append({
            "Model": model_name,
            "OOF_R2": r2,
            "OOF_MAE": mae,
            "OOF_RMSE": rmse,
            "OOF_MAPE": mape,
            "Training_Time_s": row.get("training_time_s", 0)
        })
        
    evidence_df = pd.DataFrame(evidence).sort_values("OOF_R2", ascending=False)
    
    out_path = OUT_RES_EVAL / "model_evidence_table.csv"
    evidence_df.to_csv(out_path, index=False)
    
    logger.info(f"Model Evidence Table saved to {out_path}")
    logger.info(f"\n{evidence_df.to_string(index=False)}")
    
    logger.info("END: Evaluate CV Results")
    logger.info("="*60)

if __name__ == "__main__":
    main()
