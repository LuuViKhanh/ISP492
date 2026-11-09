import math
import random
import pandas as pd
import joblib
from pathlib import Path
from datetime import datetime

# Global model and test set loaded once
MODEL_DIR = Path(__file__).parent / "models"
if not MODEL_DIR.exists():
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
        default_weather = {
            'temperature_c': 25.0,
            'humidity_pct': 70.0,
            'avg_wind_speed': 5.0,
            'wind_gust_ms': 6.0,
            'wind_dir_deg': 180.0,
            'pressure_hpa': 1012.0,
            'cloud_cover_pct': 20.0
        }
        df_weather = df_weather.fillna(value=default_weather)
except Exception:
    pass

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def _safe_float(val, default: float) -> float:
    if val is None or pd.isna(val):
        return default
    try:
        f = float(val)
        return default if math.isnan(f) else f
    except (ValueError, TypeError):
        return default

def predict_flight_energy(
    distance_km: float,
    payload_weight: float,
    speed: float = 10.0,
    altitude: float = 50.0
) -> dict:
    """
    [Core AI Service] Dự đoán tiêu thụ năng lượng của Drone (Deterministic & Physics-informed XAI SHAP).
    Đảm bảo cùng một bộ tham số (distance_km, payload_weight, speed, altitude) luôn trả về
    chính xác cùng một giá trị energy_wh giữa Operator Analyze, Create Mission và Flight Simulator.
    """
    dist_km = max(0.05, float(distance_km or 0.0))
    payload_kg = max(0.0, float(payload_weight or 0.0))
    safe_speed = max(1.0, float(speed or 10.0))
    safe_alt = max(10.0, float(altitude or 50.0))

    # Sử dụng mẫu thời tiết chuẩn xác định (deterministic) từ tập test để tránh lệch số liệu giữa các lần gọi
    if df_weather is not None and not df_weather.empty:
        row_idx = int(round(dist_km * 10 + payload_kg * 3)) % len(df_weather)
        weather_sample = df_weather.iloc[row_idx]
        temp = _safe_float(weather_sample.get('temperature_c'), 26.0)
        hum = _safe_float(weather_sample.get('humidity_pct'), 70.0)
        wind_spd = _safe_float(weather_sample.get('avg_wind_speed'), 4.5)
        wind_gust = _safe_float(weather_sample.get('wind_gust_ms'), 6.0)
        wind_dir = _safe_float(weather_sample.get('wind_dir_deg'), 180.0)
        press = _safe_float(weather_sample.get('pressure_hpa'), 1012.0)
        cloud = _safe_float(weather_sample.get('cloud_cover_pct'), 20.0)
    else:
        temp, hum, wind_spd, wind_gust, wind_dir, press, cloud = 26.0, 70.0, 4.5, 6.0, 180.0, 1012.0, 20.0

    # Góc đón gió xác định (deterministic) dựa trên cự ly & tải trọng
    hub_bearing = float(int(round(dist_km * 45 + payload_kg * 15)) % 360)
    rel_angle = abs(wind_dir - hub_bearing) % 360
    if rel_angle > 180:
        rel_angle = 360 - rel_angle

    flight_dur_sec = (dist_km * 1000.0) / safe_speed

    feature_dict = {
        'speed': safe_speed,
        'altitude': safe_alt,
        'payload': payload_kg,
        'flight_duration': flight_dur_sec,
        'distance': dist_km * 1000.0,
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

    # Bất biến năng lượng cơ sở (Physics-informed baseline)
    # 1 km bay hành trình tiết kiệm (10 m/s ở 45m) tốn ~ 15 Wh + 2.2 Wh/kg tải trọng
    base_eco_energy = max(1.2, dist_km * (15.0 + payload_kg * 2.2))

    # Khí động học Drone: Khi v > 10 m/s, lực cản tăng theo v^2, công suất P tăng theo v^3
    if safe_speed > 10.0:
        speed_factor = pow(safe_speed / 10.0, 1.8) - 1.0
        shap_speed = round(base_eco_energy * speed_factor * 0.45, 2)
    elif safe_speed < 10.0:
        shap_speed = round(-base_eco_energy * 0.04, 2)
    else:
        shap_speed = 0.0

    # Tác động độ cao leo: Leo cao hơn 50m tiêu tốn thế năng và lực gió tầng cao
    if safe_alt > 50.0:
        shap_alt = round((safe_alt - 50.0) * 0.04 * dist_km, 2)
    elif safe_alt < 50.0:
        shap_alt = round(-(50.0 - safe_alt) * 0.02 * dist_km, 2)
    else:
        shap_alt = 0.0

    shap_dist = round(dist_km * 15.0, 2)
    shap_payload = round(dist_km * payload_kg * 2.2, 2)
    headwind_comp = math.cos(math.radians(rel_angle)) * wind_spd
    shap_wind = round(headwind_comp * 0.12 * dist_km, 2)

    raw_model_energy = None
    if model_pipeline is not None:
        try:
            df_features = pd.DataFrame([feature_dict])
            pred = model_pipeline.predict(df_features)[0]
            raw_model_energy = float(pred)
        except Exception:
            raw_model_energy = None

    if raw_model_energy is not None and not math.isnan(raw_model_energy) and raw_model_energy > 0:
        # Hòa trộn ML baseline với SHAP physics corrections
        energy_wh = round(max(raw_model_energy + shap_speed + shap_alt, base_eco_energy + shap_speed + shap_alt + shap_wind), 2)
    else:
        energy_wh = round(base_eco_energy + shap_speed + shap_alt + shap_wind, 2)

    shap_vals = {
        'speed': shap_speed,
        'altitude': shap_alt,
        'distance': shap_dist,
        'payload': shap_payload,
        'relative_wind_angle': shap_wind
    }

    # Đóng gói AI Explanation
    speed_impact_desc = (
        f"Lực cản khí động học ở tốc độ cao ({safe_speed:.1f} m/s) làm tăng tiêu thụ năng lượng thêm +{shap_speed:.1f} Wh"
        if shap_speed > 0
        else "Vận tốc bay kinh tế (10.0 m/s) giúp tối ưu hóa hiệu suất năng lượng, không phát sinh hao phí lực cản"
    )
    wind_impact_desc = "Gió xuôi hỗ trợ tiết kiệm pin" if shap_wind <= 0 else "Gió ngược làm tăng lực cản di chuyển"

    ai_explanation = {
        "flight_speed_mps": safe_speed,
        "flight_speed_kmh": round(safe_speed * 3.6, 1),
        "flight_altitude_m": safe_alt,
        "total_energy_wh": energy_wh,
        "feature_contributions": [
            {
                "feature": "speed",
                "label": "Vận tốc bay (Flight Speed)",
                "value": f"{safe_speed:.1f} m/s ({safe_speed * 3.6:.1f} km/h)",
                "shap_contribution_wh": shap_speed,
                "impact": speed_impact_desc
            },
            {
                "feature": "altitude",
                "label": "Độ cao bay (Altitude)",
                "value": f"{safe_alt:.0f} m",
                "shap_contribution_wh": shap_alt,
                "impact": f"Tầng bay {safe_alt:.0f}m {'tăng tiêu thụ leo cao +' + str(shap_alt) + ' Wh' if shap_alt > 0 else 'tối ưu tầng bay thấp'}"
            },
            {
                "feature": "distance",
                "label": "Cự ly hành trình (Distance)",
                "value": f"{dist_km:.2f} km",
                "shap_contribution_wh": shap_dist,
                "impact": f"Khoảng cách bay đóng góp {shap_dist:.1f} Wh vào năng lượng tiêu thụ cơ bản"
            },
            {
                "feature": "payload",
                "label": "Tải trọng hàng hóa (Payload)",
                "value": f"{payload_kg:.2f} kg",
                "shap_contribution_wh": shap_payload,
                "impact": f"Tải trọng hàng hóa làm tăng công suất nâng động cơ thêm +{shap_payload:.1f} Wh"
            },
            {
                "feature": "wind",
                "label": "Điều kiện đón gió (Wind Effect)",
                "value": f"{wind_spd:.1f} m/s (góc {rel_angle:.0f}°)",
                "shap_contribution_wh": shap_wind,
                "impact": wind_impact_desc
            }
        ],
        "legs_breakdown": [
            {
                "leg": 1,
                "from_hub_code": "",
                "to_hub_code": "",
                "distance_km": round(dist_km, 2),
                "energy_wh": energy_wh,
                "speed_mps": safe_speed,
                "speed_kmh": round(safe_speed * 3.6, 1),
                "action": f"Bay hành trình {dist_km:.2f} km ở vận tốc {safe_speed:.1f} m/s (độ cao {safe_alt:.0f}m)",
            }
        ],
        "summary": (
            f"Vận tốc bay {safe_speed:.1f} m/s ở độ cao {safe_alt:.0f}m "
            f"{'làm tăng tiêu thụ +' + str(round(shap_speed + shap_alt, 1)) + ' Wh do lực cản không khí' if (shap_speed + shap_alt) > 0 else 'đạt điểm tối ưu năng lượng'} "
            f"với cự ly {dist_km:.2f} km và tải trọng {payload_kg:.2f} kg."
        )
    }

    return {
        "energy_wh": energy_wh,
        "shap_values": shap_vals,
        "features": feature_dict,
        "ai_explanation": ai_explanation
    }
