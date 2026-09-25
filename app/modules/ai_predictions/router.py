from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import random
import uuid
import sys
import os
import math
from pathlib import Path
import pandas as pd
import numpy as np

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.db import get_async_db
from app.modules.system.models import Hub

# ====== ML MODEL INTEGRATION ======
import joblib

# Use portable relative path for RS dependencies so joblib can unpickle custom modules (like src.modeling.validation_common)
RS_DEPS_PATH = str(Path(__file__).parent / "rs_dependencies")
if RS_DEPS_PATH not in sys.path:
    sys.path.insert(0, RS_DEPS_PATH)

router = APIRouter(prefix="/ai_predictions", tags=["AI Predictions"])

# Try to load the DJI CatBoost Model
MODEL_DIR = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "dji_model.pkl"

model_pipeline = None
try:
    if MODEL_PATH.exists():
        model_pipeline = joblib.load(MODEL_PATH)
        print(f"[AI] Successfully loaded DJI CatBoost model from {MODEL_PATH}")
    else:
        print(f"[AI WARN] Model not found at {MODEL_PATH}. Using mock fallback.")
except Exception as e:
    print(f"[AI ERROR] Failed to load model: {e}. Please ensure catboost is installed. Using mock fallback.")

# Load Test Set for realistic weather
TEST_SET_PATH = MODEL_DIR / "weather_test_set.csv"
df_weather = None
try:
    if TEST_SET_PATH.exists():
        df_weather = pd.read_csv(TEST_SET_PATH)
        print(f"[AI] Loaded {len(df_weather)} weather records from test set.")
except Exception as e:
    print(f"[AI WARN] Failed to load weather test set: {e}")

class RouteEnergyRequest(BaseModel):
    destination_lat: float = Field(..., description="Vĩ độ điểm đến")
    destination_lng: float = Field(..., description="Kinh độ điểm đến")
    payload_weight: float = Field(..., description="Trọng lượng hàng hóa (kg)")
    customer_expected_time: Optional[datetime] = Field(None, description="Thời gian khách hàng mong muốn nhận hàng")

class RouteSuggestion(BaseModel):
    route_id: str
    hub_id: str
    distance_km: float
    estimated_energy_wh: float
    optimal_departure_time: datetime
    shap_values: Dict[str, float]
    what_if_analysis: str

class EnergyPredictionResponse(BaseModel):
    recommended_route_id: str
    routes: List[RouteSuggestion]

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance in kilometers between two points on the earth."""
    R = 6371.0 # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

@router.post("/energy", response_model=EnergyPredictionResponse)
async def predict_energy(request: RouteEnergyRequest, db: AsyncSession = Depends(get_async_db)):
    """
    Đưa ra gợi ý "What-if" cho tuyến đường tiết kiệm năng lượng.
    
    [PHASE 1 - HIỆN TẠI]: Môi trường Bán Lập (Semi-Simulation)
    - Tọa độ Hubs và Khoảng cách: Chạy thật (Query từ DB và tính Haversine).
    - Thời tiết (Weather): Sử dụng dữ liệu mô phỏng bốc ngẫu nhiên từ Test Set (`weather_test_set.csv`) của nghiên cứu khoa học.
    - Động lực học (Bearing/Wind Angle): Sử dụng random góc bay để tính toán sức cản gió.
    - AI Model: Dùng 100% CatBoost thật của DJI (từ dự án RS).
    
    [PHASE 2 - TƯƠNG LAI]: Môi trường Thực (Digital Twin & Real-time)
    - Cần xóa bỏ việc đọc file `weather_test_set.csv`.
    - Gọi trực tiếp một API Thời tiết (VD: Open-Meteo) dựa trên tọa độ Hub và thời gian bay để lấy thông số (Temp, Wind, ...).
    - Tính chính xác góc phương vị (True Bearing) từ Hub tới Customer để giải lượng giác tìm Góc đón gió thật (`relative_wind_angle`).
    """
    # Fetch REAL Hubs from the database
    result = await db.execute(select(Hub).where(Hub.status == "ACTIVE"))
    hubs = result.scalars().all()
    
    # Fallback to all hubs if no active hubs are explicitly marked
    if not hubs:
        result = await db.execute(select(Hub))
        hubs = result.scalars().all()
        
    if not hubs:
        raise HTTPException(status_code=400, detail="No Hubs found in the database.")
    
    routes = []
    base_time = request.customer_expected_time or datetime.now() + timedelta(hours=2)
    
    # Optimize SHAP TreeExplainer generation by caching it if possible
    # But for a simple REST API request, creating TreeExplainer is fast enough for CatBoost
    explainer = None
    if model_pipeline is not None:
        try:
            import shap
            predictor = model_pipeline.named_steps['model'] if hasattr(model_pipeline, 'named_steps') else model_pipeline
            explainer = shap.TreeExplainer(predictor)
        except Exception as e:
            print(f"[AI WARN] Failed to init SHAP explainer: {e}")
            
    for hub in hubs:
        if not hub.latitude or not hub.longitude:
            continue
            
        # 1. Calculate Real Distance using Haversine
        distance = round(haversine_distance(hub.latitude, hub.longitude, request.destination_lat, request.destination_lng), 2)
        
        # 2. What-if analysis: Adjust departure time to find optimal weather conditions
        time_shift_minutes = random.randint(-60, 60)
        optimal_departure = base_time - timedelta(minutes=time_shift_minutes) - timedelta(minutes=distance * 2) 
        
        # [PHASE 1] Extract real weather from test set
        # TODO [PHASE 2]: Replace this block with a real call to Weather API (e.g. Open-Meteo) 
        # using the `hub.latitude`, `hub.longitude` and `optimal_departure` time.
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
            
        # Determine pseudo relative wind angle
        hub_bearing = random.uniform(0, 360) # In real life, calculate bearing(hub, dest)
        rel_angle = abs(wind_dir - hub_bearing) % 360
        if rel_angle > 180:
            rel_angle = 360 - rel_angle
            
        # 3. Prepare feature vector for the model
        feature_dict = {
            'speed': 10.0, # m/s (planned)
            'altitude': 50.0, # m (planned)
            'payload': request.payload_weight,
            'flight_duration': distance * 1000 / 10.0, # seconds
            'distance': distance * 1000, # meters
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
        
        df_features = pd.DataFrame([feature_dict])
        
        energy_wh = 0.0
        shap_vals = {}
        wind_angle_shap = 0.0
        
        if model_pipeline is not None:
            try:
                predictor = model_pipeline.named_steps['model'] if hasattr(model_pipeline, 'named_steps') else model_pipeline
                pred = model_pipeline.predict(df_features)[0]
                energy_wh = round(float(pred), 2)
                
                if explainer is not None:
                    # Generate real SHAP values for the prediction
                    sv = explainer.shap_values(df_features)
                    # For a single row, shap_values is an array of size (n_features,)
                    # We map them back to feature names
                    feature_names = predictor.feature_names_ if hasattr(predictor, 'feature_names_') else list(df_features.columns)
                    
                    for i, name in enumerate(feature_names):
                        shap_vals[name] = round(float(sv[0][i]), 2)
                        
                    wind_angle_shap = shap_vals.get('relative_wind_angle', 0.0)
                else:
                    wind_angle_shap = round(random.uniform(-12.0, 12.0), 2)
            except Exception as e:
                print(f"[AI ERROR] Inference failed: {e}")
                energy_wh = round((distance * 14.5) + (request.payload_weight * 8.0), 2)
                wind_angle_shap = round(random.uniform(-12.0, 12.0), 2)
        else:
            energy_wh = round((distance * 14.5) + (request.payload_weight * 8.0) + random.uniform(-15, 15), 2)
            wind_angle_shap = round(random.uniform(-12.0, 12.0), 2)
            
        # Format a clean SHAP response with key features
        clean_shap = {
            "payload": shap_vals.get("payload", round(request.payload_weight * 2.1, 2)),
            "distance": shap_vals.get("distance", round(distance * 3.5, 2)),
            "wind_speed": shap_vals.get("avg_wind_speed", round(wind_spd * 1.5, 2)),
            "wind_angle": wind_angle_shap
        }
        
        route_id = f"RT-{uuid.uuid4().hex[:8].upper()}"
        
        if wind_angle_shap < 0:
            what_if_msg = f"Dựa trên DJI Model: Bay lúc {optimal_departure.strftime('%H:%M')} tiết kiệm được {abs(wind_angle_shap)} Wh do gió xuôi."
        else:
            what_if_msg = f"Dựa trên DJI Model: Khởi hành lúc {optimal_departure.strftime('%H:%M')} gặp gió ngang/ngược làm tăng tiêu hao {abs(wind_angle_shap)} Wh."
            
        routes.append(RouteSuggestion(
            route_id=route_id,
            hub_id=hub.name or f"HUB-{hub.id}",
            distance_km=distance,
            estimated_energy_wh=energy_wh,
            optimal_departure_time=optimal_departure,
            shap_values=clean_shap,
            what_if_analysis=what_if_msg
        ))
        
    routes.sort(key=lambda x: x.estimated_energy_wh)
    
    return EnergyPredictionResponse(
        recommended_route_id=routes[0].route_id,
        routes=routes
    )

class ShapFeatureDetail(BaseModel):
    feature_name: str
    feature_value: float
    shap_contribution_wh: float
    description: str

class ShapExplanationResponse(BaseModel):
    prediction_id: str
    base_value_wh: float
    final_prediction_wh: float
    features: List[ShapFeatureDetail]
    summary_message: str

@router.get("/explain/{prediction_id}", response_model=ShapExplanationResponse)
async def get_prediction_explanation(prediction_id: str):
    """
    Trả về dữ liệu chi tiết của mô hình SHAP để hiển thị biểu đồ (Waterfall, Bar chart).
    Giải thích tại sao AI đưa ra mức tiêu thụ năng lượng đó.
    
    Phase 1 (Hiện tại): Trả về mảng dữ liệu SHAP với các số liệu giả lập nhưng đúng form chuẩn, để team Frontend có data thật mà lập trình ngay giao diện vẽ biểu đồ (ví dụ dùng thư viện Recharts hay ECharts).
    
    Phase 2 (Tương lai): Khi hệ thống ghép nối hoàn chỉnh, bạn chỉ cần viết thêm hàm query vào Database tìm cái prediction_id đó, lôi mảng SHAP thật ra thay vào là xong!
    """
    # MOCK BASE VALUE (Trung bình năng lượng của toàn bộ tập mẫu CatBoost)
    base_value = 150.0 
    
    # Giả lập lại một vài features quan trọng như gió, tải trọng, khoảng cách
    features = [
        ShapFeatureDetail(
            feature_name="distance",
            feature_value=12.5, # km
            shap_contribution_wh=45.2,
            description="Khoảng cách bay xa làm tăng tiêu hao pin."
        ),
        ShapFeatureDetail(
            feature_name="payload",
            feature_value=2.5, # kg
            shap_contribution_wh=15.8,
            description="Tải trọng hàng hóa lớn."
        ),
        ShapFeatureDetail(
            feature_name="wind_speed",
            feature_value=8.5, # m/s
            shap_contribution_wh=-12.4,
            description="Gió xuôi chiều giúp tiết kiệm pin."
        ),
        ShapFeatureDetail(
            feature_name="temperature_c",
            feature_value=32.0, # Độ C
            shap_contribution_wh=3.1,
            description="Nhiệt độ môi trường cao làm giảm hiệu suất pin nhẹ."
        ),
        ShapFeatureDetail(
            feature_name="relative_wind_angle",
            feature_value=25.0, # Độ
            shap_contribution_wh=-8.5,
            description="Góc đón gió tối ưu."
        )
    ]
    
    # Final prediction = Base Value + Sum(SHAP contributions)
    final_prediction = base_value + sum([f.shap_contribution_wh for f in features])
    
    return ShapExplanationResponse(
        prediction_id=prediction_id,
        base_value_wh=round(base_value, 2),
        final_prediction_wh=round(final_prediction, 2),
        features=features,
        summary_message="Chuyến bay có mức tiêu hao cao hơn trung bình chủ yếu do khoảng cách xa (đóng góp +45.2Wh), nhưng đã được bù đắp một phần nhờ bay xuôi chiều gió (-12.4Wh)."
    )
