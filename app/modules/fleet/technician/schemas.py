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
    title: Optional[str]
    priority: Optional[str]
    scheduled_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

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
    drone_id: int
    title: Optional[str] = None
    diagnosis: Optional[str] = None
    corrective_action: Optional[str] = None
    resulting_drone_status: Optional[str] = None
    inspection_items: Optional[List[InspectionItemCreate]] = []


class MaintenanceRecordResponse(BaseModel):
    id: int
    work_order_id: Optional[int]
    drone_id: Optional[int]
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
    drone_id: Optional[int]
    source: Optional[str]
    title: Optional[str]
    status: Optional[str]
    work_order_id: Optional[int]
    handled_by: Optional[str]
    created_at: Optional[datetime]
    handled_at: Optional[datetime]

    class Config:
        from_attributes = True


class MaintenanceScheduleResponse(BaseModel):
    id: int
    drone_id: Optional[int]
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
    drone_id: int
    maintenance_type: str
    interval_days: Optional[int] = None
    interval_flight_hours: Optional[float] = None
