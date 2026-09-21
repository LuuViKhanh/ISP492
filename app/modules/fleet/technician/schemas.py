from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.modules.fleet.models import DroneStatus, WorkOrderStatus


class DroneProfileResponse(BaseModel):
    id: int
    name: str
    model: str
    payload_capacity_kg: float
    max_speed: float
    status: DroneStatus
    current_hub_id: Optional[int]
    battery_level_pct: Optional[int]
    utilization_pct: Optional[float]

    class Config:
        from_attributes = True


class DroneStatusUpdate(BaseModel):
    status: DroneStatus


class MaintenanceHistoryItem(BaseModel):
    id: int
    drone_id: int
    battery_id: int
    technician_id: int
    issue_description: str
    action_taken: Optional[str]
    status: WorkOrderStatus
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True
