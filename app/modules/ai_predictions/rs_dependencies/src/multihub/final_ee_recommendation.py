"""Final RQ5 tables with fair model-based and observed-baseline comparisons."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.multihub_config import FINAL_MAX_DISTANCE_RATIO_VS_HISTORICAL
from config.multiuav_config import (
    CASE_COMBINED,
    CASE_DJI_ONLY,
    CASE_VTOL_ONLY,
    VALIDATION_CASES,
)
from config.paths import OUTPUT_DIR, REFERENCE_OUTPUT_DIR
from src.multihub.routing_graph import node_type_lookup


def _percent_gain(new_value: float, baseline_value: float) -> float:
    if not np.isfinite(baseline_value) or baseline_value <= 0:
        return float("nan")
    return float((new_value - baseline_value) / baseline_value * 100.0)


def _resolve_case(case: str | None) -> str:
    if case is not None:
        if case not in VALIDATION_CASES:
            raise ValueError(f"Unknown validation case: {case}")
        return case
    selected_path = (
        Path(REFERENCE_OUTPUT_DIR)
        / "results"
        / "multiuav"
        / "validation"
        / "selected_configuration.json"
    )
    with selected_path.open("r", encoding="utf-8") as stream:
        selected = json.load(stream)
    case = selected.get("selected_case")
    if case not in VALIDATION_CASES:
        raise ValueError(f"Invalid selected_case in {selected_path}: {case}")
    return str(case)


def _load_observed_flights(case: str) -> pd.DataFrame:
    feature_dir = Path(REFERENCE_OUTPUT_DIR) / "data" / "features"
    file_map = {
        CASE_DJI_ONLY: feature_dir / "flight_level_features.csv",
        CASE_VTOL_ONLY: feature_dir / "vtol_flight_level_features.csv",
        CASE_COMBINED: feature_dir / "multiuav_flight_level_features.csv",
    }
    path = file_map[case]
    if not path.exists():
        raise FileNotFoundError(f"Observed flight-level baseline not found: {path}")
    observed = pd.read_csv(path)
    if "flight_uid" not in observed.columns:
        dataset_id = "DJI_M100" if case == CASE_DJI_ONLY else "VTOL"
        observed["flight_uid"] = (
            dataset_id + "_" + observed["flight"].astype(str)
        )
    if "dataset_id" not in observed.columns:
        observed["dataset_id"] = (
            "DJI_M100" if case == CASE_DJI_ONLY else "VTOL"
        )
    required = [
        "flight_uid",
        "distance",
        "battery_consumed_wh",
        "energy_efficiency",
    ]
    missing = [column for column in required if column not in observed.columns]
    if missing:
        raise ValueError(f"{path} missing observed baseline fields: {missing}")
    return observed


def _hub_sequence(node_sequence: str, types: dict[str, str]) -> list[str]:
    nodes = [token for token in str(node_sequence).split(",") if token]
    return [
        node
        for node in nodes[1:-1]
        if "HUB" in types.get(node, "").upper()
    ]


def build_comparison_tables(
    requests_df: pd.DataFrame,
    paths_df: pd.DataFrame,
    observed_flights: pd.DataFrame,
    nodes_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return fair model/model, observed-supporting, and exclusion tables."""
    observed_lookup = observed_flights.set_index("flight_uid").to_dict("index")
    types = node_type_lookup(nodes_df)
    fair_rows: list[dict[str, object]] = []
    observed_rows: list[dict[str, object]] = []
    audit_rows: list[dict[str, object]] = []

    missions = requests_df[
        (requests_df["request_kind"] == "HISTORICAL_MISSION")
        & requests_df["is_routable"].astype(bool)
    ]
    for request in missions.to_dict(orient="records"):
        request_id = str(request["request_id"])
        mission_id = str(request["source_flight"])
        request_paths = paths_df[paths_df["request_id"] == request_id]
        valid = request_paths[
            request_paths["path_energy_is_valid"].astype(bool)
            & request_paths["path_platform_match"].astype(bool)
        ]
        original = valid[valid["is_original_path"].astype(bool)]
        if original.empty:
            audit_rows.append(
                {
                    "mission_id": mission_id,
                    "request_id": request_id,
                    "exclusion_reason": "predicted historical path unavailable",
                }
            )
            continue
        historical = original.iloc[0]
        observed = observed_lookup.get(mission_id)
        baseline_distance = (
            float(observed["distance"])
            if observed is not None
            else float(historical["total_distance"])
        )
        candidates = valid[
            valid["candidate_eligible"].astype(bool)
            & ~valid["is_original_path"].astype(bool)
            & valid["path_has_intermediate_hub"].astype(bool)
            & (
                pd.to_numeric(valid["total_distance"], errors="coerce")
                <= baseline_distance
                * float(FINAL_MAX_DISTANCE_RATIO_VS_HISTORICAL)
                + 1e-9
            )
        ]
        candidates = candidates.dropna(subset=["predicted_ee"])
        if candidates.empty:
            audit_rows.append(
                {
                    "mission_id": mission_id,
                    "request_id": request_id,
                    "exclusion_reason": "no plausible valid hypothetical route",
                }
            )
            continue

        candidate = candidates.loc[candidates["predicted_ee"].idxmax()]
        historical_energy = float(historical["predicted_total_energy"])
        historical_ee = float(historical["predicted_ee"])
        candidate_energy = float(candidate["predicted_total_energy"])
        candidate_ee = float(candidate["predicted_ee"])
        predicted_gain = _percent_gain(candidate_ee, historical_ee)
        use_candidate = bool(np.isfinite(predicted_gain) and predicted_gain > 0)
        recommended = candidate if use_candidate else historical
        candidate_hubs = _hub_sequence(candidate["node_sequence"], types)

        fair_rows.append(
            {
                "mission_id": mission_id,
                "request_id": request_id,
                "dataset_id": request.get("dataset_id"),
                "uav_type": request.get("uav_type"),
                "source_node": request.get("source_node"),
                "destination_node": request.get("destination_node"),
                "predicted_historical_path_id": historical["path_id"],
                "predicted_historical_route": historical["node_sequence"],
                "predicted_historical_distance": float(
                    historical["total_distance"]
                ),
                "predicted_historical_energy": historical_energy,
                "predicted_historical_ee": historical_ee,
                "predicted_candidate_path_id": candidate["path_id"],
                "predicted_candidate_route": candidate["node_sequence"],
                "predicted_candidate_hubs": ",".join(candidate_hubs),
                "predicted_candidate_distance": float(candidate["total_distance"]),
                "predicted_candidate_energy": candidate_energy,
                "predicted_candidate_ee": candidate_ee,
                "predicted_ee_gain_pct": predicted_gain,
                "recommended_option": (
                    "HYPOTHETICAL" if use_candidate else "HISTORICAL"
                ),
                "recommended_route": recommended["node_sequence"],
                "recommended_energy": float(
                    recommended["predicted_total_energy"]
                ),
                "recommended_ee": float(recommended["predicted_ee"]),
                "recommendation_decision": (
                    "USE_HYPOTHETICAL_ROUTE"
                    if use_candidate
                    else "RETAIN_HISTORICAL_ROUTE"
                ),
            }
        )

        if observed is not None:
            observed_ee = float(observed["energy_efficiency"])
            observed_rows.append(
                {
                    "mission_id": mission_id,
                    "request_id": request_id,
                    "observed_historical_distance": float(observed["distance"]),
                    "observed_historical_energy": float(
                        observed["battery_consumed_wh"]
                    ),
                    "observed_historical_ee": observed_ee,
                    "predicted_candidate_path_id": candidate["path_id"],
                    "predicted_candidate_route": candidate["node_sequence"],
                    "predicted_candidate_distance": float(
                        candidate["total_distance"]
                    ),
                    "predicted_candidate_energy": candidate_energy,
                    "predicted_candidate_ee": candidate_ee,
                    "observed_baseline_ee_gain_pct": _percent_gain(
                        candidate_ee, observed_ee
                    ),
                }
            )
        else:
            audit_rows.append(
                {
                    "mission_id": mission_id,
                    "request_id": request_id,
                    "exclusion_reason": "observed baseline unavailable for Table 2",
                }
            )

    return (
        pd.DataFrame(fair_rows),
        pd.DataFrame(observed_rows),
        pd.DataFrame(audit_rows),
    )


def build_final_ee_recommendations(
    case: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    case = _resolve_case(case)
    data_dir = Path(OUTPUT_DIR) / "data" / "multihub"
    result_dir = Path(OUTPUT_DIR) / "results" / "multihub" / "final_rq5"
    result_dir.mkdir(parents=True, exist_ok=True)

    requests_df = pd.read_csv(data_dir / "route_requests.csv")
    paths_df = pd.read_csv(data_dir / "candidate_paths.csv")
    nodes_df = pd.read_csv(data_dir / "graph_nodes.csv")
    observed_flights = _load_observed_flights(case)
    fair, observed, audit = build_comparison_tables(
        requests_df,
        paths_df,
        observed_flights,
        nodes_df,
    )

    fair.to_csv(
        result_dir / "fair_model_based_routing_comparison.csv", index=False
    )
    observed.to_csv(result_dir / "observed_baseline_validation.csv", index=False)
    audit.to_csv(result_dir / "mission_exclusion_audit.csv", index=False)
    comprehensive = fair.merge(
        observed,
        on=["mission_id", "request_id"],
        how="left",
        suffixes=("", "_observed_table"),
    )
    comprehensive.to_csv(result_dir / "mission_recommendations.csv", index=False)
    recommended_hypothetical = fair[
        fair["recommendation_decision"] == "USE_HYPOTHETICAL_ROUTE"
    ]
    hub_values = (
        recommended_hypothetical["predicted_candidate_hubs"]
        .fillna("")
        .astype(str)
        .str.split(",")
        .explode()
    )
    hub_values = hub_values[hub_values.str.strip() != ""].str.strip()
    hub_statistics = (
        hub_values.value_counts()
        .rename_axis("hub_id")
        .reset_index(name="recommendation_count")
    )
    if not hub_statistics.empty:
        hub_statistics["recommendation_frequency"] = (
            hub_statistics["recommendation_count"]
            / max(len(recommended_hypothetical), 1)
        )
    hub_statistics.to_csv(
        result_dir / "recommended_hub_statistics.csv", index=False
    )

    scope_path = data_dir / "routing_experiment_scope.csv"
    scope = (
        pd.read_csv(scope_path).iloc[0].to_dict()
        if scope_path.exists()
        else {}
    )
    summary = {
        "selected_case": case,
        **scope,
        "n_fair_model_based_comparisons": int(len(fair)),
        "n_observed_baseline_validations": int(len(observed)),
        "n_hypothetical_routes_recommended": int(
            (fair.get("recommendation_decision", pd.Series(dtype=str))
             == "USE_HYPOTHETICAL_ROUTE").sum()
        ),
        "recommendation_basis": (
            "predicted historical route versus predicted hypothetical route; "
            "same segment model, feature space, and energy aggregation"
        ),
        "observed_baseline_role": (
            "supporting external-realism check; not the primary fairness claim"
        ),
        "ee_definition": "route distance / route energy [m/Wh]",
        "energy_aggregation": "sum of predicted segment energy for both routes",
        "max_candidate_distance_ratio_vs_observed_historical": (
            FINAL_MAX_DISTANCE_RATIO_VS_HISTORICAL
        ),
    }
    with (result_dir / "summary.json").open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, ensure_ascii=False)
    pd.DataFrame([summary]).to_csv(result_dir / "summary.csv", index=False)

    print(f"Final RQ5 fair model-based comparisons: {len(fair)}")
    print(f"Final RQ5 observed-baseline validations: {len(observed)}")
    print(f"Saved final RQ5 tables to {result_dir}")
    return fair, observed


if __name__ == "__main__":
    build_final_ee_recommendations()
