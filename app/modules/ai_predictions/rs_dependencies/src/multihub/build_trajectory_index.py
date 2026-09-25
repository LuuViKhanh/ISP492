import pandas as pd
import numpy as np
from pathlib import Path
import sys

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUT_RES_MULTIHUB, ensure_dirs, OUTPUT_DIR
from src.multihub.load_multihub_data import load_and_normalize_data
from src.data_processing.target_calculation import calculate_distance_haversine

def build_trajectory_index(df):
    """
    Extracts summary metadata for each flight to build the trajectory index.
    """
    print("Building trajectory index...")
    records = []
    
    for flight_uid, group in df.groupby("flight_uid"):
        group = group.sort_values("time")
        
        start_pos_x = group["position_x"].iloc[0]
        start_pos_y = group["position_y"].iloc[0]
        end_pos_x = group["position_x"].iloc[-1]
        end_pos_y = group["position_y"].iloc[-1]
        
        # Calculate total distance using Haversine (since we have lon/lat in position_x/y for DJI and VTOL)
        # Note: In DJI & VTOL, position_x = lon, position_y = lat
        dist = calculate_distance_haversine(
            lats=group["position_y"].values,
            lons=group["position_x"].values,
            alts=group["position_z"].values,
            times=group["time"].values
        )
        
        date = group["date"].iloc[0] if "date" in group.columns else np.nan
        route = group["route"].iloc[0] if "route" in group.columns else np.nan
        dataset_id = group["dataset_id"].iloc[0]
        uav_type = group["uav_type"].iloc[0]
        
        records.append({
            "flight_uid": flight_uid,
            "dataset_id": dataset_id,
            "uav_type": uav_type,
            "start_position_x": start_pos_x,
            "start_position_y": start_pos_y,
            "end_position_x": end_pos_x,
            "end_position_y": end_pos_y,
            "n_points": len(group),
            "total_distance_m": dist,
            "date": date,
            "route": route
        })
        
    index_df = pd.DataFrame(records)
    
    out_dir = OUTPUT_DIR / "data" / "multihub"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "trajectory_index.csv"
    index_df.to_csv(out_path, index=False)
    print(f"Saved trajectory index: {out_path} ({len(index_df)} flights)")
    
    return index_df

if __name__ == "__main__":
    df = load_and_normalize_data()
    build_trajectory_index(df)
