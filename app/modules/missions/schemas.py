from pydantic import BaseModel
from typing import Dict, Optional, Union
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


class RouteLegSchema(BaseModel):
    from_hub_code: str
    to_hub_code: str
    distance_km: float
    sequence_no: Optional[int] = None
    from_hub_id: Optional[int] = None
    to_hub_id: Optional[int] = None
    from_hub_name: Optional[str] = None
    to_hub_name: Optional[str] = None
    from_latitude: Optional[float] = None
    from_longitude: Optional[float] = None
    to_latitude: Optional[float] = None
    to_longitude: Optional[float] = None
    status: Optional[str] = None


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


class MissionResponse(BaseModel):
    id: str
    order_id: Optional[str] = None
    customer_id: Optional[str] = None
    operator_id: Optional[str] = None
    drone_id: Optional[int] = None
    battery_id: Optional[Union[str, int]] = None
    pickup_location_id: Optional[int] = None
    dropoff_location_id: Optional[int] = None
    payload_weight: Optional[float] = None
    distance_m: Optional[float] = None
    delivery_fee: Optional[float] = None
    status: MissionStatus
    scheduled_time: Optional[datetime] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    origin_hub_id: Optional[int] = None
    destination_hub_id: Optional[int] = None
    departed_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None
    route_path_string: Optional[str] = None
    arrival_confirmed_by: Optional[str] = None
    arrival_confirmed_at: Optional[datetime] = None
    # Thông tin tuyến đường đã lên lịch (đồng bộ với POST /operator/missions/mission-planning/analyze)
    routeId: Optional[str] = None
    route_string: Optional[str] = None
    legs_detail: list[RouteLegSchema] = []
    distanceKm: Optional[float] = None
    relayHubs: list[str] = []
    predictedDurationMin: Optional[int] = None
    predictedEnergyWh: Optional[float] = None
    batteryConsumptionPct: Optional[int] = None
    remainingBatteryPct: Optional[int] = None
    confidencePct: Optional[int] = None
    risk: Optional[str] = None
    recommended: Optional[bool] = True
    reason: Optional[str] = None
    suggestedDroneId: Optional[int] = None
    scheduled_route: Optional[RouteOptionSchema] = None
    routes: list[RouteOptionSchema] = []

    class Config:
        from_attributes = True


class ApproveRejectRequest(BaseModel):
    drone_id: Optional[int] = None
    battery_id: Optional[str] = None
    operator_id: Optional[str] = None
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
    mission_id: Union[int, str]
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
    mission_id: Union[int, str]
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
    mission_id: str
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

class MissionPlanningAnalyzeResponse(BaseModel):
    routes: list[RouteOptionSchema]

class MissionCreateRequest(BaseModel):
    orderId: str
    droneId: int
    routeId: str
    route_string: Optional[str] = None
    legs_detail: Optional[list[RouteLegSchema]] = None
    distance_km: Optional[float] = None
    predicted_energy_wh: Optional[float] = None
    predicted_duration_min: Optional[int] = None



