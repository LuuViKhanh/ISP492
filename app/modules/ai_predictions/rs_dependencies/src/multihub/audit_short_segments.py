import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUTPUT_DIR

def audit_segments():
    print("Loading trajectory mapping and features...")
    mapping_df = pd.read_csv(OUTPUT_DIR / "data" / "multihub" / "trajectory_node_mapping.csv")
    features_df = pd.read_csv(OUTPUT_DIR / "data" / "multihub" / "segment_level_features.csv")
    
    print("\n--- MAPPING STATS ---")
    # Let's see some node sequences
    for i, row in mapping_df.head(10).iterrows():
        print(f"Flight {row['flight_uid']} Nodes ({row['num_nodes']}): {row['ordered_node_sequence'][:100]}...")
        
    print("\n--- SHORT SEGMENTS STATS ---")
    short_df = features_df[features_df["segment_distance"] <= 5.0]
    print(f"Total short segments: {len(short_df)}")
    
    if not short_df.empty:
        print("\nValue counts of start_node == end_node:")
        print((short_df["start_node"] == short_df["end_node"]).value_counts())
        
        print("\nSample short segments:")
        for i, row in short_df.head(10).iterrows():
            print(f"Seg: {row['segment_id']}, Dist: {row['segment_distance']:.2f}, Dur: {row['segment_duration']:.2f}s, Nodes: {row['start_node']} -> {row['end_node']}")
            
if __name__ == "__main__":
    audit_segments()
