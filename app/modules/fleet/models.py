import enum
from datetime import datetime
from sqlalchemy import BigInteger, Integer, Float, String, Text, DateTime, Boolean, ForeignKey, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.db import Base

# ==================== ENUMS ====================
class DroneStatus(str, enum.Enum):
    AVAILABLE   = "Available"
    IN_FLIGHT   = "In Flight"
    MAINTENANCE = "Maintenance"
    RETIRED     = "Retired"

class batteries_status(str, enum.Enum):
    ACTIVE = "Active"
    REPLACED = "Replaced"


class BatteryAvailableStatus(str, enum.Enum):
    """Computed status — không lưu DB, chỉ dùng trong API response."""
    CHARGING = "Charging"   # đang sạc tại hub (charge_level_pct < 100)
    READY    = "Ready"      # sạc đầy 100%, sẵn sàng dùng
    IN_USE   = "In Use"     # đang được dùng trong work order
    REPLACED = "Replaced"   # hết vòng đời


class BatteryLifecycleStatus(str, enum.Enum):
    """Vòng đời pin theo thời gian kể từ khi nhập kho (create_at).

    Quy ước: Pin drone có tuổi thọ khoảng 2 năm (730 ngày) kể từ ngày nhập kho.
    - ACTIVE    : còn trong vòng đời (< 730 ngày)
    - EXPIRED   : hết vòng đời (>= 730 ngày) → cần thay thế
    """
    ACTIVE  = "Active"
    EXPIRED = "Expired"


# Số ngày tối đa của vòng đời pin (2 năm)
BATTERY_LIFECYCLE_DAYS = 730


def compute_battery_available_status(battery: "Battery") -> BatteryAvailableStatus:
    """
    Tính toán trạng thái khả dụng của pin từ các field hiện có.

    Rules (ưu tiên từ trên xuống):
    1. status = Replaced                          → Replaced
    2. work_order_id IS NOT NULL                  → In Use (đang dùng trong work order)
    3. drone_id IS NOT NULL                       → Installed (đang gắn trên drone)
    4. charge_level_pct >= 100                    → Ready  (đủ 100%, có thể dùng ngay)
    5. còn lại                                    → Charging (đang sạc tại hub)
    """
    if battery.status == batteries_status.REPLACED:
        return BatteryAvailableStatus.REPLACED
    if battery.work_order_id is not None:
        return BatteryAvailableStatus.IN_USE
    if battery.drone_id is not None:
        return BatteryAvailableStatus.IN_USE
    if (battery.charge_level_pct or 0) >= 100:
        return BatteryAvailableStatus.READY
    return BatteryAvailableStatus.CHARGING


def compute_battery_lifecycle(battery: "Battery", now: datetime | None = None) -> tuple[BatteryLifecycleStatus, int]:
    """
    Tính on-the-fly vòng đời pin dựa trên `create_at` (ngày nhập kho).

    Returns:
        (lifecycle_status, days_until_expiry)
        - lifecycle_status: Active | Expired
        - days_until_expiry: số ngày còn lại đến khi hết vòng đời (0 nếu đã Expired)

    Logic:
        age_in_days = (now - create_at).days
        - Nếu create_at NULL → mặc định Active, days_until_expiry = 730 (chưa biết tuổi)
        - Nếu age_in_days < 730 → Active, days_until_expiry = 730 - age_in_days
        - Nếu age_in_days >= 730 → Expired, days_until_expiry = 0
    """
    from datetime import datetime, timezone

    if now is None:
        now = datetime.now(timezone.utc)

    # Nếu pin đã bị đánh dấu Replaced → coi như Expired
    if battery.status == batteries_status.REPLACED:
        return BatteryLifecycleStatus.EXPIRED, 0

    if battery.create_at is None:
        # Không có ngày nhập kho → mặc định Active, chưa biết tuổi
        return BatteryLifecycleStatus.ACTIVE, BATTERY_LIFECYCLE_DAYS

    # Chuẩn hóa create_at về aware UTC để so sánh
    create_at = battery.create_at
    if create_at.tzinfo is None:
        # naive → giả định UTC
        create_at = create_at.replace(tzinfo=timezone.utc)
    # Chuẩn hóa now về aware UTC
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age_in_days = (now - create_at).days

    if age_in_days >= BATTERY_LIFECYCLE_DAYS:
        return BatteryLifecycleStatus.EXPIRED, 0
    if age_in_days < 0:
        # create_at trong tương lai (do lệch múi giờ) → coi như mới
        return BatteryLifecycleStatus.ACTIVE, BATTERY_LIFECYCLE_DAYS
    return BatteryLifecycleStatus.ACTIVE, BATTERY_LIFECYCLE_DAYS - age_in_days

class WorkOrderStatus(str, enum.Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"

# ==================== MODELS ====================
class Drone(Base):
    __tablename__ = "drones"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_capacity_kg: Mapped[float] = mapped_column(Float, nullable=False)
    max_speed: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[DroneStatus] = mapped_column("operational_status", SAEnum(DroneStatus, name="drone_status", create_type=False, values_callable=lambda x: [e.value for e in x]))
    current_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    battery_level_pct: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    batteries = relationship("Battery", back_populates="drone")


class Battery(Base):
    __tablename__ = "batteries"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    drone_id: Mapped[str | None] = mapped_column(String(50), ForeignKey("drones.id"), nullable=True)
    serial_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    capacity_wh: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[batteries_status] = mapped_column(SAEnum(batteries_status, name="batteries_status", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=False)
    current_hub_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("hubs.id"), nullable=True)
    charge_level_pct: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    create_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    battery_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Thời điểm pin gần nhất được sạc đầy 100% (ghi bởi charging worker hoặc manual mark-charged)
    last_charged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    work_order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("work_orders.id"), nullable=True)
    
    drone = relationship("Drone", back_populates="batteries")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drone_id: Mapped[str | None] = mapped_column(String(50), ForeignKey("drones.id"), nullable=True)
    battery_id: Mapped[str | None] = mapped_column(String(50), ForeignKey("batteries.id"), nullable=True)
    technician_id: Mapped[str] = mapped_column(String, nullable=False)
    issue_description: Mapped[str] = mapped_column(Text, nullable=False)
    action_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[WorkOrderStatus] = mapped_column(
        SAEnum(WorkOrderStatus, name="work_orders_status", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=WorkOrderStatus.PENDING,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("maintenance_alerts.id"), nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    resulting_drone_status: Mapped[str | None] = mapped_column(String, nullable=True)
    record_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inspection_items_json: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    status_logs_json: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    # parts_replaced_json: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)  # Column does not exist in DB work_orders


class MaintenanceAlert(Base):
    __tablename__ = "maintenance_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drone_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    maintenance_schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mission_id: Mapped[str | None] = mapped_column(String, nullable=True)
    incident_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    work_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    handled_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # ── Acknowledge fields ────────────────────────────────────────────────────
    @property
    def acknowledged_by(self) -> str | None:
        return None
    @property
    def acknowledged_at(self) -> datetime | None:
        return None


class MaintenanceSchedule(Base):
    __tablename__ = "maintenance_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drone_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    maintenance_type: Mapped[str | None] = mapped_column(String, nullable=True)
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_flight_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_inspection_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_inspection_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ==================== PARTS MANAGEMENT (Quản lý vật tư - Parts) ====================
from app.modules.supplies.models import (
    PartCategoryType,
    PartsRequestStatus,
    SupplyTxnReason as TxnReason,
    PartCategory,
    PartsRequest,
    HubSupply,
    SupplyTransaction,
    HubInventory,        # backward-compat alias
    InventoryTransaction, # backward-compat alias
)


# ==================== BATTERY MODEL CATALOG ====================

class BatteryModelCatalog(Base):
    """
    Catalog các model pin drone: tên, dung lượng, drone tương thích, giá nhập kho.

    Tương tự PartCategory cho spare parts — dùng để:
    - Validate khi Admin thêm pin mới vào kho
    - Tính chi phí nhập kho trong báo cáo tài chính
    - Hiển thị thông tin tương thích cho Operator/Technician
    """
    __tablename__ = "battery_model_catalog"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Tên model chính thức của nhà sản xuất (unique)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    # Danh sách drone model tương thích: ["DJI Mavic 3", "DJI Matrice 300"]
    compatible_drone_models: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Dung lượng (Wh)
    capacity_wh: Mapped[float] = mapped_column(Float, nullable=False)
    # Giá nhập kho (VNĐ / viên)
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
