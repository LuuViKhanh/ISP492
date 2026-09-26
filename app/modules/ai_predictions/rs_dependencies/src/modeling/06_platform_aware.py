"""D12 — Platform-aware combined training (adds is_vtol indicator)."""
import os, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor
from catboost import CatBoostRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

PROJECT_ROOT=Path(__file__).resolve().parent.parent.parent
sys.path.insert(0,str(PROJECT_ROOT))
from config.paths import OUT_DATA_FEATURES, OUT_RES_MULTIUAV
from config.multiuav_config import DEFAULT_FEATURE_SET, PRIMARY_TARGET, get_feature_set

def wmape(y,p):
    d=np.sum(np.abs(y)); return np.sum(np.abs(y-p))/d if d>0 else np.nan

def metrics(y,p): return mean_absolute_error(y,p),np.sqrt(mean_squared_error(y,p)),r2_score(y,p),wmape(y,p)
def models():
    return {"LinearRegression":LinearRegression(),"Ridge":Ridge(alpha=1.0),"ExtraTrees":ExtraTreesRegressor(n_estimators=100,random_state=42),"XGBoost":XGBRegressor(n_estimators=100,learning_rate=0.1,random_state=42,n_jobs=-1),"CatBoost":CatBoostRegressor(n_estimators=100,learning_rate=0.1,random_state=42,verbose=0)}

def make_pipe(name,model):
    steps=[("imputer",SimpleImputer(strategy="median"))]
    if name in {"LinearRegression","Ridge"}: steps.append(("scaler",StandardScaler()))
    steps.append(("model",model)); return Pipeline(steps)

def main():
    base=get_feature_set(DEFAULT_FEATURE_SET); features=base+["is_vtol"]
    df=pd.read_csv(Path(OUT_DATA_FEATURES)/"multiuav_flight_level_features.csv")
    df=df[(df.battery_consumed_wh>0)&(df.energy_efficiency>0)].copy(); df["is_vtol"]=(df.uav_type=="VTOL_FIXED_WING").astype(int)
    dji=df[df.dataset_id=="DJI_M100"].reset_index(drop=True); vtol=df[df.dataset_id=="VTOL"].reset_index(drop=True)
    dfolds=list(GroupKFold(n_splits=min(5,dji.date.nunique())).split(dji[base],dji[PRIMARY_TARGET],groups=dji.date))
    vfolds=list(GroupKFold(n_splits=min(5,vtol.date.nunique())).split(vtol[base],vtol[PRIMARY_TARGET],groups=vtol.date))
    nfold=min(len(dfolds),len(vfolds)); rows=[]; preds=[]
    print(f"D12 — PLATFORM-AWARE | target={PRIMARY_TARGET} | feature_set={DEFAULT_FEATURE_SET}+is_vtol")
    for name,model in models().items():
        for fi in range(nfold):
            dtr,dte=dfolds[fi]; vtr,vte=vfolds[fi]
            tr=pd.concat([dji.iloc[dtr],vtol.iloc[vtr]],ignore_index=True); td=dji.iloc[dte]; tv=vtol.iloc[vte]; tc=pd.concat([td,tv],ignore_index=True)
            pipe=make_pipe(name,model); pipe.fit(tr[features],tr[PRIMARY_TARGET])
            pdj=pipe.predict(td[features]); pvt=pipe.predict(tv[features]); pcb=pipe.predict(tc[features])
            md=metrics(td[PRIMARY_TARGET],pdj); mv=metrics(tv[PRIMARY_TARGET],pvt); mc=metrics(tc[PRIMARY_TARGET],pcb)
            rows.append({"FeatureSet":DEFAULT_FEATURE_SET,"Target":PRIMARY_TARGET,"model":name,"fold":fi+1,"DJI_WMAPE":md[3],"VTOL_WMAPE":mv[3],"Combined_WMAPE":mc[3],"Macro_WMAPE":(md[3]+mv[3])/2,"DJI_R2":md[2],"VTOL_R2":mv[2],"Combined_R2":mc[2]})
            for uid,true,pred,flag in zip(tc.flight_uid,tc[PRIMARY_TARGET],pcb,tc.is_vtol): preds.append({"FeatureSet":DEFAULT_FEATURE_SET,"Target":PRIMARY_TARGET,"model":name,"fold":fi+1,"flight_uid":uid,"is_vtol":flag,"y_true":true,"y_pred":pred})
    out=Path(OUT_RES_MULTIUAV); os.makedirs(out,exist_ok=True); fold=pd.DataFrame(rows); summary=fold.groupby(["FeatureSet","Target","model"],as_index=False).mean(numeric_only=True).drop(columns=["fold"])
    fold.to_csv(out/"D12_per_fold_results.csv",index=False); summary.to_csv(out/"D12_platform_aware_summary.csv",index=False); pd.DataFrame(preds).to_csv(out/"D12_predictions.csv",index=False)
    d11=out/"D11_combined_training_summary.csv"
    if d11.exists():
        u=pd.read_csv(d11)[["model","Macro_WMAPE"]].rename(columns={"Macro_WMAPE":"Universal_Macro_WMAPE"}); c=summary.merge(u,on="model",how="left"); c["PlatformAware_Delta_WMAPE"]=c.Macro_WMAPE-c.Universal_Macro_WMAPE; c.to_csv(out/"D12_vs_D11_comparison.csv",index=False)
    print(summary.sort_values("Macro_WMAPE").to_string(index=False))
if __name__=="__main__": main()
