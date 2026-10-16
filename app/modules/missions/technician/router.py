from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast, String, and_
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser, get_current_user
from app.shared.roles import UserRole
from app.modules.missions.models import Mission, HandlingStatus, MissionStatus, Order, OrderStatus
from app.modules.system.models import Hub
from app.modules.fleet.models import Drone, Battery, batteries_status
from app.modules.auth.models import User

# Bảng tra cứu mặc định (khớp 100% với 3 loại Drone và 3 loại Pin thực tế trong Database)
DRONE_BATTERY_COMPATIBILITY = {
    "DJI Matrice 100": ["TB48D"],
    "Aurelia X6 Pro": ["Aurelia UltraPower"],
    "DJI FlyCart 15 Heavy": ["DJI DB2000"],
}


async def get_compatible_battery_models(db: AsyncSession, drone_model: Optional[str]) -> list[str]:
    """Truy vấn trực tiếp từ Database để lấy danh sách dòng pin (battery_model) tương thích với model của Drone."""
    if not drone_model:
        return []
    stmt = (
        select(Battery.battery_model)
        .join(Drone, Battery.drone_id == Drone.id)
        .where(
            and_(
                Drone.model == drone_model,
                Battery.battery_model.is_not(None)
            )
        )
        .distinct()
    )
    res = await db.execute(stmt)
    db_models = [r for r in res.scalars().all() if r]
    if db_models:
        return db_models
    return DRONE_BATTERY_COMPATIBILITY.get(drone_model, [drone_model])


class PrepareBatteryRequest(BaseModel):
    battery_id: Optional[str] = None

router = APIRouter()

missions_router = APIRouter(
    prefix="/technician/missions",
    tags=["Missions"]
)

deliveries_router = APIRouter(
    prefix="/technician/deliveries",
    tags=["Missions"]
)

allow_technician = RoleChecker([UserRole.TECHNICIAN, UserRole.ADMIN])

@missions_router.get("/")
def get_technician_missions():
    return {"message": "This is technician endpoint for missions"}


async def get_mission_by_order_id(db: AsyncSession, orderId: str):
    # orderId could be a mission ID or an order ID. We just check both.
    result = await db.execute(
        select(Mission).where((Mission.order_id == orderId) | (Mission.id == orderId))
    )
    
    mission = result.scalars().first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


from app.modules.missions.schemas import MissionResponse

@deliveries_router.get("/outbound")
async def get_outbound_deliveries(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    """API lấy danh sách kiện hàng đang chờ bay đi."""
    import traceback
    try:
        query = select(
            Mission.id,
            Mission.order_id,
            Mission.id.label("mission_code"),
            cast(Mission.status, String).label("status"),
            cast(Mission.handling_status, String).label("handling_status"),
            Hub.name.label("destination_hub_name"),
            Drone.id.label("drone_id"),
            Drone.name.label("drone_name"),
            Drone.model.label("drone_model"),
            Battery.id.label("battery_id"),
            Battery.serial_number.label("battery_serial"),
            Battery.battery_model.label("battery_model")
        ).outerjoin(
            Hub, Mission.destination_hub_id == Hub.id
        ).outerjoin(
            Drone, Mission.drone_id == Drone.id
        ).outerjoin(
            Battery, Mission.battery_id == Battery.id
        ).where(
            Mission.departed_at.is_(None),
            Mission.status != MissionStatus.MISSION_COMPLETED,
            Mission.handling_status.in_([HandlingStatus.INCOMING, HandlingStatus.AT_HUB, HandlingStatus.READY])
        )
        if user.hub_id and str(user.hub_id).isdigit():
            query = query.where(Mission.origin_hub_id == int(user.hub_id))
            
        result = await db.execute(query.order_by(Mission.id.desc()))
        rows = result.all()
        return [
            {
                "id": row.id,
                "order_code": row.order_id,
                "mission_code": row.mission_code,
                "destination_hub": row.destination_hub_name,
                "drone_id": row.drone_id,
                "drone": row.drone_name,
                "drone_model": row.drone_model,
                "battery_id": row.battery_id,
                "battery": row.battery_serial,
                "battery_model": row.battery_model,
                "status": getattr(row.status, "value", row.status),
                "handling_status": getattr(row.handling_status, "value", row.handling_status)
            } for row in rows
        ]
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@deliveries_router.post("/outbound/{orderId}/receive")
async def receive_outbound_delivery(
    orderId: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    """Xác nhận kiện hàng khách gửi đã vào kho Hub."""
    mission = await get_mission_by_order_id(db, orderId)
    
    if mission.handling_status not in [None, HandlingStatus.INCOMING]:
        raise HTTPException(status_code=400, detail="Mission is not in INCOMING status")
        
    mission.handling_status = HandlingStatus.AT_HUB
    await db.commit()
    await db.refresh(mission)
    return {"message": "Package received at Hub", "mission": mission}


@deliveries_router.post("/outbound/{orderId}/prepare-battery", summary="Lắp Pin chuẩn bị bay")
async def prepare_battery_for_delivery(
    orderId: str,
    body: Optional[PrepareBatteryRequest] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Xác nhận Technician đã chọn và lắp Pin vào máy bay để chuẩn bị bay.
    
    Yêu cầu:
    - Nếu FE gửi `battery_id` trong request body: Backend sẽ kiểm tra viên pin Technician chỉ định.
    - Nếu FE không gửi `battery_id`: Backend sẽ tự động tìm 1 viên pin sạc 100% tương thích tại Hub (Fallback).
    
    Các điều kiện validate nghiêm ngặt:
        1. Kiện hàng đang ở Hub (`handling_status == AT_HUB`).
        2. Chuyến bay đã được phân công Drone (`mission.drone_id` tồn tại).
        3. Pin tồn tại trong hệ thống.
        4. Pin thuộc đúng Hub xuất phát của chuyến bay.
        5. Pin tương thích với model Drone được phân công (truy vấn trực tiếp từ Database).
        6. Pin ở trạng thái sẵn sàng (Active / Available).
        7. Mức sạc của Pin đạt 100%.
        8. Pin chưa bị gán cho Drone khác hoặc Mission đang hoạt động khác.
    """
    mission = await get_mission_by_order_id(db, orderId)
    
    # 1. Validate trạng thái hiện tại của kiện hàng
    if mission.handling_status != HandlingStatus.AT_HUB:
        raise HTTPException(
            status_code=400,
            detail="Kiện hàng phải ở trạng thái tại trạm (At hub) trước khi chuẩn bị pin."
        )
        
    # 2. Validate chuyến bay đã có Drone được phân công
    if not mission.drone_id:
        raise HTTPException(
            status_code=400,
            detail="Chuyến bay chưa được gán Drone. Vui lòng liên hệ Operator để phân công máy bay trước khi chuẩn bị pin."
        )
        
    drone = await db.get(Drone, mission.drone_id)
    if not drone:
        raise HTTPException(
            status_code=404,
            detail=f"Máy bay (ID: {mission.drone_id}) của chuyến bay không tồn tại."
        )

    hub_id = mission.origin_hub_id
    if not hub_id and user.hub_id and str(user.hub_id).isdigit():
        hub_id = int(user.hub_id)

    # Truy vấn trực tiếp từ Database để lấy dòng pin tương thích với model Drone
    compatible_battery_models = await get_compatible_battery_models(db, drone.model)

    # TRƯỜNG HỢP A: Technician chủ động chọn pin (FE truyền battery_id)
    if body and body.battery_id and str(body.battery_id).strip():
        target_battery_id = str(body.battery_id).strip()
        battery = await db.get(Battery, target_battery_id)
        if not battery:
            raise HTTPException(
                status_code=404,
                detail=f"Không tìm thấy pin có mã '{body.battery_id}' trong hệ thống."
            )

        # Validate Pin thuộc đúng Hub xuất phát
        if hub_id and battery.current_hub_id != hub_id:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {battery.id} hiện đang ở Hub {battery.current_hub_id}, không thuộc Hub xuất phát của đơn hàng (Hub {hub_id})."
            )

        # Validate tương thích giữa model Drone và model Pin
        if battery.battery_model and compatible_battery_models and battery.battery_model not in compatible_battery_models:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {battery.id} (Model: {battery.battery_model}) không tương thích với máy bay {drone.name} (Model: {drone.model}). Yêu cầu dòng pin: {', '.join(compatible_battery_models)}."
            )

        # Validate trạng thái hoạt động của Pin (Active / Available / Ready)
        if battery.status != batteries_status.ACTIVE and str(battery.status).upper() not in ["ACTIVE", "AVAILABLE", "READY"]:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {battery.id} hiện không ở trạng thái sẵn sàng (Trạng thái: {battery.status})."
            )

        # Validate mức sạc pin phải đạt 100%
        if (battery.charge_level_pct or 0) < 100:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {battery.id} chưa sạc đầy 100% (Mức sạc hiện tại: {battery.charge_level_pct}%). Chỉ pin đầy 100% mới đủ điều kiện bay."
            )

        # Validate pin chưa bị gán cho Drone khác
        if battery.drone_id is not None and battery.drone_id != mission.drone_id:
            other_drone = await db.get(Drone, battery.drone_id)
            other_name = other_drone.name if other_drone else f"ID {battery.drone_id}"
            raise HTTPException(
                status_code=400,
                detail=f"Pin {battery.id} hiện đang được gắn trên máy bay khác ({other_name}). Vui lòng chọn viên pin khả dụng khác."
            )

    # TRƯỜNG HỢP B: FE không truyền battery_id (Fallback tự động chọn 1 pin 100% tương thích tại Hub)
    else:
        auto_bat_stmt = select(Battery).where(
            and_(
                Battery.current_hub_id == hub_id,
                Battery.battery_model.in_(compatible_battery_models),
                Battery.charge_level_pct >= 100,
                Battery.status == batteries_status.ACTIVE,
                (Battery.drone_id == mission.drone_id) | (Battery.drone_id.is_(None))
            )
        ).order_by(Battery.id)
        res_auto = await db.execute(auto_bat_stmt)
        battery = res_auto.scalars().first()
        if not battery:
            raise HTTPException(
                status_code=400,
                detail=f"Tại Hub {hub_id} hiện không có viên pin nào sạc đầy 100% tương thích với máy bay {drone.name} ({drone.model}). Vui lòng sạc pin hoặc chọn viên pin khác."
            )

    # Validate pin chưa bị gán cho Mission đang hoạt động khác
    active_mission_stmt = select(Mission).where(
        and_(
            Mission.battery_id == battery.id,
            Mission.id != mission.id,
            Mission.status.in_([
                MissionStatus.SCHEDULED,
                MissionStatus.IN_PROGRESS,
                MissionStatus.APPROVED,
                MissionStatus.FLYING,
                MissionStatus.ACTIVE_MISSION
            ])
        )
    )
    res_active = await db.execute(active_mission_stmt)
    other_mission = res_active.scalars().first()
    if other_mission:
        raise HTTPException(
            status_code=400,
            detail=f"Pin {battery.id} hiện đang được giữ bởi một nhiệm vụ bay đang hoạt động khác ({other_mission.id})."
        )

    # --- TIẾN HÀNH GÁN PIN VÀ ĐỒNG BỘ TRẠNG THÁI ---
    mission.battery_id = battery.id
    mission.handling_status = HandlingStatus.READY
    battery.drone_id = mission.drone_id
    if mission.origin_hub_id:
        battery.current_hub_id = mission.origin_hub_id

    await db.commit()
    await db.refresh(mission)
    await db.refresh(battery)

    handling_status_val = mission.handling_status.value if hasattr(mission.handling_status, "value") else str(mission.handling_status)
    status_val = mission.status.value if hasattr(mission.status, "value") else str(mission.status)

    return {
        "message": "Battery prepared, ready to fly",
        "order_id": mission.order_id,
        "mission_id": mission.id,
        "battery_id": battery.id,
        "battery_serial": battery.serial_number,
        "battery_model": battery.battery_model,
        "battery_level_pct": battery.charge_level_pct,
        "drone_id": drone.id,
        "drone_name": drone.name,
        "drone_model": drone.model,
        "handling_status": handling_status_val,
        "status": status_val,
        "mission": {
            "id": mission.id,
            "order_id": mission.order_id,
            "battery_id": mission.battery_id,
            "drone_id": mission.drone_id,
            "handling_status": handling_status_val,
            "status": status_val,
            "origin_hub_id": mission.origin_hub_id,
            "destination_hub_id": mission.destination_hub_id
        }
    }


@deliveries_router.get("/confirmations")
async def get_confirmations(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    """Lấy danh sách hàng drone đã chở tới, chờ khách tới Hub lấy."""
    import traceback
    try:
        query = select(
            Mission.id,
            Mission.order_id,
            Mission.id.label("mission_code"),
            cast(Mission.status, String).label("status"),
            cast(Mission.handling_status, String).label("handling_status"),
            Hub.name.label("origin_hub_name"),
            Drone.name.label("drone_name"),
            Battery.serial_number.label("battery_serial"),
            User.full_name.label("customer_name")
        ).outerjoin(
            Hub, Mission.origin_hub_id == Hub.id
        ).outerjoin(
            Drone, Mission.drone_id == Drone.id
        ).outerjoin(
            Battery, Mission.battery_id == Battery.id
        ).outerjoin(
            User, Mission.customer_id == User.email
        ).where(
            Mission.arrived_at.is_not(None),
            Mission.arrival_confirmed_at.is_(None),
            Mission.status != MissionStatus.MISSION_COMPLETED
        )
        if user.hub_id and str(user.hub_id).isdigit():
            query = query.where(Mission.destination_hub_id == int(user.hub_id))
            
        result = await db.execute(query.order_by(Mission.id.desc()))
        rows = result.all()
        return [
            {
                "id": row.id,
                "order_code": row.order_id,
                "mission_code": row.mission_code,
                "origin_hub": row.origin_hub_name,
                "drone": row.drone_name,
                "battery": row.battery_serial,
                "customer_name": row.customer_name,
                "status": getattr(row.status, "value", row.status),
                "handling_status": getattr(row.handling_status, "value", row.handling_status)
            } for row in rows
        ]
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


@deliveries_router.post("/confirmations/{orderId}/confirm-pickup")
async def confirm_pickup(
    orderId: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    """Xác nhận khách đã tới lấy hàng thành công."""
    mission = await get_mission_by_order_id(db, orderId)
    
    if mission.arrived_at is None:
        raise HTTPException(status_code=400, detail="Mission has not arrived yet")
        
    if mission.arrival_confirmed_at is not None:
        raise HTTPException(status_code=400, detail="Pickup already confirmed")
        
    mission.arrival_confirmed_at = datetime.now(timezone.utc)
    mission.arrival_confirmed_by = user.id
    mission.status = MissionStatus.MISSION_COMPLETED
    
    await db.commit()
    await db.refresh(mission)
    return {"message": "Pickup confirmed successfully", "mission": mission}


router.include_router(missions_router)
router.include_router(deliveries_router)
