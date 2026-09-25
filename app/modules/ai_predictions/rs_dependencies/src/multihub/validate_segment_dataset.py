import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUTPUT_DIR

def validate_segment_dataset(df):
    """
    Validates the segment-level dataset before modeling (G07).
    """
    print("=" * 60)
    print("SEGMENT DATASET VALIDATION SUMMARY")
    print("=" * 60)
    
    # Basic counts
    n_segments = len(df)
    n_flights = df["flight_uid"].nunique()
    print(f"Total Segments: {n_segments}")
    print(f"Total Source Flights: {n_flights}")
    print(f"Average segments per flight: {n_segments / n_flights:.2f}" if n_flights > 0 else "0")
    
    # Dataset Distribution
    print("\nSegments by Dataset:")
    print(df["dataset_id"].value_counts().to_string())
    
    # Invalid flags
    print("\nData Sanity Checks:")
    invalid_dist = (df["segment_distance"] <= 0).sum()
    invalid_dur = (df["segment_duration"] <= 0).sum()
    invalid_eng = (df["segment_energy_wh"] <= 0).sum()
    
    print(f"  - Segments with distance <= 0: {invalid_dist}")
    print(f"  - Segments with duration <= 0: {invalid_dur}")
    print(f"  - Segments with energy <= 0:   {invalid_eng}")
    
    # Missing features
    print("\nMissing Features Check:")
    cols_to_check = ["speed", "altitude", "payload", "temperature_c", "wind_speed_ms", "wind_dir_deg", "segment_distance"]
    for col in cols_to_check:
        if col in df.columns:
            missing = df[col].isnull().sum()
            print(f"  - {col:18s}: {missing} missing ({missing/n_segments*100:.1f}%)")
        else:
            print(f"  - {col:18s}: COLUMN NOT FOUND")
            
    print("=" * 60)
    
    out_dir = OUTPUT_DIR / "results" / "multihub"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Optional: save report
    summary = {
        "Total_Segments": n_segments,
        "Total_Flights": n_flights,
        "Invalid_Distance": invalid_dist,
        "Invalid_Energy": invalid_eng
    }
    pd.DataFrame([summary]).to_csv(out_dir / "segment_validation_summary.csv", index=False)
    
    return summary

if __name__ == "__main__":
    features_path = OUTPUT_DIR / "data" / "multihub" / "segment_level_features.csv"
    if features_path.exists():
        df = pd.read_csv(features_path)
        validate_segment_dataset(df)
    else:
        print(f"File not found: {features_path}")
