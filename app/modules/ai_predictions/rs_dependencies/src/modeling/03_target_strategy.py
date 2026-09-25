import pandas as pd
import numpy as np
import sys
import os
from pathlib import Path
from sklearn.model_selection import GroupKFold
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.ensemble import ExtraTreesRegressor
from xgboost import XGBRegressor
from catboost import CatBoostRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score, median_absolute_error
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.paths import OUT_DATA_FEATURES, OUT_RES_MULTIUAV
from config.multiuav_config import DEFAULT_FEATURE_SET, get_feature_set

def wmape(y_true, y_pred):
    valid_idx = ~np.isnan(y_pred)
    if not np.any(valid_idx):
        return np.nan
    y_t = y_true[valid_idx]
    y_p = y_pred[valid_idx]
    return np.sum(np.abs(y_t - y_p)) / np.sum(np.abs(y_t))

def calculate_metrics(y_true, y_pred):
    valid_idx = ~np.isnan(y_pred) & ~np.isinf(y_pred)
    if not np.any(valid_idx):
        return np.nan, np.nan, np.nan, np.nan, np.nan, np.nan
        
    y_t = y_true[valid_idx]
    y_p = y_pred[valid_idx]
    
    mae = mean_absolute_error(y_t, y_p)
    rmse = np.sqrt(mean_squared_error(y_t, y_p))
    r2 = r2_score(y_t, y_p) if len(y_t) > 1 and np.var(y_t) > 0 else np.nan
    wmape_val = wmape(y_t, y_p)
    bias = np.mean(y_p - y_t)
    medae = median_absolute_error(y_t, y_p)
    return mae, rmse, r2, wmape_val, bias, medae

def get_models():
    return {
        "LinearRegression": LinearRegression(),
        "Ridge": Ridge(random_state=42),
        "ExtraTrees": ExtraTreesRegressor(n_estimators=100, random_state=42),
        "XGBoost": XGBRegressor(n_estimators=100, learning_rate=0.1, random_state=42, n_jobs=-1),
        "CatBoost": CatBoostRegressor(n_estimators=100, learning_rate=0.1, random_state=42, verbose=0)
    }

def needs_scaling(model_name):
    return model_name in ["LinearRegression", "Ridge"]

def run_d09_experiment(df, dataset_id, features):
    print(f"\n============================================================")
    print(f"RUNNING D09 ON: {dataset_id}")
    print(f"============================================================")
    
    X = df[features].copy()
    y_EE = df["energy_efficiency"].values
    y_Energy = df["battery_consumed_wh"].values
    d_context = df["distance"].values  # Oracle context, not a feature
    groups = df["date"].values
    
    cv = GroupKFold(n_splits=min(5, len(np.unique(groups))))
    
    models_A = get_models() # For Strategy A (Direct EE)
    models_B = get_models() # For Strategy B (Energy-First)
    
    fold_results = []
    predictions_list = []
    
    fold = 1
    for train_idx, test_idx in cv.split(X, y_Energy, groups=groups):
        X_train, X_test = X.iloc[train_idx].copy(), X.iloc[test_idx].copy()
        imputer = SimpleImputer(strategy="median")
        X_train_imp = imputer.fit_transform(X_train)
        X_test_imp = imputer.transform(X_test)
        
        y_train_EE, y_test_EE = y_EE[train_idx], y_EE[test_idx]
        y_train_Energy, y_test_Energy = y_Energy[train_idx], y_Energy[test_idx]
        
        d_test = d_context[test_idx]
        uids = df["flight_uid"].iloc[test_idx].values
        
        for name in models_A.keys():
            if needs_scaling(name):
                scaler = StandardScaler()
                X_train_scaled = scaler.fit_transform(X_train_imp)
                X_test_scaled = scaler.transform(X_test_imp)
            else:
                X_train_scaled = X_train_imp
                X_test_scaled = X_test_imp
                
            model_A = models_A[name]
            model_B = models_B[name]
            
            # Strategy A: Predict EE
            model_A.fit(X_train_scaled, y_train_EE)
            pred_EE = model_A.predict(X_test_scaled)
            
            # Strategy B: Predict Energy
            model_B.fit(X_train_scaled, y_train_Energy)
            pred_Energy = model_B.predict(X_test_scaled)
            
            # Invalid predictions (<= 0)
            invalid_EE_mask = pred_EE <= 0
            invalid_Energy_mask = pred_Energy <= 0
            
            invalid_EE_rate = invalid_EE_mask.mean()
            invalid_Energy_rate = invalid_Energy_mask.mean()
            
            # Derived metrics
            # If pred_EE <= 0, Derived Energy is NaN
            derived_Energy = np.where(~invalid_EE_mask, d_test / pred_EE, np.nan)
            
            # If pred_Energy <= 0, Derived EE is NaN
            derived_EE = np.where(~invalid_Energy_mask, d_test / pred_Energy, np.nan)
            
            # Store predictions
            for i in range(len(test_idx)):
                predictions_list.append({
                    "dataset_id": dataset_id,
                    "fold": fold,
                    "flight_uid": uids[i],
                    "model": name,
                    "true_EE": y_test_EE[i],
                    "true_Energy": y_test_Energy[i],
                    "oracle_distance": d_test[i],
                    "pred_Native_EE": pred_EE[i],
                    "pred_Native_Energy": pred_Energy[i],
                    "derived_Energy": derived_Energy[i],
                    "derived_EE": derived_EE[i]
                })
            
            # Calculate metrics on Energy space
            mae_E_nat, rmse_E_nat, r2_E_nat, wmape_E_nat, bias_E_nat, medae_E_nat = calculate_metrics(y_test_Energy, pred_Energy)
            mae_E_der, rmse_E_der, r2_E_der, wmape_E_der, bias_E_der, medae_E_der = calculate_metrics(y_test_Energy, derived_Energy)
            
            # Calculate metrics on EE space
            mae_EE_nat, rmse_EE_nat, r2_EE_nat, wmape_EE_nat, bias_EE_nat, medae_EE_nat = calculate_metrics(y_test_EE, pred_EE)
            mae_EE_der, rmse_EE_der, r2_EE_der, wmape_EE_der, bias_EE_der, medae_EE_der = calculate_metrics(y_test_EE, derived_EE)
            
            fold_results.append({
                "dataset_id": dataset_id,
                "model": name,
                "fold": fold,
                
                # Invalid rates
                "invalid_EE_rate": invalid_EE_rate,
                "invalid_Energy_rate": invalid_Energy_rate,
                
                # Energy Space Comparison
                "Energy_Native_RMSE": rmse_E_nat,
                "Energy_Derived_RMSE": rmse_E_der,
                "Energy_Native_WMAPE": wmape_E_nat,
                "Energy_Derived_WMAPE": wmape_E_der,
                
                # EE Space Comparison
                "EE_Native_RMSE": rmse_EE_nat,
                "EE_Derived_RMSE": rmse_EE_der,
                "EE_Native_WMAPE": wmape_EE_nat,
                "EE_Derived_WMAPE": wmape_EE_der,
            })
            
        fold += 1
        
    fold_df = pd.DataFrame(fold_results)
    preds_df = pd.DataFrame(predictions_list)
    
    # Calculate summary
    summary_df = fold_df.groupby(["dataset_id", "model"]).mean().reset_index().drop(columns=["fold"])
    
    return summary_df, fold_df, preds_df

def main():
    data_path = Path(OUT_DATA_FEATURES) / "multiuav_flight_level_features.csv"
    df = pd.read_csv(data_path)
    
    # Filter common valid flight cohort
    df = df[(df["battery_consumed_wh"] > 0) & (df["energy_efficiency"] > 0)].copy()
    
    dji_df = df[df["dataset_id"] == "DJI_M100"].copy()
    vtol_df = df[df["dataset_id"] == "VTOL"].copy()
    
    out_dir = Path(OUT_RES_MULTIUAV)
    os.makedirs(out_dir, exist_ok=True)
    
    features = get_feature_set(DEFAULT_FEATURE_SET)
    print(f"Feature set: {DEFAULT_FEATURE_SET} ({len(features)} features)")
    sum_dji, fold_dji, preds_dji = run_d09_experiment(dji_df, "DJI_M100", features)
    sum_vtol, fold_vtol, preds_vtol = run_d09_experiment(vtol_df, "VTOL", features)
    
    all_summary = pd.concat([sum_dji, sum_vtol], ignore_index=True)
    all_folds = pd.concat([fold_dji, fold_vtol], ignore_index=True)
    all_preds = pd.concat([preds_dji, preds_vtol], ignore_index=True)
    
    all_summary.to_csv(out_dir / "D09_target_strategy_summary.csv", index=False)
    all_folds.to_csv(out_dir / "D09_target_strategy_folds.csv", index=False)
    all_preds.to_csv(out_dir / "D09_target_strategy_predictions.csv", index=False)
    
    print("\nSaved D09 outputs to output/results/multiuav/")
    print(all_summary[["dataset_id", "model", "Energy_Native_WMAPE", "Energy_Derived_WMAPE"]].to_string(index=False))
    
    print("\n============================================================")
    print("D09 COMPLETED")
    print("============================================================")

if __name__ == "__main__":
    main()
