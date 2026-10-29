from pydantic import BaseModel
from typing import Optional, List, Union
from datetime import datetime
from app.modules.fleet.models import DroneStatus, WorkOrderStatus


class DroneProfileResponse(BaseModel):
    id: Union[str, int]
    name: str
    model: str
    payload_capacity_kg: float
    max_speed: float
    status: DroneStatus
    current_hub_id: Optional[int] = None
    battery_level_pct: Optional[int] = None
    utilization_pct: Optional[float] = None
    hub_name: Optional[str] = None
    installed_battery_id: Optional[str] = None
    battery_code: Optional[str] = None
    last_flight_at: Optional[datetime] = None
    last_maintenance_at: Optional[datetime] = None
    next_maintenance_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class IncomingMissionResponse(BaseModel):
    mission_code: str
    drone_id: Optional[Union[str, int]] = None
    drone_code: Optional[str] = None
    model: Optional[str] = None
    mission_state: Optional[str] = None
    battery_code: Optional[str] = None
    battery_percent: Optional[int] = None
    handling_status: Optional[str] = None
    arrival_confirmed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BatteryResponse(BaseModel):
    id: str
    serial_number: str
    capacity_wh: float
    status: str
    drone_id: Optional[Union[str, int]] = None
    current_hub_id: Optional[int] = None
    charge_level_pct: Optional[int] = None
    create_at: Optional[datetime] = None
    update_at: Optional[datetime] = None
    battery_model: Optional[str] = None
    last_charged_at: Optional[datetime] = None
    estimated_minutes_remaining: Optional[int] = None
    # Vòng đời pin (tính on-the-fly, không lưu DB)
    lifecycle_status: Optional[str] = None       # Active | Expired
    days_until_expiry: Optional[int] = None      # số ngày còn lại đến khi hết vòng đời (730 ngày)


class BatteryUseRequest(BaseModel):
    mission_id: str


class DroneStatusUpdate(BaseModel):
    status: DroneStatus


class MaintenanceHistoryItem(BaseModel):
    id: int
    drone_id: Optional[Union[str, int]] = None
    battery_id: Optional[str] = None
    technician_id: str
    issue_description: str
    action_taken: Optional[str] = None
    status: WorkOrderStatus
    resolved_at: Optional[datetime] = None
    title: Optional[str] = None
    priority: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class InspectionItemResponse(BaseModel):
    id: int
    item_name: Optional[str]
    is_completed: Optional[bool]
    checked_at: Optional[datetime]

    class Config:
        from_attributes = True


class InspectionItemCreate(BaseModel):
    item_name: str
    is_completed: bool = False


class MaintenanceRecordCreate(BaseModel):
    work_order_id: int
    drone_id: Union[str, int]
    title: Optional[str] = None
    diagnosis: Optional[str] = None
    corrective_action: Optional[str] = None
    resulting_drone_status: Optional[str] = None
    inspection_items: Optional[List[InspectionItemCreate]] = []


class MaintenanceRecordResponse(BaseModel):
    id: int
    work_order_id: Optional[int]
    drone_id: Optional[Union[str, int]] = None
    performed_by: Optional[str]
    title: Optional[str]
    diagnosis: Optional[str]
    corrective_action: Optional[str]
    resulting_drone_status: Optional[str]
    created_at: Optional[datetime]
    completed_at: Optional[datetime]
    inspection_items: List[InspectionItemResponse] = []

    class Config:
        from_attributes = True


class WorkOrderLogResponse(BaseModel):
    id: int
    work_order_id: Optional[int]
    action: Optional[str]
    from_status: Optional[str]
    to_status: Optional[str]
    changed_by: Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class MaintenanceAlertResponse(BaseModel):
    id: int
    drone_id: Optional[str] = None
    source: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    work_order_id: Optional[int] = None
    handled_by: Optional[str] = None
    created_at: Optional[datetime] = None
    handled_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MaintenanceScheduleResponse(BaseModel):
    id: int
    drone_id: Optional[Union[str, int]] = None
    maintenance_type: Optional[str]
    interval_days: Optional[int]
    interval_flight_hours: Optional[float]
    last_inspection_at: Optional[datetime]
    next_inspection_at: Optional[datetime]
    status: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class MaintenanceScheduleCreate(BaseModel):
    drone_id: Union[str, int]
    maintenance_type: str
    interval_days: Optional[int] = None
    interval_flight_hours: Optional[float] = None


# ── HubSupply (Quản lý vật tư tại Hub) ───────────────────────────────────────

class HubSupplyCreate(BaseModel):
    """Tạo mới bản ghi vật tư tại hub."""
    hub_id: int
    item_name: str
    quantity: int = 0
    unit: Optional[str] = None       # vd: "cái", "bộ", "chiếc"


class HubSupplyUpdate(BaseModel):
    """Cập nhật vật tư (partial update — chỉ field nào được truyền mới thay đổi)."""
    item_name: Optional[str] = None
    quantity: Optional[int] = None
    unit: Optional[str] = None


class HubSupplyResponse(BaseModel):
    """Phản hồi khi truy vấn vật tư."""
    id: int
    hub_id: Optional[int] = None
    item_name: str
    quantity: int
    unit: Optional[str] = None
    updated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── SupplyRequest (Yêu cầu cấp phát vật tư) ───────────────────────────────────

class SupplyRequestItemSchema(BaseModel):
    """Item trong yêu cầu cấp phát."""
    item_name: str
    quantity: int
    unit: Optional[str] = None  # vd: "cái", "bộ"


class SupplyRequestCreate(BaseModel):
    """Tạo yêu cầu cấp phát vật tư."""
    items: List[SupplyRequestItemSchema]
    reason: Optional[str] = None


class SupplyRequestApprove(BaseModel):
    """Admin duyệt yêu cầu."""
    admin_notes: Optional[str] = None


class SupplyRequestReject(BaseModel):
    """Admin từ chối yêu cầu."""
    admin_notes: Optional[str] = None


class SupplyRequestConfirmReceipt(BaseModel):
    """Technician xác nhận đã nhập hàng."""
    pass  # Không cần tham số, chỉ cần xác nhận


class SupplyRequestResponse(BaseModel):
    """Phản hồi yêu cầu cấp phát vật tư."""
    id: int
    technician_id: str
    hub_id: int
    status: str  # Pending | Approved | Rejected | Received
    items: str  # JSON string
    reason: Optional[str] = None
    admin_notes: Optional[str] = None
    requested_at: datetime
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    received_at: Optional[datetime] = None
    received_by: Optional[str] = None

    class Config:
        from_attributes = True
