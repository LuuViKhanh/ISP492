from pydantic import BaseModel
from typing import Dict, Optional
from datetime import datetime
from app.modules.missions.models import MissionStatus


class MissionStatusCount(BaseModel):
    status: str
    count: int


class MissionKPIResponse(BaseModel):
    total_missions: int
    completed: int
    cancelled: int
    rejected: int
    success_rate: float
    avg_distance_m: float
    avg_payload_kg: float
    total_revenue: float


class MissionStatusResponse(BaseModel):
    status_breakdown: Dict[str, int]


class ActiveFlightsResponse(BaseModel):
    active_count: int
    missions: list


class MissionResponse(BaseModel):
    id: int
    customer_id: Optional[str]
    operator_id: Optional[str]
    drone_id: Optional[int]
    battery_id: Optional[int]
    pickup_location_id: Optional[int]
    dropoff_location_id: Optional[int]
    payload_weight: Optional[float]
    distance_m: Optional[float]
    delivery_fee: Optional[float]
    status: MissionStatus
    scheduled_time: Optional[datetime]
    start_time: Optional[datetime]
    end_time: Optional[datetime]

    class Config:
        from_attributes = True


class ApproveRejectRequest(BaseModel):
    drone_id: Optional[int] = None
    battery_id: Optional[int] = None
    operator_id: Optional[int] = None
    reason: Optional[str] = None
