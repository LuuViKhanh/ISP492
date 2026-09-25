"""D13 — Complete evidence table + guarded final model freeze.

D13 follows the agreed Multi-UAV plan:
- D07: within-domain evidence
- D08: cross-domain transfer evidence
- D09: Direct-EE vs Energy-First target-strategy evidence
- D11: Universal combined-training evidence
- D12: Platform-aware evidence

D10 is explanatory domain-shift analysis and is intentionally NOT included in
the numeric model rank.

A final model is frozen only after
decisions/final_model_decision.yaml is explicitly completed and approved by
the researchers.

Important:
- Only UNIVERSAL and PLATFORM_AWARE architectures are valid here, matching the
  agreed plan.
- Evidence_Rank_Sum is a screening aid only. It does not auto-approve a model.
"""

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml

from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.ensemble import ExtraTreesRegressor

from xgboost import XGBRegressor
from catboost import CatBoostRegressor


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.paths import OUT_DATA_FEATURES, OUT_BEST_MODEL, OUT_RES_MULTIUAV
from config.multiuav_config import (
    FEATURE_SETS,
    DEFAULT_FEATURE_SET,
    PRIMARY_TARGET,
    get_feature_set,
)

DECISION_PATH = PROJECT_ROOT / "decisions" / "final_model_decision.yaml"


def model_factory(name):
    models = {
        "LinearRegression": LinearRegression(),
        "Ridge": Ridge(alpha=1.0),
        "ExtraTrees": ExtraTreesRegressor(
            n_estimators=100,
            random_state=42,
        ),
        "XGBoost": XGBRegressor(
            n_estimators=100,
            learning_rate=0.1,
            random_state=42,
            n_jobs=-1,
        ),
        "CatBoost": CatBoostRegressor(
            n_estimators=100,
            learning_rate=0.1,
            random_state=42,
            verbose=0,
        ),
    }
    if name not in models:
        raise KeyError(f"Unknown model {name}")
    return models[name]


def _read_csv_if_exists(path):
    path = Path(path)
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _scalar(df, column):
    """Return first numeric scalar or NaN."""
    if df.empty or column not in df.columns:
        return np.nan
    s = pd.to_numeric(df[column], errors="coerce").dropna()
    return float(s.iloc[0]) if not s.empty else np.nan


def _mean(values):
    values = [v for v in values if pd.notna(v)]
    return float(np.mean(values)) if values else np.nan


def _min_with_label(universal, platform_aware):
    """Return best combined WMAPE and matching architecture candidate."""
    candidates = []
    if pd.notna(universal):
        candidates.append(("UNIVERSAL", float(universal)))
    if pd.notna(platform_aware):
        candidates.append(("PLATFORM_AWARE", float(platform_aware)))
    if not candidates:
        return np.nan, None
    arch, value = min(candidates, key=lambda x: x[1])
    return value, arch


def target_strategy_table(out):
    """Compile D09 evidence in the primary EE space and energy space.

    Strategy A (Direct EE):
        X -> EE
        Energy is derived using oracle historical distance only for this
        diagnostic experiment.

    Strategy B (Energy-First):
        X -> Energy
        EE is derived using oracle historical distance only for this
        diagnostic experiment.

    This table is evidence for target formulation; it is NOT a deployment test.
    """
    p9 = out / "D09_target_strategy_summary.csv"
    d9 = _read_csv_if_exists(p9)
    if d9.empty:
        return pd.DataFrame()

    rows = []
    for model in sorted(d9["model"].dropna().unique()):
        z = d9[d9["model"] == model].copy()

        direct_ee_macro = (
            pd.to_numeric(z["EE_Native_WMAPE"], errors="coerce").mean()
            if "EE_Native_WMAPE" in z.columns else np.nan
        )
        energy_first_to_ee_macro = (
            pd.to_numeric(z["EE_Derived_WMAPE"], errors="coerce").mean()
            if "EE_Derived_WMAPE" in z.columns else np.nan
        )

        energy_first_energy_macro = (
            pd.to_numeric(z["Energy_Native_WMAPE"], errors="coerce").mean()
            if "Energy_Native_WMAPE" in z.columns else np.nan
        )
        direct_ee_to_energy_macro = (
            pd.to_numeric(z["Energy_Derived_WMAPE"], errors="coerce").mean()
            if "Energy_Derived_WMAPE" in z.columns else np.nan
        )

        # Per-platform evidence is retained because D09 showed platform-dependent
        # behavior.
        dji = z[z["dataset_id"] == "DJI_M100"]
        vtol = z[z["dataset_id"] == "VTOL"]

        rows.append({
            "Model": model,

            "D09_DirectEE_EE_Macro_WMAPE": direct_ee_macro,
            "D09_EnergyFirst_to_EE_Macro_WMAPE": energy_first_to_ee_macro,
            "D09_EnergyFirst_Energy_Macro_WMAPE": energy_first_energy_macro,
            "D09_DirectEE_to_Energy_Macro_WMAPE": direct_ee_to_energy_macro,

            "D09_DJI_DirectEE_EE_WMAPE": _scalar(dji, "EE_Native_WMAPE"),
            "D09_DJI_EnergyFirst_to_EE_WMAPE": _scalar(dji, "EE_Derived_WMAPE"),
            "D09_VTOL_DirectEE_EE_WMAPE": _scalar(vtol, "EE_Native_WMAPE"),
            "D09_VTOL_EnergyFirst_to_EE_WMAPE": _scalar(vtol, "EE_Derived_WMAPE"),

            "D09_DJI_EnergyFirst_Energy_WMAPE": _scalar(dji, "Energy_Native_WMAPE"),
            "D09_DJI_DirectEE_to_Energy_WMAPE": _scalar(dji, "Energy_Derived_WMAPE"),
            "D09_VTOL_EnergyFirst_Energy_WMAPE": _scalar(vtol, "Energy_Native_WMAPE"),
            "D09_VTOL_DirectEE_to_Energy_WMAPE": _scalar(vtol, "Energy_Derived_WMAPE"),
        })

    return pd.DataFrame(rows)


def evidence_table(out):
    """Build complete D07/D08/D09/D11/D12 evidence per model.

    D07/D08 are model evidence.
    D11 and D12 compare the two agreed deployment architectures:
      - UNIVERSAL
      - PLATFORM_AWARE

    D09 is included as target-strategy evidence but is not mixed blindly into
    the main model rank because it answers a different research question.
    """
    d11 = _read_csv_if_exists(out / "D11_combined_training_summary.csv")
    d12 = _read_csv_if_exists(out / "D12_platform_aware_summary.csv")
    d09 = target_strategy_table(out)

    rows = []

    for fs in FEATURE_SETS:
        p7 = out / f"D07_within_domain_summary_{fs}.csv"
        p8 = out / f"D08_cross_domain_summary_{fs}.csv"

        d7 = _read_csv_if_exists(p7)
        d8 = _read_csv_if_exists(p8)

        u = d11[d11["FeatureSet"] == fs].copy() if (
            not d11.empty and "FeatureSet" in d11.columns
        ) else pd.DataFrame()

        p = d12[d12["FeatureSet"] == fs].copy() if (
            not d12.empty and "FeatureSet" in d12.columns
        ) else pd.DataFrame()

        models = set()

        if not d7.empty and "Model" in d7.columns:
            models.update(d7["Model"].dropna().unique())
        if not d8.empty and "Model" in d8.columns:
            models.update(d8["Model"].dropna().unique())
        if not u.empty and "model" in u.columns:
            models.update(u["model"].dropna().unique())
        if not p.empty and "model" in p.columns:
            models.update(p["model"].dropna().unique())

        # D09 currently uses the configured default feature set.
        if fs == DEFAULT_FEATURE_SET and not d09.empty:
            models.update(d09["Model"].dropna().unique())

        for model in sorted(models):
            r = {
                "FeatureSet": fs,
                "Model": model,
                "PrimaryTarget": PRIMARY_TARGET,
            }

            # ------------------------------------------------------------
            # D07 — within-domain
            # ------------------------------------------------------------
            if not d7.empty:
                z = d7[d7["Model"] == model]

                dji = z[z["Dataset"] == "DJI_M100"]
                vtol = z[z["Dataset"] == "VTOL"]

                r["D07_DJI_WMAPE"] = _scalar(dji, "WMAPE")
                r["D07_DJI_R2"] = _scalar(dji, "R2")
                r["D07_DJI_Bias"] = _scalar(dji, "Bias")

                r["D07_VTOL_WMAPE"] = _scalar(vtol, "WMAPE")
                r["D07_VTOL_R2"] = _scalar(vtol, "R2")
                r["D07_VTOL_Bias"] = _scalar(vtol, "Bias")

                r["Within_Macro_WMAPE"] = _mean([
                    r["D07_DJI_WMAPE"],
                    r["D07_VTOL_WMAPE"],
                ])
                r["Within_Macro_R2"] = _mean([
                    r["D07_DJI_R2"],
                    r["D07_VTOL_R2"],
                ])
                r["Within_MeanAbsBias"] = _mean([
                    abs(r["D07_DJI_Bias"]) if pd.notna(r["D07_DJI_Bias"]) else np.nan,
                    abs(r["D07_VTOL_Bias"]) if pd.notna(r["D07_VTOL_Bias"]) else np.nan,
                ])

            # ------------------------------------------------------------
            # D08 — cross-domain transfer
            # ------------------------------------------------------------
            if not d8.empty:
                z = d8[d8["Model"] == model]

                dji_to_vtol = z[
                    (z["Source"] == "DJI_M100") &
                    (z["Target"] == "VTOL")
                ]
                vtol_to_dji = z[
                    (z["Source"] == "VTOL") &
                    (z["Target"] == "DJI_M100")
                ]

                r["D08_DJI_to_VTOL_WMAPE"] = _scalar(dji_to_vtol, "WMAPE")
                r["D08_DJI_to_VTOL_R2"] = _scalar(dji_to_vtol, "R2")
                r["D08_DJI_to_VTOL_Bias"] = _scalar(dji_to_vtol, "Bias")

                r["D08_VTOL_to_DJI_WMAPE"] = _scalar(vtol_to_dji, "WMAPE")
                r["D08_VTOL_to_DJI_R2"] = _scalar(vtol_to_dji, "R2")
                r["D08_VTOL_to_DJI_Bias"] = _scalar(vtol_to_dji, "Bias")

                r["Cross_Macro_WMAPE"] = _mean([
                    r["D08_DJI_to_VTOL_WMAPE"],
                    r["D08_VTOL_to_DJI_WMAPE"],
                ])
                r["Cross_Macro_R2"] = _mean([
                    r["D08_DJI_to_VTOL_R2"],
                    r["D08_VTOL_to_DJI_R2"],
                ])
                r["Cross_MeanAbsBias"] = _mean([
                    abs(r["D08_DJI_to_VTOL_Bias"])
                    if pd.notna(r["D08_DJI_to_VTOL_Bias"]) else np.nan,
                    abs(r["D08_VTOL_to_DJI_Bias"])
                    if pd.notna(r["D08_VTOL_to_DJI_Bias"]) else np.nan,
                ])

            # ------------------------------------------------------------
            # D11 — Universal combined model
            # ------------------------------------------------------------
            if not u.empty:
                zu = u[u["model"] == model]

                r["Universal_DJI_WMAPE"] = _scalar(zu, "DJI_WMAPE")
                r["Universal_VTOL_WMAPE"] = _scalar(zu, "VTOL_WMAPE")
                r["Universal_Macro_WMAPE"] = _scalar(zu, "Macro_WMAPE")

                r["Universal_DJI_R2"] = _scalar(zu, "DJI_R2")
                r["Universal_VTOL_R2"] = _scalar(zu, "VTOL_R2")
                r["Universal_Combined_R2"] = _scalar(zu, "Combined_R2")

            # ------------------------------------------------------------
            # D12 — Platform-aware combined model
            # ------------------------------------------------------------
            if not p.empty:
                zp = p[p["model"] == model]

                r["PlatformAware_DJI_WMAPE"] = _scalar(zp, "DJI_WMAPE")
                r["PlatformAware_VTOL_WMAPE"] = _scalar(zp, "VTOL_WMAPE")
                r["PlatformAware_Macro_WMAPE"] = _scalar(zp, "Macro_WMAPE")

                r["PlatformAware_DJI_R2"] = _scalar(zp, "DJI_R2")
                r["PlatformAware_VTOL_R2"] = _scalar(zp, "VTOL_R2")
                r["PlatformAware_Combined_R2"] = _scalar(zp, "Combined_R2")

            # Architecture evidence for the same algorithm.
            best_combined, preferred_arch = _min_with_label(
                r.get("Universal_Macro_WMAPE", np.nan),
                r.get("PlatformAware_Macro_WMAPE", np.nan),
            )
            r["Best_Combined_Macro_WMAPE"] = best_combined
            r["Combined_Architecture_Candidate"] = preferred_arch

            if (
                pd.notna(r.get("Universal_Macro_WMAPE", np.nan))
                and pd.notna(r.get("PlatformAware_Macro_WMAPE", np.nan))
            ):
                # Negative means platform-aware improved WMAPE.
                r["PlatformAware_Delta_Macro_WMAPE"] = (
                    r["PlatformAware_Macro_WMAPE"]
                    - r["Universal_Macro_WMAPE"]
                )

            # ------------------------------------------------------------
            # D09 — target strategy evidence
            # ------------------------------------------------------------
            if fs == DEFAULT_FEATURE_SET and not d09.empty:
                z9 = d09[d09["Model"] == model]
                if not z9.empty:
                    for c in z9.columns:
                        if c != "Model":
                            r[c] = z9.iloc[0][c]

            rows.append(r)

    ev = pd.DataFrame(rows)

    if ev.empty:
        return ev

    # Main screening rank follows the agreed final-selection criteria:
    # within-domain + cross-domain + combined performance + bias.
    # D09 is NOT mixed into this rank because target-strategy selection is a
    # separate methodological decision.
    rank_inputs = [
        "Within_Macro_WMAPE",
        "Cross_Macro_WMAPE",
        "Cross_MeanAbsBias",
        "Best_Combined_Macro_WMAPE",
    ]

    rank_cols = []
    for c in rank_inputs:
        if c in ev.columns:
            rc = c + "_Rank"
            ev[rc] = ev[c].rank(method="min", na_option="bottom")
            rank_cols.append(rc)

    if rank_cols:
        ev["Evidence_Rank_Sum"] = ev[rank_cols].sum(axis=1)

    return ev


def _write_decision_template():
    DECISION_PATH.parent.mkdir(parents=True, exist_ok=True)
    DECISION_PATH.write_text(
        f"""# Fill only after reviewing D07-D12 evidence.
# D10 is explanatory domain-shift evidence and is not part of the rank.
# D09 should be reviewed when confirming the target strategy.
approved: false
selected_feature_set: {DEFAULT_FEATURE_SET}
selected_model: null
architecture: null  # UNIVERSAL | PLATFORM_AWARE
target: {PRIMARY_TARGET}
rationale: null
approved_by: null
approved_at: null
""",
        encoding="utf-8",
    )


def main():
    out = Path(OUT_RES_MULTIUAV)
    out.mkdir(parents=True, exist_ok=True)

    # Complete D07/D08/D09/D11/D12 evidence.
    ev = evidence_table(out)
    ev.to_csv(out / "D13_model_evidence_table.csv", index=False)

    # D09 target-strategy evidence is also exported separately for clarity.
    d09_ev = target_strategy_table(out)
    if not d09_ev.empty:
        d09_ev.to_csv(out / "D13_target_strategy_evidence.csv", index=False)

    print("\nD13 COMPLETE MODEL EVIDENCE TABLE")
    if ev.empty:
        print("No evidence rows found. Check D07/D08/D11/D12 outputs.")
    else:
        display_cols = [
            "FeatureSet",
            "Model",
            "Within_Macro_WMAPE",
            "Cross_Macro_WMAPE",
            "Cross_MeanAbsBias",
            "Universal_Macro_WMAPE",
            "PlatformAware_Macro_WMAPE",
            "PlatformAware_Delta_Macro_WMAPE",
            "Best_Combined_Macro_WMAPE",
            "Combined_Architecture_Candidate",
            "Evidence_Rank_Sum",
        ]
        display_cols = [c for c in display_cols if c in ev.columns]

        show = (
            ev.sort_values("Evidence_Rank_Sum")
            if "Evidence_Rank_Sum" in ev.columns
            else ev
        )
        print(show[display_cols].head(20).to_string(index=False))

    if not d09_ev.empty:
        print("\nD13 TARGET-STRATEGY EVIDENCE FROM D09")
        cols = [
            "Model",
            "D09_DirectEE_EE_Macro_WMAPE",
            "D09_EnergyFirst_to_EE_Macro_WMAPE",
            "D09_EnergyFirst_Energy_Macro_WMAPE",
            "D09_DirectEE_to_Energy_Macro_WMAPE",
        ]
        cols = [c for c in cols if c in d09_ev.columns]
        print(d09_ev[cols].sort_values("D09_DirectEE_EE_Macro_WMAPE").to_string(index=False))

    if not DECISION_PATH.exists():
        _write_decision_template()
        print(
            f"\nCreated decision gate: {DECISION_PATH}. "
            "Review D13_model_evidence_table.csv and "
            "D13_target_strategy_evidence.csv, fill the YAML, then rerun D13."
        )
        return

    decision = yaml.safe_load(
        DECISION_PATH.read_text(encoding="utf-8")
    ) or {}

    if not decision.get("approved", False):
        print(
            f"\nD13 STOPPED: final decision not approved in {DECISION_PATH}"
        )
        print(
            "This is expected. Review the complete D07/D08/D09/D11/D12 "
            "evidence before setting approved: true."
        )
        return

    fs = decision.get("selected_feature_set")
    model_name = decision.get("selected_model")
    arch = decision.get("architecture")

    if (
        fs not in FEATURE_SETS
        or not model_name
        or arch not in {"UNIVERSAL", "PLATFORM_AWARE"}
    ):
        raise ValueError(
            "Decision must specify a valid selected_feature_set, "
            "selected_model, and architecture "
            "(UNIVERSAL or PLATFORM_AWARE)."
        )

    if decision.get("target") != PRIMARY_TARGET:
        raise ValueError(
            f"Decision target must match PRIMARY_TARGET={PRIMARY_TARGET}"
        )

    # Guard against approving a model/feature-set pair for which no D13 evidence
    # was compiled.
    chosen = ev[
        (ev["FeatureSet"] == fs) &
        (ev["Model"] == model_name)
    ]
    if chosen.empty:
        raise ValueError(
            f"No D13 evidence found for feature_set={fs}, model={model_name}"
        )

    # Also require evidence for the selected architecture.
    arch_metric = (
        "Universal_Macro_WMAPE"
        if arch == "UNIVERSAL"
        else "PlatformAware_Macro_WMAPE"
    )
    if (
        arch_metric not in chosen.columns
        or pd.isna(chosen.iloc[0].get(arch_metric, np.nan))
    ):
        raise ValueError(
            f"No {arch} evidence found for model={model_name}, "
            f"feature_set={fs}. Run the corresponding D11/D12 experiment first."
        )

    features = get_feature_set(fs)

    df = pd.read_csv(
        Path(OUT_DATA_FEATURES) / "multiuav_flight_level_features.csv"
    )
    df = df[
        (df["battery_consumed_wh"] > 0)
        & (df["energy_efficiency"] > 0)
    ].copy()

    if arch == "PLATFORM_AWARE":
        df["is_vtol"] = (
            df["uav_type"] == "VTOL_FIXED_WING"
        ).astype(int)
        features = features + ["is_vtol"]

    model = model_factory(model_name)

    steps = [
        ("imputer", SimpleImputer(strategy="median")),
    ]
    if model_name in {"LinearRegression", "Ridge"}:
        steps.append(("scaler", StandardScaler()))

    steps.append(("model", model))
    pipe = Pipeline(steps)
    pipe.fit(df[features], df[PRIMARY_TARGET])

    Path(OUT_BEST_MODEL).mkdir(parents=True, exist_ok=True)

    model_path = Path(OUT_BEST_MODEL) / "multiuav_final_model.pkl"

    artifact = {
        "pipeline": pipe,
        "features": features,
        "feature_set": fs,
        "architecture": arch,
        "target": PRIMARY_TARGET,
        "model_name": model_name,
    }

    joblib.dump(artifact, model_path)

    meta = {
        k: v for k, v in artifact.items()
        if k != "pipeline"
    }
    meta["rationale"] = decision.get("rationale")
    meta["n_flights"] = len(df)

    # Save the evidence values used for the approved decision.
    evidence_row = chosen.iloc[0].to_dict()
    meta["selected_evidence"] = {
        k: (
            None
            if pd.isna(v)
            else v.item()
            if isinstance(v, np.generic)
            else v
        )
        for k, v in evidence_row.items()
    }

    (
        Path(OUT_BEST_MODEL) / "final_model_metadata.json"
    ).write_text(
        json.dumps(meta, indent=2),
        encoding="utf-8",
    )

    pd.DataFrame([{
        "feature_set": fs,
        "model_name": model_name,
        "architecture": arch,
        "target": PRIMARY_TARGET,
        "n_flights": len(df),
        "rationale": decision.get("rationale"),
        arch_metric: chosen.iloc[0].get(arch_metric, np.nan),
        "Within_Macro_WMAPE": chosen.iloc[0].get(
            "Within_Macro_WMAPE", np.nan
        ),
        "Cross_Macro_WMAPE": chosen.iloc[0].get(
            "Cross_Macro_WMAPE", np.nan
        ),
        "Cross_MeanAbsBias": chosen.iloc[0].get(
            "Cross_MeanAbsBias", np.nan
        ),
    }]).to_csv(
        out / "D13_final_model_selection.csv",
        index=False,
    )

    print(f"\nFINAL MODEL FROZEN: {model_path}")
    print(
        f"Architecture={arch} | Model={model_name} | "
        f"FeatureSet={fs} | Target={PRIMARY_TARGET}"
    )


if __name__ == "__main__":
    main()