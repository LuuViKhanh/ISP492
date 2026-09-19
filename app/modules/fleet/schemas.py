from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import List, Optional
from app.modules.fleet.models import WorkOrderStatus

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
    
    id: int
    serial_number: str
    capacity_wh: float
    status: str
    drone_id: Optional[int]

class AvailabilityResponse(BaseModel):
    available_drones: List[AvailableDroneSchema]
    available_batteries: List[AvailableBatterySchema]


# ==================== DTO CHO WORK ORDER (BẢO TRÌ) ====================
class WorkOrderCreate(BaseModel):
    drone_id: int
    battery_id: int
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
    battery_id: int
    technician_id: int
    issue_description: str
    action_taken: Optional[str]
    status: WorkOrderStatus
    resolved_at: Optional[datetime]