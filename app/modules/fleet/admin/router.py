from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, Union
from pydantic import BaseModel
from datetime import datetime

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.fleet.models import Drone, DroneStatus, Battery, batteries_status, BatteryAvailableStatus, compute_battery_available_status

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
    id: Union[str, int]
    name: str
    model: str
    payload_capacity_kg: float
    max_speed: float
    status: DroneStatus
    current_hub_id: Optional[int] = None
    battery_level_pct: Optional[int] = None
    utilization_pct: Optional[float] = None

    class Config:
        from_attributes = True



class BatteryCreate(BaseModel):
    serial_number: str
    capacity_wh: float
    status: batteries_status = batteries_status.ACTIVE
    current_hub_id: Optional[int] = None
    drone_id: Optional[Union[str, int]] = None
    charge_level_pct: Optional[int] = 100


class BatteryUpdate(BaseModel):
    serial_number: Optional[str] = None
    capacity_wh: Optional[float] = None
    status: Optional[batteries_status] = None
    current_hub_id: Optional[int] = None
    drone_id: Optional[Union[str, int]] = None
    charge_level_pct: Optional[int] = None
    last_charged_at: Optional[datetime] = None


class BatteryResponse(BaseModel):
    id: str
    serial_number: str
    capacity_wh: float
    status: batteries_status
    drone_id: Optional[Union[str, int]] = None
    current_hub_id: Optional[int] = None
    charge_level_pct: Optional[int] = None
    battery_model: Optional[str] = None
    last_charged_at: Optional[datetime] = None
    available_status: Optional[BatteryAvailableStatus] = None  # computed, không lưu DB
    # Vòng đời pin (tính on-the-fly, không lưu DB)
    lifecycle_status: Optional[str] = None       # Active | Expired
    days_until_expiry: Optional[int] = None      # số ngày còn lại đến khi hết vòng đời (730 ngày)

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
    drone_id: str,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    drone = await db.get(Drone, str(drone_id))
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
    Tự động sinh mã ID theo chuẩn DRN-XXXX (ví dụ: DRN-0001).
    """
    from app.shared.id_generator import generate_sequential_id
    drone_id = await generate_sequential_id(db, Drone, "DRN")

    drone = Drone(
        id=drone_id,
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
    drone_id: str,
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
    drone_id: str,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Xóa vĩnh viễn drone khỏi DB.
    Thường chỉ dùng khi drone bị hỏng hoàn toàn hoặc nhập sai.
    Nếu muốn ngưng sử dụng, hãy dùng PUT để chuyển status sang `Retired`.
    """
    drone = await db.get(Drone, str(drone_id))
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")
    await db.delete(drone)

    await db.commit()


# ── Battery Endpoints ─────────────────────────────────────────────────────────

def _battery_to_response(battery: Battery) -> BatteryResponse:
    """Chuyển Battery ORM object thành BatteryResponse kèm computed available_status & lifecycle."""
    from app.modules.fleet.models import compute_battery_lifecycle
    lifecycle_status, days_until_expiry = compute_battery_lifecycle(battery)
    return BatteryResponse(
        id=battery.id,
        serial_number=battery.serial_number,
        capacity_wh=battery.capacity_wh,
        status=battery.status,
        drone_id=battery.drone_id,
        current_hub_id=battery.current_hub_id,
        charge_level_pct=battery.charge_level_pct,
        battery_model=battery.battery_model,
        last_charged_at=battery.last_charged_at,
        available_status=compute_battery_available_status(battery),
        lifecycle_status=lifecycle_status.value if hasattr(lifecycle_status, "value") else str(lifecycle_status),
        days_until_expiry=days_until_expiry,
    )


@router.get("/batteries", response_model=list[BatteryResponse], summary="Lấy danh sách tất cả pin")
async def list_batteries(
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(select(Battery).order_by(Battery.id))
    return [_battery_to_response(b) for b in result.scalars().all()]


@router.get("/batteries/{battery_id}", response_model=BatteryResponse, summary="Xem chi tiết một pin")
async def get_battery(
    battery_id: str,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    battery = await db.get(Battery, str(battery_id))
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")
    return _battery_to_response(battery)


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
    return _battery_to_response(battery)


@router.put("/batteries/{battery_id}", response_model=BatteryResponse, summary="Cập nhật thông tin pin")
async def update_battery(
    battery_id: str,
    body: BatteryUpdate,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    battery = await db.get(Battery, str(battery_id))
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
    if body.last_charged_at is not None:
        battery.last_charged_at = body.last_charged_at

    await db.commit()
    await db.refresh(battery)
    return _battery_to_response(battery)


@router.delete("/batteries/{battery_id}", status_code=204, summary="Xóa pin khỏi hệ thống")
async def delete_battery(
    battery_id: str,
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    battery = await db.get(Battery, str(battery_id))
    if not battery:
        raise HTTPException(status_code=404, detail="Battery not found")
    await db.delete(battery)
    await db.commit()
