import pandas as pd
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUTPUT_DIR
from src.multihub.load_multihub_data import load_and_normalize_data
from src.multihub.detect_hub_nodes import detect_hub_nodes
from src.multihub.cluster_hub_nodes import cluster_hub_nodes


def _dedupe_consecutive(assignments):
    """
    Preserve ordered Method-C anchors but collapse consecutive anchors that
    normalize to the same shared graph node.
    """
    final_nodes = []
    final_hits = []

    for row in assignments.itertuples(index=False):
        node_id = row.node_id
        if pd.isna(node_id) or str(node_id).strip() == "":
            continue

        node_id = str(node_id)
        hit_idx = int(row.telemetry_index)

        if not final_nodes or final_nodes[-1] != node_id:
            final_nodes.append(node_id)
            final_hits.append(hit_idx)

    return final_nodes, final_hits


def map_trajectory_nodes(df, graph_nodes_df):
    """
    Build each flight's ordered graph-node sequence from the exact Method-C
    candidate anchors saved by G02/G03.

    We intentionally DO NOT query every telemetry point against all graph nodes.
    That old nearest-node approach created node-jitter such as A->B->A->B and
    inflated VTOL flights from ~32 intended anchors to hundreds of fake visits.
    """
    print(
        "Mapping trajectories to graph nodes using "
        "ordered Method-C anchors..."
    )

    out_dir = OUTPUT_DIR / "data" / "multihub"
    assignments_path = (
        out_dir / "hub_candidate_assignments.csv"
    )

    if not assignments_path.exists():
        raise FileNotFoundError(
            f"Missing {assignments_path}. "
            "Re-run G02/G03 before G04."
        )

    assignments_df = pd.read_csv(assignments_path)

    required = {
        "flight_uid",
        "dataset_id",
        "candidate_order",
        "telemetry_index",
        "node_id",
    }
    missing = required - set(assignments_df.columns)
    if missing:
        raise ValueError(
            "Candidate assignment file missing columns: "
            f"{sorted(missing)}"
        )

    mapping_records = []

    # Keep all original flights in the mapping audit, even if a flight has
    # fewer than two retained anchors.
    flight_meta = (
        df.groupby("flight_uid", as_index=False)
        .agg(dataset_id=("dataset_id", "first"))
    )

    grouped_assignments = {
        flight_uid: group.sort_values(
            ["candidate_order", "telemetry_index"]
        )
        for flight_uid, group in assignments_df.groupby(
            "flight_uid"
        )
    }

    for row in flight_meta.itertuples(index=False):
        flight_uid = row.flight_uid
        dataset_id = row.dataset_id

        group = grouped_assignments.get(flight_uid)

        if group is None or group.empty:
            final_nodes = []
            final_hits = []
            raw_candidate_count = 0
        else:
            # Safety: a flight must never consume assignments from another
            # spatial dataset.
            group = group[
                group["dataset_id"] == dataset_id
            ].copy()

            raw_candidate_count = len(group)
            final_nodes, final_hits = (
                _dedupe_consecutive(group)
            )

        mapping_records.append({
            "flight_uid": flight_uid,
            "dataset_id": dataset_id,
            "ordered_node_sequence": ",".join(
                final_nodes
            ),
            "hit_indices": ",".join(
                str(x) for x in final_hits
            ),
            "num_candidate_anchors": raw_candidate_count,
            "num_nodes": len(final_nodes),
        })

    mapping_df = pd.DataFrame(mapping_records)

    out_path = (
        out_dir / "trajectory_node_mapping.csv"
    )
    mapping_df.to_csv(
        out_path,
        index=False,
    )

    print(
        f"Mapped {len(mapping_df)} flights. "
        f"Saved to {out_path}"
    )

    print("Mapped-node summary by dataset:")
    print(
        mapping_df.groupby("dataset_id")[
            ["num_candidate_anchors", "num_nodes"]
        ]
        .agg(["count", "mean", "median", "min", "max"])
        .to_string()
    )

    # Hard guard: ordered graph visits cannot exceed the candidate anchors
    # that generated them.
    bad = mapping_df[
        mapping_df["num_nodes"]
        > mapping_df["num_candidate_anchors"]
    ]
    if len(bad):
        raise RuntimeError(
            f"G04 mapping invariant failed for {len(bad)} flights: "
            "num_nodes > num_candidate_anchors."
        )

    return mapping_df


if __name__ == "__main__":
    df = load_and_normalize_data()
    c_df = detect_hub_nodes(df)
    n_df = cluster_hub_nodes(c_df)
    map_trajectory_nodes(df, n_df)
