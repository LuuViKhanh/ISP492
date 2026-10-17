from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.fleet.models import Drone, Battery, DroneStatus, batteries_status, BatteryAvailableStatus, compute_battery_available_status
from app.modules.system.models import Hub
from typing import Optional
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/operator/fleet", tags=["Fleet"])

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
    id: str
    serial_number: str
    battery_model: Optional[str] = None
    capacity_wh: float
    status: batteries_status
    drone_id: Optional[int] = None
    current_hub_id: Optional[int] = None
    hub_name: Optional[str] = None
    charge_level_pct: Optional[int] = None
    available_status: BatteryAvailableStatus  # computed, không lưu DB

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
        d = {
            "id": drone.id,
            "name": drone.name,
            "model": drone.model,
            "payload_capacity_kg": drone.payload_capacity_kg,
            "max_speed": drone.max_speed,
            "status": drone.status,
            "current_hub_id": drone.current_hub_id,
            "hub_name": hub_name,
            "battery_level_pct": drone.battery_level_pct,
            "utilization_pct": drone.utilization_pct,
        }
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
    return {
        "id": drone.id,
        "name": drone.name,
        "model": drone.model,
        "payload_capacity_kg": drone.payload_capacity_kg,
        "max_speed": drone.max_speed,
        "status": drone.status,
        "current_hub_id": drone.current_hub_id,
        "hub_name": hub_name,
        "battery_level_pct": drone.battery_level_pct,
        "utilization_pct": drone.utilization_pct,
    }


# ── Battery endpoints ─────────────────────────────────────────────────────────

@router.get("/batteries", response_model=list[BatteryOverviewResponse], summary="Tất cả pin ở mọi hub")
async def list_all_batteries(
    hub_id: Optional[int] = Query(None, description="Lọc pin theo Hub ID"),
    available_status: Optional[BatteryAvailableStatus] = Query(
        None,
        description="Lọc theo trạng thái khả dụng: Charging | Ready | In Use | Replaced"
    ),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Operator xem toàn bộ pin ở tất cả các hub, kèm `available_status` tính toán thực tế.

    **Query params (tuỳ chọn):**
    - `hub_id` — chỉ lấy pin thuộc Hub đó
    - `available_status` — lọc theo trạng thái:
        - `Charging` — đang sạc tại hub (charge < 100%)
        - `Ready`    — sạc đầy 100%, sẵn sàng lắp cho drone
        - `In Use`   — đang gắn trên drone
        - `Replaced` — hết vòng đời

    **Dùng để chọn hub thay pin:** lọc `available_status=Ready` kèm `hub_id` để biết
    hub nào đang có pin sẵn sàng thay khi drone dừng trên route.
    """
    stmt = (
        select(Battery, Hub.name.label("hub_name"))
        .outerjoin(Hub, Battery.current_hub_id == Hub.id)
        .order_by(Battery.current_hub_id, Battery.id)
    )

    # Lọc theo hub_id nếu có
    if hub_id is not None:
        stmt = stmt.where(Battery.current_hub_id == hub_id)

    result = await db.execute(stmt)
    rows = result.all()

    response = []
    for battery, hub_name in rows:
        computed = compute_battery_available_status(battery)

        # Lọc theo available_status nếu có (filter sau khi tính toán)
        if available_status is not None and computed != available_status:
            continue

        response.append({
            "id": battery.id,
            "serial_number": battery.serial_number,
            "battery_model": battery.battery_model,
            "capacity_wh": battery.capacity_wh,
            "status": battery.status,
            "drone_id": battery.drone_id,
            "current_hub_id": battery.current_hub_id,
            "hub_name": hub_name,
            "charge_level_pct": battery.charge_level_pct,
            "available_status": computed,
        })
    return response
