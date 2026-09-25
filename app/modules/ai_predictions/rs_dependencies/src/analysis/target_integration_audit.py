"""Audit legacy vs gap-consistent energy/EE targets for DJI and VTOL.

Run BEFORE treating D07/D08 as final paper results.
Outputs to output/results/multiuav/:
  target_gap_audit_per_flight.csv
  target_energy_variant_comparison.csv
  vtol_ee_stability_by_configuration.csv (when VTOL data is available)
  target_audit_summary.csv
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.paths import DATA_PROCESSED_DIR, OUT_RES_MULTIUAV
from src.data_processing.target_calculation import (
    calculate_battery_energy,
    calculate_distance_haversine,
    calculate_distance_euclidean,
)

MAX_GAP_SEC = 30.0


def position_mode(df):
    x, y = df["position_x"], df["position_y"]
    geographic = (
        x.between(-180, 180).all()
        and y.between(-90, 90).all()
        and (x.max() - x.min()) < 1
        and (y.max() - y.min()) < 1
    )
    return "GEOGRAPHIC" if geographic else "LOCAL_XYZ"


def distance_for_flight(g, mode):
    kwargs = dict(times=g.time.values, max_gap_sec=MAX_GAP_SEC)
    if mode == "GEOGRAPHIC":
        return calculate_distance_haversine(
            g.position_y.values, g.position_x.values, g.position_z.values, **kwargs
        )
    return calculate_distance_euclidean(
        g.position_x.values, g.position_y.values, g.position_z.values, **kwargs
    )


def audit_dataset(path, dataset_id):
    if not path.exists():
        print(f"[SKIP] {dataset_id}: missing {path}")
        return pd.DataFrame()
    df = pd.read_csv(path)
    mode = position_mode(df)
    rows = []

    for flight, g in df.groupby("flight", sort=True):
        g = g.sort_values("time").reset_index(drop=True)
        if len(g) < 2:
            continue
        t = pd.to_numeric(g.time, errors="coerce").to_numpy(float)
        v = pd.to_numeric(g.battery_voltage, errors="coerce").to_numpy(float)
        i = pd.to_numeric(g.battery_current, errors="coerce").to_numpy(float)
        dt = np.diff(t)
        finite_dt = np.isfinite(dt)
        gap_mask = finite_dt & (dt > MAX_GAP_SEC)
        positive_dt = finite_dt & (dt > 0)

        dist = distance_for_flight(g, mode)
        e_legacy = calculate_battery_energy(v, i, t, normalize_sign=False, max_gap_sec=None)
        e_signed = calculate_battery_energy(v, i, t, normalize_sign=False, max_gap_sec=MAX_GAP_SEC)
        e_positive = calculate_battery_energy(v, np.maximum(i, 0), t, normalize_sign=False, max_gap_sec=MAX_GAP_SEC)
        e_absolute = calculate_battery_energy(v, i, t, normalize_sign=True, max_gap_sec=MAX_GAP_SEC)

        def ee(e):
            return dist / e if np.isfinite(e) and e > 0 else np.nan

        row = {
            "dataset_id": dataset_id,
            "flight": flight,
            "date": g["date"].iloc[0] if "date" in g else np.nan,
            "position_mode": mode,
            "n_samples": len(g),
            "n_gaps_gt_30": int(gap_mask.sum()),
            "max_gap_sec": float(np.nanmax(dt)) if np.any(finite_dt) else np.nan,
            "total_gap_duration_sec": float(np.nansum(dt[gap_mask])),
            "observed_duration_sec": float(np.nansum(dt[positive_dt])),
            "gap_duration_pct": (
                float(np.nansum(dt[gap_mask]) / np.nansum(dt[positive_dt]) * 100)
                if np.nansum(dt[positive_dt]) > 0 else np.nan
            ),
            "current_positive_pct": float(np.mean(i > 0) * 100),
            "current_negative_pct": float(np.mean(i < 0) * 100),
            "distance_gap_filtered_m": dist,
            "energy_legacy_all_gaps_wh": e_legacy,
            "energy_signed_gap_filtered_wh": e_signed,
            "energy_positive_only_gap_filtered_wh": e_positive,
            "energy_absolute_gap_filtered_wh": e_absolute,
            "ee_legacy_m_per_wh": ee(e_legacy),
            "ee_signed_gap_filtered_m_per_wh": ee(e_signed),
            "ee_positive_only_m_per_wh": ee(e_positive),
            "ee_absolute_m_per_wh": ee(e_absolute),
        }
        row["legacy_energy_inflation_pct_vs_signed"] = (
            (e_legacy - e_signed) / e_signed * 100 if e_signed > 0 else np.nan
        )
        row["ee_change_pct_signed_vs_legacy"] = (
            (row["ee_signed_gap_filtered_m_per_wh"] - row["ee_legacy_m_per_wh"])
            / row["ee_legacy_m_per_wh"] * 100
            if row["ee_legacy_m_per_wh"] > 0 else np.nan
        )
        for col in ["speed", "altitude", "payload", "pattern", "battery_discharge_rate"]:
            row[col] = g[col].iloc[0] if col in g.columns else np.nan
        rows.append(row)

    return pd.DataFrame(rows)


def main():
    out_dir = Path(OUT_RES_MULTIUAV)
    out_dir.mkdir(parents=True, exist_ok=True)
    dji = audit_dataset(Path(DATA_PROCESSED_DIR) / "cleaned_flight_data.csv", "DJI_M100")
    vtol = audit_dataset(Path(DATA_PROCESSED_DIR) / "vtol_cleaned_flight_data.csv", "VTOL")
    all_df = pd.concat([x for x in [dji, vtol] if not x.empty], ignore_index=True)
    if all_df.empty:
        raise FileNotFoundError("No processed DJI/VTOL telemetry found")

    all_df.to_csv(out_dir / "target_gap_audit_per_flight.csv", index=False)
    variant_cols = [
        "dataset_id", "flight", "energy_legacy_all_gaps_wh",
        "energy_signed_gap_filtered_wh", "energy_positive_only_gap_filtered_wh",
        "energy_absolute_gap_filtered_wh", "ee_legacy_m_per_wh",
        "ee_signed_gap_filtered_m_per_wh", "ee_positive_only_m_per_wh",
        "ee_absolute_m_per_wh",
    ]
    all_df[variant_cols].to_csv(out_dir / "target_energy_variant_comparison.csv", index=False)

    summary = all_df.groupby("dataset_id").agg(
        flights=("flight", "count"),
        flights_with_gap_gt30=("n_gaps_gt_30", lambda s: int((s > 0).sum())),
        total_gaps_gt30=("n_gaps_gt_30", "sum"),
        median_max_gap_sec=("max_gap_sec", "median"),
        mean_legacy_energy_inflation_pct=("legacy_energy_inflation_pct_vs_signed", "mean"),
        median_legacy_energy_inflation_pct=("legacy_energy_inflation_pct_vs_signed", "median"),
        mean_ee_change_pct=("ee_change_pct_signed_vs_legacy", "mean"),
    ).reset_index()
    summary.to_csv(out_dir / "target_audit_summary.csv", index=False)

    if not vtol.empty:
        group_cols = ["speed", "altitude", "payload", "pattern", "battery_discharge_rate"]
        stability = vtol.groupby(group_cols, dropna=False).agg(
            n=("flight", "count"),
            EE_old_mean=("ee_legacy_m_per_wh", "mean"),
            EE_old_std=("ee_legacy_m_per_wh", "std"),
            EE_corrected_mean=("ee_signed_gap_filtered_m_per_wh", "mean"),
            EE_corrected_std=("ee_signed_gap_filtered_m_per_wh", "std"),
        ).reset_index()
        stability["EE_old_CV"] = stability.EE_old_std / stability.EE_old_mean
        stability["EE_corrected_CV"] = stability.EE_corrected_std / stability.EE_corrected_mean
        stability["cv_reliable_n_ge_3"] = stability.n >= 3
        stability.to_csv(out_dir / "vtol_ee_stability_by_configuration.csv", index=False)

    print("\nTARGET INTEGRATION AUDIT SUMMARY")
    print(summary.to_string(index=False))
    print(f"\nSaved audit outputs to: {out_dir}")


if __name__ == "__main__":
    main()
