from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[Any]
    action: str
    resource_table: Optional[str]
    details_json: Optional[dict]
    created_at: datetime


class AuditLogListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AuditLogResponse]

class HubCreate(BaseModel):
    code: Optional[str] = None
    name: str
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = "Active"

class HubUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = None

class HubResponse(BaseModel):
    id: int
    code: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class KPIChartData(BaseModel):
    date: str
    revenue: float

class KPIDashboardResponse(BaseModel):
    total_drones: int
    total_batteries: int
    successful_flights: int
    failed_flights: int
    total_revenue: float
    revenue_chart: list[KPIChartData]
