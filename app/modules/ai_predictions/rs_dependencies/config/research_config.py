"""
research_config.py
==================
Configuration constants for Research V2.
"""
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RESEARCH_OUT_DIR = PROJECT_ROOT / "output" / "research_v2"
RESULTS_DIR = RESEARCH_OUT_DIR / "results"
PREDICTIONS_DIR = RESEARCH_OUT_DIR / "predictions"
MANIFESTS_DIR = RESEARCH_OUT_DIR / "manifests"
FEATURES_DIR = RESEARCH_OUT_DIR / "features"
MODELS_DIR = RESEARCH_OUT_DIR / "models"
FIGURES_DIR = RESEARCH_OUT_DIR / "figures"
REPORTS_DIR = RESEARCH_OUT_DIR / "reports"

DECISIONS_DIR = PROJECT_ROOT / "decisions"

# Make sure all directories exist
for directory in [
    RESULTS_DIR, PREDICTIONS_DIR, MANIFESTS_DIR, FEATURES_DIR, 
    MODELS_DIR, FIGURES_DIR, REPORTS_DIR, DECISIONS_DIR
]:
    directory.mkdir(parents=True, exist_ok=True)

# Random seeds for multi-seed stability
STABILITY_SEEDS = [42, 7, 21, 84, 123]

# Shortlist of models to run for Feature Engineering Evaluation (E03 - E06)
SHORTLIST_MODELS = ["LinearRegression", "ExtraTrees", "XGBoost", "CatBoost"]
