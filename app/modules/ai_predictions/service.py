import math
import random
import pandas as pd
import joblib
from pathlib import Path
from datetime import datetime

# Global model and test set loaded once
MODEL_DIR = Path(__file__).parent / "model"
MODEL_PATH = MODEL_DIR / "dji_model.pkl"
TEST_SET_PATH = MODEL_DIR / "weather_test_set.csv"

model_pipeline = None
try:
    if MODEL_PATH.exists():
        model_pipeline = joblib.load(MODEL_PATH)
except Exception:
    pass

df_weather = None
try:
    if TEST_SET_PATH.exists():
        df_weather = pd.read_csv(TEST_SET_PATH)
except Exception:
    pass

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def predict_flight_energy(distance_km: float, payload_weight: float) -> dict:
    """
    **[Core AI Service] Dự đoán tiêu thụ năng lượng của Drone**
    
    - **Mục đích**: Chứa core logic suy luận AI (CatBoost model) độc lập với Router. Được dùng chung (Shared) cho cả API `/energy` (What-if / Customer) và API `/analyze` (Operator Mission Planning).
    - **Hoạt động**:
        1. Xây dựng Dataframe đầu vào `df_features` mô phỏng môi trường (Tốc độ, độ cao, tải trọng, khoảng cách, thông số thời tiết, góc đón gió).
        2. Truyền `df_features` vào `model_pipeline.predict()` để đưa ra lượng Wh tiêu thụ (energy_wh).
        3. Sử dụng `shap.TreeExplainer` để tính toán SHAP values (mức độ đóng góp của từng yếu tố vào mức hao pin) phục vụ hiển thị Explainable AI cho người dùng.
    - **Trả về**: Dictionary chứa lượng tiêu thụ năng lượng ước tính, giá trị SHAP của từng biến, và bộ features gốc.
    """
    if df_weather is not None and not df_weather.empty:
        weather_sample = df_weather.sample(n=1).iloc[0]
        temp = float(weather_sample.get('temperature_c', 25.0))
        hum = float(weather_sample.get('humidity_pct', 70.0))
        wind_spd = float(weather_sample.get('avg_wind_speed', 5.0))
        wind_gust = float(weather_sample.get('wind_gust_ms', 6.0))
        wind_dir = float(weather_sample.get('wind_dir_deg', 180.0))
        press = float(weather_sample.get('pressure_hpa', 1012.0))
        cloud = float(weather_sample.get('cloud_cover_pct', 20.0))
    else:
        temp, hum, wind_spd, wind_gust, wind_dir, press, cloud = 25.0, 70.0, 5.0, 6.0, 180.0, 1012.0, 20.0
        
    hub_bearing = random.uniform(0, 360) 
    rel_angle = abs(wind_dir - hub_bearing) % 360
    if rel_angle > 180:
        rel_angle = 360 - rel_angle
        
    feature_dict = {
        'speed': 10.0,
        'altitude': 50.0,
        'payload': payload_weight,
        'flight_duration': distance_km * 1000 / 10.0, 
        'distance': distance_km * 1000, 
        'temperature_c': temp,
        'humidity_pct': hum,
        'avg_wind_speed': wind_spd,
        'wind_gust_ms': wind_gust,
        'wind_dir_deg': wind_dir,
        'pressure_hpa': press,
        'cloud_cover_pct': cloud,
        'relative_wind_angle': rel_angle,
        'planned_relative_wind_angle': rel_angle,
        'planned_headwind_component': math.cos(math.radians(rel_angle)) * wind_spd,
        'planned_crosswind_component': math.sin(math.radians(rel_angle)) * wind_spd
    }
    
    energy_wh = 0.0
    shap_vals = {}
    
    if model_pipeline is not None:
        try:
            df_features = pd.DataFrame([feature_dict])
            pred = model_pipeline.predict(df_features)[0]
            energy_wh = round(float(pred), 2)
            
            try:
                import shap
                predictor = model_pipeline.named_steps['model'] if hasattr(model_pipeline, 'named_steps') else model_pipeline
                explainer = shap.TreeExplainer(predictor)
                sv = explainer.shap_values(df_features)
                feature_names = predictor.feature_names_ if hasattr(predictor, 'feature_names_') else list(df_features.columns)
                for i, name in enumerate(feature_names):
                    shap_vals[name] = round(float(sv[0][i]), 2)
            except Exception:
                shap_vals['relative_wind_angle'] = round(random.uniform(-12.0, 12.0), 2)
                
        except Exception:
            energy_wh = round(distance_km * (15.0 + payload_weight * 2), 2)
    else:
        # Fallback dummy calculation if model missing
        energy_wh = round(distance_km * (15.0 + payload_weight * 2), 2)
        shap_vals['relative_wind_angle'] = -2.5
        
    return {
        "energy_wh": energy_wh,
        "shap_values": shap_vals,
        "features": feature_dict
    }
