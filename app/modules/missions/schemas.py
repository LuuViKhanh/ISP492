from pydantic import BaseModel
from typing import Dict, Optional, Union, Any
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


class RelayHubBatteryOptionSchema(BaseModel):
    """Thông tin một viên pin khả dụng tại Hub trung gian."""
    id: str
    serial_number: str
    battery_model: Optional[str] = None
    capacity_wh: float
    charge_level_pct: int = 100
    available_energy_wh: float


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
    # Thông tin năng lượng chặng & viên pin cụ thể được sử dụng cho chặng bay này
    energy_wh: Optional[float] = None
    battery_id: Optional[str] = None
    battery_serial: Optional[str] = None
    battery_model: Optional[str] = None
    battery_capacity_wh: Optional[float] = None
    battery_charge_pct: Optional[int] = None
    battery_energy_wh: Optional[float] = None
    battery_consumption_pct: Optional[int] = None
    remaining_battery_pct: Optional[int] = None
    leg_type: str = "DELIVERY"  # DISPATCH (bay sang nhận hàng) hoặc DELIVERY (giao hàng)
    is_dispatch: bool = False
    payload_kg: Optional[float] = None
    note: Optional[str] = None


class HubBatteryStatus(BaseModel):
    """Trạng thái pin và viên pin được chỉ định thay thế tại một Hub trung gian trong relay route."""
    hub_id: int
    hub_code: str
    hub_name: str
    ready_count: int = 0        # số pin Ready (sạc 100%, tương thích, chưa gắn drone, chưa bị giữ chỗ)
    compatible_models: list[str] = []
    feasible: bool = True       # True nếu có ít nhất 1 pin Ready tương thích và đủ năng lượng (dự phòng >= 20%)
    # Chỉ định chính xác viên pin tại Hub trung gian sẽ được dùng để thay cho chặng tiếp theo
    selected_battery_id: Optional[str] = None
    selected_battery_serial: Optional[str] = None
    selected_battery_model: Optional[str] = None
    selected_battery_capacity_wh: Optional[float] = None
    selected_battery_charge_pct: Optional[int] = None
    selected_battery_energy_wh: Optional[float] = None
    swap_battery_id: Optional[str] = None
    swap_battery_serial: Optional[str] = None
    available_batteries: list[RelayHubBatteryOptionSchema] = []


class FeatureContributionSchema(BaseModel):
    feature: str
    label: str
    value: str
    shap_contribution_wh: float
    impact: str


class LegBreakdownSchema(BaseModel):
    leg: int
    from_hub_code: str
    to_hub_code: str
    distance_km: float
    energy_wh: float
    speed_mps: float
    speed_kmh: Optional[float] = None
    action: Optional[str] = None


class AIExplanationSchema(BaseModel):
    flight_speed_mps: float
    flight_speed_kmh: float
    total_energy_wh: float
    feature_contributions: list[FeatureContributionSchema] = []
    legs_breakdown: list[LegBreakdownSchema] = []
    summary: str


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
    suggestedDroneId: Optional[Union[str, int]] = None
    # Thông tin viên pin được assign cho Drone thực hiện nhiệm vụ (Chặng 1)
    assignedBatteryId: Optional[str] = None
    assignedBatterySerial: Optional[str] = None
    assignedBatteryModel: Optional[str] = None
    assignedBatteryCapacityWh: Optional[float] = None
    assignedBatteryChargePct: Optional[int] = None
    assignedBatteryEnergyWh: Optional[float] = None
    # Battery feasibility & kế hoạch thay pin ở các Hub trung gian
    feasible: bool = True
    infeasible_reason: Optional[str] = None
    is_selectable: bool = True
    unselectable_reason: Optional[str] = None
    relay_battery_status: list[HubBatteryStatus] = []
    # Cross-Hub Drone Dispatch: thông tin điều phối Drone từ Hub khác sang Hub nhận hàng
    is_cross_hub: bool = False
    drone_home_hub_id: Optional[int] = None
    drone_home_hub_name: Optional[str] = None
    drone_home_hub_code: Optional[str] = None
    pickup_hub_id: Optional[int] = None
    pickup_hub_name: Optional[str] = None
    pickup_hub_code: Optional[str] = None
    # Thông tin tốc độ hành trình & giải thích AI XAI (SHAP theo FA26IS01)
    flight_speed_mps: Optional[float] = None
    speed_profile_label: Optional[str] = None
    shap_values: Optional[Dict[str, float]] = None
    ai_explanation: Optional[Union[AIExplanationSchema, Dict[str, Any]]] = None


class MissionResponse(BaseModel):
    id: str
    order_id: Optional[str] = None
    customer_id: Optional[str] = None
    operator_id: Optional[str] = None
    drone_id: Optional[Union[str, int]] = None
    battery_id: Optional[Union[str, int]] = None
    battery_serial: Optional[str] = None
    battery_model: Optional[str] = None
    battery_capacity_wh: Optional[float] = None
    battery_charge_pct: Optional[int] = None
    battery_energy_wh: Optional[float] = None
    assignedBatteryId: Optional[str] = None
    assignedBatterySerial: Optional[str] = None
    assignedBatteryModel: Optional[str] = None
    assignedBatteryCapacityWh: Optional[float] = None
    assignedBatteryChargePct: Optional[int] = None
    assignedBatteryEnergyWh: Optional[float] = None
    pickup_location_id: Optional[int] = None
    dropoff_location_id: Optional[int] = None
    payload_weight: Optional[float] = None
    bundled_order_ids: Optional[list[str]] = None
    total_orders_count: Optional[int] = 1
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
    leg_destination_type: Optional[str] = None  # "destination" hoặc "connection"
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
    suggestedDroneId: Optional[Union[str, int]] = None
    relay_battery_status: list[HubBatteryStatus] = []
    scheduled_route: Optional[RouteOptionSchema] = None
    routes: list[RouteOptionSchema] = []

    class Config:
        from_attributes = True


class ApproveRejectRequest(BaseModel):
    drone_id: Optional[Union[str, int]] = None
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
    # ── Failsafe In-flight Status ─────────────────────────────
    failsafe_status: Optional[str] = "SAFE"
    failsafe_alert: Optional[str] = None
    is_diverted: bool = False
    divert_hub_id: Optional[int] = None
    divert_hub_name: Optional[str] = None

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
    drone_id: Optional[Union[str, int]]
    status: MissionStatus
    latest_lat: Optional[float]
    latest_lng: Optional[float]
    latest_altitude: Optional[float]
    latest_speed: Optional[float]
    latest_battery_voltage: Optional[float]
    latest_battery_pct: Optional[int] = None
    last_updated: Optional[datetime]
    checkpoints_passed: Optional[int] = 0
    current_leg: Optional[dict] = None
    legs: list[dict] = []
    trail: list[dict] = []
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
    # ── Failsafe In-flight Status ─────────────────────────────
    failsafe_status: Optional[str] = "SAFE"
    failsafe_alert: Optional[str] = None
    is_diverted: bool = False
    divert_hub_id: Optional[int] = None
    divert_hub_name: Optional[str] = None


class LiveTrackingResponse(BaseModel):
    active_count: int
    drones: list[LiveTrackingDrone]

class MissionPlanningAnalyzeRequest(BaseModel):
    orderId: str
    droneId: Union[str, int]
    batteryId: Optional[str] = None
    battery_id: Optional[str] = None
    bundledOrderIds: Optional[list[str]] = None

class MissionPlanningAnalyzeResponse(BaseModel):
    orderId: Optional[str] = None
    bundledOrderIds: Optional[list[str]] = None
    totalPayloadKg: Optional[float] = None
    droneId: Optional[Union[str, int]] = None
    assignedBatteryId: Optional[str] = None
    assignedBatterySerial: Optional[str] = None
    assignedBatteryModel: Optional[str] = None
    assignedBatteryCapacityWh: Optional[float] = None
    assignedBatteryChargePct: Optional[int] = None
    assignedBatteryEnergyWh: Optional[float] = None
    droneCurrentHubId: Optional[int] = None
    droneCurrentHubName: Optional[str] = None
    droneCurrentHubCode: Optional[str] = None
    isCrossHubDispatch: bool = False
    pickupHubId: Optional[int] = None
    pickupHubName: Optional[str] = None
    pickupHubCode: Optional[str] = None
    routes: list[RouteOptionSchema]

class MissionCreateRequest(BaseModel):
    orderId: str
    droneId: Union[str, int]
    batteryId: Optional[str] = None
    battery_id: Optional[str] = None
    routeId: str

    route_string: Optional[str] = None
    legs_detail: Optional[list[RouteLegSchema]] = None
    relay_battery_ids: Optional[Dict[Union[int, str], str]] = None
    distance_km: Optional[float] = None
    predicted_energy_wh: Optional[float] = None
    predicted_duration_min: Optional[int] = None
    bundledOrderIds: Optional[list[str]] = None







# ── Order Consolidation / Piggybacking Schemas ───────────────

class PreAnalyzeBundleCandidatesRequest(BaseModel):
    orderId: str

class ValidateBundleRequest(BaseModel):
    orderIds: list[str]
    droneId: Optional[Union[str, int]] = None

class PostAnalyzeBundleCandidatesRequest(BaseModel):
    orderId: str
    droneId: Union[str, int]
    routeId: Optional[str] = None

# ── Failsafe & Dynamic In-flight Divert Schemas ───────────────

class FailsafeEvaluateRequest(BaseModel):
    current_lat: Optional[float] = None
    current_lng: Optional[float] = None
    current_altitude: Optional[float] = 50.0
    override_battery_pct: Optional[float] = None
    override_wind_speed: Optional[float] = None
    simulate_wind_anomaly: bool = False  # Giả lập gặp gió lớn ngược chiều (> 8 m/s) làm suy hao pin nhanh

class FailsafeHubInfo(BaseModel):
    hub_id: int
    hub_code: Optional[str] = None
    hub_name: str
    latitude: float
    longitude: float
    distance_km: float
    estimated_battery_needed_pct: float
    estimated_remaining_pct: float
    is_safe: bool

class FailsafeEvaluateResponse(BaseModel):
    mission_id: str
    current_status: str
    drone_id: Optional[Union[str, int]] = None
    current_lat: float
    current_lng: float
    current_battery_pct: float
    wind_speed: float
    wind_warning: bool
    status: str  # SAFE | WARNING | RTH_TRIGGERED | DIVERT_TRIGGERED | EMERGENCY_LAND_TRIGGERED
    tier: int    # 0: Bình thường | 1: RTH | 2: Divert Hub | 3: Emergency Landing
    action: str  # CONTINUE | RETURN_TO_HOME | DIVERT_TO_HUB | EMERGENCY_LAND
    reason: str
    pnr_reached: bool
    target_hub: Optional[FailsafeHubInfo] = None
    origin_hub: Optional[FailsafeHubInfo] = None
    recommended_hub: Optional[FailsafeHubInfo] = None
    available_emergency_hubs: list[FailsafeHubInfo] = []
    divert_route: list[dict] = []
    can_execute: bool = False

class FailsafeExecuteRequest(BaseModel):
    action: str  # RETURN_TO_HOME | DIVERT_TO_HUB | EMERGENCY_LAND
    target_hub_id: Optional[int] = None
    reason: Optional[str] = "Phát hiện suy hao pin bất thường giữa chặng"

class FailsafeExecuteResponse(BaseModel):
    mission_id: str
    previous_status: str
    current_status: str
    action_taken: str
    target_hub_id: Optional[int] = None
    target_hub_name: Optional[str] = None
    incident_id: Optional[int] = None
    alert_id: Optional[int] = None
    message: str
