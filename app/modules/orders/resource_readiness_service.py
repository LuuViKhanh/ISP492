import math
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.modules.system.models import Hub
from app.modules.fleet.models import Drone, DroneStatus, Battery, batteries_status
from app.modules.missions.models import Order, OrderStatus, Mission, MissionStatus

# Tốc độ sạc pin giả định chuẩn CC-CV (% / phút)
CHARGING_RATE_PCT_PER_MIN = 1.0
# Ngưỡng pin tối thiểu để thực hiện chuyến bay an toàn
MIN_BATTERY_READY_PCT = 80
# Thời gian xử lý & cất cánh trung bình mỗi đơn chờ tại trạm (phút)
MINUTES_PER_WAITING_ORDER = 15
# Thời gian bay điều phối từ Hub khác sang nhận hàng (Cross-Hub Dispatch) (phút)
CROSS_HUB_DISPATCH_BUFFER_MIN = 20
# Thời gian chờ khi toàn hệ thống đang bận (phút)
FLEET_BUSY_BUFFER_MIN = 30


async def evaluate_hub_resource_readiness(
    db: AsyncSession,
    origin_hub_id: int,
    destination_hub_id: int,
    payload_kg: float,
) -> dict[str, Any]:
    """
    Đánh giá tài nguyên và ước tính thời gian chờ ban đầu tại Hub gửi:
    - Hàng chờ đơn dồn về Hub (T_queue)
    - Tình trạng sạc pin tại Hub (T_charge)
    - Tình trạng máy bay tại Hub hoặc điều phối Cross-Hub (T_dispatch)
    - Phản hồi thông điệp trực quan và tự động bù thời gian vào ETA giao hàng cho Khách hàng.
    """
    origin_hub = await db.get(Hub, origin_hub_id)
    dest_hub = await db.get(Hub, destination_hub_id)

    # 1. Đánh giá tải trọng so với phi đội Drone
    res_drones = await db.execute(select(Drone))
    all_drones = res_drones.scalars().all()
    max_payload = max([d.payload_capacity_kg for d in all_drones] or [10.0])

    is_payload_supported = payload_kg <= max_payload
    suggested_tier = "Light (Swift)" if payload_kg <= 1.0 else ("Medium (Falcon)" if payload_kg <= 5.0 else "Heavy (Eagle)")

    # 2. Đánh giá hàng chờ đơn tại Trạm gửi (T_queue)
    active_mission_statuses = [
        MissionStatus.SCHEDULED,
        MissionStatus.IN_PROGRESS,
        MissionStatus.APPROVED,
        MissionStatus.FLYING,
        MissionStatus.ACTIVE_MISSION,
    ]
    res_active_missions = await db.execute(
        select(Mission.order_id).where(
            Mission.status.in_(active_mission_statuses),
            Mission.order_id.isnot(None)
        )
    )
    busy_order_ids = set(res_active_missions.scalars().all())

    res_orders = await db.execute(
        select(Order).where(
            Order.origin_hub_id == origin_hub_id,
            Order.status.in_([OrderStatus.PENDING, OrderStatus.AT_HUB]),
        )
    )
    waiting_orders = [o for o in res_orders.scalars().all() if o.id not in busy_order_ids]
    waiting_orders_count = len(waiting_orders)
    queue_wait_minutes = waiting_orders_count * MINUTES_PER_WAITING_ORDER

    # 3. Đánh giá tình trạng Pin tại Trạm gửi (T_charge)
    res_bats = await db.execute(
        select(Battery).where(
            Battery.current_hub_id == origin_hub_id,
            Battery.status == batteries_status.ACTIVE,
        )
    )
    hub_batteries = res_bats.scalars().all()

    # Lấy pin chưa gắn trên Drone nào hoặc gắn trên drone rảnh
    ready_bats = [b for b in hub_batteries if (b.charge_level_pct or 0) >= MIN_BATTERY_READY_PCT]
    charge_wait_minutes = 0
    highest_charge_pct = 0
    battery_readiness = "READY"

    if len(ready_bats) > 0:
        charge_wait_minutes = 0
        battery_readiness = "READY"
    elif len(hub_batteries) > 0:
        highest_charge_pct = max([b.charge_level_pct or 0 for b in hub_batteries] or [0])
        needed_pct = max(0, MIN_BATTERY_READY_PCT - highest_charge_pct)
        charge_wait_minutes = math.ceil(needed_pct / CHARGING_RATE_PCT_PER_MIN)
        battery_readiness = "CHARGING"
    else:
        # Không có pin tại Hub gửi
        charge_wait_minutes = 25
        battery_readiness = "EMPTY"

    # 4. Đánh giá tình trạng Máy bay tại Hub gửi & Cross-Hub Dispatch
    local_available_drones = [
        d for d in all_drones
        if d.current_hub_id == origin_hub_id
        and d.status == DroneStatus.AVAILABLE
        and (d.payload_capacity_kg or 0.0) >= payload_kg
    ]
    dispatch_buffer_minutes = 0
    drone_readiness = "AVAILABLE_AT_HUB"

    if len(local_available_drones) > 0:
        drone_readiness = "AVAILABLE_AT_HUB"
        dispatch_buffer_minutes = 0
    else:
        # Kiểm tra Drone rảnh đủ tải ở Hub khác
        other_available_drones = [
            d for d in all_drones
            if d.current_hub_id != origin_hub_id
            and d.status == DroneStatus.AVAILABLE
            and (d.payload_capacity_kg or 0.0) >= payload_kg
        ]
        if len(other_available_drones) > 0:
            drone_readiness = "CROSS_HUB_DISPATCH_REQUIRED"
            dispatch_buffer_minutes = CROSS_HUB_DISPATCH_BUFFER_MIN
        else:
            drone_readiness = "BUSY_FLEET"
            dispatch_buffer_minutes = FLEET_BUSY_BUFFER_MIN

    # 5. Tổng hợp thời gian chờ bổ sung
    total_extra_wait_minutes = max(queue_wait_minutes, charge_wait_minutes) + dispatch_buffer_minutes

    if total_extra_wait_minutes == 0:
        readiness_level = "OPTIMAL"
    elif total_extra_wait_minutes <= 30:
        readiness_level = "MODERATE"
    else:
        readiness_level = "BUSY"

    # 6. Soạn thảo thông điệp phản hồi minh bạch cho Khách hàng
    notice_parts = []
    if waiting_orders_count > 0:
        notice_parts.append(f"Trạm gửi đang có {waiting_orders_count} đơn hàng chờ xử lý (+{queue_wait_minutes} phút)")

    if battery_readiness == "CHARGING":
        notice_parts.append(f"Pin tại trạm đang sạc (hiện đạt {highest_charge_pct}%, dự kiến sẵn sàng sau {charge_wait_minutes} phút)")
    elif battery_readiness == "EMPTY":
        notice_parts.append(f"Trạm gửi đang chuẩn bị nguồn pin dự phòng (+{charge_wait_minutes} phút)")

    if drone_readiness == "CROSS_HUB_DISPATCH_REQUIRED":
        notice_parts.append("Trạm gửi trống máy bay tại chỗ; hệ thống sẽ tự động điều phối Drone từ trạm lân cận sang lấy hàng (+20 phút)")
    elif drone_readiness == "BUSY_FLEET":
        notice_parts.append("Các máy bay phù hợp đang thực hiện nhiệm vụ trước đó; đơn sẽ được xếp vào hàng chờ ưu tiên")

    if not notice_parts:
        customer_notice = "Tài nguyên tại Trạm gửi sẵn sàng (có sẵn Drone và Pin đầy). Đơn hàng sẽ được xử lý ngay sau khi bàn giao."
    else:
        customer_notice = (
            f"Thông báo điều phối tại Trạm {origin_hub.name if origin_hub else ''}: "
            + "; ".join(notice_parts)
            + f". Tổng thời gian chờ chuẩn bị ước tính khoảng {total_extra_wait_minutes} phút (đã được tự động bù vào khung giờ giao hàng)."
        )

    return {
        "readiness_level": readiness_level,
        "is_payload_supported": is_payload_supported,
        "suggested_drone_tier": suggested_tier,
        "total_extra_wait_minutes": total_extra_wait_minutes,
        "queue_orders_count": waiting_orders_count,
        "queue_wait_minutes": queue_wait_minutes,
        "battery_readiness": battery_readiness,
        "highest_battery_charge_pct": highest_charge_pct,
        "charge_wait_minutes": charge_wait_minutes,
        "drone_readiness": drone_readiness,
        "dispatch_buffer_minutes": dispatch_buffer_minutes,
        "customer_notice": customer_notice,
    }
