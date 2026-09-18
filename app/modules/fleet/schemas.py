from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.modules.fleet.models import WorkOrderStatus


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
