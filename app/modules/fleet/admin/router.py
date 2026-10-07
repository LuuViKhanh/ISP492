from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from pydantic import BaseModel

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.fleet.models import Drone, DroneStatus, Battery, batteries_status

router = APIRouter(prefix="/admin/fleet", tags=["Fleet"])

allow_admin = RoleChecker([UserRole.ADMIN])


# ── Schemas ───────────────────────────────────────────────────────────────────

class DroneCreate(BaseModel):
    name: str
    model: str
    payload_capacity_kg: float
    max_speed: float
    status: DroneStatus = DroneStatus.AVAILABLE
    current_hub_id: Optional[int] = None


class DroneUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    payload_capacity_kg: Optional[float] = None
    max_speed: Optional[float] = None
    status: Optional[DroneStatus] = None
    current_hub_id: Optional[int] = None


class DroneResponse(BaseModel):
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


class BatteryCreate(BaseModel):
    serial_number: str
    capacity_wh: float
    status: batteries_status = batteries_status.ACTIVE
    current_hub_id: Optional[int] = None
    drone_id: Optional[int] = None
    charge_level_pct: Optional[int] = 100


class BatteryUpdate(BaseModel):
    serial_number: Optional[str] = None
    capacity_wh: Optional[float] = None
    status: Optional[batteries_status] = None
    current_hub_id: Optional[int] = None
    drone_id: Optional[int] = None
    charge_level_pct: Optional[int] = None


class BatteryResponse(BaseModel):
    id: int
    serial_number: str
    capacity_wh: float
    status: batteries_status
    drone_id: Optional[int]
    current_hub_id: Optional[int]
    charge_level_pct: Optional[int]

    class Config:
        from_attributes = True


# ── Drone Endpoints ───────────────────────────────────────────────────────────

@router.get("/drones", response_model=list[DroneResponse], summary="Lấy danh sách tất cả drone")
async def list_drones(
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Trả về toàn bộ drone trong hệ thống, sắp xếp theo id."""
    result = await db.execute(select(Drone).order_by(Drone.id))
    return result.scalars().all()


@router.get("/drones/{drone_id}", response_model=DroneResponse, summary="Xem chi tiết một drone")
async def get_drone(
    drone_id: int,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    drone = await db.get(Drone, drone_id)
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")
    return drone


@router.post("/drones", response_model=DroneResponse, status_code=201, summary="Thêm drone mới vào kho")
async def create_drone(
    body: DroneCreate,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Tạo mới một drone và thêm vào kho fleet.

    Các trường bắt buộc: `name`, `model`, `payload_capacity_kg`, `max_speed`.
    Status mặc định là `Available` nếu không truyền.
    """
    drone = Drone(
        name=body.name,
        model=body.model,
        payload_capacity_kg=body.payload_capacity_kg,
        max_speed=body.max_speed,
        status=body.status,
        current_hub_id=body.current_hub_id,
    )
    db.add(drone)
    await db.commit()
    await db.refresh(drone)
    return drone


@router.put("/drones/{drone_id}", response_model=DroneResponse, summary="Cập nhật toàn bộ thông tin drone")
async def update_drone(
    drone_id: int,
    body: DroneUpdate,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Cập nhật thông tin drone (partial update — chỉ field nào được truyền mới thay đổi).
    Dùng để sửa tên, model, thông số kỹ thuật, trạng thái hoặc hub hiện tại.
    """
    drone = await db.get(Drone, drone_id)
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")

    if body.name is not None:
        drone.name = body.name
    if body.model is not None:
        drone.model = body.model
    if body.payload_capacity_kg is not None:
        drone.payload_capacity_kg = body.payload_capacity_kg
    if body.max_speed is not None:
        drone.max_speed = body.max_speed
    if body.status is not None:
        drone.status = body.status
    if body.current_hub_id is not None:
        drone.current_hub_id = body.current_hub_id

    await db.commit()
    await db.refresh(drone)
    return drone


@router.delete("/drones/{drone_id}", status_code=204, summary="Xóa drone khỏi hệ thống")
async def delete_drone(
    drone_id: int,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Xóa vĩnh viễn drone khỏi DB.
    Thường chỉ dùng khi drone bị hỏng hoàn toàn hoặc nhập sai.
    Nếu muốn ngưng sử dụng, hãy dùng PUT để chuyển status sang `Retired`.
    """
    drone = await db.get(Drone, drone_id)
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")
    await db.delete(drone)
    await db.commit()


# ── Battery Endpoints ─────────────────────────────────────────────────────────

@router.get("/batteries", response_model=list[BatteryResponse], summary="Lấy danh sách tất cả pin")
async def list_batteries(
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(select(Battery).order_by(Battery.id))
    return result.scalars().all()


@router.get("/batteries/{battery_id}", response_model=BatteryResponse, summary="Xem chi tiết một pin")
async def get_battery(
    battery_id: int,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")
    return battery


@router.post("/batteries", response_model=BatteryResponse, status_code=201, summary="Thêm pin mới vào kho")
async def create_battery(
    body: BatteryCreate,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    # Kiểm tra serial_number không trùng
    existing = await db.execute(
        select(Battery).where(Battery.serial_number == body.serial_number)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Serial number already exists")

    from app.shared.id_generator import generate_sequential_id
    battery_id = await generate_sequential_id(db, Battery, "BAT")

    battery = Battery(
        id=battery_id,
        serial_number=body.serial_number,
        capacity_wh=body.capacity_wh,
        status=body.status,
        current_hub_id=body.current_hub_id,
        drone_id=body.drone_id,
        charge_level_pct=body.charge_level_pct,
    )
    db.add(battery)
    await db.commit()
    await db.refresh(battery)
    return battery


@router.put("/batteries/{battery_id}", response_model=BatteryResponse, summary="Cập nhật thông tin pin")
async def update_battery(
    battery_id: int,
    body: BatteryUpdate,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")

    if body.serial_number is not None:
        battery.serial_number = body.serial_number
    if body.capacity_wh is not None:
        battery.capacity_wh = body.capacity_wh
    if body.status is not None:
        battery.status = body.status
    if body.current_hub_id is not None:
        battery.current_hub_id = body.current_hub_id
    if body.drone_id is not None:
        battery.drone_id = body.drone_id
    if body.charge_level_pct is not None:
        battery.charge_level_pct = body.charge_level_pct

    await db.commit()
    await db.refresh(battery)
    return battery


@router.delete("/batteries/{battery_id}", status_code=204, summary="Xóa pin khỏi hệ thống")
async def delete_battery(
    battery_id: int,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    battery = await db.get(Battery, battery_id)
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")
    await db.delete(battery)
    await db.commit()
