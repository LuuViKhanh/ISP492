"""
app/shared/weather.py
─────────────────────
Fetch thời tiết thực tế từ Open-Meteo API theo tọa độ (lat, lng).

Open-Meteo:
- Hoàn toàn miễn phí, không cần API key, không giới hạn calls
- Dữ liệu từ các mô hình khí tượng quốc gia (NOAA, ECMWF, DWD)
- Docs: https://open-meteo.com/en/docs
"""

import httpx
from typing import Optional
from dataclasses import dataclass


@dataclass
class WeatherData:
    temperature: Optional[float] = None        # °C
    apparent_temperature: Optional[float] = None  # °C (cảm giác thực)
    humidity: Optional[float] = None           # %
    precipitation: Optional[float] = None      # mm
    cloud_cover: Optional[float] = None        # %
    wind_speed: Optional[float] = None         # m/s
    wind_direction: Optional[float] = None     # độ (0-360)
    weather_code: Optional[int] = None         # WMO code


# WMO weather code → mô tả ngắn gọn
WMO_DESCRIPTIONS = {
    0:  "Trời quang",
    1:  "Ít mây",
    2:  "Nhiều mây",
    3:  "Âm u",
    45: "Sương mù",
    48: "Sương mù đóng băng",
    51: "Mưa phùn nhẹ",
    53: "Mưa phùn",
    55: "Mưa phùn dày",
    61: "Mưa nhẹ",
    63: "Mưa vừa",
    65: "Mưa to",
    71: "Tuyết nhẹ",
    73: "Tuyết vừa",
    75: "Tuyết dày",
    80: "Mưa rào nhẹ",
    81: "Mưa rào",
    82: "Mưa rào mạnh",
    95: "Giông",
    96: "Giông kèm mưa đá",
    99: "Giông kèm mưa đá lớn",
}


async def fetch_weather(lat: float, lng: float) -> Optional[WeatherData]:
    """
    Gọi Open-Meteo API lấy thời tiết hiện tại tại tọa độ (lat, lng).
    Trả về WeatherData hoặc None nếu lỗi (không làm hỏng telemetry flow).
    
    Timeout 3s để không làm chậm telemetry endpoint.
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lng}"
        f"&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
        f"precipitation,cloud_cover,wind_speed_10m,wind_direction_10m,weather_code"
        f"&wind_speed_unit=ms"  # m/s thay vì km/h
        f"&timezone=Asia%2FHo_Chi_Minh"
    )

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None

            data = resp.json().get("current", {})
            return WeatherData(
                temperature=data.get("temperature_2m"),
                apparent_temperature=data.get("apparent_temperature"),
                humidity=data.get("relative_humidity_2m"),
                precipitation=data.get("precipitation"),
                cloud_cover=data.get("cloud_cover"),
                wind_speed=data.get("wind_speed_10m"),
                wind_direction=data.get("wind_direction_10m"),
                weather_code=data.get("weather_code"),
            )

    except Exception:
        # Không throw — weather là optional, không được làm fail telemetry
        return None


def weather_description(code: Optional[int]) -> str:
    """Chuyển WMO weather code thành mô tả tiếng Việt."""
    if code is None:
        return "Không rõ"
    return WMO_DESCRIPTIONS.get(code, f"Mã {code}")
