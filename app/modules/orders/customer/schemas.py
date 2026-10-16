from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.modules.missions.models import DeliveryMode, OrderStatus

class HubResponse(BaseModel):
    id: int
    name: str
    address: Optional[str] = None
    
    class Config:
        from_attributes = True


class OrderEstimateRequest(BaseModel):
    origin_hub_id: int
    destination_hub_id: int
    payload_kg: float
    package_size: str
    package_type: str = "Standard"
    delivery_mode: DeliveryMode
    requested_delivery_at: Optional[datetime] = None
    has_insurance: bool = False

class FeeDetails(BaseModel):
    base_fee: float
    distance_fee: float
    payload_fee: float
    insurance_fee: float
    express_surcharge: float
    fragile_surcharge: float

class OrderEstimateResponse(BaseModel):
    delivery_fee: float
    service_fee: float
    total_fee: float
    fee_details: FeeDetails
    distance_km: float
    estimated_departure: Optional[datetime] = None
    estimated_arrival: Optional[datetime] = None
    handover_deadline: Optional[datetime] = None

class CreateOrderRequest(BaseModel):
    package_type: str
    package_label: Optional[str] = None
    payload_kg: float
    package_size: str
    origin_hub_id: int
    destination_hub_id: int
    delivery_mode: DeliveryMode
    requested_delivery_at: Optional[datetime] = None
    sender_phone: Optional[str] = None
    receiver_name: Optional[str] = None
    receiver_phone: Optional[str] = None
    receiver_email: Optional[str] = None
    has_insurance: bool = False

class OrderCreateResponse(BaseModel):
    id: str
    status: OrderStatus
    origin_hub_id: int
    destination_hub_id: int
    created_at: datetime
    
    class Config:
        from_attributes = True
