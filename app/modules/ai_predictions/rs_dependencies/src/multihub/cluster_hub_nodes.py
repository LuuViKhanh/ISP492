import pandas as pd
import numpy as np
from pathlib import Path
import sys
from sklearn.cluster import AgglomerativeClustering

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUTPUT_DIR
from config.multihub_config import (
    HUB_DISTANCE_THRESHOLD_M,
    MIN_HUB_SUPPORT,
)
from src.multihub.load_multihub_data import load_and_normalize_data
from src.multihub.detect_hub_nodes import detect_hub_nodes


EARTH_RADIUS_M = 6371000.0


def _safe_token(value):
    return "".join(
        ch if ch.isalnum() else "_"
        for ch in str(value)
    )


def _project_to_local_meters(df):
    """Approximate local lon/lat -> x/y meters for bounded clustering."""
    lat0 = np.radians(float(df["position_y"].median()))
    lon0 = np.radians(float(df["position_x"].median()))

    lat = np.radians(
        df["position_y"].to_numpy(dtype=float)
    )
    lon = np.radians(
        df["position_x"].to_numpy(dtype=float)
    )

    x = (
        (lon - lon0)
        * EARTH_RADIUS_M
        * np.cos(lat0)
    )
    y = (lat - lat0) * EARTH_RADIUS_M

    return np.column_stack([x, y])


def cluster_hub_nodes(candidates_df):
    """
    Cluster Method-C candidate anchors into shared graph nodes.

    Key output:
    - graph_nodes.csv: unique physical graph nodes.
    - hub_candidate_assignments.csv: EACH candidate anchor mapped to node_id.

    G04 must consume the assignment file so it preserves the original ordered
    Method-C anchors instead of re-snapping all telemetry points.
    """
    print(
        f"Clustering {len(candidates_df)} candidate nodes..."
    )

    required = {
        "flight_uid",
        "dataset_id",
        "candidate_order",
        "telemetry_index",
        "position_x",
        "position_y",
        "position_z",
        "type",
    }
    missing = required - set(candidates_df.columns)
    if missing:
        raise ValueError(
            "Missing Method-C candidate columns: "
            f"{sorted(missing)}. Re-run G02 first."
        )

    graph_nodes = []
    assignment_parts = []

    for dataset_id, dset in candidates_df.groupby(
        "dataset_id",
        sort=True,
    ):
        dset = dset.reset_index(drop=True).copy()
        xy_m = _project_to_local_meters(dset)

        if len(dset) == 1:
            labels = np.array([0], dtype=int)
        else:
            clustering = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=HUB_DISTANCE_THRESHOLD_M,
                linkage="complete",
                metric="euclidean",
            )
            labels = clustering.fit_predict(xy_m)

        dset["cluster_local"] = labels
        prefix = _safe_token(dataset_id)

        cluster_to_node = {}

        for local_id, group in dset.groupby(
            "cluster_local",
            sort=True,
        ):
            support_count = int(
                group["flight_uid"].nunique()
            )

            types = set(
                group["type"].dropna().astype(str)
            )
            if "START" in types and "DESTINATION" in types:
                node_type = "START_DESTINATION"
            elif "START" in types:
                node_type = "START"
            elif "DESTINATION" in types:
                node_type = "DESTINATION"
            else:
                node_type = "HUB"

            # START/DESTINATION are always kept.
            if (
                node_type == "HUB"
                and support_count < MIN_HUB_SUPPORT
            ):
                cluster_to_node[int(local_id)] = None
                continue

            node_id = f"N_{prefix}_{int(local_id)}"
            cluster_to_node[int(local_id)] = node_id

            uav_types = sorted(
                group["uav_type"]
                .dropna()
                .astype(str)
                .unique()
            )

            graph_nodes.append({
                "node_id": node_id,
                "dataset_id": dataset_id,
                "node_type": node_type,
                "position_x": float(
                    group["position_x"].median()
                ),
                "position_y": float(
                    group["position_y"].median()
                ),
                "position_z": float(
                    group["position_z"].median()
                ),
                "support_count": support_count,
                "supporting_uav_types": ",".join(
                    uav_types
                ),
                "source_method": "METHOD_C_ON_ROUTE",
            })

        dset["node_id"] = dset[
            "cluster_local"
        ].map(cluster_to_node)

        assignment_parts.append(
            dset[
                [
                    "flight_uid",
                    "dataset_id",
                    "uav_type",
                    "candidate_order",
                    "telemetry_index",
                    "cumulative_distance_m",
                    "type",
                    "node_id",
                    "position_x",
                    "position_y",
                    "position_z",
                ]
            ].copy()
        )

    graph_nodes_df = pd.DataFrame(graph_nodes)
    assignments_df = pd.concat(
        assignment_parts,
        ignore_index=True,
    )

    out_dir = OUTPUT_DIR / "data" / "multihub"
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = out_dir / "graph_nodes.csv"
    assign_path = (
        out_dir / "hub_candidate_assignments.csv"
    )

    graph_nodes_df.to_csv(
        nodes_path,
        index=False,
    )
    assignments_df.to_csv(
        assign_path,
        index=False,
    )

    print(
        f"Created {len(graph_nodes_df)} graph nodes using "
        f"bounded {HUB_DISTANCE_THRESHOLD_M:.1f} m clustering."
    )
    print("Nodes by dataset:")
    print(
        graph_nodes_df["dataset_id"]
        .value_counts()
        .to_string()
    )
    print(f"Saved graph nodes to {nodes_path}")
    print(
        f"Saved candidate->node assignments to "
        f"{assign_path}"
    )

    return graph_nodes_df


if __name__ == "__main__":
    df = load_and_normalize_data()
    c_df = detect_hub_nodes(df)
    cluster_hub_nodes(c_df)
