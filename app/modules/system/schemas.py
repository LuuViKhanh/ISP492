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
    area: Optional[str] = None
    capacity: Optional[int] = 24
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = "Active"


class HubUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    area: Optional[str] = None
    capacity: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = None


class HubResponse(BaseModel):
    id: int
    code: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    area: Optional[str] = None
    capacity: Optional[int] = 24
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class HubSummaryResponse(BaseModel):
    total_hubs: int
    active_hubs: int
    total_drones: int
    total_batteries: int


class HubAssignedDroneItem(BaseModel):
    id: str
    name: str
    model: str
    status: str
    serial_number: Optional[str] = None
    battery_level_pct: Optional[int] = None
    payload_capacity_kg: Optional[float] = None

    class Config:
        from_attributes = True


class HubListItemResponse(BaseModel):
    id: int
    code: Optional[str] = None
    name: str
    area: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = None
    capacity: int = 24
    drones: int = 0
    drones_count: int = 0
    batteries: int = 0
    batteries_count: int = 0
    technicians: int = 0
    technicians_count: int = 0
    assigned_drones: list[HubAssignedDroneItem] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class HubAssignedTechnician(BaseModel):
    id: str
    full_name: str
    email: str

    class Config:
        from_attributes = True


class HubAssignedBatteryItem(BaseModel):
    id: str
    serial_number: Optional[str] = None
    battery_model: Optional[str] = None
    status: str
    capacity_wh: Optional[float] = None
    charge_level_pct: Optional[int] = None
    drone_id: Optional[str] = None

    class Config:
        from_attributes = True


class HubDetailResponse(BaseModel):
    id: int
    code: Optional[str] = None
    name: str
    area: Optional[str] = None
    address: Optional[str] = None
    capacity: int = 24
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    assigned_technician: Optional[HubAssignedTechnician] = None
    assigned_technicians: list[HubAssignedTechnician] = []
    assigned_drones: list[HubAssignedDroneItem] = []
    assigned_batteries: list[HubAssignedBatteryItem] = []

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
