from pydantic import BaseModel
from typing import Optional, List, Union
from datetime import datetime
from app.modules.missions.models import OrderStatus, DeliveryMode

class OriginPackageSchema(BaseModel):
    received: bool
    received_at: Optional[datetime] = None

class HubSchema(BaseModel):
    id: int
    name: str

class OrderResponse(BaseModel):
    id: str
    customer_id: Optional[str]
    package_label: Optional[str]
    package_type: Optional[str]
    payload_kg: Optional[float]
    origin_hub: Optional[HubSchema] = None
    destination_hub: Optional[HubSchema] = None
    delivery_mode: Optional[Union[DeliveryMode, str]] = None
    status: Optional[Union[OrderStatus, str]] = None
    payment_status: Optional[str] = None
    mission_id: Optional[str] = None
    planningState: Optional[str] = None
    planning_state_label: Optional[str] = None
    can_create_mission: bool = False
    originPackage: Optional[OriginPackageSchema] = None
    requested_delivery_at: Optional[datetime] = None
    estimated_window_start: Optional[datetime] = None
    # estimated_window_end = ETA (giờ giao dự kiến, dynamic)
    estimated_window_end: Optional[datetime] = None
    planning_at: Optional[datetime] = None
    # ── Deadline & Urgency fields (computed, không lưu DB) ────────────
    # customer_deadline: deadline cam kết với khách (= estimated_window_end khi tạo đơn)
    customer_deadline: Optional[datetime] = None
    # coordination_deadline: thời điểm Operator phải assign xong drone để kịp deadline
    coordination_deadline: Optional[datetime] = None
    # overdue_minutes > 0 = số phút đã trễ so với customer_deadline
    overdue_minutes: Optional[float] = None
    is_overdue: bool = False
    # Aging Priority fields
    is_aging_priority: bool = False
    aging_deadline: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class BatteryEligibleSchema(BaseModel):
    id: str
    serial_number: str
    battery_model: Optional[str] = None
    capacity_wh: float
    charge_level_pct: Optional[int] = None
    available_energy_wh: float
    current_hub_id: Optional[int] = None
    drone_id: Optional[Union[str, int]] = None
    is_installed_on_drone: bool = False
    available_status: Optional[str] = "Ready"

class DroneEligibleSchema(BaseModel):
    id: Union[str, int]
    name: Optional[str] = None
    model: str
    maxPayloadKg: float
    battery_level_pct: Optional[int] = None
    installed_battery_id: Optional[str] = None
    default_battery_id: Optional[str] = None
    compatible_battery_models: List[str] = []
    eligible_batteries: List[BatteryEligibleSchema] = []

class HubEligibleSchema(BaseModel):
    id: int
    name: str
    totalDrones: int
    availableDrones: int

class EligibleDronesResponse(BaseModel):
    hub: HubEligibleSchema
    drones: List[DroneEligibleSchema]
    batteries: List[BatteryEligibleSchema] = []

class EligibleBatteriesResponse(BaseModel):
    order_id: Optional[str] = None
    hub_id: int
    hub_name: Optional[str] = None
    drone_id: Union[str, int]
    drone_name: Optional[str] = None
    drone_model: str
    compatible_battery_models: List[str] = []
    default_battery_id: Optional[str] = None
    batteries: List[BatteryEligibleSchema] = []


# ======================================================================
# CUSTOMER — Order Management & Tracking (Spec VI)
# ======================================================================

class OrderCreateRequest(BaseModel):
    """Customer tạo yêu cầu giao hàng (Create Delivery). Bỏ qua thanh toán."""
    package_label: Optional[str] = None
    package_type: Optional[str] = None
    payload_kg: float
    origin_hub_id: int          # Điểm lấy hàng (Pickup)
    destination_hub_id: int     # Điểm giao hàng (Delivery)
    delivery_mode: Optional[Union[DeliveryMode, str]] = None
    requested_delivery_at: Optional[datetime] = None


class OrderUpdateRequest(BaseModel):
    """Operator cập nhật đơn (chỉ khi đơn còn PENDING)."""
    package_label: Optional[str] = None
    package_type: Optional[str] = None
    payload_kg: Optional[float] = None
    destination_hub_id: Optional[int] = None
    delivery_mode: Optional[Union[DeliveryMode, str]] = None
    requested_delivery_at: Optional[datetime] = None
    planning_at: Optional[datetime] = None


class OrderCancelRequest(BaseModel):
    reason: Optional[str] = None


class CustomerOrderSummary(BaseModel):
    """Đơn hàng dưới góc nhìn Customer (list + detail), kèm trạng thái giao thân thiện."""
    id: str
    package_label: Optional[str] = None
    package_type: Optional[str] = None
    payload_kg: Optional[float] = None
    delivery_mode: Optional[Union[DeliveryMode, str]] = None
    status: Optional[Union[OrderStatus, str]] = None
    delivery_status: str
    delivery_status_label: str
    origin_hub_id: Optional[int] = None
    origin_hub_name: Optional[str] = None
    destination_hub_id: Optional[int] = None
    destination_hub_name: Optional[str] = None
    mission_id: Optional[str] = None
    requested_delivery_at: Optional[datetime] = None
    estimated_window_start: Optional[datetime] = None
    estimated_window_end: Optional[datetime] = None
    created_at: Optional[datetime] = None


# ── Tracking ──────────────────────────────────────────────────────────

class DroneLocationSchema(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    speed: Optional[float] = None
    battery_voltage: Optional[float] = None
    battery_pct: Optional[int] = None
    last_updated: Optional[datetime] = None


class RoutePointSchema(BaseModel):
    latitude: float
    longitude: float
    name: Optional[str] = None


class TrackingHubSchema(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class TrackingLegSchema(BaseModel):
    id: Optional[int] = None
    sequence_no: int
    status: Optional[str] = None
    from_hub: Optional[TrackingHubSchema] = None
    to_hub: Optional[TrackingHubSchema] = None
    battery_id: Optional[str] = None


class TrackingCurrentLegSchema(BaseModel):
    id: Optional[int] = None
    sequence_no: int
    status: Optional[str] = None
    from_hub: Optional[TrackingHubSchema] = None
    to_hub: Optional[TrackingHubSchema] = None
    assigned_battery_id: Optional[str] = None


class TrailPointSchema(BaseModel):
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    speed: Optional[float] = None
    timestamp: Optional[datetime] = None


class TrackingResponse(BaseModel):
    order_id: str
    delivery_status: str
    delivery_status_label: str
    eta: Optional[datetime] = None                 # = order.estimated_window_end
    estimated_window_start: Optional[datetime] = None
    mission_id: Optional[str] = None
    mission_status: Optional[str] = None
    drone_id: Optional[Union[str, int]] = None
    drone_location: Optional[DroneLocationSchema] = None
    origin: Optional[RoutePointSchema] = None
    destination: Optional[RoutePointSchema] = None
    travelled_path: List[RoutePointSchema] = []
    checkpoints_passed: int = 0
    current_leg: Optional[TrackingCurrentLegSchema] = None
    legs: List[TrackingLegSchema] = []
    trail: List[TrailPointSchema] = []


# ── Mission Report ────────────────────────────────────────────────────

class ReportHubSchema(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class ReportRouteSchema(BaseModel):
    origin: Optional[ReportHubSchema] = None
    destination: Optional[ReportHubSchema] = None
    distance_m: Optional[float] = None


class ReportTimelineSchema(BaseModel):
    created_at: Optional[datetime] = None
    planned_start_at: Optional[datetime] = None
    departed_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None
    arrival_confirmed_at: Optional[datetime] = None


class ReportPredictionSchema(BaseModel):
    predicted_duration_min: Optional[int] = None
    estimated_energy_wh: Optional[float] = None
    battery_consumption_pct: Optional[int] = None
    predicted_remaining_battery_pct: Optional[int] = None
    confidence_pct: Optional[int] = None
    risk_level: Optional[str] = None


class ReportDroneSchema(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    model: Optional[str] = None


class ReportActualsSchema(BaseModel):
    actual_duration_min: Optional[float] = None
    actual_energy_wh: Optional[float] = None
    telemetry_points: int = 0


class MissionReportResponse(BaseModel):
    order_id: str
    mission_id: Optional[str] = None
    mission_code: Optional[str] = None
    package_label: Optional[str] = None
    package_type: Optional[str] = None
    payload_kg: Optional[float] = None
    delivery_mode: Optional[Union[DeliveryMode, str]] = None
    status: Optional[Union[OrderStatus, str]] = None
    delivery_status_label: str
    route: ReportRouteSchema
    timeline: ReportTimelineSchema
    prediction: ReportPredictionSchema
    drone: Optional[ReportDroneSchema] = None
    actuals: ReportActualsSchema
    generated_at: datetime
