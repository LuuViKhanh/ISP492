"""Reviewer-facing P0-P4 route-baseline comparisons.

The primary table is model-to-model: the recorded historical route and every
candidate policy are evaluated by the same segment model. Observed historical
measurements are retained in a separate supporting table.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.multihub_config import MULTI_OBJECTIVE_WEIGHT_SENSITIVITY
from config.paths import OUTPUT_DIR
from src.multihub.routing_policies import select_routing_policies


STRATEGIES = (
    ("historical", "Historical", "Recorded route"),
    ("shortest", "Shortest-distance", "Minimize distance"),
    ("min_energy", "Minimum-energy", "Minimize predicted Wh"),
    ("max_ee", "Maximum-EE", "Maximize predicted EE"),
    ("multi_objective", "Multi-objective", "Balance EE and Energy"),
)

LEGACY_RESULT_FILES = (
    "historical_strategy_comparison.csv",
    "historical_strategy_summary.csv",
)


def _pct_change(new_value: float, baseline_value: float) -> float:
    if not np.isfinite(baseline_value) or baseline_value == 0:
        return float("nan")
    return float((new_value - baseline_value) / baseline_value * 100.0)


def _energy_saving(strategy_energy: float, baseline_energy: float) -> float:
    """Positive values mean that the strategy consumes less Energy."""
    return -_pct_change(strategy_energy, baseline_energy)


def _route_record(prefix: str, path: pd.Series) -> dict[str, object]:
    energy = float(path["predicted_total_energy"])
    ee = float(path["predicted_ee"])
    record = {
        f"{prefix}_path_id": path["path_id"],
        f"{prefix}_route": path["node_sequence"],
        f"{prefix}_distance": float(path["total_distance"]),
        f"{prefix}_predicted_energy": energy,
        f"{prefix}_predicted_ee": ee,
    }
    if prefix == "max_ee":
        record["max_ee"] = ee
    return record


def build_route_baseline_comparison(
    requests_df: pd.DataFrame,
    paths_df: pd.DataFrame,
    selections_df: pd.DataFrame,
    observed_baseline_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Place P0 Historical and P1-P4 metrics on the same mission row."""
    request_lookup = requests_df.set_index("request_id")
    path_lookup = paths_df.set_index("path_id", drop=False)
    observed_lookup: dict[str, dict[str, object]] = {}
    if observed_baseline_df is not None and not observed_baseline_df.empty:
        observed_lookup = observed_baseline_df.set_index("request_id").to_dict(
            orient="index"
        )

    historical_selections = selections_df[
        selections_df["request_kind"] == "HISTORICAL_MISSION"
    ]
    rows: list[dict[str, object]] = []
    for selection in historical_selections.to_dict(orient="records"):
        request_id = str(selection["request_id"])
        if request_id not in request_lookup.index:
            continue
        ids = {
            "historical": selection.get("P0_ORIGINAL_PATH_ID"),
            "shortest": selection.get("P1_SHORTEST_PATH_ID"),
            "min_energy": selection.get("P2_MIN_ENERGY_PATH_ID"),
            "max_ee": selection.get("P3_MAX_EE_PATH_ID"),
            "multi_objective": selection.get("P4_MULTI_OBJECTIVE_PATH_ID"),
        }
        if any(
            pd.isna(path_id) or path_id not in path_lookup.index
            for path_id in ids.values()
        ):
            continue

        request = request_lookup.loc[request_id]
        selected_paths = {
            name: path_lookup.loc[path_id] for name, path_id in ids.items()
        }
        historical = selected_paths["historical"]
        historical_distance = float(historical["total_distance"])
        historical_energy = float(historical["predicted_total_energy"])
        historical_ee = float(historical["predicted_ee"])
        observed = observed_lookup.get(request_id, {})
        row: dict[str, object] = {
            "mission_id": request["source_flight"],
            "request_id": request_id,
            "dataset_id": request.get("dataset_id"),
            "uav_type": request.get("uav_type"),
            "source_node": request["source_node"],
            "destination_node": request["destination_node"],
        }
        for prefix, path in selected_paths.items():
            row.update(_route_record(prefix, path))
        row.update(
            {
                "historical_observed_distance": observed.get(
                    "observed_historical_distance", np.nan
                ),
                "historical_observed_energy": observed.get(
                    "observed_historical_energy", np.nan
                ),
                "historical_observed_ee": observed.get(
                    "observed_historical_ee", np.nan
                ),
                "multi_objective_score": float(
                    selection["P4_MULTI_OBJECTIVE_SCORE"]
                ),
                "multi_objective_ee_weight": float(selection["P4_EE_WEIGHT"]),
                "multi_objective_energy_weight": float(
                    selection["P4_ENERGY_WEIGHT"]
                ),
            }
        )

        for prefix in ("shortest", "min_energy", "max_ee", "multi_objective"):
            path = selected_paths[prefix]
            row[f"{prefix}_distance_change_vs_historical_pct"] = _pct_change(
                float(path["total_distance"]), historical_distance
            )
            energy_saving_column = (
                "min_energy_saving_vs_historical_pct"
                if prefix == "min_energy"
                else f"{prefix}_energy_saving_vs_historical_pct"
            )
            row[energy_saving_column] = _energy_saving(
                float(path["predicted_total_energy"]), historical_energy
            )
            ee_gain_column = (
                "max_ee_gain_vs_historical_pct"
                if prefix == "max_ee"
                else f"{prefix}_ee_gain_vs_historical_pct"
            )
            row[ee_gain_column] = _pct_change(
                float(path["predicted_ee"]), historical_ee
            )

        row.update(
            {
                "shortest_equals_min_energy": ids["shortest"]
                == ids["min_energy"],
                "shortest_equals_max_ee": ids["shortest"] == ids["max_ee"],
                "min_energy_equals_max_ee": ids["min_energy"]
                == ids["max_ee"],
                "multi_objective_equals_energy_first": ids["multi_objective"]
                == ids["min_energy"],
                "multi_objective_equals_ee_first": ids["multi_objective"]
                == ids["max_ee"],
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def build_route_baseline_summary(comparison: pd.DataFrame) -> pd.DataFrame:
    """Return the five-row paper table: Historical plus P1-P4."""
    rows: list[dict[str, object]] = []
    for prefix, label, objective in STRATEGIES:
        if comparison.empty:
            continue
        row = {
            "strategy": label,
            "objective": objective,
            "n_missions": int(len(comparison)),
            "n_unique_selected_routes": int(
                comparison[f"{prefix}_route"].nunique()
            ),
            "mean_distance": float(comparison[f"{prefix}_distance"].mean()),
            "median_distance": float(
                comparison[f"{prefix}_distance"].median()
            ),
            "mean_predicted_energy": float(
                comparison[f"{prefix}_predicted_energy"].mean()
            ),
            "median_predicted_energy": float(
                comparison[f"{prefix}_predicted_energy"].median()
            ),
            "mean_predicted_ee": float(
                comparison[f"{prefix}_predicted_ee"].mean()
            ),
            "median_predicted_ee": float(
                comparison[f"{prefix}_predicted_ee"].median()
            ),
        }
        if prefix == "historical":
            row.update(
                {
                    "mean_distance_change_vs_historical_pct": 0.0,
                    "mean_energy_saving_vs_historical_pct": 0.0,
                    "mean_ee_gain_vs_historical_pct": 0.0,
                }
            )
        else:
            row.update(
                {
                    "mean_distance_change_vs_historical_pct": float(
                        comparison[
                            f"{prefix}_distance_change_vs_historical_pct"
                        ].mean()
                    ),
                    "mean_energy_saving_vs_historical_pct": float(
                        comparison[
                            "min_energy_saving_vs_historical_pct"
                            if prefix == "min_energy"
                            else f"{prefix}_energy_saving_vs_historical_pct"
                        ].mean()
                    ),
                    "mean_ee_gain_vs_historical_pct": float(
                        comparison[
                            "max_ee_gain_vs_historical_pct"
                            if prefix == "max_ee"
                            else f"{prefix}_ee_gain_vs_historical_pct"
                        ].mean()
                    ),
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


def build_observed_route_baseline_validation(
    comparison: pd.DataFrame,
) -> pd.DataFrame:
    """Compare observed P0 with predicted P1-P4 as a supporting check."""
    rows: list[dict[str, object]] = []
    for mission in comparison.to_dict(orient="records"):
        observed_energy = float(mission["historical_observed_energy"])
        observed_ee = float(mission["historical_observed_ee"])
        observed_distance = float(mission["historical_observed_distance"])
        if not all(np.isfinite(v) for v in (observed_energy, observed_ee)):
            continue
        row: dict[str, object] = {
            "mission_id": mission["mission_id"],
            "request_id": mission["request_id"],
            "observed_historical_route": mission["historical_route"],
            "observed_historical_distance": observed_distance,
            "observed_historical_energy": observed_energy,
            "observed_historical_ee": observed_ee,
            "comparison_role": (
                "supporting realism check; observed Historical versus predicted "
                "candidate policies"
            ),
        }
        for prefix in ("shortest", "min_energy", "max_ee", "multi_objective"):
            row[f"{prefix}_route"] = mission[f"{prefix}_route"]
            row[f"{prefix}_predicted_distance"] = mission[
                f"{prefix}_distance"
            ]
            row[f"{prefix}_predicted_energy"] = mission[
                f"{prefix}_predicted_energy"
            ]
            row[f"{prefix}_predicted_ee"] = mission[f"{prefix}_predicted_ee"]
            row[f"{prefix}_observed_baseline_energy_saving_pct"] = (
                _energy_saving(
                    float(mission[f"{prefix}_predicted_energy"]),
                    observed_energy,
                )
            )
            row[f"{prefix}_observed_baseline_ee_gain_pct"] = _pct_change(
                float(mission[f"{prefix}_predicted_ee"]), observed_ee
            )
        rows.append(row)
    return pd.DataFrame(rows)


def build_weight_sensitivity(
    requests_df: pd.DataFrame,
    paths_df: pd.DataFrame,
    baseline_comparison: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    request_lookup = requests_df.set_index("request_id")
    path_lookup = paths_df.set_index("path_id", drop=False)
    balanced_lookup = (
        baseline_comparison.set_index("request_id")["multi_objective_path_id"]
        .to_dict()
        if not baseline_comparison.empty
        else {}
    )
    detail_rows: list[dict[str, object]] = []
    for ee_weight, energy_weight in MULTI_OBJECTIVE_WEIGHT_SENSITIVITY:
        selections = select_routing_policies(
            requests_df,
            paths_df,
            ee_weight=float(ee_weight),
            energy_weight=float(energy_weight),
        )
        historical = selections[
            selections["request_kind"] == "HISTORICAL_MISSION"
        ]
        for selection in historical.to_dict(orient="records"):
            request_id = str(selection["request_id"])
            path_id = selection["P4_MULTI_OBJECTIVE_PATH_ID"]
            if (
                request_id not in request_lookup.index
                or path_id not in path_lookup.index
            ):
                continue
            request = request_lookup.loc[request_id]
            path = path_lookup.loc[path_id]
            detail_rows.append(
                {
                    "mission_id": request["source_flight"],
                    "request_id": request_id,
                    "ee_weight": float(ee_weight),
                    "energy_weight": float(energy_weight),
                    "selected_path_id": path_id,
                    "selected_route": path["node_sequence"],
                    "selected_distance": float(path["total_distance"]),
                    "selected_predicted_energy": float(
                        path["predicted_total_energy"]
                    ),
                    "selected_predicted_ee": float(path["predicted_ee"]),
                    "multi_objective_score": float(
                        selection["P4_MULTI_OBJECTIVE_SCORE"]
                    ),
                    "same_as_balanced_50_50": path_id
                    == balanced_lookup.get(request_id),
                    "same_as_energy_first": path_id
                    == selection["P2_MIN_ENERGY_PATH_ID"],
                    "same_as_ee_first": path_id
                    == selection["P3_MAX_EE_PATH_ID"],
                }
            )

    detail = pd.DataFrame(detail_rows)
    summary_rows: list[dict[str, object]] = []
    if not detail.empty:
        for (ee_weight, energy_weight), group in detail.groupby(
            ["ee_weight", "energy_weight"], sort=True
        ):
            summary_rows.append(
                {
                    "ee_weight": float(ee_weight),
                    "energy_weight": float(energy_weight),
                    "n_missions": int(len(group)),
                    "decision_consistency_vs_balanced_50_50": float(
                        group["same_as_balanced_50_50"].mean()
                    ),
                    "energy_first_selection_rate": float(
                        group["same_as_energy_first"].mean()
                    ),
                    "ee_first_selection_rate": float(
                        group["same_as_ee_first"].mean()
                    ),
                    "mean_selected_distance": float(
                        group["selected_distance"].mean()
                    ),
                    "mean_selected_predicted_energy": float(
                        group["selected_predicted_energy"].mean()
                    ),
                    "mean_selected_predicted_ee": float(
                        group["selected_predicted_ee"].mean()
                    ),
                }
            )
    return detail, pd.DataFrame(summary_rows)


def _load_observed_baseline(result_dir: Path) -> pd.DataFrame:
    path = result_dir / "final_rq5" / "observed_baseline_validation.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def build_and_save_strategy_comparisons(
    observed_baseline_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    data_dir = Path(OUTPUT_DIR) / "data" / "multihub"
    result_dir = Path(OUTPUT_DIR) / "results" / "multihub"
    result_dir.mkdir(parents=True, exist_ok=True)
    requests_df = pd.read_csv(data_dir / "route_requests.csv")
    paths_df = pd.read_csv(data_dir / "candidate_paths.csv")
    selections_df = pd.read_csv(data_dir / "policy_selections.csv")
    if observed_baseline_df is None:
        observed_baseline_df = _load_observed_baseline(result_dir)

    comparison = build_route_baseline_comparison(
        requests_df, paths_df, selections_df, observed_baseline_df
    )
    summary = build_route_baseline_summary(comparison)
    observed_validation = build_observed_route_baseline_validation(comparison)
    comparison.to_csv(result_dir / "route_baseline_comparison.csv", index=False)
    summary.to_csv(result_dir / "route_baseline_summary.csv", index=False)
    observed_validation.to_csv(
        result_dir / "observed_route_baseline_validation.csv", index=False
    )

    weight_detail, weight_summary = build_weight_sensitivity(
        requests_df, paths_df, comparison
    )
    weight_detail.to_csv(
        result_dir / "multi_objective_weight_sensitivity.csv", index=False
    )
    weight_summary.to_csv(
        result_dir / "multi_objective_weight_sensitivity_summary.csv",
        index=False,
    )

    # Superseded v3 tables are removed after their P0-P4 replacements exist.
    for legacy_name in LEGACY_RESULT_FILES:
        legacy_path = result_dir / legacy_name
        if legacy_path.exists():
            legacy_path.unlink()

    print(
        "Route baseline comparison: "
        f"{len(comparison)} missions with P0/P1/P2/P3/P4 metrics"
    )
    print(f"Route baseline summary: {len(summary)} strategies")
    print(f"Observed route-baseline support: {len(observed_validation)} missions")
    print(
        "Multi-objective weight sensitivity: "
        f"{len(weight_detail)} mission-weight evaluations"
    )
    return comparison


if __name__ == "__main__":
    build_and_save_strategy_comparisons()
