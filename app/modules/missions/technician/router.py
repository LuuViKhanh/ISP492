from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast, String
from datetime import datetime, timezone

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser, get_current_user
from app.shared.roles import UserRole
from app.modules.missions.models import Mission, HandlingStatus, MissionStatus
from app.modules.hubs.models import Hub
from app.modules.fleet.models import Drone, Battery
from app.modules.auth.models import User

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
    try:
        mission_id = int(orderId)
        result = await db.execute(select(Mission).where(Mission.id == mission_id))
    except ValueError:
        result = await db.execute(select(Mission).where(Mission.order_code == orderId))
    
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
            Mission.order_code,
            Mission.mission_code,
            cast(Mission.status, String).label("status"),
            cast(Mission.handling_status, String).label("handling_status"),
            Hub.name.label("destination_hub_name"),
            Drone.name.label("drone_name"),
            Battery.serial_number.label("battery_serial")
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
                "order_code": row.order_code,
                "mission_code": row.mission_code,
                "destination_hub": row.destination_hub_name,
                "drone": row.drone_name,
                "battery": row.battery_serial,
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


@deliveries_router.post("/outbound/{orderId}/prepare-battery")
async def prepare_battery_for_delivery(
    orderId: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    """Xác nhận đã lắp Pin vào máy bay để chuẩn bị bay."""
    mission = await get_mission_by_order_id(db, orderId)
    
    if mission.handling_status != HandlingStatus.AT_HUB:
        raise HTTPException(status_code=400, detail="Mission must be AT_HUB before preparing battery")
        
    mission.handling_status = HandlingStatus.READY
    await db.commit()
    await db.refresh(mission)
    return {"message": "Battery prepared, ready to fly", "mission": mission}


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
            Mission.order_code,
            Mission.mission_code,
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
                "order_code": row.order_code,
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
