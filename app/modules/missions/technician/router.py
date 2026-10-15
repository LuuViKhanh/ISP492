from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast, String
from datetime import datetime, timezone

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser, get_current_user
from app.shared.roles import UserRole
from app.modules.missions.models import Mission, HandlingStatus, MissionStatus, Order, OrderStatus, PaymentStatus
from app.modules.system.models import Hub
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
    """
    Lấy danh sách kiện hàng chờ gửi đi tại origin hub.
    Flow mới: query từ Order (payment_status=PAID), không cần có Mission.
    Hiển thị từ lúc KH thanh toán xong cho đến khi drone cất cánh.
    """
    import traceback
    try:
        query = (
            select(
                Order.id.label("order_id"),
                Order.status.label("order_status"),
                Order.payment_status.label("payment_status"),
                Order.package_label,
                Order.package_type,
                Order.payload_kg,
                Order.delivery_mode,
                Order.created_at,
                Order.receiver_phone,
                Hub.name.label("destination_hub_name"),
                Mission.id.label("mission_id"),
                Mission.status.label("mission_status"),
                Drone.name.label("drone_name"),
                Battery.serial_number.label("battery_serial"),
            )
            .outerjoin(Hub, Order.destination_hub_id == Hub.id)
            .outerjoin(Mission, Mission.order_id == Order.id)
            .outerjoin(Drone, Mission.drone_id == Drone.id)
            .outerjoin(Battery, Mission.battery_id == Battery.id)
            .where(
                Order.payment_status == PaymentStatus.PAID,
                Order.status.in_([
                    OrderStatus.PENDING,
                    OrderStatus.AWAITING_DROP_OFF,
                    OrderStatus.AT_HUB,
                ])
            )
        )

        if user.hub_id and str(user.hub_id).isdigit():
            query = query.where(Order.origin_hub_id == int(user.hub_id))

        result = await db.execute(query.order_by(Order.created_at.desc()))
        rows = result.all()

        return [
            {
                "order_id": row.order_id,
                "order_status": row.order_status.value if hasattr(row.order_status, "value") else row.order_status,
                "package_label": row.package_label,
                "package_type": row.package_type,
                "payload_kg": row.payload_kg,
                "delivery_mode": row.delivery_mode.value if hasattr(row.delivery_mode, "value") else row.delivery_mode,
                "destination_hub": row.destination_hub_name,
                "receiver_phone": row.receiver_phone,
                "mission_id": row.mission_id,
                "mission_status": row.mission_status.value if hasattr(row.mission_status, "value") else row.mission_status,
                "drone": row.drone_name,
                "battery": row.battery_serial,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
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
    """
    Xác nhận KH đã mang hàng đến hub gửi.
    Flow mới: update Order.status = AT_HUB, không cần có Mission.
    """
    # Tìm Order theo order_id
    order_result = await db.execute(select(Order).where(Order.id == orderId))
    order = order_result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Order chưa được thanh toán")

    if order.status not in [OrderStatus.PENDING, OrderStatus.AWAITING_DROP_OFF]:
        raise HTTPException(status_code=400, detail=f"Order đang ở trạng thái '{order.status.value}', không thể nhận hàng")

    order.status = OrderStatus.AT_HUB
    await db.commit()
    return {"message": "Package received at Hub", "order_id": orderId, "order_status": OrderStatus.AT_HUB}


@deliveries_router.post("/outbound/{orderId}/prepare-battery")
async def prepare_battery_for_delivery(
    orderId: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Xác nhận đã lắp pin vào drone, sẵn sàng bay.
    Chỉ được thực hiện sau khi Operator đã tạo Mission (order.status = AT_HUB và có Mission SCHEDULED).
    """
    order_result = await db.execute(select(Order).where(Order.id == orderId))
    order = order_result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != OrderStatus.AT_HUB:
        raise HTTPException(status_code=400, detail="Hàng chưa được nhận vào hub (cần AT_HUB trước)")

    # Kiểm tra đã có Mission chưa
    mission_result = await db.execute(
        select(Mission).where(
            Mission.order_id == orderId,
            Mission.status == MissionStatus.SCHEDULED
        ).limit(1)
    )
    mission = mission_result.scalars().first()
    if not mission:
        raise HTTPException(status_code=400, detail="Chưa có mission được tạo. Operator cần tạo mission trước.")

    mission.handling_status = HandlingStatus.READY
    await db.commit()
    return {"message": "Battery prepared, ready to fly", "order_id": orderId, "mission_id": mission.id}


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
