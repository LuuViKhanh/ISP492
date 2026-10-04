from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.modules.missions.models import OrderStatus, DeliveryMode

class OriginPackageSchema(BaseModel):
    received: bool
    received_at: Optional[datetime] = None

class HubSchema(BaseModel):
    id: int
    name: str

class OrderResponse(BaseModel):
    id: str
    customer_id: Optional[str]
    package_label: Optional[str]
    package_type: Optional[str]
    payload_kg: Optional[float]
    delivery_mode: Optional[DeliveryMode]
    status: OrderStatus
    planningState: Optional[str] = None
    originPackage: Optional[OriginPackageSchema] = None
    requested_delivery_at: Optional[datetime] = None
    estimated_window_start: Optional[datetime] = None
    estimated_window_end: Optional[datetime] = None
    planning_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class DroneEligibleSchema(BaseModel):
    id: int
    model: str
    maxPayloadKg: float

class HubEligibleSchema(BaseModel):
    id: int
    name: str
    totalDrones: int
    availableDrones: int

class EligibleDronesResponse(BaseModel):
    hub: HubEligibleSchema
    drones: List[DroneEligibleSchema]
