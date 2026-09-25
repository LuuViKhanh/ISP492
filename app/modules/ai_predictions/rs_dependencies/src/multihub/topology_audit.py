"""Topology dominance audit and dominant-hub removal experiment."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.paths import OUTPUT_DIR
from src.multihub.enumerate_candidate_paths import build_candidate_paths
from src.multihub.routing_policies import select_routing_policies


def _nodes(sequence: object) -> list[str]:
    if pd.isna(sequence):
        return []
    return [token for token in str(sequence).split(",") if token]


def _intermediate_hubs(
    sequence: object,
    node_types: dict[str, str],
) -> set[str]:
    path = _nodes(sequence)
    return {
        node
        for node in path[1:-1]
        if "HUB" in node_types.get(node, "").upper()
    }


def run_topology_audit() -> tuple[pd.DataFrame, pd.DataFrame]:
    data_dir = Path(OUTPUT_DIR) / "data" / "multihub"
    result_dir = Path(OUTPUT_DIR) / "results" / "multihub" / "topology_audit"
    result_dir.mkdir(parents=True, exist_ok=True)

    nodes_df = pd.read_csv(data_dir / "graph_nodes.csv")
    edges_df = pd.read_csv(data_dir / "graph_edges.csv")
    requests_df = pd.read_csv(data_dir / "route_requests.csv")
    predictions_df = pd.read_csv(data_dir / "edge_predictions.csv")
    paths_df = pd.read_csv(data_dir / "candidate_paths.csv")
    selections_df = pd.read_csv(data_dir / "policy_selections.csv")

    graph = nx.from_pandas_edgelist(
        edges_df,
        "start_node",
        "end_node",
        ["segment_distance"],
        create_using=nx.DiGraph(),
    )
    node_types = (
        nodes_df.set_index("node_id")["node_type"]
        .fillna("")
        .astype(str)
        .to_dict()
    )
    betweenness = nx.betweenness_centrality(
        graph,
        weight="segment_distance",
        normalized=True,
    )

    topology_paths = paths_df[
        (paths_df["request_kind"] == "TOPOLOGY_OD")
        & paths_df["candidate_eligible"].astype(bool)
        & paths_df["path_energy_is_valid"].astype(bool)
    ]
    path_count = Counter()
    od_sets: dict[str, set[str]] = {
        str(node): set() for node in graph.nodes
    }
    for path in topology_paths.to_dict(orient="records"):
        for node in _intermediate_hubs(path["node_sequence"], node_types):
            path_count[node] += 1
            od_sets.setdefault(node, set()).add(str(path["od_pair_id"]))

    topology_selections = selections_df[
        selections_df["request_kind"] == "TOPOLOGY_OD"
    ]
    historical_selections = selections_df[
        selections_df["request_kind"] == "HISTORICAL_MISSION"
    ]
    path_lookup = paths_df.set_index("path_id")
    topology_recommendation_count = Counter()
    for path_id in topology_selections["P3_MAX_EE_PATH_ID"].dropna():
        if path_id not in path_lookup.index:
            continue
        for node in _intermediate_hubs(
            path_lookup.loc[path_id, "node_sequence"], node_types
        ):
            topology_recommendation_count[node] += 1

    historical_recommendation_count = Counter()
    final_table_path = (
        Path(OUTPUT_DIR)
        / "results"
        / "multihub"
        / "final_rq5"
        / "fair_model_based_routing_comparison.csv"
    )
    if final_table_path.exists():
        final_table = pd.read_csv(final_table_path)
        recommended_hypothetical = final_table[
            final_table["recommendation_decision"] == "USE_HYPOTHETICAL_ROUTE"
        ]
        historical_recommendation_sequences = recommended_hypothetical[
            "recommended_route"
        ].dropna().tolist()
    else:
        # Standalone G14 audits can still run before FINAL RQ5; in that case
        # P3 is the best available proxy for the historical recommendation.
        historical_recommendation_sequences = []
        for path_id in historical_selections["P3_MAX_EE_PATH_ID"].dropna():
            if path_id in path_lookup.index:
                historical_recommendation_sequences.append(
                    path_lookup.loc[path_id, "node_sequence"]
                )
    for sequence in historical_recommendation_sequences:
        for node in _intermediate_hubs(sequence, node_types):
            historical_recommendation_count[node] += 1

    denominator_paths = max(len(topology_paths), 1)
    topology_recommendation_denominator = max(len(topology_selections), 1)
    historical_recommendation_denominator = max(
        len(historical_recommendation_sequences), 1
    )
    audit_rows: list[dict[str, object]] = []
    for node in sorted(str(item) for item in graph.nodes):
        audit_rows.append(
            {
                "node_id": node,
                "node_type": node_types.get(node, ""),
                "in_degree": int(graph.in_degree(node)),
                "out_degree": int(graph.out_degree(node)),
                "total_degree": int(graph.in_degree(node) + graph.out_degree(node)),
                "betweenness_centrality": float(betweenness.get(node, 0.0)),
                "candidate_path_count": int(path_count[node]),
                "path_participation_rate": float(
                    path_count[node] / denominator_paths
                ),
                "candidate_availability_od_count": int(len(od_sets[node])),
                "historical_recommendation_count": int(
                    historical_recommendation_count[node]
                ),
                "historical_recommendation_frequency": float(
                    historical_recommendation_count[node]
                    / historical_recommendation_denominator
                ),
                "topology_recommendation_count": int(
                    topology_recommendation_count[node]
                ),
                "topology_recommendation_frequency": float(
                    topology_recommendation_count[node]
                    / topology_recommendation_denominator
                ),
            }
        )
    audit = pd.DataFrame(audit_rows).merge(
        nodes_df[
            ["node_id", "dataset_id", "position_x", "position_y", "position_z"]
        ],
        on="node_id",
        how="left",
    )
    audit = audit.sort_values(
        [
            "historical_recommendation_count",
            "topology_recommendation_count",
            "betweenness_centrality",
            "node_id",
        ],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)
    audit.to_csv(result_dir / "topology_node_audit.csv", index=False)

    hub_audit = audit[audit["node_type"].str.contains("HUB", case=False, na=False)]
    if hub_audit.empty:
        raise RuntimeError("Topology audit found no intermediate HUB node.")
    dominant = str(hub_audit.iloc[0]["node_id"])

    topology_requests = requests_df[
        requests_df["request_kind"] == "TOPOLOGY_OD"
    ].copy()
    comparable_source = topology_requests[
        (topology_requests["source_node"].astype(str) != dominant)
        & (topology_requests["destination_node"].astype(str) != dominant)
    ]
    removed_paths = build_candidate_paths(
        comparable_source,
        edges_df,
        predictions_df,
        nodes_df,
        excluded_nodes={dominant},
    )
    removed_selections = select_routing_policies(
        comparable_source,
        removed_paths,
    )
    removed_paths.to_csv(
        result_dir / "dominant_hub_removed_candidate_paths.csv", index=False
    )
    removed_selections.to_csv(
        result_dir / "dominant_hub_removed_policy_selections.csv", index=False
    )

    baseline_selection = topology_selections[
        topology_selections["request_id"].isin(comparable_source["request_id"])
    ][["request_id", "P3_MAX_EE_PATH_ID"]].rename(
        columns={"P3_MAX_EE_PATH_ID": "baseline_path_id"}
    )
    removed_selection = removed_selections[
        ["request_id", "P3_MAX_EE_PATH_ID"]
    ].rename(columns={"P3_MAX_EE_PATH_ID": "removed_path_id"})
    comparison = baseline_selection.merge(
        removed_selection,
        on="request_id",
        how="inner",
    )
    baseline_path_lookup = paths_df.set_index("path_id")
    removed_path_lookup = removed_paths.set_index("path_id")
    same_routes: list[bool] = []
    ee_changes: list[float] = []
    for row in comparison.itertuples(index=False):
        baseline_path = baseline_path_lookup.loc[row.baseline_path_id]
        removed_path = removed_path_lookup.loc[row.removed_path_id]
        same_routes.append(
            str(baseline_path["node_sequence"])
            == str(removed_path["node_sequence"])
        )
        baseline_ee = float(baseline_path["predicted_ee"])
        removed_ee = float(removed_path["predicted_ee"])
        ee_changes.append(
            (removed_ee - baseline_ee) / baseline_ee * 100.0
            if baseline_ee > 0
            else float("nan")
        )

    baseline_comparable_count = len(baseline_selection)
    removed_count = len(removed_selections)
    routability_loss_rate = (
        1.0 - removed_count / baseline_comparable_count
        if baseline_comparable_count
        else float("nan")
    )
    dominant_row = hub_audit.iloc[0]
    dominant_selection_rate = float(
        dominant_row["historical_recommendation_frequency"]
    )
    dominant_topology_selection_rate = float(
        dominant_row["topology_recommendation_frequency"]
    )
    if np.isfinite(routability_loss_rate) and routability_loss_rate >= 0.20:
        evidence_pattern = "TOPOLOGICAL_BOTTLENECK_EVIDENCE"
    elif dominant_selection_rate >= 0.50:
        evidence_pattern = "ENERGY_PREFERENCE_WITH_ALTERNATIVES"
    else:
        evidence_pattern = "NO_SINGLE_HUB_DOMINANCE"

    summary = pd.DataFrame(
        [
            {
                "dominant_hub": dominant,
                "dominant_hub_recommendation_frequency": dominant_selection_rate,
                "dominant_hub_historical_recommendation_frequency": (
                    dominant_selection_rate
                ),
                "dominant_hub_topology_recommendation_frequency": (
                    dominant_topology_selection_rate
                ),
                "dominant_hub_betweenness_centrality": float(
                    dominant_row["betweenness_centrality"]
                ),
                "baseline_topology_selections": int(len(topology_selections)),
                "baseline_comparable_after_endpoint_exclusion": int(
                    baseline_comparable_count
                ),
                "selections_after_dominant_hub_removal": int(removed_count),
                "routability_loss_rate": routability_loss_rate,
                "n_comparable_recommendations": int(len(comparison)),
                "same_route_rate_after_removal": (
                    float(np.mean(same_routes)) if same_routes else float("nan")
                ),
                "mean_best_ee_change_pct_after_removal": (
                    float(np.nanmean(ee_changes)) if ee_changes else float("nan")
                ),
                "evidence_pattern": evidence_pattern,
            }
        ]
    )
    summary.to_csv(result_dir / "dominant_hub_removal_summary.csv", index=False)
    with (result_dir / "dominant_hub_removal_summary.json").open(
        "w", encoding="utf-8"
    ) as stream:
        json.dump(summary.iloc[0].to_dict(), stream, indent=2, default=str)

    print(
        f"Topology audit dominant hub: {dominant} "
        f"(selection frequency={dominant_selection_rate:.3f})"
    )
    print(
        "Dominant-hub removal routability loss: "
        f"{routability_loss_rate:.3f}"
    )
    return audit, summary


if __name__ == "__main__":
    run_topology_audit()
