from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.fleet.models import Drone, Battery, DroneStatus, batteries_status
from app.modules.system.models import Hub
from typing import Optional
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/operator/fleet", tags=["Operator - Fleet"])

allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])


# ── Schemas ───────────────────────────────────────────────────────────────────

class DroneOverviewResponse(BaseModel):
    id: int
    name: str
    model: str
    payload_capacity_kg: float
    max_speed: float
    status: DroneStatus
    current_hub_id: Optional[int]
    hub_name: Optional[str]
    battery_level_pct: Optional[int]
    utilization_pct: Optional[float]

    class Config:
        from_attributes = True


class BatteryOverviewResponse(BaseModel):
    id: int
    serial_number: str
    capacity_wh: float
    status: batteries_status
    drone_id: Optional[int]
    current_hub_id: Optional[int]
    hub_name: Optional[str]
    charge_level_pct: Optional[int]

    class Config:
        from_attributes = True


# ── Drone endpoints ───────────────────────────────────────────────────────────

@router.get("/drones", response_model=list[DroneOverviewResponse], summary="Tất cả drone ở mọi hub")
async def list_all_drones(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """Operator xem toàn bộ drone ở tất cả các hub."""
    result = await db.execute(
        select(Drone, Hub.name.label("hub_name"))
        .outerjoin(Hub, Drone.current_hub_id == Hub.id)
        .order_by(Drone.current_hub_id, Drone.id)
    )
    rows = result.all()
    response = []
    for drone, hub_name in rows:
        d = {c.key: getattr(drone, c.key) for c in drone.__table__.columns}
        d["hub_name"] = hub_name
        response.append(d)
    return response


@router.get("/drones/{drone_id}", response_model=DroneOverviewResponse, summary="Chi tiết một drone")
async def get_drone(
    drone_id: int,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    from fastapi import HTTPException
    result = await db.execute(
        select(Drone, Hub.name.label("hub_name"))
        .outerjoin(Hub, Drone.current_hub_id == Hub.id)
        .where(Drone.id == drone_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Drone not found")
    drone, hub_name = row
    d = {c.key: getattr(drone, c.key) for c in drone.__table__.columns}
    d["hub_name"] = hub_name
    return d


# ── Battery endpoints ─────────────────────────────────────────────────────────

@router.get("/batteries", response_model=list[BatteryOverviewResponse], summary="Tất cả pin ở mọi hub")
async def list_all_batteries(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """Operator xem toàn bộ pin ở tất cả các hub."""
    result = await db.execute(
        select(Battery, Hub.name.label("hub_name"))
        .outerjoin(Hub, Battery.current_hub_id == Hub.id)
        .order_by(Battery.current_hub_id, Battery.id)
    )
    rows = result.all()
    response = []
    for battery, hub_name in rows:
        b = {c.key: getattr(battery, c.key) for c in battery.__table__.columns}
        b["hub_name"] = hub_name
        response.append(b)
    return response
