from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import List, Optional

# --- DTO cho Request ---
class CheckAvailabilityRequest(BaseModel):
    scheduled_time: datetime
    payload_weight_kg: float
    estimated_energy_wh: Optional[float] = None # Lấy từ AI Model Prediction để lọc Pin

# --- DTO cho Response ---
class AvailableDroneSchema(BaseModel):
    # Cấu hình để Pydantic convert từ SQLAlchemy instance
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    name: str
    model: str
    payload_capacity_kg: float # Sửa từ max_payload_kg thành payload_capacity_kg
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