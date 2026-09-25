import json
import os
from config.paths import OUT_METADATA
from src.utils.logging_utils import get_logger

logger = get_logger("PipelineGuard")

def get_pipeline_status():
    """Read the current pipeline status."""
    status_file = OUT_METADATA / "pipeline_status.json"
    if not os.path.exists(status_file):
        return {}
    with open(status_file, "r") as f:
        return json.load(f)

def update_pipeline_status(step, status):
    """Update the status of a pipeline step."""
    status_file = OUT_METADATA / "pipeline_status.json"
    current_status = get_pipeline_status()
    current_status[step] = status
    with open(status_file, "w") as f:
        json.dump(current_status, f, indent=4)
    logger.info(f"Pipeline status updated: {step} = {status}")

def check_gate_approved(yaml_path):
    """Check if a human decision gate YAML is approved."""
    if not os.path.exists(yaml_path):
        return False
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        content = f.read()
        return "approved: true" in content.lower()

def check_step_completed(step):
    """Check if a specific pipeline step is marked as PASS or COMPLETED."""
    status = get_pipeline_status()
    return status.get(step) in ["PASS", "COMPLETED"]

def require_step(required_step, current_step):
    """Assert that a required previous step is completed."""
    if not check_step_completed(required_step):
        logger.error(f"Cannot execute '{current_step}'. Required step '{required_step}' is not complete.")
        raise RuntimeError(f"Pipeline flow violation: '{required_step}' must be completed first.")
