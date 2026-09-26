import pandas as pd
import numpy as np
import os
import sys
from pathlib import Path
from scipy.stats import ks_2samp, wasserstein_distance
import matplotlib.pyplot as plt
import seaborn as sns

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.paths import OUT_DATA_FEATURES, OUT_RES_MULTIUAV, OUT_FIG_MULTIUAV
from config.multiuav_config import COMMON_PREFLIGHT_FEATURES, ROUTE_PROXY_AERO_FEATURES, PRIMARY_TARGET

def compute_smd(dji_vals, vtol_vals):
    n1, n2 = len(dji_vals), len(vtol_vals)
    var1, var2 = np.var(dji_vals, ddof=1), np.var(vtol_vals, ddof=1)
    
    # Pooled standard deviation
    pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
    pooled_std = np.sqrt(pooled_var)
    
    if pooled_std == 0:
        return 0.0, 0.0
        
    mean1, mean2 = np.mean(dji_vals), np.mean(vtol_vals)
    # Signed SMD (VTOL vs DJI, positive means VTOL is larger)
    signed_smd = (mean2 - mean1) / pooled_std
    return signed_smd, abs(signed_smd)

def compute_oor_percentage(source_vals, target_vals):
    s_min, s_max = np.min(source_vals), np.max(source_vals)
    oor_count = np.sum((target_vals < s_min) | (target_vals > s_max))
    return (oor_count / len(target_vals)) * 100.0

def main():
    data_path = Path(OUT_DATA_FEATURES) / "multiuav_flight_level_features.csv"
    df = pd.read_csv(data_path)
    
    # Filter valid cohort (must remain strictly frozen, no outlier dropping)
    df = df[(df["battery_consumed_wh"] > 0) & (df["energy_efficiency"] > 0)].copy()
    
    dji_df = df[df["dataset_id"] == "DJI_M100"].copy()
    vtol_df = df[df["dataset_id"] == "VTOL"].copy()
    
    out_res_dir = Path(OUT_RES_MULTIUAV)
    os.makedirs(out_res_dir, exist_ok=True)
    
    out_fig_dir = Path(OUT_FIG_MULTIUAV) / "D10_domain_shift"
    os.makedirs(out_fig_dir, exist_ok=True)
    
    print(f"============================================================")
    print(f"RUNNING D10: DOMAIN SHIFT ANALYSIS")
    print(f"============================================================")
    
    features_to_analyze = list(dict.fromkeys(COMMON_PREFLIGHT_FEATURES + ROUTE_PROXY_AERO_FEATURES + ["distance", "battery_consumed_wh", "energy_efficiency"]))
    
    results = []
    
    for feat in features_to_analyze:
        print(f"Analyzing {feat}...")
        
        dji_vals = dji_df[feat].dropna().values
        vtol_vals = vtol_df[feat].dropna().values
        
        if len(dji_vals) == 0 or len(vtol_vals) == 0:
            continue
            
        # 1. KS Test
        ks_stat, ks_pval = ks_2samp(dji_vals, vtol_vals)
        
        # 2. SMD
        signed_smd, abs_smd = compute_smd(dji_vals, vtol_vals)
        
        # 3. Pooled Wasserstein Distance
        combined = np.concatenate([dji_vals, vtol_vals])
        mean_c, std_c = np.mean(combined), np.std(combined, ddof=1)
        
        if std_c > 0:
            dji_scaled = (dji_vals - mean_c) / std_c
            vtol_scaled = (vtol_vals - mean_c) / std_c
            w_dist = wasserstein_distance(dji_scaled, vtol_scaled)
        else:
            w_dist = 0.0
            
        # 4. Out-of-Range (OOR) Percentages
        oor_vtol_to_dji = compute_oor_percentage(source_vals=dji_vals, target_vals=vtol_vals)
        oor_dji_to_vtol = compute_oor_percentage(source_vals=vtol_vals, target_vals=dji_vals)
        
        results.append({
            "Feature": feat,
            "KS_Stat": ks_stat,
            "KS_Pvalue": ks_pval,
            "Signed_SMD": signed_smd,
            "Abs_SMD": abs_smd,
            "Wasserstein_Pooled": w_dist,
            "VTOL_OOR_pct": oor_vtol_to_dji,
            "DJI_OOR_pct": oor_dji_to_vtol
        })
        
        # Plotting
        plt.figure(figsize=(8, 5))
        sns.kdeplot(dji_vals, label="DJI_M100", fill=True, alpha=0.4, color="blue")
        sns.kdeplot(vtol_vals, label="VTOL", fill=True, alpha=0.4, color="orange")
        plt.title(f"Distribution Shift: {feat}")
        plt.xlabel(feat)
        plt.ylabel("Density")
        plt.legend()
        plt.tight_layout()
        plt.savefig(out_fig_dir / f"{feat}_shift.png")
        plt.close()
        
    results_df = pd.DataFrame(results)
    
    # Sort by Absolute SMD to highlight largest shifts
    results_df = results_df.sort_values(by="Abs_SMD", ascending=False)
    
    out_csv = out_res_dir / "D10_domain_shift_metrics.csv"
    results_df.to_csv(out_csv, index=False)
    
    print("\n[TOP 5 SHIFTS BY ABSOLUTE SMD]")
    print(results_df[["Feature", "Abs_SMD", "Wasserstein_Pooled", "VTOL_OOR_pct", "DJI_OOR_pct"]].head(5).to_string(index=False))
    
    print(f"\nSaved metrics to: {out_csv}")
    print(f"Saved density plots to: {out_fig_dir}")
    print(f"============================================================")
    print(f"D10 COMPLETED")
    print(f"============================================================")

if __name__ == "__main__":
    main()
