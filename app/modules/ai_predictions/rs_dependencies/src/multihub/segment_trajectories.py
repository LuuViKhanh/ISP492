import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUTPUT_DIR
from src.multihub.load_multihub_data import load_and_normalize_data
from src.multihub.map_trajectory_nodes import map_trajectory_nodes

def segment_trajectories(df, mapping_df):
    """
    Split full trajectories into segments between consecutive mapped nodes.
    Retains raw telemetry within the time interval for each segment.
    """
    print("Segmenting trajectories...")
    
    # We will build a list of segment telemetry DataFrames
    segment_telemetry_list = []
    
    for flight_uid, group in df.groupby("flight_uid"):
        group = group.sort_values("time")
        
        # Find mapping for this flight
        mapping_row = mapping_df[mapping_df["flight_uid"] == flight_uid]
        if mapping_row.empty:
            continue
            
        node_seq_str = mapping_row.iloc[0]["ordered_node_sequence"]
        if not node_seq_str:
            continue
            
        node_seq = node_seq_str.split(",")
        if len(node_seq) < 2:
            continue
            
        # We need to assign each point to a segment.
        # Since we don't have exact timestamps of node hits from G04, we will re-approximate:
        # Or better yet, we can equally divide or use a simple heuristic.
        # A much more robust way is to just divide the trajectory evenly if it's Method C,
        # but to be exact, we can find the indices where the drone is closest to the nodes.
        pass # We will do it properly
        
    # Wait, the better way is to have `map_trajectory_nodes` export the timestamp of when a node is reached.
    # Let's write the code here assuming we can re-query the node positions or just do it.

    segment_metadata = []
    
    for flight_uid, group in df.groupby("flight_uid"):
        group = group.sort_values("time").reset_index(drop=True)
        
        mapping_row = mapping_df[mapping_df["flight_uid"] == flight_uid]
        if mapping_row.empty:
            continue
        
        node_seq_str = mapping_row.iloc[0]["ordered_node_sequence"]
        hit_idx_str = mapping_row.iloc[0]["hit_indices"]
        
        if pd.isna(node_seq_str) or pd.isna(hit_idx_str):
            continue
            
        if not hit_idx_str or str(hit_idx_str).strip() == "":
            continue
            
        node_seq = [x for x in str(node_seq_str).split(",") if x.strip()]
        hit_indices = [int(x) for x in str(hit_idx_str).split(",") if x.strip()]
        
        if len(node_seq) < 2:
            continue
            
        # Ensure indices are strictly increasing
        for i in range(1, len(hit_indices)):
            if hit_indices[i] <= hit_indices[i-1]:
                hit_indices[i] = hit_indices[i-1] + 1
                
        # Now create segments
        for i in range(len(node_seq) - 1):
            start_node = node_seq[i]
            end_node = node_seq[i+1]
            
            start_idx = hit_indices[i]
            end_idx = hit_indices[i+1]
            
            if end_idx >= len(group):
                end_idx = len(group) - 1
            if start_idx >= end_idx:
                continue
                
            seg_df = group.iloc[start_idx:end_idx+1].copy()
            segment_id = f"{flight_uid}_S{i:03d}_{start_node}_TO_{end_node}"
            
            seg_df["segment_id"] = segment_id
            seg_df["start_node"] = start_node
            seg_df["end_node"] = end_node
            
            segment_telemetry_list.append(seg_df)
            
    if not segment_telemetry_list:
        print("No valid segments created.")
        return pd.DataFrame()
        
    all_segments_df = pd.concat(segment_telemetry_list, ignore_index=True)
    
    out_dir = OUTPUT_DIR / "data" / "multihub"
    out_path = out_dir / "segmented_telemetry.csv"
    all_segments_df.to_csv(out_path, index=False)
    print(f"Created {all_segments_df['segment_id'].nunique()} segments. Saved telemetry to {out_path}")
    
    return all_segments_df

if __name__ == "__main__":
    df = load_and_normalize_data()
    mapping_path = OUTPUT_DIR / "data" / "multihub" / "trajectory_node_mapping.csv"
    mapping_df = pd.read_csv(mapping_path)
    segment_trajectories(df, mapping_df)
