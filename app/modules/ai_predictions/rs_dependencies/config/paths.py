import os
from pathlib import Path

# =====================================================================
# PROJECT ROOT
# =====================================================================
PROJECT_ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# =====================================================================
# DATA DIRECTORIES
# =====================================================================
DATA_DIR = PROJECT_ROOT / "data"
DATA_RAW_DIR = DATA_DIR / "raw"
DATA_INTERMEDIATE_DIR = DATA_DIR / "intermediate"
DATA_PROCESSED_DIR = DATA_DIR / "processed"

# Platform-specific raw directories
DATA_RAW_DJI = DATA_RAW_DIR / "dji"
DATA_RAW_VTOL = DATA_RAW_DIR / "vtol"

# =====================================================================
# DECISIONS DIRECTORY
# =====================================================================
DECISIONS_DIR = PROJECT_ROOT / "decisions"

# =====================================================================
# OUTPUT DIRECTORIES
# =====================================================================
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"

# Sensitivity runs execute the same audited pipeline in isolated output roots.
# The default remains completely backward compatible for normal experiments.
OUTPUT_DIR = Path(
    os.environ.get("RS_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))
).resolve()

# Observed flight-level baselines and the frozen segment model normally live in
# the canonical output tree even when a sensitivity run writes elsewhere.
REFERENCE_OUTPUT_DIR = Path(
    os.environ.get("RS_REFERENCE_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))
).resolve()

# 1. Output Data (Quality reports, Features, Splits)
OUT_DATA = OUTPUT_DIR / "data"
OUT_DATA_QUALITY = OUT_DATA / "quality"
OUT_DATA_FEATURES = OUT_DATA / "features"
OUT_DATA_SPLITS = OUT_DATA / "splits"

# 2. Output Metadata (Manifests, Hashes, Provenance)
OUT_METADATA = OUTPUT_DIR / "metadata"

# 3. Output Models
OUT_MODELS = OUTPUT_DIR / "models"
OUT_MODELS_TRAINED = OUT_MODELS / "trained"
OUT_BEST_MODEL = OUT_MODELS / "best_model"

# 4. Output Results
OUT_RESULTS = OUTPUT_DIR / "results"
OUT_RES_QUALITY = OUT_RESULTS / "data_quality"
OUT_RES_EVAL = OUT_RESULTS / "model_evaluation"
OUT_RES_ABLATION = OUT_RESULTS / "ablation"
OUT_RES_ROUTE = OUT_RESULTS / "route"
OUT_RES_ENV = OUT_RESULTS / "environmental"
OUT_RES_MULTIHUB = OUT_RESULTS / "multihub"
OUT_RES_SCHED = OUT_RESULTS / "scheduling"
OUT_RES_MULTIUAV = OUT_RESULTS / "multiuav"

# 5. Output Figures
OUT_FIGURES = OUTPUT_DIR / "figures"
OUT_FIG_QUALITY = OUT_FIGURES / "data_quality"
OUT_FIG_FEATURE = OUT_FIGURES / "feature_analysis"
OUT_FIG_SPLITS = OUT_FIGURES / "splits"
OUT_FIG_EVAL = OUT_FIGURES / "model_evaluation"
OUT_FIG_DIAGNOSTICS = OUT_FIGURES / "diagnostics"
OUT_FIG_ABLATION = OUT_FIGURES / "ablation"
OUT_FIG_SHAP = OUT_FIGURES / "shap"
OUT_FIG_ROUTE = OUT_FIGURES / "route"
OUT_FIG_ENV = OUT_FIGURES / "environmental"
OUT_FIG_MULTIHUB = OUT_FIGURES / "multihub"
OUT_FIG_SCHED = OUT_FIGURES / "scheduling"
OUT_FIG_MULTIUAV = OUT_FIGURES / "multiuav"

def ensure_dirs():
    """Create all necessary output directories if they don't exist."""
    dirs_to_create = [
        # Data Dirs
        DATA_RAW_DIR, DATA_INTERMEDIATE_DIR, DATA_PROCESSED_DIR,
        # Config & Decisions
        DECISIONS_DIR,
        # Output Data
        OUT_DATA_QUALITY, OUT_DATA_FEATURES, OUT_DATA_SPLITS,
        # Output Metadata
        OUT_METADATA,
        # Output Models
        OUT_MODELS_TRAINED, OUT_BEST_MODEL,
        # Output Results
        OUT_RES_QUALITY, OUT_RES_EVAL, OUT_RES_ABLATION, 
        OUT_RES_ROUTE, OUT_RES_ENV, OUT_RES_MULTIHUB, OUT_RES_SCHED,
        OUT_RES_MULTIUAV,
        # Output Figures
        OUT_FIG_QUALITY, OUT_FIG_FEATURE, OUT_FIG_SPLITS, 
        OUT_FIG_EVAL, OUT_FIG_DIAGNOSTICS, OUT_FIG_ABLATION, OUT_FIG_SHAP, 
        OUT_FIG_ROUTE, OUT_FIG_ENV, OUT_FIG_MULTIHUB, OUT_FIG_SCHED,
        OUT_FIG_MULTIUAV,
    ]
    for d in dirs_to_create:
        os.makedirs(d, exist_ok=True)
