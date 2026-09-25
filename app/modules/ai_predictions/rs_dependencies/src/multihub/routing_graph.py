"""Shared graph/path rules for the Multi-Hub routing experiment.

The same eligibility definition is used when constructing OD scenarios and
when enumerating model-scored routes. Keeping it here prevents the reported OD
count from disagreeing with the routes that are actually evaluated.
"""

from __future__ import annotations

from collections.abc import Iterable

import networkx as nx
import pandas as pd


def csv_tokens(value: object) -> set[str]:
    if pd.isna(value):
        return set()
    return {
        token.strip()
        for token in str(value).split(",")
        if token.strip()
    }


def build_supported_graph(
    edges_df: pd.DataFrame,
    *,
    uav_type: str | None = None,
    dataset_id: str | None = None,
    excluded_nodes: Iterable[str] = (),
) -> nx.DiGraph:
    """Build the directed graph restricted to historically supported edges."""
    excluded = {str(node) for node in excluded_nodes}
    graph = nx.DiGraph()

    for row in edges_df.itertuples(index=False):
        start = str(row.start_node)
        end = str(row.end_node)
        if start in excluded or end in excluded:
            continue

        if uav_type:
            supported_uavs = csv_tokens(
                getattr(row, "supporting_uav_types", "")
            )
            if str(uav_type) not in supported_uavs:
                continue
        if dataset_id:
            supported_datasets = csv_tokens(
                getattr(row, "supporting_dataset_ids", "")
            )
            if str(dataset_id) not in supported_datasets:
                continue

        graph.add_edge(
            start,
            end,
            edge_id=str(row.edge_id),
            segment_distance=float(row.segment_distance),
        )
    return graph


def node_type_lookup(nodes_df: pd.DataFrame) -> dict[str, str]:
    if nodes_df.empty or "node_type" not in nodes_df.columns:
        return {}
    return (
        nodes_df.assign(node_id=nodes_df["node_id"].astype(str))
        .set_index("node_id")["node_type"]
        .fillna("")
        .astype(str)
        .to_dict()
    )


def has_intermediate_hub(
    node_path: list[str],
    types: dict[str, str],
) -> bool:
    """Return True only when a non-endpoint graph node is a HUB region."""
    return any(
        "HUB" in types.get(str(node), "").upper()
        for node in node_path[1:-1]
    )


def path_distance(graph: nx.DiGraph, node_path: list[str]) -> float:
    return float(
        sum(
            graph[node_path[i]][node_path[i + 1]]["segment_distance"]
            for i in range(len(node_path) - 1)
        )
    )


def path_edge_ids(graph: nx.DiGraph, node_path: list[str]) -> list[str]:
    return [
        str(graph[node_path[i]][node_path[i + 1]]["edge_id"])
        for i in range(len(node_path) - 1)
    ]


def enumerate_feasible_paths(
    graph: nx.DiGraph,
    source: str,
    destination: str,
    *,
    types: dict[str, str],
    max_hops: int,
    max_distance_ratio: float,
) -> list[tuple[list[str], float]]:
    """Enumerate platform-valid, connected, plausible Multi-Hub paths.

    The detour cap is relative to the weighted shortest path in the same
    platform/dataset graph, including a possible direct route. This makes the
    ratio a genuine plausibility filter rather than normalizing by the shortest
    already-detoured alternative.
    """
    source = str(source)
    destination = str(destination)
    if source == destination or source not in graph or destination not in graph:
        return []

    try:
        shortest_distance = float(
            nx.shortest_path_length(
                graph,
                source,
                destination,
                weight="segment_distance",
            )
        )
        raw_paths = nx.all_simple_paths(
            graph,
            source,
            destination,
            cutoff=int(max_hops),
        )
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []

    distance_cap = shortest_distance * float(max_distance_ratio)
    feasible: list[tuple[list[str], float]] = []
    for raw_path in raw_paths:
        path = [str(node) for node in raw_path]
        if not has_intermediate_hub(path, types):
            continue
        distance = path_distance(graph, path)
        if distance <= distance_cap + 1e-9:
            feasible.append((path, distance))

    feasible.sort(key=lambda item: (item[1], tuple(item[0])))
    return feasible


def enumerate_eligible_od_pairs(
    graph: nx.DiGraph,
    *,
    types: dict[str, str],
    max_hops: int,
    max_distance_ratio: float,
) -> list[dict[str, object]]:
    """Return every ordered OD with at least two feasible Multi-Hub paths."""
    rows: list[dict[str, object]] = []
    nodes = sorted(str(node) for node in graph.nodes)
    for source in nodes:
        for destination in nodes:
            if source == destination:
                continue
            paths = enumerate_feasible_paths(
                graph,
                source,
                destination,
                types=types,
                max_hops=max_hops,
                max_distance_ratio=max_distance_ratio,
            )
            if len(paths) < 2:
                continue
            rows.append(
                {
                    "source_node": source,
                    "destination_node": destination,
                    "n_candidate_paths": len(paths),
                    "shortest_candidate_distance": paths[0][1],
                    "longest_candidate_distance": max(
                        distance for _, distance in paths
                    ),
                }
            )
    return rows
