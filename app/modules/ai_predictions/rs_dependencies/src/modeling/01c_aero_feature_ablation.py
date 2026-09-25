"""Run controlled aerodynamic feature ablation (F0/F1/F2/F3).

This wrapper intentionally runs the same D07 protocol for every feature set and
optionally D08 cross-domain transfer, then aggregates the resulting CSVs.
"""
import argparse
import subprocess
import sys
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from config.multiuav_config import FEATURE_SETS
from config.paths import OUT_RES_MULTIUAV


def run(script, feature_set, allow_endpoint_proxy=False):
    cmd = [sys.executable, str(PROJECT_ROOT / script), "--feature-set", feature_set]
    if allow_endpoint_proxy and feature_set != "F0_BASE":
        cmd.append("--allow-endpoint-proxy")
    print("RUN:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-cross-domain", action="store_true")
    parser.add_argument("--allow-endpoint-proxy", action="store_true", help="Diagnostic only; endpoint proxies are not final planned-route features")
    args = parser.parse_args()

    for fs in FEATURE_SETS:
        if fs != "F0_BASE" and not args.allow_endpoint_proxy:
            print(f"SKIP {fs}: requires true planned route geometry. Use --allow-endpoint-proxy for diagnostic-only ablation.")
            continue
        run("src/modeling/01_within_domain.py", fs, args.allow_endpoint_proxy)
        if args.include_cross_domain:
            run("src/modeling/02_cross_domain.py", fs, args.allow_endpoint_proxy)

    out_dir = Path(OUT_RES_MULTIUAV)
    d07 = []
    d08 = []
    for fs in FEATURE_SETS:
        p = out_dir / f"D07_within_domain_summary_{fs}.csv"
        if p.exists(): d07.append(pd.read_csv(p))
        p = out_dir / f"D08_cross_domain_summary_{fs}.csv"
        if p.exists(): d08.append(pd.read_csv(p))

    if d07:
        combined = pd.concat(d07, ignore_index=True)
        combined.to_csv(out_dir / "D07_aero_feature_ablation_all.csv", index=False)
        print("\nWITHIN-DOMAIN ABLATION")
        print(combined.sort_values(["Dataset", "WMAPE"]).to_string(index=False))
    if d08:
        combined = pd.concat(d08, ignore_index=True)
        combined.to_csv(out_dir / "D08_aero_feature_ablation_all.csv", index=False)
        print("\nCROSS-DOMAIN ABLATION")
        print(combined.sort_values(["Source", "Target", "WMAPE"]).to_string(index=False))


if __name__ == "__main__":
    main()
