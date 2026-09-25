import pandas as pd
import numpy as np
import networkx as nx
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from config.paths import OUTPUT_DIR

def build_route_graph():
    print("="*60)
    print("BUILDING DIRECTED GRAPH (G08)")
    print("="*60)
    
    segments_path = OUTPUT_DIR / "data" / "multihub" / "segment_level_features.csv"
    if not segments_path.exists():
        raise FileNotFoundError(f"{segments_path} not found. Run G06 first.")
        
    df = pd.read_csv(segments_path)
    nodes_path = OUTPUT_DIR / "data" / "multihub" / "graph_nodes.csv"
    nodes_df = pd.read_csv(nodes_path) if nodes_path.exists() else pd.DataFrame()
    node_lookup = nodes_df.set_index("node_id") if not nodes_df.empty else None
    
    # 1. Group segments by start_node and end_node
    # Edge properties: median distance, support count
    edge_records = []
    
    for (start_node, end_node), group in df.groupby(["start_node", "end_node"]):
        median_dist = group["segment_distance"].median()
        support_count = len(group)
        support_ids = group["segment_id"].tolist()
        
        # Platform-support metadata
        uav_types = group["uav_type"].dropna().unique().tolist() if "uav_type" in group.columns else []
        dataset_ids = group["dataset_id"].dropna().unique().tolist() if "dataset_id" in group.columns else []
        
        edge_id = f"{start_node}_TO_{end_node}"
        
        start_meta = node_lookup.loc[start_node] if node_lookup is not None and start_node in node_lookup.index else None
        end_meta = node_lookup.loc[end_node] if node_lookup is not None and end_node in node_lookup.index else None

        edge_records.append({
            "edge_id": edge_id,
            "start_node": start_node,
            "end_node": end_node,
            "start_x": start_meta["position_x"] if start_meta is not None else np.nan,
            "start_y": start_meta["position_y"] if start_meta is not None else np.nan,
            "end_x": end_meta["position_x"] if end_meta is not None else np.nan,
            "end_y": end_meta["position_y"] if end_meta is not None else np.nan,
            "segment_distance": median_dist,
            "historical_support_count": support_count,
            "supporting_segment_ids": ",".join(support_ids),
            "supporting_uav_types": ",".join(map(str, uav_types)),
            "supporting_dataset_ids": ",".join(map(str, dataset_ids))
        })
        
    edges_df = pd.DataFrame(edge_records)
    
    # 2. Build NetworkX Graph (for later path enumeration, though we export CSV as primary artifact)
    G = nx.DiGraph()
    for _, row in edges_df.iterrows():
        G.add_edge(row["start_node"], row["end_node"], 
                   edge_id=row["edge_id"], 
                   distance=row["segment_distance"], 
                   weight=row["segment_distance"]) # weight for shortest path
                   
    print(f"Graph constructed with {G.number_of_nodes()} nodes and {G.number_of_edges()} directed edges.")
    
    # Save graph edges
    out_dir = OUTPUT_DIR / "data" / "multihub"
    out_dir.mkdir(parents=True, exist_ok=True)
    edges_df.to_csv(out_dir / "graph_edges.csv", index=False)
    print(f"Saved edge list to {out_dir / 'graph_edges.csv'}")
    
    return G, edges_df

if __name__ == "__main__":
    build_route_graph()
