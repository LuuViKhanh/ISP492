"""G09 — Build historical requests and graph-wide OD routing scenarios."""

from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.multihub_config import (
    EXPAND_TOPOLOGY_OD_PAIRS,
    MAX_PATH_DISTANCE_RATIO,
    MAX_PATH_HOPS,
    MIN_RECOMMENDED_UNIQUE_OD_PAIRS,
)
from config.multiuav_config import COMMON_PREFLIGHT_FEATURES
from config.paths import OUTPUT_DIR
from src.data_processing.target_calculation import circular_mean
from src.multihub.routing_graph import (
    build_supported_graph,
    enumerate_eligible_od_pairs,
    enumerate_feasible_paths,
    node_type_lookup,
)


def _safe_token(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_")


def _aggregate_conditions(group: pd.DataFrame) -> dict[str, float]:
    values: dict[str, float] = {}
    for feature in COMMON_PREFLIGHT_FEATURES:
        numeric = pd.to_numeric(group[feature], errors="coerce").dropna()
        if feature == "wind_dir_deg":
            values[feature] = (
                float(circular_mean(numeric.to_numpy(float)))
                if len(numeric)
                else float("nan")
            )
        else:
            values[feature] = float(numeric.mean()) if len(numeric) else float("nan")
    return values


def _condition_tables(
    features_df: pd.DataFrame,
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    by_flight: dict[str, dict[str, object]] = {}
    for flight_uid, group in features_df.groupby("flight_uid", sort=False):
        by_flight[str(flight_uid)] = {
            "dataset_id": str(group["dataset_id"].iloc[0]),
            "uav_type": str(group["uav_type"].iloc[0]),
            **_aggregate_conditions(group),
        }

    profiles: list[dict[str, object]] = []
    for (dataset_id, uav_type), group in features_df.groupby(
        ["dataset_id", "uav_type"],
        sort=True,
    ):
        profiles.append(
            {
                "dataset_id": str(dataset_id),
                "uav_type": str(uav_type),
                "condition_profile_id": (
                    f"PROFILE_{_safe_token(dataset_id)}_{_safe_token(uav_type)}"
                ),
                **_aggregate_conditions(group),
            }
        )
    return by_flight, profiles


def build_route_requests() -> pd.DataFrame:
    """Create mission-specific baselines plus one representative scenario/OD.

    Historical requests retain observed mission conditions and original paths.
    Topology scenarios use a platform/dataset median condition profile. They are
    not presented as independent observed missions; their role is to test route
    recommendation coverage across graph topology.
    """
    data_dir = Path(OUTPUT_DIR) / "data" / "multihub"
    mapping_df = pd.read_csv(data_dir / "trajectory_node_mapping.csv")
    features_df = pd.read_csv(data_dir / "segment_level_features.csv")
    edges_df = pd.read_csv(data_dir / "graph_edges.csv")
    nodes_df = pd.read_csv(data_dir / "graph_nodes.csv")

    by_flight, profiles = _condition_tables(features_df)
    types = node_type_lookup(nodes_df)
    requests: list[dict[str, object]] = []

    # Mission-specific requests are retained for the fair and observed-baseline
    # comparisons. Their count may exceed their number of unique OD pairs.
    for mapping in mapping_df.itertuples(index=False):
        flight_uid = str(mapping.flight_uid)
        raw_seq = getattr(mapping, "ordered_node_sequence", None)
        if pd.isna(raw_seq):
            continue
        node_sequence = [
            token.strip()
            for token in str(raw_seq).split(",")
            if token.strip()
        ]
        if len(node_sequence) < 2 or node_sequence[0] == node_sequence[-1]:
            continue

        condition = by_flight.get(flight_uid)
        if condition is None:
            continue
        source, destination = node_sequence[0], node_sequence[-1]
        graph = build_supported_graph(
            edges_df,
            uav_type=str(condition["uav_type"]),
            dataset_id=str(condition["dataset_id"]),
        )
        feasible = enumerate_feasible_paths(
            graph,
            source,
            destination,
            types=types,
            max_hops=MAX_PATH_HOPS,
            max_distance_ratio=MAX_PATH_DISTANCE_RATIO,
        )
        original_available = all(
            graph.has_edge(node_sequence[i], node_sequence[i + 1])
            for i in range(len(node_sequence) - 1)
        )
        edge_sequence = [
            f"{node_sequence[i]}_TO_{node_sequence[i + 1]}"
            for i in range(len(node_sequence) - 1)
        ]
        request = {
            "request_id": f"REQ_{flight_uid}",
            "request_kind": "HISTORICAL_MISSION",
            "od_pair_id": f"{source}__TO__{destination}",
            "source_flight": flight_uid,
            "condition_profile_id": flight_uid,
            "source_node": source,
            "destination_node": destination,
            "is_valid_od": True,
            "is_trivial_od": False,
            "has_observed_baseline": True,
            "original_path_available": bool(original_available),
            "original_node_sequence": ",".join(node_sequence),
            "original_edge_sequence": ",".join(edge_sequence),
            "dataset_id": condition["dataset_id"],
            "uav_type": condition["uav_type"],
            "n_candidate_paths": len(feasible),
            "n_paths_capped_2": min(len(feasible), 2),
            "is_routable": len(feasible) >= 2,
        }
        request.update(
            {feature: condition[feature] for feature in COMMON_PREFLIGHT_FEATURES}
        )
        requests.append(request)

    # One representative operating scenario is created for every eligible OD,
    # rather than replicating an OD for every historical condition profile.
    if EXPAND_TOPOLOGY_OD_PAIRS:
        for profile in profiles:
            graph = build_supported_graph(
                edges_df,
                uav_type=str(profile["uav_type"]),
                dataset_id=str(profile["dataset_id"]),
            )
            eligible_ods = enumerate_eligible_od_pairs(
                graph,
                types=types,
                max_hops=MAX_PATH_HOPS,
                max_distance_ratio=MAX_PATH_DISTANCE_RATIO,
            )
            for od in eligible_ods:
                source = str(od["source_node"])
                destination = str(od["destination_node"])
                request = {
                    "request_id": (
                        f"SCN_{_safe_token(profile['dataset_id'])}_"
                        f"{_safe_token(profile['uav_type'])}_"
                        f"{_safe_token(source)}_TO_{_safe_token(destination)}"
                    ),
                    "request_kind": "TOPOLOGY_OD",
                    "od_pair_id": f"{source}__TO__{destination}",
                    "source_flight": "",
                    "condition_profile_id": profile["condition_profile_id"],
                    "source_node": source,
                    "destination_node": destination,
                    "is_valid_od": True,
                    "is_trivial_od": False,
                    "has_observed_baseline": False,
                    "original_path_available": False,
                    "original_node_sequence": "",
                    "original_edge_sequence": "",
                    "dataset_id": profile["dataset_id"],
                    "uav_type": profile["uav_type"],
                    "n_candidate_paths": int(od["n_candidate_paths"]),
                    "n_paths_capped_2": 2,
                    "is_routable": True,
                }
                request.update(
                    {
                        feature: profile[feature]
                        for feature in COMMON_PREFLIGHT_FEATURES
                    }
                )
                requests.append(request)

    output = pd.DataFrame(requests)
    if output.empty:
        raise RuntimeError("G09 produced no valid route requests.")
    if output["request_id"].duplicated().any():
        duplicates = output.loc[
            output["request_id"].duplicated(), "request_id"
        ].tolist()
        raise RuntimeError(f"Duplicate G09 request IDs: {duplicates[:5]}")

    output.to_csv(data_dir / "route_requests.csv", index=False)
    topology = output[output["request_kind"] == "TOPOLOGY_OD"]
    historical = output[output["request_kind"] == "HISTORICAL_MISSION"]
    unique_ods = output.loc[output["is_routable"], "od_pair_id"].nunique()
    unique_topology_ods = topology["od_pair_id"].nunique()
    scope = pd.DataFrame(
        [
            {
                "n_historical_mission_requests": len(historical),
                "n_topology_od_scenarios": len(topology),
                "n_routing_scenarios": len(output),
                "n_routable_scenarios": int(output["is_routable"].sum()),
                "n_unique_routable_od_pairs": int(unique_ods),
                "n_unique_topology_od_pairs": int(unique_topology_ods),
                "minimum_recommended_unique_od_pairs": (
                    MIN_RECOMMENDED_UNIQUE_OD_PAIRS
                ),
            }
        ]
    )
    scope.to_csv(data_dir / "routing_experiment_scope.csv", index=False)

    if unique_topology_ods < MIN_RECOMMENDED_UNIQUE_OD_PAIRS:
        warnings.warn(
            "Expanded graph provides only "
            f"{unique_topology_ods} eligible OD pairs; target is at least "
            f"{MIN_RECOMMENDED_UNIQUE_OD_PAIRS}.",
            RuntimeWarning,
        )

    print(f"G09 historical mission requests: {len(historical)}")
    print(f"G09 expanded topology scenarios: {len(topology)}")
    print(f"G09 total routing scenarios: {len(output)}")
    print(f"G09 unique routable OD pairs: {unique_ods}")
    return output


if __name__ == "__main__":
    build_route_requests()
