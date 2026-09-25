"""
Phase 4 - Step 5: Evaluate Test Set (FINAL EVALUATION)
Hard gate: Checks if model_selection_decision.yaml is approved.
If approved, loads the selected model and evaluates it on the hold-out test set.
Calculates Adjusted R2, standard R2, RMSE, MAE.
Saves final_test_results.csv and LOCKS the test set by updating pipeline_status.json (test_locked: true).
"""

import pandas as pd
import numpy as np
import joblib
import yaml
import json
from pathlib import Path
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
import matplotlib.pyplot as plt
import seaborn as sns

from config.paths import (
    OUT_DATA_SPLITS, OUT_MODELS_TRAINED, OUT_BEST_MODEL, 
    OUT_RES_EVAL, OUT_FIG_EVAL, DECISIONS_DIR, OUT_METADATA
)
from config.dataset_config import ALL_FEATURES, TARGET
from src.utils.logging_utils import get_logger
from src.utils.pipeline_guard import get_pipeline_status, update_pipeline_status

logger = get_logger("EvaluateTest")

def load_model_decision():
    decision_path = DECISIONS_DIR / "model_selection_decision.yaml"
    if not decision_path.exists():
        logger.error(f"Decision file not found at {decision_path}")
        return None
        
    with open(decision_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def calculate_adjusted_r2(r2, n, p):
    """Calculate Adjusted R-squared."""
    # Adjusted R2 = 1 - (1 - R2) * (n - 1) / (n - p - 1)
    if n <= p + 1:
        return float('nan') # Cannot calculate if n <= p + 1
    return 1 - (1 - r2) * (n - 1) / (n - p - 1)

def run_leakage_audit_level_6():
    """Verify test lock hasn't been triggered already."""
    status = get_pipeline_status()
    if status.get("test_locked", False):
        logger.error("LEAKAGE AUDIT LEVEL 6 FAILED: Test set is LOCKED.")
        logger.error("You cannot re-evaluate the test set for this experiment.")
        logger.error("If you changed methodology or decisions, you must create a NEW experiment.")
        return False
    return True

def main():
    logger.info("="*60)
    logger.info("START: Final Test Evaluation")
    
    # Check Lock
    if not run_leakage_audit_level_6():
        return
        
    decision = load_model_decision()
    if not decision:
        return
        
    if not decision.get("approved", False):
        logger.error("Model selection decision has NOT been approved! Pipeline halted.")
        return
        
    selected_model_name = decision.get("selected_model")
    if not selected_model_name:
        logger.error("No selected_model specified in model_selection_decision.yaml.")
        return
        
    logger.info(f"Using decisions approved by: {decision.get('approved_by')} on {decision.get('date')}")
    logger.info(f"Selected Model: {selected_model_name}")
    
    # Load Model
    model_filename = f"{selected_model_name.lower().replace(' ', '_')}.pkl"
    model_path = OUT_MODELS_TRAINED / model_filename
    if not model_path.exists():
        logger.error(f"Trained model file not found: {model_path}")
        return
        
    pipeline = joblib.load(model_path)
    
    # Copy to best_model dir
    import shutil
    shutil.copy(model_path, OUT_BEST_MODEL / "final_model.pkl")
    with open(OUT_BEST_MODEL / "best_model_name.txt", "w") as f:
        f.write(selected_model_name)
    
    # Load Test Data
    test_path = OUT_DATA_SPLITS / "test.csv"
    if not test_path.exists():
        logger.error(f"Test data not found at {test_path}")
        return
        
    test_df = pd.read_csv(test_path)
    X_test = test_df[ALL_FEATURES].values
    y_test = test_df[TARGET].values
    
    # Evaluate
    logger.info("Evaluating on Test Set...")
    y_pred = pipeline.predict(X_test)
    
    r2 = r2_score(y_test, y_pred)
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    
    # Adjusted R2 calculation
    n = len(X_test)
    # Estimate effective degrees of freedom / parameters (p)
    # For linear models, p is number of features
    # For tree models, we approximate with number of features, though it's non-linear
    p = len(ALL_FEATURES) 
    adj_r2 = calculate_adjusted_r2(r2, n, p)
    
    results = {
        "Model": selected_model_name,
        "Test_Samples": n,
        "Test_R2": r2,
        "Test_Adjusted_R2": adj_r2,
        "Test_MAE": mae,
        "Test_RMSE": rmse
    }
    
    results_df = pd.DataFrame([results])
    results_path = OUT_RES_EVAL / "final_test_results.csv"
    results_df.to_csv(results_path, index=False)
    
    logger.info(f"\n{results_df.to_string(index=False)}")
    
    # Plot true vs predicted
    plt.figure(figsize=(8, 6))
    sns.scatterplot(x=y_test, y=y_pred, alpha=0.5)
    
    # Plot perfect prediction line
    min_val = min(y_test.min(), y_pred.min())
    max_val = max(y_test.max(), y_pred.max())
    plt.plot([min_val, max_val], [min_val, max_val], color='red', linestyle='--')
    
    plt.title(f"Final Model ({selected_model_name}) - True vs Predicted (Test Set)")
    plt.xlabel("True Target")
    plt.ylabel("Predicted Target")
    plt.tight_layout()
    plt.savefig(OUT_FIG_EVAL / "final_test_true_vs_predicted.png", dpi=150)
    plt.close()
    
    # LOCK THE TEST SET
    logger.info("*"*60)
    logger.info("WARNING: LOCKING TEST SET")
    logger.info("*"*60)
    update_pipeline_status("test_locked", True)
    
    logger.info("Final Evaluation completed. Test set is now locked.")
    logger.info("END: Final Test Evaluation")
    logger.info("="*60)

if __name__ == "__main__":
    main()
