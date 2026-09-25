import pandas as pd
import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUTPUT_DIR
from config.multihub_config import (
    HUB_PLACEMENT_METHOD,
    ON_ROUTE_HUB_INTERVAL_M,
)
from src.multihub.load_multihub_data import load_and_normalize_data


EARTH_RADIUS_M = 6371000.0


def _step_distance_m(lon1, lat1, lon2, lat2):
    """2D haversine distance in meters."""
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2 - lon1)

    a = (
        np.sin(dp / 2.0) ** 2
        + np.cos(p1) * np.cos(p2) * np.sin(dl / 2.0) ** 2
    )
    return float(
        2.0
        * EARTH_RADIUS_M
        * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    )


def audit_topology(df):
    print("\n--- Topology Audit ---")
    print(f"Total flights: {df['flight_uid'].nunique()}")

    if "dataset_id" in df.columns:
        for dataset_id, dset in df.groupby("dataset_id"):
            routes = (
                dset["route"].dropna().unique()
                if "route" in dset.columns
                else ["Unknown"]
            )
            print(
                f"[{dataset_id}] flights={dset['flight_uid'].nunique()}, "
                f"labelled_routes={len(routes)}"
            )

    print(
        f"Using audited on-route placement interval: "
        f"{ON_ROUTE_HUB_INTERVAL_M:.1f} m"
    )
    print("----------------------\n")


def method_c_on_route(df, interval_m):
    """
    Place candidate nodes on each observed trajectory at a cumulative-distance
    interval.

    IMPORTANT:
    We persist `telemetry_index` and `candidate_order`. G04 must use these
    exact per-flight anchors instead of re-snapping every telemetry row to the
    global graph, otherwise nearest-node jitter can create hundreds of fake
    node visits.
    """
    candidates = []

    for flight_uid, group in df.groupby("flight_uid"):
        group = group.sort_values("time").reset_index(drop=True)
        if len(group) < 2:
            continue

        dataset_id = group["dataset_id"].iloc[0]
        uav_type = (
            group["uav_type"].iloc[0]
            if "uav_type" in group.columns
            else "UNKNOWN"
        )

        candidate_order = 0
        cumulative_distance_m = 0.0
        distance_since_last_hub = 0.0

        def append_row(i, node_type, cum_dist):
            nonlocal candidate_order
            candidates.append({
                "flight_uid": flight_uid,
                "dataset_id": dataset_id,
                "uav_type": uav_type,
                "candidate_order": candidate_order,
                "telemetry_index": int(i),
                "cumulative_distance_m": float(cum_dist),
                "position_x": float(group.loc[i, "position_x"]),
                "position_y": float(group.loc[i, "position_y"]),
                "position_z": float(group.loc[i, "position_z"]),
                "type": node_type,
                "source_method": "METHOD_C_ON_ROUTE",
            })
            candidate_order += 1

        append_row(0, "START", 0.0)

        for i in range(1, len(group)):
            step_m = _step_distance_m(
                group.loc[i - 1, "position_x"],
                group.loc[i - 1, "position_y"],
                group.loc[i, "position_x"],
                group.loc[i, "position_y"],
            )
            cumulative_distance_m += step_m
            distance_since_last_hub += step_m

            # Last point is always DESTINATION, not an intermediate HUB.
            if i < len(group) - 1 and distance_since_last_hub >= interval_m:
                append_row(i, "HUB", cumulative_distance_m)
                distance_since_last_hub = 0.0

        append_row(
            len(group) - 1,
            "DESTINATION",
            cumulative_distance_m,
        )

    return pd.DataFrame(candidates)


def detect_hub_nodes(df):
    print("Detecting candidate hub nodes...")
    audit_topology(df)

    if HUB_PLACEMENT_METHOD != "METHOD_C_ON_ROUTE":
        raise ValueError(
            "Current audited configuration requires "
            "HUB_PLACEMENT_METHOD='METHOD_C_ON_ROUTE'."
        )

    candidates_df = method_c_on_route(
        df,
        interval_m=ON_ROUTE_HUB_INTERVAL_M,
    )

    out_dir = OUTPUT_DIR / "data" / "multihub"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "hub_candidates.csv"
    candidates_df.to_csv(out_path, index=False)

    print(
        f"Saved {len(candidates_df)} candidate hub nodes to {out_path}"
    )
    print("Candidates by dataset:")
    print(candidates_df["dataset_id"].value_counts().to_string())

    per_flight = (
        candidates_df.groupby("dataset_id")
        .apply(
            lambda x: x.groupby("flight_uid").size().agg(
                ["mean", "median", "min", "max"]
            ),
            include_groups=False,
        )
    )
    print("Candidate anchors per flight:")
    print(per_flight.to_string())

    return candidates_df


if __name__ == "__main__":
    df = load_and_normalize_data()
    detect_hub_nodes(df)
