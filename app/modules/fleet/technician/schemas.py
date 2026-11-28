from pydantic import BaseModel
from typing import Optional, List, Union
from datetime import datetime
from app.modules.fleet.models import DroneStatus, WorkOrderStatus, PartCategoryType, PartsRequestStatus


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
    available_status: Optional[str] = None       # In Use | Ready | Charging | Replaced
    # Vòng đời pin (tính on-the-fly, không lưu DB)
    lifecycle_status: Optional[str] = None       # Active | Expired
    days_until_expiry: Optional[int] = None  # số ngày còn lại đến khi hết vòng đời (730 ngày)


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
    description: Optional[str] = None
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


# ── PartCategory (Danh mục loại vật tư) ───────────────────────────────────────

class PartCategoryCreate(BaseModel):
    """Tạo mới danh mục vật tư."""
    name: str  # e.g., "DJI Matrice 100 Propeller"
    category_type: PartCategoryType  # Loại vật tư (Propeller, Battery, Motor, etc.)
    compatible_drone_models: Optional[List[str]] = None  # e.g., ["DJI Matrice 100", "Aurelia X6"]
    unit_cost: Optional[float] = None  # Giá per unit (VND)
    description: Optional[str] = None
    is_active: bool = True


class PartCategoryUpdate(BaseModel):
    """Cập nhật danh mục vật tư (partial update)."""
    name: Optional[str] = None
    category_type: Optional[PartCategoryType] = None
    compatible_drone_models: Optional[List[str]] = None
    unit_cost: Optional[float] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class PartCategoryResponse(BaseModel):
    """Phản hồi danh mục vật tư."""
    id: int
    name: str
    category_type: PartCategoryType
    compatible_drone_models: Optional[List[str]] = None
    unit_cost: Optional[float] = None
    description: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── PartsRequest (Yêu cầu cấp phát vật tư) ───────────────────────────────────

class PartsRequestCreate(BaseModel):
    """Tạo yêu cầu cấp phát vật tư."""
    part_category_id: int  # ID của loại vật tư
    quantity_needed: int  # Số lượng cần
    description: str  # Lý do/mô tả chi tiết
    drone_id: Optional[str] = None  # Drone cần vật tư này (optional)
    photo_urls: Optional[List[str]] = None  # URLs của ảnh chứng minh


class PartsRequestApprove(BaseModel):
    """Admin duyệt yêu cầu cấp phát vật tư."""
    admin_note: Optional[str] = None  # Ghi chú từ admin


class PartsRequestReject(BaseModel):
    """Admin từ chối yêu cầu cấp phát vật tư."""
    admin_note: Optional[str] = None  # Ghi chú từ admin


class PartsRequestResponse(BaseModel):
    """Phản hồi yêu cầu cấp phát vật tư."""
    id: int
    requested_by: str  # Technician ID
    hub_id: int
    drone_id: Optional[str] = None
    part_category_id: int
    quantity_needed: int
    description: str
    photo_urls: Optional[List[str]] = None
    status: PartsRequestStatus
    reviewed_by: Optional[str] = None
    admin_note: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    actual_cost: Optional[float] = None  # Actual cost when approved (VND)
    created_at: datetime
    fulfilled_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── HubInventory (Tồn kho vật tư tại Hub) ───────────────────────────────────

class HubInventoryCreate(BaseModel):
    """Tạo bản ghi tồn kho vật tư tại hub."""
    part_category_id: int  # ID loại vật tư
    quantity: int = 0  # Số lượng ban đầu
    min_stock_threshold: int = 5  # Mức tồn kho tối thiểu


class HubInventoryUpdate(BaseModel):
    """Cập nhật tồn kho vật tư (partial update)."""
    quantity: Optional[int] = None
    min_stock_threshold: Optional[int] = None


class HubInventoryResponse(BaseModel):
    """Phản hồi tồn kho vật tư."""
    id: int
    hub_id: int
    part_category_id: int
    quantity: int
    min_stock_threshold: int
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class HubInventoryDetailedResponse(BaseModel):
    """Phản hồi tồn kho với thông tin chi tiết về loại vật tư."""
    id: int
    hub_id: int
    part_category: PartCategoryResponse  # Thông tin về loại vật tư
    quantity: int
    min_stock_threshold: int
    is_low_stock: bool = False  # Có phải dưới mức tồn kho tối thiểu không?
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True



# ═════════════════════════════════════════════════════════════════════════════
# Mục 9: Drone, Battery & Maintenance Proof Images & Checklists
# ═════════════════════════════════════════════════════════════════════════════

class BatteryHistoryEntry(BaseModel):
    """Một bản ghi trong lịch sử pin - gắn/tháo/kiểm tra."""
    stage: str  # BATTERY_INSTALLED_ON_DRONE | BATTERY_REMOVED_FROM_DRONE
    action: str  # install | remove | inspection
    removed_battery_id: Optional[str] = None  # Pin cũ được tháo
    installed_battery_id: Optional[str] = None  # Pin mới được lắp
    battery_pct_before: Optional[float] = None  # % pin trước tháo
    battery_pct_after: Optional[float] = None  # % pin sau lắp
    hub_id: Optional[int] = None  # Hub nơi xử lý
    mission_id: Optional[str] = None  # Mission ID (nếu có)
    drone_id: str  # Drone được xử lý
    technician_id: Optional[str] = None  # Technician thực hiện
    note: Optional[str] = None  # Ghi chú thêm
    recorded_at: datetime  # Thời gian ghi nhận


class DroneStatusChangeEntry(BaseModel):
    """Một bản ghi thay đổi trạng thái drone."""
    old_status: str  # Trạng thái cũ
    new_status: str  # Trạng thái mới
    reason: Optional[str] = None  # Lý do thay đổi
    incident_id: Optional[int] = None  # Incident ID (nếu liên quan)
    work_order_id: Optional[int] = None  # Work Order ID (nếu liên quan)
    technician_id: Optional[str] = None  # Technician thực hiện
    note: Optional[str] = None  # Ghi chú
    recorded_at: datetime  # Thời gian ghi nhận


class InspectionItem(BaseModel):
    """Một item trong checklist kiểm tra."""
    item: str  # Mô tả item (ví dụ: "Check motor left")
    checked: bool = False  # Đã check hay chưa
    status: Optional[str] = None  # OK | FAIL | REPAIR_NEEDED
    note: Optional[str] = None  # Ghi chú chi tiết


class WorkOrderProofImage(BaseModel):
    """Hình ảnh minh chứng cho Work Order."""
    type: str  # before | after | during | parts_replaced | incident_recovery
    proof_image_url: Optional[str] = None  # Base64 Data URL
    description: Optional[str] = None  # Mô tả ảnh
    recorded_at: datetime  # Thời gian ghi


class WorkOrderStatusLog(BaseModel):
    """Log thay đổi trạng thái của Work Order."""
    old_status: str
    new_status: str
    changed_by: Optional[str] = None  # User ID
    reason: Optional[str] = None
    changed_at: datetime


class DefectType(str):
    """Enum loại lỗi để auto-generate inspection_items_json."""
    MOTOR_ESC = "MOTOR_ESC"  # Motor/ESC
    SIGNAL_LOSS = "SIGNAL_LOSS"  # Telemetry/Signal Loss
    BATTERY_BMS = "BATTERY_BMS"  # Battery/BMS
    SENSOR_GPS = "SENSOR_GPS"  # Sensor/GPS
    FRAME_DAMAGE = "FRAME_DAMAGE"  # Frame damage
    PROPELLER_DAMAGE = "PROPELLER_DAMAGE"  # Propeller damage
    OTHER = "OTHER"  # Other


def build_inspection_items_from_defect_type(defect_type: str) -> list:
    """Tự động sinh inspection_items_json dựa vào loại lỗi."""
    templates = {
        "MOTOR_ESC": [
            {"item": "Check motor left", "checked": False},
            {"item": "Check motor right", "checked": False},
            {"item": "Check ESC connections", "checked": False},
            {"item": "Test motor rotation (no load)", "checked": False},
        ],
        "SIGNAL_LOSS": [
            {"item": "Check telemetry module", "checked": False},
            {"item": "Check antenna", "checked": False},
            {"item": "Test signal strength", "checked": False},
            {"item": "Verify receiver pairing", "checked": False},
        ],
        "BATTERY_BMS": [
            {"item": "Check battery voltage", "checked": False},
            {"item": "Check battery cells balance", "checked": False},
            {"item": "Inspect battery case for damage", "checked": False},
            {"item": "Test BMS communication", "checked": False},
        ],
        "SENSOR_GPS": [
            {"item": "Check GPS module", "checked": False},
            {"item": "Check compass calibration", "checked": False},
            {"item": "Check barometer", "checked": False},
            {"item": "Verify IMU orientation", "checked": False},
        ],
        "FRAME_DAMAGE": [
            {"item": "Inspect frame for cracks", "checked": False},
            {"item": "Check arm alignment", "checked": False},
            {"item": "Verify landing gear", "checked": False},
        ],
        "PROPELLER_DAMAGE": [
            {"item": "Inspect all 4 propellers", "checked": False},
            {"item": "Check propeller balance", "checked": False},
            {"item": "Verify propeller mounting", "checked": False},
        ],
    }
    return templates.get(defect_type, [{"item": "General inspection", "checked": False}])
