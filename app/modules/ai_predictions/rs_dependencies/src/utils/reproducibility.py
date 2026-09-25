import hashlib
import json
import os
import random
import numpy as np
import pandas as pd
from datetime import datetime

def set_seed(seed=42):
    """Fix random seeds for reproducibility."""
    random.seed(seed)
    np.random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)

def compute_file_hash(filepath):
    """Compute SHA256 hash of a file."""
    if not os.path.exists(filepath):
        return None
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        # Read and update hash string value in blocks of 4K
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def compute_dataframe_hash(df):
    """Compute SHA256 hash of a Pandas DataFrame."""
    # Convert dataframe to json string representation and hash it
    # Ensuring order consistency
    df_json = df.to_json(orient='records', date_format='iso')
    return hashlib.sha256(df_json.encode('utf-8')).hexdigest()

def save_manifest(manifest_dict, filepath):
    """Save a dictionary as a JSON manifest file."""
    manifest_dict["generated_at"] = datetime.now().isoformat()
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(manifest_dict, f, indent=4)
