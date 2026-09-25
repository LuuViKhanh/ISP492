"""G12 — Enumerate candidate paths using the same rules as OD creation."""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.multihub_config import MAX_PATH_DISTANCE_RATIO, MAX_PATH_HOPS
from config.paths import OUTPUT_DIR
from src.multihub.routing_graph import (
    build_supported_graph,
    enumerate_feasible_paths,
    node_type_lookup,
    path_edge_ids,
)


PATH_COLUMNS = [
    "request_id",
    "request_kind",
    "od_pair_id",
    "path_id",
    "source_node",
    "destination_node",
    "node_sequence",
    "edge_sequence",
    "num_hops",
    "total_distance",
    "predicted_total_energy",
    "predicted_ee",
    "path_energy_is_valid",
    "path_platform_match",
    "path_has_intermediate_hub",
    "candidate_eligible",
    "is_original_path",
]


def _path_id(request_id: str, nodes: list[str]) -> str:
    digest = hashlib.sha1(
        "|".join(nodes).encode("utf-8")
    ).hexdigest()[:10]
    return f"{request_id}_PATH_{digest}"


def build_candidate_paths(
    requests_df: pd.DataFrame,
    edges_df: pd.DataFrame,
    predictions_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    *,
    excluded_nodes: Iterable[str] = (),
) -> pd.DataFrame:
    """Build scored paths in memory; used by baseline and hub-removal runs."""
    energy_lookup = predictions_df.set_index(
        ["request_id", "edge_id"]
    )["predicted_segment_energy_wh"].to_dict()
    valid_lookup = predictions_df.set_index(
        ["request_id", "edge_id"]
    )["prediction_is_valid"].to_dict()
    types = node_type_lookup(nodes_df)
    excluded = {str(node) for node in excluded_nodes}
    records: list[dict[str, object]] = []

    for request in requests_df.to_dict(orient="records"):
        request_id = str(request["request_id"])
        source = str(request["source_node"])
        destination = str(request["destination_node"])
        if source in excluded or destination in excluded:
            continue

        graph = build_supported_graph(
            edges_df,
            uav_type=str(request.get("uav_type", "")),
            dataset_id=str(request.get("dataset_id", "")),
            excluded_nodes=excluded,
        )
        feasible = enumerate_feasible_paths(
            graph,
            source,
            destination,
            types=types,
            max_hops=MAX_PATH_HOPS,
            max_distance_ratio=MAX_PATH_DISTANCE_RATIO,
        )
        path_rows: list[tuple[list[str], float, bool]] = [
            (nodes, distance, True) for nodes, distance in feasible
        ]

        raw_original = request.get("original_node_sequence", "")
        original_nodes = (
            [
                token.strip()
                for token in str(raw_original).split(",")
                if token.strip()
            ]
            if pd.notna(raw_original)
            else []
        )
        if len(original_nodes) >= 2:
            original_is_present = any(
                nodes == original_nodes for nodes, _, _ in path_rows
            )
            original_is_valid = all(
                graph.has_edge(original_nodes[i], original_nodes[i + 1])
                for i in range(len(original_nodes) - 1)
            )
            if not original_is_present and original_is_valid:
                original_distance = float(
                    sum(
                        graph[original_nodes[i]][original_nodes[i + 1]][
                            "segment_distance"
                        ]
                        for i in range(len(original_nodes) - 1)
                    )
                )
                path_rows.append((original_nodes, original_distance, False))

        seen: set[tuple[str, ...]] = set()
        for nodes, distance, candidate_eligible in path_rows:
            signature = tuple(nodes)
            if signature in seen:
                continue
            seen.add(signature)
            edge_ids = path_edge_ids(graph, nodes)
            energies: list[float] = []
            all_energy_valid = True
            for edge_id in edge_ids:
                value = energy_lookup.get((request_id, edge_id))
                is_valid = bool(valid_lookup.get((request_id, edge_id), False))
                if value is None or pd.isna(value) or not is_valid:
                    all_energy_valid = False
                    break
                energies.append(float(value))

            total_energy = float(sum(energies)) if all_energy_valid else float("nan")
            predicted_ee = (
                float(distance) / total_energy
                if all_energy_valid and total_energy > 0
                else float("nan")
            )
            is_original = len(original_nodes) >= 2 and nodes == original_nodes
            has_hub = any(
                "HUB" in types.get(str(node), "").upper()
                for node in nodes[1:-1]
            )
            records.append(
                {
                    "request_id": request_id,
                    "request_kind": request.get("request_kind", ""),
                    "od_pair_id": request.get(
                        "od_pair_id", f"{source}__TO__{destination}"
                    ),
                    "path_id": _path_id(request_id, nodes),
                    "source_node": source,
                    "destination_node": destination,
                    "node_sequence": ",".join(nodes),
                    "edge_sequence": ",".join(edge_ids),
                    "num_hops": len(nodes) - 1,
                    "total_distance": float(distance),
                    "predicted_total_energy": total_energy,
                    "predicted_ee": predicted_ee,
                    "path_energy_is_valid": bool(all_energy_valid),
                    "path_platform_match": True,
                    "path_has_intermediate_hub": bool(has_hub),
                    "candidate_eligible": bool(candidate_eligible and has_hub),
                    "is_original_path": bool(is_original),
                }
            )

    return pd.DataFrame(records, columns=PATH_COLUMNS)


def enumerate_candidate_paths() -> pd.DataFrame:
    print("=" * 70)
    print("G12 — ENUMERATING CONSISTENT MULTI-HUB CANDIDATE PATHS")
    print("=" * 70)
    data_dir = Path(OUTPUT_DIR) / "data" / "multihub"
    requests_df = pd.read_csv(data_dir / "route_requests.csv")
    edges_df = pd.read_csv(data_dir / "graph_edges.csv")
    predictions_df = pd.read_csv(data_dir / "edge_predictions.csv")
    nodes_df = pd.read_csv(data_dir / "graph_nodes.csv")

    paths_df = build_candidate_paths(
        requests_df,
        edges_df,
        predictions_df,
        nodes_df,
    )
    paths_df.to_csv(data_dir / "candidate_paths.csv", index=False)

    valid_candidates = paths_df[
        paths_df["candidate_eligible"]
        & paths_df["path_energy_is_valid"]
        & paths_df["path_platform_match"]
    ]
    request_counts = valid_candidates.groupby("request_id").size()
    routable_request_ids = set(request_counts[request_counts >= 2].index)
    topology = requests_df[requests_df["request_kind"] == "TOPOLOGY_OD"]
    topology_routable = topology[
        topology["request_id"].isin(routable_request_ids)
    ]

    scope_path = data_dir / "routing_experiment_scope.csv"
    if scope_path.exists():
        scope = pd.read_csv(scope_path)
        scope["n_candidate_routes"] = len(valid_candidates)
        scope["n_requests_with_scored_routes"] = valid_candidates[
            "request_id"
        ].nunique()
        scope["n_scored_topology_od_pairs"] = topology_routable[
            "od_pair_id"
        ].nunique()
        scope.to_csv(scope_path, index=False)

    print(f"G12 scored candidate routes: {len(valid_candidates)}")
    print(
        "G12 requests with >=2 scored candidates: "
        f"{len(routable_request_ids)}"
    )
    print(
        "G12 unique scored topology OD pairs: "
        f"{topology_routable['od_pair_id'].nunique()}"
    )
    return paths_df


if __name__ == "__main__":
    enumerate_candidate_paths()
