from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast, String, and_
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel
from app.core.timezone import get_vn_naive_datetime

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser, get_current_user
from app.shared.roles import UserRole
from app.modules.missions.models import (
    Mission,
    HandlingStatus,
    MissionStatus,
    Order,
    OrderStatus,
    PaymentStatus,
)
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
    clean_id = (orderId or "").strip()
    # orderId could be a mission ID or an order ID. We check both, prioritizing latest mission.
    result = await db.execute(
        select(Mission)
        .where((Mission.order_id == clean_id) | (Mission.id == clean_id))
        .order_by(Mission.id.desc())
    )
    mission = result.scalars().first()
    if not mission:
        from sqlalchemy import func
        result = await db.execute(
            select(Mission)
            .where(
                (func.upper(func.trim(Mission.order_id)) == clean_id.upper())
                | (func.upper(func.trim(Mission.id)) == clean_id.upper())
            )
            .order_by(Mission.id.desc())
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
    Hiển thị từ lúc KH thanh toán xong cho đến khi drone cất cánh, kèm đầy đủ thông tin Drone & Pin.
    Lưu ý (Issue #1 & #2):
    - Trường `id` luôn trả về `order_id` để FE gọi `/outbound/{orderId}/receive` không bị lỗi 404.
    - Khi đơn hàng chưa được Technician xác nhận nhận tại Hub (`order_status != AT_HUB`),
      không trả về thông tin Mission/Drone/Pin (tránh hiển thị mission/drone khi đơn còn AWAITING_DROP_OFF).
    """
    import traceback
    try:
        query = (
            select(
                Order.id.label("order_id"),
                Order.status.label("order_status"),
                Order.payment_status.label("payment_status"),
                Order.origin_hub_id.label("origin_hub_id"),
                Order.origin_received_at.label("origin_received_at"),
                Order.package_label,
                Order.package_type,
                Order.payload_kg,
                Order.delivery_mode,
                Order.created_at,
                Order.receiver_phone,
                Hub.name.label("destination_hub_name"),
                Mission.id.label("mission_id"),
                Mission.status.label("mission_status"),
                Mission.handling_status.label("handling_status"),
                Drone.id.label("drone_id"),
                Drone.name.label("drone_name"),
                Drone.model.label("drone_model"),
                Battery.id.label("battery_id"),
                Battery.serial_number.label("battery_serial"),
                Battery.battery_model.label("battery_model"),
            )
            .outerjoin(Hub, Order.destination_hub_id == Hub.id)
            .outerjoin(
                Mission,
                and_(
                    Mission.order_id == Order.id,
                    Mission.status.in_([
                        MissionStatus.SCHEDULED,
                        MissionStatus.APPROVED,
                        MissionStatus.PENDING_APPROVAL,
                        MissionStatus.IN_PROGRESS,
                        MissionStatus.FLYING,
                        MissionStatus.ACTIVE_MISSION,
                    ])
                )
            )
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

        result = await db.execute(query.order_by(Order.created_at.desc(), Mission.id.desc()))
        rows = result.all()

        seen_orders = set()
        items = []
        for row in rows:
            if row.order_id in seen_orders:
                continue
            seen_orders.add(row.order_id)

            order_status_val = row.order_status.value if hasattr(row.order_status, "value") else row.order_status
            is_at_hub = (row.order_status == OrderStatus.AT_HUB) or (str(order_status_val) == OrderStatus.AT_HUB.value)

            # Issue #2: Chỉ hiển thị thông tin Mission & Drone khi đơn hàng đã AT_HUB
            m_status_val = (
                row.mission_status.value if hasattr(row.mission_status, "value") else row.mission_status
            ) if is_at_hub else None
            h_status_val = (
                row.handling_status.value if hasattr(row.handling_status, "value") else row.handling_status
            ) if is_at_hub else None

            items.append({
                "id": row.order_id,
                "order_id": row.order_id,
                "order_code": row.order_id,
                "order_status": order_status_val,
                "origin_hub_id": row.origin_hub_id,
                "origin_received_at": row.origin_received_at.isoformat() if row.origin_received_at else None,
                "package_label": row.package_label,
                "package_type": row.package_type,
                "payload_kg": row.payload_kg,
                "delivery_mode": row.delivery_mode.value if hasattr(row.delivery_mode, "value") else row.delivery_mode,
                "destination_hub": row.destination_hub_name,
                "receiver_phone": row.receiver_phone,
                "mission_id": row.mission_id if is_at_hub else None,
                "mission_code": row.mission_id if is_at_hub else None,
                "mission_status": m_status_val,
                "status": m_status_val,
                "handling_status": h_status_val,
                "drone_id": row.drone_id if is_at_hub else None,
                "drone": row.drone_name if is_at_hub else None,
                "drone_model": row.drone_model if is_at_hub else None,
                "battery_id": row.battery_id if is_at_hub else None,
                "battery": row.battery_serial if is_at_hub else None,
                "battery_model": row.battery_model if is_at_hub else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })

        return items
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
    Hỗ trợ tìm kiếm linh hoạt theo order_id (hoặc mission_id nếu FE truyền nhầm id cũ).
    """
    from sqlalchemy import func

    clean_id = (orderId or "").strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="orderId is required")

    # 1. Tìm Order theo order_id (chính xác hoặc không phân biệt hoa thường/khoảng trắng)
    order_result = await db.execute(select(Order).where(Order.id == clean_id))
    order = order_result.scalar_one_or_none()

    if not order:
        order_result = await db.execute(
            select(Order).where(func.upper(func.trim(Order.id)) == clean_id.upper())
        )
        order = order_result.scalars().first()

    # 2. Fallback: Nếu FE truyền mission_id (ví dụ từ cache cũ trả về id = mission_id), suy ngược ra Order
    if not order:
        mission_res = await db.execute(
            select(Mission).where(
                (Mission.id == clean_id)
                | (func.upper(func.trim(Mission.id)) == clean_id.upper())
                | (func.upper(func.trim(Mission.order_id)) == clean_id.upper())
            ).order_by(Mission.id.desc())
        )
        linked_mission = mission_res.scalars().first()
        if linked_mission and linked_mission.order_id:
            order_result = await db.execute(
                select(Order).where(
                    (Order.id == linked_mission.order_id)
                    | (func.upper(func.trim(Order.id)) == linked_mission.order_id.strip().upper())
                )
            )
            order = order_result.scalars().first()

    if not order:
        raise HTTPException(status_code=404, detail=f"Order '{clean_id}' not found")

    # 3. Kiểm tra điều kiện Hub của Technician (nếu Technician được gán hub_id cụ thể)
    if user.hub_id and str(user.hub_id).isdigit() and order.origin_hub_id:
        if int(user.hub_id) != int(order.origin_hub_id):
            raise HTTPException(
                status_code=403,
                detail=f"Đơn hàng {order.id} thuộc Hub xuất phát #{order.origin_hub_id}, không thuộc Hub #{user.hub_id} của Technician."
            )

    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Order chưa được thanh toán")

    now = get_vn_naive_datetime()

    # Nếu đơn đã ở trạng thái AT_HUB rồi thì xử lý idempotent (không báo lỗi)
    if order.status == OrderStatus.AT_HUB:
        if not order.origin_received_at:
            order.origin_received_at = now
        if not order.origin_received_by:
            order.origin_received_by = str(user.id)
        order.updated_at = now
        await db.commit()
        return {
            "message": "Package already received at Hub",
            "order_id": order.id,
            "order_status": OrderStatus.AT_HUB.value,
            "origin_received_at": order.origin_received_at.isoformat() if order.origin_received_at else None,
        }

    if order.status not in [OrderStatus.PENDING, OrderStatus.AWAITING_DROP_OFF]:
        raise HTTPException(
            status_code=400,
            detail=f"Order đang ở trạng thái '{order.status.value}', không thể nhận hàng"
        )

    # Issue #2: Nếu có bất kỳ Mission nào bị tạo sớm (seed/legacy) khi đơn còn PENDING/AWAITING_DROP_OFF,
    # hủy mission tạo sớm đó và giải phóng drone/pin để Operator tạo mission chính thức sau khi AT_HUB.
    premature_missions_res = await db.execute(
        select(Mission).where(
            Mission.order_id == order.id,
            Mission.status.in_([
                MissionStatus.SCHEDULED,
                MissionStatus.PENDING_APPROVAL,
                MissionStatus.APPROVED,
            ])
        )
    )
    for pm in premature_missions_res.scalars().all():
        pm.status = MissionStatus.CANCELLED
        pm.handling_status = None

    order.status = OrderStatus.AT_HUB
    order.origin_received_at = now
    order.origin_received_by = str(user.id)
    order.updated_at = now

    await db.commit()
    return {
        "message": "Package received at Hub",
        "order_id": order.id,
        "order_status": OrderStatus.AT_HUB.value,
        "origin_received_at": order.origin_received_at.isoformat() if order.origin_received_at else None,
    }


@deliveries_router.post("/outbound/{orderId}/prepare-battery", summary="Lắp Pin chuẩn bị bay")
async def prepare_battery_for_delivery(
    orderId: str,
    body: Optional[PrepareBatteryRequest] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Xác nhận Technician đã chọn và lắp Pin vào máy bay để chuẩn bị bay.
    Chỉ được thực hiện sau khi hàng đã nhận vào Hub (order.status = AT_HUB) và Operator đã tạo Mission.

    Yêu cầu:
    - Nếu FE gửi `battery_id` trong request body: Backend sẽ kiểm tra viên pin Technician chỉ định.
    - Nếu FE không gửi `battery_id`: Backend sẽ tự động tìm 1 viên pin sạc 100% tương thích tại Hub (Fallback).
    """
    from sqlalchemy import func
    clean_id = (orderId or "").strip()

    # 1. Kiểm tra Order và trạng thái nhận hàng tại Hub (theo flow mới của main)
    order_result = await db.execute(
        select(Order).where(
            (Order.id == clean_id) | (func.upper(func.trim(Order.id)) == clean_id.upper())
        )
    )
    order = order_result.scalars().first()

    if order:
        if order.status != OrderStatus.AT_HUB:
            raise HTTPException(
                status_code=400,
                detail="Hàng chưa được nhận vào hub (cần AT_HUB trước)"
            )
        mission_result = await db.execute(
            select(Mission).where(
                Mission.order_id == order.id,
                Mission.status == MissionStatus.SCHEDULED
            ).order_by(Mission.id.desc()).limit(1)
        )
        mission = mission_result.scalars().first()
        if not mission:
            # Fallback tìm theo order_id nếu mission có status active khác SCHEDULED
            mission_fallback = await db.execute(
                select(Mission).where(
                    Mission.order_id == order.id,
                    Mission.status.in_([
                        MissionStatus.APPROVED,
                        MissionStatus.IN_PROGRESS,
                        MissionStatus.FLYING,
                        MissionStatus.ACTIVE_MISSION,
                    ])
                ).order_by(Mission.id.desc()).limit(1)
            )
            mission = mission_fallback.scalars().first()
        if not mission:
            raise HTTPException(
                status_code=400,
                detail="Chưa có mission được tạo. Operator cần tạo mission trước."
            )
    else:
        # Hỗ trợ trường hợp truyền trực tiếp mission_id
        mission = await get_mission_by_order_id(db, clean_id)
        if mission.order_id:
            parent_order = await db.get(Order, mission.order_id)
            if parent_order and parent_order.status != OrderStatus.AT_HUB:
                raise HTTPException(
                    status_code=400,
                    detail="Hàng chưa được nhận vào hub (cần AT_HUB trước)"
                )
        if mission.handling_status not in [None, HandlingStatus.AT_HUB, HandlingStatus.READY]:
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

    hub_id = mission.origin_hub_id or (order.origin_hub_id if order else None)
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

        # Validate mức sạc pin phải còn năng lượng
        if (battery.charge_level_pct or 0) <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {battery.id} cạn năng lượng (Mức sạc hiện tại: {battery.charge_level_pct}%). Vui lòng sạc pin hoặc chọn viên pin khác."
            )

        # Validate pin chưa bị gán cho Drone khác
        if battery.drone_id is not None and battery.drone_id != mission.drone_id:
            other_drone = await db.get(Drone, battery.drone_id)
            other_name = other_drone.name if other_drone else f"ID {battery.drone_id}"
            raise HTTPException(
                status_code=400,
                detail=f"Pin {battery.id} hiện đang được gắn trên máy bay khác ({other_name}). Vui lòng chọn viên pin khả dụng khác."
            )

    # TRƯỜNG HỢP B: FE không truyền battery_id -> Ưu tiên viên pin Operator đã chọn sẵn cho Mission (mission.battery_id),
    # nếu chưa có mới fallback tự động chọn 1 pin tương thích tại Hub
    else:
        battery = None
        if mission.battery_id:
            preassigned_bat = await db.get(Battery, str(mission.battery_id))
            if (
                preassigned_bat
                and (not hub_id or preassigned_bat.current_hub_id == hub_id or preassigned_bat.drone_id == mission.drone_id)
                and (not compatible_battery_models or not preassigned_bat.battery_model or preassigned_bat.battery_model in compatible_battery_models)
                and (preassigned_bat.status == batteries_status.ACTIVE or str(preassigned_bat.status).upper() in ["ACTIVE", "AVAILABLE", "READY"])
                and (preassigned_bat.drone_id is None or preassigned_bat.drone_id == mission.drone_id)
            ):
                battery = preassigned_bat

        if not battery:
            auto_bat_stmt = select(Battery).where(
                and_(
                    Battery.current_hub_id == hub_id,
                    Battery.battery_model.in_(compatible_battery_models),
                    Battery.charge_level_pct > 0,
                    Battery.status == batteries_status.ACTIVE,
                    (Battery.drone_id == mission.drone_id) | (Battery.drone_id.is_(None))
                )
            ).order_by(Battery.charge_level_pct.desc(), Battery.capacity_wh.desc())
            res_auto = await db.execute(auto_bat_stmt)
            battery = res_auto.scalars().first()
        if not battery:
            raise HTTPException(
                status_code=400,
                detail=f"Tại Hub {hub_id} hiện không có viên pin khả dụng nào tương thích với máy bay {drone.name} ({drone.model}). Vui lòng sạc pin hoặc chọn viên pin khác."
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

    # Nếu trước đó drone đang gắn viên pin khác, tháo viên pin cũ ra
    if mission.battery_id and mission.battery_id != battery.id:
        old_bat = await db.get(Battery, mission.battery_id)
        if old_bat and old_bat.drone_id == mission.drone_id:
            old_bat.drone_id = None

    # --- TIẾN HÀNH GÁN PIN VÀ ĐỒNG BỘ TRẠNG THÁI ---
    mission.battery_id = battery.id
    mission.handling_status = HandlingStatus.READY
    battery.drone_id = mission.drone_id
    if hub_id:
        battery.current_hub_id = hub_id

    # Đồng bộ battery_id cho chặng đầu tiên (sequence_no = 1) của MissionLeg
    from app.modules.missions.models import MissionLeg
    leg1_res = await db.execute(
        select(MissionLeg)
        .where(MissionLeg.mission_id == mission.id)
        .order_by(MissionLeg.sequence_no.asc(), MissionLeg.id.asc())
        .limit(1)
    )
    leg1 = leg1_res.scalars().first()
    if leg1:
        leg1.battery_id = battery.id

    await db.commit()
    await db.refresh(mission)
    await db.refresh(battery)

    handling_status_val = mission.handling_status.value if hasattr(mission.handling_status, "value") else str(mission.handling_status)
    status_val = mission.status.value if hasattr(mission.status, "value") else str(mission.status)

    return {
        "message": "Battery prepared, ready to fly",
        "order_id": mission.order_id or clean_id,
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
            "order_id": mission.order_id or clean_id,
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
            User, (Mission.customer_id == User.id) | (Mission.customer_id == User.email)
        ).where(
            Mission.arrived_at.is_not(None),
            Mission.arrival_confirmed_at.is_(None),
            Mission.status.notin_([
                MissionStatus.MISSION_COMPLETED,
                MissionStatus.CANCELLED,
            ])
        )
        if user.hub_id and str(user.hub_id).isdigit():
            query = query.where(Mission.destination_hub_id == int(user.hub_id))
            
        result = await db.execute(query.order_by(Mission.id.desc()))
        rows = result.all()
        return [
            {
                "id": row.id,
                "order_id": row.order_id,
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
        
    if mission.arrival_confirmed_at is not None and mission.status == MissionStatus.MISSION_COMPLETED:
        raise HTTPException(status_code=400, detail="Pickup already confirmed")
        
    now = get_vn_naive_datetime()
    mission.arrival_confirmed_at = now
    mission.arrival_confirmed_by = user.id
    mission.status = MissionStatus.MISSION_COMPLETED
    mission.handling_status = None
    
    await db.commit()
    await db.refresh(mission)
    return {
        "message": "Pickup confirmed successfully",
        "mission_id": mission.id,
        "order_id": mission.order_id,
        "status": mission.status.value if hasattr(mission.status, "value") else str(mission.status),
        "mission": mission,
    }


from app.modules.fleet.technician.router import (
    ConfirmArrivalRequest,
    SwapBatteryRequest,
    confirm_arrival as _fleet_confirm_arrival,
    confirm_relay_arrival as _fleet_confirm_relay_arrival,
    swap_battery_at_relay as _fleet_swap_battery_at_relay,
    dispatch_drone as _fleet_dispatch_drone,
)


from app.modules.fleet.technician.router import (
    ConfirmArrivalRequest,
    SwapBatteryRequest,
    confirm_arrival as _fleet_confirm_arrival,
    confirm_relay_arrival as _fleet_confirm_relay_arrival,
    swap_battery_at_relay as _fleet_swap_battery_at_relay,
    dispatch_drone as _fleet_dispatch_drone,
)


# ── Audit Trail & Order Timeline ──────────────────────────────────────────────

@missions_router.get("/{mission_id}/audit-trail", summary="Audit Trail - Lịch sử timeline của đơn hàng")
async def get_order_audit_trail(
    mission_id: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy lịch sử timeline của 1 mission/order cho audit:
    - Khi nào tạo đơn (created_at)
    - Khi nào thanh toán (paid_at)
    - Khi nào nhận tại hub (origin_received_at)
    - Khi nào dispatch (mission.departed_at)
    - Khi nào đáp hub đích (mission.arrived_at)
    - Khi nào khách confirm nhận (arrival_confirmed_at)
    - Khi nào bảo trì (work_order.resolved_at)
    
    Trả về dữ liệu để audit và kiểm tra tuân thủ SLA.
    """
    from app.modules.missions.models import Order as OrderModel, MissionLeg
    from app.modules.fleet.models import WorkOrder as WO
    
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    
    # Lấy Order
    order = None
    if mission.order_id:
        order_res = await db.execute(
            select(OrderModel).where(OrderModel.id == mission.order_id)
        )
        order = order_res.scalar_one_or_none()
    
    # Lấy Mission Legs
    legs_res = await db.execute(
        select(MissionLeg).where(MissionLeg.mission_id == mission_id).order_by(MissionLeg.sequence_no)
    )
    legs = legs_res.scalars().all()
    
    # Lấy Work Orders (maintenance)
    wo_res = await db.execute(
        select(WO).where(WO.drone_id == mission.drone_id).order_by(WO.resolved_at.desc().nullslast())
    )
    work_orders = wo_res.scalars().all()
    
    # Build timeline
    timeline = []
    
    if order:
        if order.created_at:
            timeline.append({
                "timestamp": order.created_at,
                "event": "Order Created",
                "status": order.status.value if order.status else None,
                "actor": "Customer"
            })
        
        if order.paid_at:
            timeline.append({
                "timestamp": order.paid_at,
                "event": "Payment Confirmed",
                "payment_status": order.payment_status.value if order.payment_status else None,
                "actor": "Payment System"
            })
        
        if order.origin_received_at:
            timeline.append({
                "timestamp": order.origin_received_at,
                "event": "Package Received at Origin Hub",
                "received_by": order.origin_received_by,
                "hub_id": order.origin_hub_id,
                "actor": "Technician"
            })
    
    if mission.departed_at:
        timeline.append({
            "timestamp": mission.departed_at,
            "event": "Drone Dispatched",
            "mission_status": mission.status.value if mission.status else None,
            "drone_id": mission.drone_id,
            "leg_count": len(legs),
            "actor": "Technician"
        })
    
    # Legs timeline
    for leg in legs:
        if leg.started_at:
            timeline.append({
                "timestamp": leg.started_at,
                "event": f"Leg {leg.sequence_no} Started",
                "from_hub_id": leg.from_hub_id,
                "to_hub_id": leg.to_hub_id,
                "sequence_no": leg.sequence_no,
                "actor": "Drone"
            })
        
        if leg.completed_at:
            timeline.append({
                "timestamp": leg.completed_at,
                "event": f"Leg {leg.sequence_no} Completed",
                "sequence_no": leg.sequence_no,
                "actor": "Drone"
            })
    
    if mission.arrived_at:
        timeline.append({
            "timestamp": mission.arrived_at,
            "event": "Drone Arrived at Destination Hub",
            "hub_id": mission.destination_hub_id,
            "actor": "Drone"
        })
    
    if mission.arrival_confirmed_at:
        timeline.append({
            "timestamp": mission.arrival_confirmed_at,
            "event": "Customer Confirmed Pickup",
            "confirmed_by": mission.arrival_confirmed_by,
            "actor": "Customer"
        })
    
    # Work orders
    for wo in work_orders[:5]:  # Show last 5
        if wo.resolved_at:
            timeline.append({
                "timestamp": wo.resolved_at,
                "event": "Maintenance Completed",
                "work_order_id": wo.id,
                "issue": wo.issue_description,
                "action": wo.action_taken,
                "actor": "Technician"
            })
    
    # Sort by timestamp
    timeline = sorted(timeline, key=lambda x: x.get("timestamp") or datetime.min)
    
    return {
        "mission_id": mission_id,
        "order_id": mission.order_id,
        "drone_id": mission.drone_id,
        "timeline": timeline,
        "summary": {
            "total_events": len(timeline),
            "first_event": timeline[0].get("timestamp") if timeline else None,
            "last_event": timeline[-1].get("timestamp") if timeline else None,
            "total_duration": (
                (timeline[-1].get("timestamp") - timeline[0].get("timestamp")).total_seconds() / 3600
                if timeline and len(timeline) > 1 else None
            ),  # in hours
        }
    }


@missions_router.get("/audit/sla-report", summary="SLA Report - Báo cáo tuân thủ SLA")
async def get_sla_report(
    start_date: str,  # "2026-10-01"
    end_date: str,    # "2026-10-31"
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Báo cáo audit SLA cho khoảng thời gian:
    - Tổng số đơn hàng
    - Số đơn hoàn thành đúng hạn
    - Số đơn trễ
    - Thời gian trung bình từ đơn đặt → giao thành công
    - Drone uptime %
    - Maintenance history
    """
    from datetime import datetime
    from app.modules.missions.models import Order as OrderModel
    
    try:
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
    except:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    # Lấy orders trong khoảng thời gian
    orders_res = await db.execute(
        select(OrderModel).where(
            and_(
                OrderModel.created_at >= start,
                OrderModel.created_at <= end,
                OrderModel.status.in_([
                    OrderStatus.DELIVERED_TO_HUB,
                    OrderStatus.CANCELLED
                ])
            )
        )
    )
    orders = orders_res.scalars().all()
    
    # Tính toán metrics
    total = len(orders)
    completed = len([o for o in orders if o.status == OrderStatus.DELIVERED_TO_HUB and o.destination_received_at])
    on_time = 0
    durations = []
    
    for order in orders:
        if order.created_at and order.destination_received_at:
            duration = (order.destination_received_at - order.created_at).total_seconds() / 3600
            durations.append(duration)
            
            # Assume SLA = 24 hours
            if duration <= 24:
                on_time += 1
    
    avg_duration = sum(durations) / len(durations) if durations else 0
    sla_compliance = (on_time / total * 100) if total > 0 else 0
    
    return {
        "period": f"{start_date} to {end_date}",
        "metrics": {
            "total_orders": total,
            "completed_orders": completed,
            "cancelled_orders": len([o for o in orders if o.status == OrderStatus.CANCELLED]),
            "on_time_orders": on_time,
            "sla_compliance_percent": round(sla_compliance, 2),
            "avg_delivery_hours": round(avg_duration, 2),
        }
    }


@missions_router.post("/{mission_id}/confirm-arrival", summary="Confirm drone đáp hub đích (hoàn tất mission)")
async def technician_mission_confirm_arrival(
    mission_id: str,
    body: Optional[ConfirmArrivalRequest] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    return await _fleet_confirm_arrival(mission_id=mission_id, body=body, user=user, db=db)


@missions_router.post("/{mission_id}/confirm-relay-arrival", summary="Confirm drone đáp relay hub trung gian")
async def technician_mission_confirm_relay_arrival(
    mission_id: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    return await _fleet_confirm_relay_arrival(mission_id=mission_id, user=user, db=db)


@missions_router.post("/{mission_id}/swap-battery", summary="Thay pin tại relay hub trung gian")
async def technician_mission_swap_battery(
    mission_id: str,
    body: Optional[SwapBatteryRequest] = None,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    return await _fleet_swap_battery_at_relay(mission_id=mission_id, body=body, user=user, db=db)


@missions_router.post("/{mission_id}/dispatch", summary="Dispatch drone bay tiếp chặng kế tiếp")
async def technician_mission_dispatch(
    mission_id: str,
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    return await _fleet_dispatch_drone(mission_id=mission_id, user=user, db=db)


router.include_router(missions_router)
router.include_router(deliveries_router)