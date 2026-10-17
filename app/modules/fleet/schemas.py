from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import List, Optional, Union
from app.modules.fleet.models import WorkOrderStatus, BatteryAvailableStatus, compute_battery_available_status

# ==================== DTO CHO CHECK AVAILABILITY ====================
class CheckAvailabilityRequest(BaseModel):
    scheduled_time: datetime
    payload_weight_kg: float
    estimated_energy_wh: Optional[float] = None # Lấy từ AI Model Prediction để lọc Pin

class AvailableDroneSchema(BaseModel):
    # Cấu hình để Pydantic convert từ SQLAlchemy instance
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    name: str
    model: str
    payload_capacity_kg: float
    status: str

class AvailableBatterySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    serial_number: str
    capacity_wh: float
    status: str
    drone_id: Optional[int] = None
    current_hub_id: Optional[int] = None
    charge_level_pct: Optional[int] = None
    create_at: Optional[datetime] = None
    update_at: Optional[datetime] = None
    battery_model: Optional[str] = None
    available_status: Optional[BatteryAvailableStatus] = None  # computed, không lưu DB

    @classmethod
    def from_battery(cls, battery) -> "AvailableBatterySchema":
        """Tạo schema kèm computed available_status từ Battery ORM object."""
        data = {c.name: getattr(battery, c.name) for c in battery.__table__.columns}
        data["available_status"] = compute_battery_available_status(battery)
        return cls(**data)

class AvailabilityResponse(BaseModel):
    available_drones: List[AvailableDroneSchema]
    available_batteries: List[AvailableBatterySchema]


# ==================== DTO CHO WORK ORDER (BẢO TRÌ) ====================
class WorkOrderCreate(BaseModel):
    drone_id: int
    battery_id: Optional[str] = None
    issue_description: str

class WorkOrderUpdate(BaseModel):
    issue_description: Optional[str] = None
    action_taken: Optional[str] = None

class InspectionUpdate(BaseModel):
    action_taken: str
    status: WorkOrderStatus

class WorkOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    drone_id: int
    battery_id: Optional[str] = None
    technician_id: str
    issue_description: str
    action_taken: Optional[str] = None
    status: WorkOrderStatus
    resolved_at: Optional[datetime] = None
    alert_id: Optional[int] = None
    title: Optional[str] = None
    priority: Optional[str] = None
    created_by: Optional[str] = None
    assigned_to: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    drone_code: Optional[str] = None
    model: Optional[str] = None
    assigned_technician_name: Optional[str] = None