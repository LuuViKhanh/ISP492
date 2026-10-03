from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime
from app.modules.missions.models import MissionStatus


class MissionStatusCount(BaseModel):
    status: str
    count: int


class MissionKPIResponse(BaseModel):
    total_missions: int
    completed: int
    cancelled: int
    rejected: int
    success_rate: float
    avg_distance_m: float
    avg_payload_kg: float
    total_revenue: float


class MissionStatusResponse(BaseModel):
    status_breakdown: Dict[str, int]


class ActiveFlightsResponse(BaseModel):
    active_count: int
    missions: list


class MissionResponse(BaseModel):
    id: int
    customer_id: Optional[str]
    operator_id: Optional[str]
    drone_id: Optional[int]
    battery_id: Optional[int]
    pickup_location_id: Optional[int]
    dropoff_location_id: Optional[int]
    payload_weight: Optional[float]
    distance_m: Optional[float]
    delivery_fee: Optional[float]
    status: MissionStatus
    scheduled_time: Optional[datetime]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    origin_hub_id: Optional[int]
    destination_hub_id: Optional[int]
    departed_at: Optional[datetime]
    arrived_at: Optional[datetime]
    route_path_string: Optional[str] = None
    arrival_confirmed_by: Optional[str]
    arrival_confirmed_at: Optional[datetime]

    class Config:
        from_attributes = True


class ApproveRejectRequest(BaseModel):
    drone_id: Optional[int] = None
    battery_id: Optional[int] = None
    operator_id: Optional[int] = None
    reason: Optional[str] = None


class TelemetryDataCreate(BaseModel):
    timestamp: datetime
    latitude: float
    longitude: float
    altitude: float
    speed: float
    battery_voltage: Optional[float] = None
    energy_consumed_wh: Optional[float] = None
    wind_speed: Optional[float] = None


class TelemetryDataResponse(TelemetryDataCreate):
    id: int
    mission_id: int
    weather_temperature: Optional[float] = None
    weather_apparent_temp: Optional[float] = None
    weather_dew_point: Optional[float] = None
    weather_humidity: Optional[float] = None
    weather_wind_speed: Optional[float] = None
    weather_wind_gust: Optional[float] = None
    weather_wind_direction: Optional[float] = None
    weather_precipitation: Optional[float] = None
    weather_pressure: Optional[float] = None
    weather_cloud_cover: Optional[float] = None

    class Config:
        from_attributes = True


class HubCheckpointResponse(BaseModel):
    """Log mỗi lần drone đi qua Hub trung gian"""
    id: int
    mission_id: int
    hub_id: Optional[int]
    location_id: Optional[int]
    hub_name: Optional[str]
    hub_latitude: Optional[float]
    hub_longitude: Optional[float]
    drone_latitude: float
    drone_longitude: float
    distance_to_hub_m: Optional[float]
    passed_at: datetime
    checkpoint_order: Optional[int]

    class Config:
        from_attributes = True


class TelemetryIngestResponse(BaseModel):
    """Response của POST /telemetry — bao gồm logs đã lưu và checkpoint mới tạo (nếu có)"""
    saved_count: int
    logs: list[TelemetryDataResponse]
    new_checkpoints: list[HubCheckpointResponse]
    mission_status: MissionStatus


class LiveTrackingDrone(BaseModel):
    mission_id: int
    drone_id: Optional[int]
    status: MissionStatus
    latest_lat: Optional[float]
    latest_lng: Optional[float]
    latest_altitude: Optional[float]
    latest_speed: Optional[float]
    latest_battery_voltage: Optional[float]
    last_updated: Optional[datetime]
    checkpoints_passed: Optional[int] = 0
    weather_temperature: Optional[float] = None
    weather_apparent_temp: Optional[float] = None
    weather_dew_point: Optional[float] = None
    weather_humidity: Optional[float] = None
    weather_wind_speed: Optional[float] = None
    weather_wind_gust: Optional[float] = None
    weather_wind_direction: Optional[float] = None
    weather_precipitation: Optional[float] = None
    weather_pressure: Optional[float] = None
    weather_cloud_cover: Optional[float] = None


class LiveTrackingResponse(BaseModel):
    active_count: int
    drones: list[LiveTrackingDrone]

class MissionPlanningAnalyzeRequest(BaseModel):
    orderId: str
    droneId: int

class RouteLegSchema(BaseModel):
    from_hub_code: str
    to_hub_code: str
    distance_km: float

class RouteOptionSchema(BaseModel):
    routeId: str
    route_string: str
    legs_detail: list[RouteLegSchema]
    distanceKm: float
    relayHubs: list[str]
    predictedDurationMin: int
    predictedEnergyWh: float
    batteryConsumptionPct: int
    remainingBatteryPct: int
    confidencePct: int
    risk: str
    recommended: bool
    reason: Optional[str] = None
    suggestedDroneId: Optional[int] = None

class MissionPlanningAnalyzeResponse(BaseModel):
    routes: list[RouteOptionSchema]

class MissionCreateRequest(BaseModel):
    orderId: str
    droneId: int
    routeId: str

