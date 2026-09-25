"""G13 — Select distance, energy, EE, and balanced routing policies."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.multihub_config import (
    MULTI_OBJECTIVE_EE_WEIGHT,
    MULTI_OBJECTIVE_ENERGY_WEIGHT,
)
from config.paths import OUTPUT_DIR


SELECTION_COLUMNS = [
    "request_id",
    "request_kind",
    "od_pair_id",
    "P0_ORIGINAL_PATH_ID",
    "P1_SHORTEST_PATH_ID",
    "P2_MIN_ENERGY_PATH_ID",
    "P3_MAX_EE_PATH_ID",
    "P4_MULTI_OBJECTIVE_PATH_ID",
    "P4_MULTI_OBJECTIVE_SCORE",
    "P4_EE_WEIGHT",
    "P4_ENERGY_WEIGHT",
]


def _validate_weights(ee_weight: float, energy_weight: float) -> None:
    if ee_weight < 0 or energy_weight < 0:
        raise ValueError("Multi-objective weights must be non-negative.")
    if not np.isclose(ee_weight + energy_weight, 1.0, atol=1e-12):
        raise ValueError(
            "Multi-objective EE and Energy weights must sum to 1.0; "
            f"received {ee_weight} + {energy_weight}."
        )


def _minmax_neutral(values: pd.Series) -> pd.Series:
    """Min-max normalize; return neutral 0.5 when an objective is constant."""
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    minimum = float(numeric.min())
    maximum = float(numeric.max())
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        raise ValueError("Multi-objective scoring received non-finite values.")
    if np.isclose(maximum, minimum, atol=1e-12):
        return pd.Series(0.5, index=numeric.index, dtype=float)
    return (numeric - minimum) / (maximum - minimum)


def score_multi_objective_paths(
    alternatives: pd.DataFrame,
    *,
    ee_weight: float = MULTI_OBJECTIVE_EE_WEIGHT,
    energy_weight: float = MULTI_OBJECTIVE_ENERGY_WEIGHT,
) -> pd.DataFrame:
    """Score candidate routes using EE benefit and inverse Energy cost.

    Score = w_EE * EE_norm + w_Energy * (1 - Energy_norm)

    Normalization is request-local. Existing candidate eligibility and detour
    constraints are applied before this function, so the score cannot admit an
    otherwise implausible route.
    """
    _validate_weights(float(ee_weight), float(energy_weight))
    if alternatives.empty:
        return alternatives.copy()
    required = {"predicted_ee", "predicted_total_energy"}
    missing = required - set(alternatives.columns)
    if missing:
        raise ValueError(f"Candidate paths missing score fields: {sorted(missing)}")

    scored = alternatives.dropna(
        subset=["predicted_ee", "predicted_total_energy"]
    ).copy()
    if scored.empty:
        return scored
    scored["multi_objective_ee_norm"] = _minmax_neutral(
        scored["predicted_ee"]
    )
    scored["multi_objective_energy_norm"] = _minmax_neutral(
        scored["predicted_total_energy"]
    )
    scored["multi_objective_score"] = (
        float(ee_weight) * scored["multi_objective_ee_norm"]
        + float(energy_weight)
        * (1.0 - scored["multi_objective_energy_norm"])
    )
    scored["multi_objective_ee_weight"] = float(ee_weight)
    scored["multi_objective_energy_weight"] = float(energy_weight)
    return scored


def _select_balanced(scored: pd.DataFrame) -> pd.Series:
    if scored.empty:
        raise ValueError("Cannot select a Multi-objective route from no candidates.")
    # Deterministic tie-breaking: lower absolute energy, higher EE, shorter
    # distance, then stable path ID.
    return scored.sort_values(
        [
            "multi_objective_score",
            "predicted_total_energy",
            "predicted_ee",
            "total_distance",
            "path_id",
        ],
        ascending=[False, True, False, True, True],
        kind="mergesort",
    ).iloc[0]


def select_routing_policies(
    requests_df: pd.DataFrame,
    paths_df: pd.DataFrame,
    *,
    ee_weight: float = MULTI_OBJECTIVE_EE_WEIGHT,
    energy_weight: float = MULTI_OBJECTIVE_ENERGY_WEIGHT,
) -> pd.DataFrame:
    _validate_weights(float(ee_weight), float(energy_weight))
    valid = paths_df[
        paths_df["path_energy_is_valid"].astype(bool)
        & paths_df["path_platform_match"].astype(bool)
    ].copy()
    rows: list[dict[str, object]] = []

    for request in requests_df.to_dict(orient="records"):
        request_id = str(request["request_id"])
        request_paths = valid[valid["request_id"] == request_id]
        if request_paths.empty:
            continue

        original = request_paths[
            request_paths["is_original_path"].astype(bool)
        ]
        alternatives = request_paths[
            request_paths["candidate_eligible"].astype(bool)
            & ~request_paths["is_original_path"].astype(bool)
        ]
        alternatives = alternatives.dropna(
            subset=["predicted_total_energy", "predicted_ee"]
        )
        if alternatives.empty:
            continue

        shortest_id = alternatives.loc[
            alternatives["total_distance"].idxmin(), "path_id"
        ]
        min_energy_id = alternatives.loc[
            alternatives["predicted_total_energy"].idxmin(), "path_id"
        ]
        max_ee_id = alternatives.loc[
            alternatives["predicted_ee"].idxmax(), "path_id"
        ]
        balanced = _select_balanced(
            score_multi_objective_paths(
                alternatives,
                ee_weight=ee_weight,
                energy_weight=energy_weight,
            )
        )
        rows.append(
            {
                "request_id": request_id,
                "request_kind": request.get("request_kind", ""),
                "od_pair_id": request.get("od_pair_id", ""),
                "P0_ORIGINAL_PATH_ID": (
                    original.iloc[0]["path_id"] if not original.empty else None
                ),
                "P1_SHORTEST_PATH_ID": shortest_id,
                "P2_MIN_ENERGY_PATH_ID": min_energy_id,
                "P3_MAX_EE_PATH_ID": max_ee_id,
                "P4_MULTI_OBJECTIVE_PATH_ID": balanced["path_id"],
                "P4_MULTI_OBJECTIVE_SCORE": float(
                    balanced["multi_objective_score"]
                ),
                "P4_EE_WEIGHT": float(ee_weight),
                "P4_ENERGY_WEIGHT": float(energy_weight),
            }
        )
    return pd.DataFrame(rows, columns=SELECTION_COLUMNS)


def build_multi_objective_score_table(
    paths_df: pd.DataFrame,
    *,
    ee_weight: float = MULTI_OBJECTIVE_EE_WEIGHT,
    energy_weight: float = MULTI_OBJECTIVE_ENERGY_WEIGHT,
) -> pd.DataFrame:
    valid = paths_df[
        paths_df["path_energy_is_valid"].astype(bool)
        & paths_df["path_platform_match"].astype(bool)
        & paths_df["candidate_eligible"].astype(bool)
        & ~paths_df["is_original_path"].astype(bool)
    ]
    parts: list[pd.DataFrame] = []
    for _, alternatives in valid.groupby("request_id", sort=False):
        scored = score_multi_objective_paths(
            alternatives,
            ee_weight=ee_weight,
            energy_weight=energy_weight,
        )
        if not scored.empty:
            parts.append(scored)
    if not parts:
        return pd.DataFrame()
    scored_paths = pd.concat(parts, ignore_index=True)
    return scored_paths[
        [
            "request_id",
            "request_kind",
            "od_pair_id",
            "path_id",
            "total_distance",
            "predicted_total_energy",
            "predicted_ee",
            "multi_objective_ee_norm",
            "multi_objective_energy_norm",
            "multi_objective_score",
            "multi_objective_ee_weight",
            "multi_objective_energy_weight",
        ]
    ]


def run_routing_policies() -> pd.DataFrame:
    print("=" * 70)
    print("G13 — ROUTING POLICY SELECTION")
    print("=" * 70)
    data_dir = Path(OUTPUT_DIR) / "data" / "multihub"
    requests_df = pd.read_csv(data_dir / "route_requests.csv")
    paths_df = pd.read_csv(data_dir / "candidate_paths.csv")
    selections = select_routing_policies(requests_df, paths_df)
    selections.to_csv(data_dir / "policy_selections.csv", index=False)
    scores = build_multi_objective_score_table(paths_df)
    scores.to_csv(data_dir / "multi_objective_candidate_scores.csv", index=False)
    print(f"G13 policy selections: {len(selections)} scenarios")
    print(
        "G13 topology OD selections: "
        f"{int((selections['request_kind'] == 'TOPOLOGY_OD').sum())}"
    )
    print(
        "G13 policies: P1 Shortest, P2 Energy-first, P3 EE-first, "
        "P4 Multi-objective (0.50 EE / 0.50 Energy)"
    )
    return selections


if __name__ == "__main__":
    run_routing_policies()
