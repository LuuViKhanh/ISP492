import pandas as pd
import numpy as np
import sys
from pathlib import Path
from sklearn.model_selection import GroupKFold
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.dummy import DummyRegressor
import catboost as cb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.paths import OUT_DATA_FEATURES
from config.multiuav_config import COMMON_PREFLIGHT_FEATURES, PRIMARY_TARGET

def wmape(y_true, y_pred):
    return np.sum(np.abs(y_true - y_pred)) / np.sum(np.abs(y_true))

def main():
    data_path = Path(OUT_DATA_FEATURES) / "multiuav_flight_level_features.csv"
    df = pd.read_csv(data_path)
    
    df = df[df[PRIMARY_TARGET] > 0].copy()
    
    # We only care about VTOL for this deep dive
    vtol_df = df[df["dataset_id"] == "VTOL"].copy()
    
    print("========================================")
    print("VTOL D07 DIAGNOSTIC REPORT")
    print("========================================\n")
    
    dates = vtol_df["date"].unique()
    print(f"Total VTOL flights: {len(vtol_df)}")
    print(f"Total unique dates: {len(dates)}")
    
    # 1. Target distribution by date
    print("\n[1] TARGET DISTRIBUTION BY DATE")
    print("-" * 50)
    stats = []
    for d in sorted(dates):
        d_df = vtol_df[vtol_df["date"] == d]
        y_d = d_df[PRIMARY_TARGET].values
        stats.append({
            "date": d,
            "n_flights": len(d_df),
            "energy_mean": np.mean(y_d),
            "energy_std": np.std(y_d),
            "energy_min": np.min(y_d),
            "energy_max": np.max(y_d)
        })
    stats_df = pd.DataFrame(stats)
    print(stats_df.to_string(index=False))
    
    # 2. Per-fold metrics & comparison to Dummy
    print("\n[2] PER-FOLD EVALUATION (GroupKFold by date)")
    print("-" * 50)
    
    X = vtol_df[COMMON_PREFLIGHT_FEATURES].copy()
    X = X.fillna(X.mean())
    y = vtol_df[PRIMARY_TARGET].values
    groups = vtol_df["date"].values
    
    cv = GroupKFold(n_splits=min(5, len(dates)))
    
    models = {
        "Dummy": DummyRegressor(strategy="mean"),
        "ExtraTrees": ExtraTreesRegressor(n_estimators=100, random_state=42),
        "CatBoost": cb.CatBoostRegressor(n_estimators=100, learning_rate=0.1, random_state=42, verbose=0)
    }
    
    fold_results = []
    
    fold = 1
    for train_idx, test_idx in cv.split(X, y, groups=groups):
        test_dates = np.unique(groups[test_idx])
        
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        # 3. Feature range comparison
        out_of_range = []
        for feat in COMMON_PREFLIGHT_FEATURES:
            tr_min, tr_max = X_train[feat].min(), X_train[feat].max()
            te_min, te_max = X_test[feat].min(), X_test[feat].max()
            
            if te_min < tr_min or te_max > tr_max:
                out_of_range.append(f"{feat} (Train: [{tr_min:.1f}, {tr_max:.1f}], Test: [{te_min:.1f}, {te_max:.1f}])")
        
        fold_info = {"Fold": fold, "Test Dates": ",".join(test_dates), "N_Test": len(y_test)}
        
        print(f"\nFold {fold} | Test Dates: {','.join(test_dates)} | N_Test: {len(y_test)}")
        if out_of_range:
            print("  Out of range features (Test vs Train):")
            for f in out_of_range:
                print(f"    - {f}")
        else:
            print("  No features out of training range.")
            
        print(f"  Target Stats -> Train Mean: {np.mean(y_train):.1f}, Test Mean: {np.mean(y_test):.1f}")
        
        for name, model in models.items():
            model.fit(X_train_scaled, y_train)
            y_pred = model.predict(X_test_scaled)
            
            mae = mean_absolute_error(y_test, y_pred)
            rmse = np.sqrt(mean_squared_error(y_test, y_pred))
            if len(y_test) > 1 and np.var(y_test) > 0:
                r2 = r2_score(y_test, y_pred)
            else:
                r2 = np.nan
            wmape_val = wmape(y_test, y_pred)
            
            print(f"    [{name:10s}] MAE: {mae:5.2f} | RMSE: {rmse:5.2f} | R2: {r2:6.3f} | WMAPE: {wmape_val*100:5.2f}%")
            
            res = fold_info.copy()
            res["Model"] = name
            res["MAE"] = mae
            res["RMSE"] = rmse
            res["R2"] = r2
            res["WMAPE_pct"] = wmape_val * 100
            fold_results.append(res)
            
        fold += 1
        
    print("\n========================================")
    print("DIAGNOSTIC SUMMARY")
    print("========================================\n")
    
    fr_df = pd.DataFrame(fold_results)
    
    avg_dummy = fr_df[fr_df["Model"] == "Dummy"]["WMAPE_pct"].mean()
    avg_cat = fr_df[fr_df["Model"] == "CatBoost"]["WMAPE_pct"].mean()
    avg_et = fr_df[fr_df["Model"] == "ExtraTrees"]["WMAPE_pct"].mean()
    
    print(f"Average WMAPE across folds:")
    print(f"  Dummy:      {avg_dummy:.2f}%")
    print(f"  CatBoost:   {avg_cat:.2f}%")
    print(f"  ExtraTrees: {avg_et:.2f}%")
    
if __name__ == "__main__":
    main()
