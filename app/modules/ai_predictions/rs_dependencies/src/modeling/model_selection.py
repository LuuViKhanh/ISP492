"""
Phase 4 - Step 4: Model Selection
Prepares the final evidence report for HUMAN GATE 3.
Does not make the decision, but compiles CV R2, Training Time, and points to diagnostics.
"""

import pandas as pd
from pathlib import Path

from config.paths import OUT_RES_EVAL
from src.utils.logging_utils import get_logger

logger = get_logger("ModelSelection")

def main():
    logger.info("="*60)
    logger.info("START: Prepare Model Selection Evidence")
    
    evidence_path = OUT_RES_EVAL / "model_evidence_table.csv"
    if not evidence_path.exists():
        logger.error("Evidence table not found. Run evaluate_cv first.")
        return
        
    evidence_df = pd.read_csv(evidence_path)
    
    logger.info("\n" + evidence_df.head(5).to_string(index=False))
    
    logger.info("Model selection evidence prepared.")
    logger.info("Waiting for HUMAN GATE 3.")
    logger.info("Please review output/results/model_evaluation/model_evidence_table.csv and output/figures/diagnostics/")
    logger.info("Then update decisions/model_selection_decision.yaml")
    logger.info("END: Prepare Model Selection Evidence")
    logger.info("="*60)

if __name__ == "__main__":
    main()
