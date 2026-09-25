import pandas as pd
import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.paths import OUTPUT_DIR


def evaluate_routing():
    print("=" * 60)
    print("EVALUATING ROUTING RESULTS (G14)")
    print("=" * 60)

    sel_path = OUTPUT_DIR / "data" / "multihub" / "policy_selections.csv"
    paths_path = OUTPUT_DIR / "data" / "multihub" / "candidate_paths.csv"
    requests_path = OUTPUT_DIR / "data" / "multihub" / "route_requests.csv"

    sel_df = pd.read_csv(sel_path)
    paths_df = pd.read_csv(paths_path).set_index("path_id")
    requests_df = pd.read_csv(requests_path)

    valid_od_lookup = (
      requests_df
      .set_index("request_id")["is_valid_od"]
      .astype(bool)
      .to_dict()
   )

    if "is_routable" not in requests_df.columns:
        raise ValueError(
            "route_requests.csv has no is_routable column. "
            "Run the fixed G09 first."
        )

    routable_lookup = (
        requests_df
        .set_index("request_id")["is_routable"]
        .astype(bool)
        .to_dict()
    )

    results = []

    total_valid = 0
    all_disagreements = 0

    eligible_count = 0
    eligible_disagreements = 0
    eligible_savings = []

    cross_platform_count = 0

    for _, row in sel_df.iterrows():
        req_id = row["request_id"]

        # Exclude trivial source == destination missions
        if not bool(valid_od_lookup.get(req_id, False)):
           continue

        p0_id = row["P0_ORIGINAL_PATH_ID"]
        p1_id = row["P1_SHORTEST_PATH_ID"]
        p2_id = row["P2_MIN_ENERGY_PATH_ID"]
        p3_id = row["P3_MAX_EE_PATH_ID"]
        p4_id = row["P4_MULTI_OBJECTIVE_PATH_ID"]

        if any(pd.isna(path_id) for path_id in (p1_id, p2_id, p3_id, p4_id)):
            continue

        p1_path = paths_df.loc[p1_id]
        p2_path = paths_df.loc[p2_id]
        p3_path = paths_df.loc[p3_id]
        p4_path = paths_df.loc[p4_id]

        is_eligible = bool(routable_lookup.get(req_id, False))

        total_valid += 1

        disagreement = p1_id != p2_id

        if disagreement:
            all_disagreements += 1

        if is_eligible:
            eligible_count += 1
            if disagreement:
                eligible_disagreements += 1

        if not bool(p2_path["path_platform_match"]):
            cross_platform_count += 1

        e_shortest = float(p1_path["predicted_total_energy"])
        e_min = float(p2_path["predicted_total_energy"])
        e_max_ee = float(p3_path["predicted_total_energy"])
        e_multi = float(p4_path["predicted_total_energy"])
        ee_shortest = float(p1_path["predicted_ee"])
        ee_min_energy = float(p2_path["predicted_ee"])
        ee_max = float(p3_path["predicted_ee"])
        ee_multi = float(p4_path["predicted_ee"])

        saving_vs_shortest = (
            ((e_shortest - e_min) / e_shortest) * 100.0
            if e_shortest > 0
            else 0.0
        )

        if is_eligible:
            eligible_savings.append(saving_vs_shortest)

        res = {
            "request_id": req_id,
            "is_routing_eligible": is_eligible,
            "has_original": not pd.isna(p0_id),
            "shortest_path_id": p1_id,
            "min_energy_path_id": p2_id,
            "max_ee_path_id": p3_id,
            "multi_objective_path_id": p4_id,
            "shortest_vs_min_energy_disagree": disagreement,
            "shortest_vs_max_ee_disagree": p1_id != p3_id,
            "min_energy_vs_max_ee_disagree": p2_id != p3_id,
            "multi_objective_vs_energy_first_disagree": p4_id != p2_id,
            "multi_objective_vs_ee_first_disagree": p4_id != p3_id,
            "shortest_distance": float(p1_path["total_distance"]),
            "shortest_energy": e_shortest,
            "shortest_ee": ee_shortest,
            "min_energy_distance": float(p2_path["total_distance"]),
            "min_energy_energy": e_min,
            "min_energy_ee": ee_min_energy,
            "max_ee_distance": float(p3_path["total_distance"]),
            "max_ee_energy": e_max_ee,
            "max_ee": ee_max,
            "multi_objective_distance": float(p4_path["total_distance"]),
            "multi_objective_energy": e_multi,
            "multi_objective_ee": ee_multi,
            "multi_objective_score": float(row["P4_MULTI_OBJECTIVE_SCORE"]),
            "multi_objective_ee_weight": float(row["P4_EE_WEIGHT"]),
            "multi_objective_energy_weight": float(row["P4_ENERGY_WEIGHT"]),
            "energy_saving_vs_shortest_pct": saving_vs_shortest,
            "max_ee_gain_vs_shortest_pct": (
                ((ee_max - ee_shortest) / ee_shortest) * 100.0
                if ee_shortest > 0
                else np.nan
            ),
            "max_ee_energy_change_vs_min_energy_pct": (
                ((e_max_ee - e_min) / e_min) * 100.0
                if e_min > 0
                else np.nan
            ),
            "multi_objective_ee_gain_vs_energy_first_pct": (
                ((ee_multi - ee_min_energy) / ee_min_energy) * 100.0
                if ee_min_energy > 0
                else np.nan
            ),
            "multi_objective_energy_change_vs_ee_first_pct": (
                ((e_multi - e_max_ee) / e_max_ee) * 100.0
                if e_max_ee > 0
                else np.nan
            ),
            "recommended_path_platform_match": bool(
                p2_path["path_platform_match"]
            ),
            "all_strategy_paths_platform_match": all(
                bool(path["path_platform_match"])
                for path in (p1_path, p2_path, p3_path, p4_path)
            ),
        }

        # --------------------------------------------------------
        # Historical P0 comparison
        # This remains MODEL-IMPLIED because P0 and P2 energy are
        # predictions from the segment model.
        # --------------------------------------------------------
        if not pd.isna(p0_id):
            p0_path = paths_df.loc[p0_id]
            e_orig = float(p0_path["predicted_total_energy"])
            e_rec = e_min

            res["original_predicted_energy"] = e_orig
            res["energy_saving_vs_original_pct"] = (
                ((e_orig - e_rec) / e_orig) * 100.0
                if e_orig > 0
                else np.nan
            )
        else:
            res["original_predicted_energy"] = np.nan
            res["energy_saving_vs_original_pct"] = np.nan

        results.append(res)

    res_df = pd.DataFrame(results)

    all_disagreement_rate = (
        all_disagreements / total_valid * 100.0
        if total_valid
        else 0.0
    )

    eligible_disagreement_rate = (
        eligible_disagreements / eligible_count * 100.0
        if eligible_count
        else 0.0
    )

    mean_saving_all = (
        res_df["energy_saving_vs_shortest_pct"].mean()
        if not res_df.empty
        else np.nan
    )

    mean_saving_eligible = (
        float(np.mean(eligible_savings))
        if eligible_savings
        else np.nan
    )

    print("\n--- EVALUATION SUMMARY ---")
    print(f"Total Evaluated Requests: {total_valid}")
    print(
        f"Eligible Multi-Hub Requests (G09 >=2 graph paths): "
        f"{eligible_count}"
    )
    print(
        f"Shortest vs Min-Energy Disagreement Rate "
        f"(all requests): {all_disagreement_rate:.2f}%"
    )
    print(
        f"Shortest vs Min-Energy Disagreement Rate "
        f"(eligible requests): {eligible_disagreement_rate:.2f}%"
    )
    print(
        f"Cross-Platform Requests (Recommended Path): "
        f"{cross_platform_count}"
    )
    print(
        f"Average Energy Saving vs Shortest Path "
        f"(all requests): {mean_saving_all:.2f}%"
    )
    print(
        f"Average Energy Saving vs Shortest Path "
        f"(eligible requests): {mean_saving_eligible:.2f}%"
    )

    with_orig = res_df.dropna(
        subset=["energy_saving_vs_original_pct"]
    )

    if not with_orig.empty:
        mean_saving_orig = (
            with_orig["energy_saving_vs_original_pct"].mean()
        )
        print(
            f"Average Model-Implied Energy Saving vs Predicted "
            f"Original Path: {mean_saving_orig:.2f}% "
            f"(over {len(with_orig)} flights)"
        )

    out_dir = OUTPUT_DIR / "results" / "multihub"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "policy_comparison.csv"
    res_df.to_csv(out_path, index=False)

    print(f"\nSaved evaluation to {out_path}")


if __name__ == "__main__":
    evaluate_routing()
