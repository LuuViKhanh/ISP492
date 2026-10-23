from typing import Optional, List, Dict, Any, Union
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from fastapi import HTTPException

from app.modules.missions.models import Order, OrderStatus, PaymentStatus, Mission, MissionStatus
from app.modules.fleet.models import Drone, DroneStatus
from app.modules.system.models import Hub

ACTIVE_MISSION_STATUSES = [
    MissionStatus.SCHEDULED,
    MissionStatus.IN_PROGRESS,
    MissionStatus.APPROVED,
    MissionStatus.FLYING,
    MissionStatus.ACTIVE_MISSION,
]

async def get_active_mission_order_ids(db: AsyncSession) -> set[str]:
    """Lấy danh sách order_id của tất cả các Mission đang hoạt động."""
    res = await db.execute(
        select(Mission.order_id).where(
            Mission.status.in_(ACTIVE_MISSION_STATUSES),
            Mission.order_id.isnot(None)
        )
    )
    return set(res.scalars().all())

async def get_available_orders_query(
    db: AsyncSession,
    origin_hub_id: int,
    destination_hub_id: int,
    exclude_order_ids: Optional[set[str]] = None,
    require_paid: bool = False,
) -> list[Order]:
    """
    Truy vấn các đơn hàng sẵn sàng gộp tại cùng Trạm gửi và Trạm nhận:
    - Trạng thái: AT_HUB
    - Chưa gắn với Mission đang hoạt động nào.
    - require_paid: Lọc thêm điều kiện thanh toán PAID nếu cần.
    """
    active_order_ids = await get_active_mission_order_ids(db)
    if exclude_order_ids:
        active_order_ids.update(exclude_order_ids)

    conditions = [
        Order.origin_hub_id == origin_hub_id,
        Order.destination_hub_id == destination_hub_id,
        Order.status == OrderStatus.AT_HUB,
    ]
    if require_paid:
        conditions.append(Order.payment_status == PaymentStatus.PAID)

    query = select(Order).where(*conditions).order_by(Order.created_at.asc())
    result = await db.execute(query)
    all_orders = result.scalars().all()
    return [o for o in all_orders if o.id not in active_order_ids]

# ══════════════════════════════════════════════════════════════════════════════
# LUỒNG 1: PRE-ANALYZE CONSOLIDATION (Gom đơn trước khi Phân tích Lộ trình)
# ══════════════════════════════════════════════════════════════════════════════

async def get_pre_analyze_bundle_candidates(
    db: AsyncSession,
    primary_order_id: str,
    require_paid: bool = False,
) -> dict[str, Any]:
    """
    Lấy danh sách các đơn hàng có thể gộp trước với đơn hàng chính.
    Hiển thị tổng tải trọng tiềm năng và gợi ý dòng Drone phù hợp (Swift, Falcon, Eagle).
    """
    clean_id = primary_order_id.strip()
    primary_order = await db.get(Order, clean_id)
    if not primary_order:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy đơn hàng '{primary_order_id}'")

    if not primary_order.origin_hub_id or not primary_order.destination_hub_id:
        raise HTTPException(status_code=400, detail="Đơn hàng thiếu thông tin Trạm xuất phát hoặc Trạm đích")

    origin_hub = await db.get(Hub, primary_order.origin_hub_id)
    dest_hub = await db.get(Hub, primary_order.destination_hub_id)

    candidates = await get_available_orders_query(
        db=db,
        origin_hub_id=primary_order.origin_hub_id,
        destination_hub_id=primary_order.destination_hub_id,
        exclude_order_ids={primary_order.id},
        require_paid=require_paid,
    )

    candidates_data = []
    for o in candidates:
        weight = float(o.payload_kg or 0.0)
        candidates_data.append({
            "order_id": o.id,
            "package_label": o.package_label,
            "payload_kg": weight,
            "receiver_name": o.receiver_name,
            "receiver_phone": o.receiver_phone,
            "delivery_fee": float(o.delivery_fee or 0.0),
            "payment_status": o.payment_status.value if o.payment_status else "PENDING",
            "is_paid": o.payment_status == PaymentStatus.PAID,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })

    return {
        "workflow": "PRE_ANALYZE",
        "primary_order": {
            "order_id": primary_order.id,
            "package_label": primary_order.package_label,
            "payload_kg": float(primary_order.payload_kg or 0.0),
            "origin_hub_id": primary_order.origin_hub_id,
            "origin_hub_name": origin_hub.name if origin_hub else None,
            "destination_hub_id": primary_order.destination_hub_id,
            "destination_hub_name": dest_hub.name if dest_hub else None,
            "payment_status": primary_order.payment_status.value if primary_order.payment_status else "PENDING",
            "is_paid": primary_order.payment_status == PaymentStatus.PAID,
            "status": primary_order.status.value if primary_order.status else None,
        },
        "candidate_orders": candidates_data,
        "total_candidates": len(candidates_data),
        "drone_capacity_guidelines": [
            {"tier": "Light", "name_prefix": "Swift", "max_payload_kg": 1.0, "note": "Phù hợp chở 1 đơn nhẹ"},
            {"tier": "Medium", "name_prefix": "Falcon", "max_payload_kg": 5.0, "note": "Phù hợp gộp 2 - 4 đơn"},
            {"tier": "Heavy", "name_prefix": "Eagle", "max_payload_kg": 10.0, "note": "Phù hợp gộp nhiều đơn lớn"},
        ]
    }

async def validate_bundle_orders(
    db: AsyncSession,
    order_ids: list[str],
    drone_id: Optional[Union[str, int]] = None,
    strict_payment: bool = False,
) -> dict[str, Any]:
    """
    Xác thực tập hợp đơn hàng định gộp chung một chuyến bay:
    1. Đảm bảo tất cả đơn hàng cùng origin_hub và destination_hub.
    2. Đảm bảo tất cả đơn hàng đang AT_HUB (và PAID nếu strict_payment=True).
    3. Đảm bảo không đơn nào đang nằm trong Mission active khác.
    4. Nếu có chọn Drone: Kiểm tra tổng tải trọng <= tải trọng tối đa của Drone.
    """
    clean_ids = list(dict.fromkeys([str(oid).strip() for oid in order_ids if str(oid).strip()]))
    if not clean_ids:
        raise HTTPException(status_code=400, detail="Danh sách đơn hàng gộp không được rỗng")

    active_order_ids = await get_active_mission_order_ids(db)
    orders: list[Order] = []
    total_payload_kg = 0.0
    total_fee = 0.0

    ref_origin = None
    ref_dest = None

    for oid in clean_ids:
        o = await db.get(Order, oid)
        if not o:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy đơn hàng #{oid}")

        if strict_payment and o.payment_status != PaymentStatus.PAID:
            raise HTTPException(status_code=400, detail=f"Đơn hàng #{oid} chưa được thanh toán (PAID)")

        if o.status != OrderStatus.AT_HUB:
            raise HTTPException(status_code=400, detail=f"Đơn hàng #{oid} chưa ở trạng thái sẵn sàng tại trạm (AT_HUB)")

        if o.id in active_order_ids:
            raise HTTPException(status_code=400, detail=f"Đơn hàng #{oid} hiện đã có nhiệm vụ bay đang thực hiện")

        if ref_origin is None:
            ref_origin = o.origin_hub_id
            ref_dest = o.destination_hub_id
        else:
            if o.origin_hub_id != ref_origin or o.destination_hub_id != ref_dest:
                raise HTTPException(
                    status_code=400,
                    detail=f"Đơn hàng #{oid} có tuyến (Hub #{o.origin_hub_id} -> #{o.destination_hub_id}) không khớp với các đơn khác trong gói (Hub #{ref_origin} -> #{ref_dest})"
                )

        payload = float(o.payload_kg or 0.0)
        fee = float(o.delivery_fee or 0.0)
        total_payload_kg += payload
        total_fee += fee
        orders.append(o)

    total_payload_kg = round(total_payload_kg, 2)
    total_fee = round(total_fee, 2)

    drone_obj = None
    drone_capacity_kg = None
    remaining_capacity_kg = None
    is_valid = True
    validation_message = "Tập đơn hàng hợp lệ để gộp chuyến bay."

    if drone_id is not None:
        clean_drone_id = str(drone_id).strip()
        drone_obj = await db.get(Drone, clean_drone_id)
        if not drone_obj and clean_drone_id.isdigit():
            alt_id = f"DRN-{int(clean_drone_id):04d}"
            drone_obj = await db.get(Drone, alt_id)

        if not drone_obj:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy Drone '{drone_id}'")

        drone_capacity_kg = float(drone_obj.payload_capacity_kg or 0.0)
        remaining_capacity_kg = round(drone_capacity_kg - total_payload_kg, 2)

        if total_payload_kg > drone_capacity_kg:
            is_valid = False
            validation_message = (
                f"Tổng tải trọng ({total_payload_kg} kg) vượt quá sức chở của Drone "
                f"#{drone_obj.id} ({drone_obj.name}: {drone_capacity_kg} kg). Vui lòng chọn Drone lớn hơn hoặc bỏ bớt đơn."
            )

    return {
        "is_valid": is_valid,
        "message": validation_message,
        "total_orders": len(orders),
        "order_ids": clean_ids,
        "total_payload_kg": total_payload_kg,
        "total_delivery_fee": total_fee,
        "origin_hub_id": ref_origin,
        "destination_hub_id": ref_dest,
        "drone_id": drone_obj.id if drone_obj else None,
        "drone_name": drone_obj.name if drone_obj else None,
        "drone_capacity_kg": drone_capacity_kg,
        "remaining_capacity_kg": remaining_capacity_kg,
        "orders_detail": [
            {
                "order_id": o.id,
                "package_label": o.package_label,
                "payload_kg": float(o.payload_kg or 0.0),
                "receiver_name": o.receiver_name,
                "delivery_fee": float(o.delivery_fee or 0.0),
                "payment_status": o.payment_status.value if o.payment_status else "PENDING",
                "is_paid": o.payment_status == PaymentStatus.PAID,
            }
            for o in orders
        ]
    }

# ══════════════════════════════════════════════════════════════════════════════
# LUỒNG 2: POST-ANALYZE CONSOLIDATION (Ghép đơn sau khi đã Phân tích Tuyến đường)
# ══════════════════════════════════════════════════════════════════════════════

async def get_post_analyze_bundle_candidates(
    db: AsyncSession,
    primary_order_id: str,
    drone_id: Union[str, int],
    route_id: Optional[str] = None,
    require_paid: bool = False,
) -> dict[str, Any]:
    """
    Hiển thị các đơn hàng có thể ghép thêm vào Drone và tuyến đường đã chọn:
    - Tính tải trọng còn dư của Drone sau khi chở đơn chính:
        remaining_capacity = drone.payload_capacity - primary_order.payload
    - Tìm các đơn hàng cùng Hub xuất phát & Hub đích, có payload <= remaining_capacity.
    - Sắp xếp theo FIFO (đơn tạo trước được ưu tiên ghép).
    - Không yêu cầu phân tích lại lộ trình (tránh vòng lặp lớn).
    """
    clean_order_id = str(primary_order_id).strip()
    primary_order = await db.get(Order, clean_order_id)
    if not primary_order:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy đơn hàng '{primary_order_id}'")

    clean_drone_id = str(drone_id).strip()
    drone_obj = await db.get(Drone, clean_drone_id)
    if not drone_obj and clean_drone_id.isdigit():
        alt_id = f"DRN-{int(clean_drone_id):04d}"
        drone_obj = await db.get(Drone, alt_id)

    if not drone_obj:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy Drone '{drone_id}'")

    primary_payload = float(primary_order.payload_kg or 0.0)
    drone_capacity = float(drone_obj.payload_capacity_kg or 0.0)
    remaining_capacity = round(drone_capacity - primary_payload, 2)

    origin_hub = await db.get(Hub, primary_order.origin_hub_id)
    dest_hub = await db.get(Hub, primary_order.destination_hub_id)

    candidates = await get_available_orders_query(
        db=db,
        origin_hub_id=primary_order.origin_hub_id,
        destination_hub_id=primary_order.destination_hub_id,
        exclude_order_ids={primary_order.id},
        require_paid=require_paid,
    )

    eligible_candidates = []
    for o in candidates:
        order_weight = float(o.payload_kg or 0.0)
        can_fit = order_weight <= remaining_capacity
        eligible_candidates.append({
            "order_id": o.id,
            "package_label": o.package_label,
            "payload_kg": order_weight,
            "receiver_name": o.receiver_name,
            "receiver_phone": o.receiver_phone,
            "delivery_fee": float(o.delivery_fee or 0.0),
            "payment_status": o.payment_status.value if o.payment_status else "PENDING",
            "is_paid": o.payment_status == PaymentStatus.PAID,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "can_fit_in_remaining_capacity": can_fit,
            "remaining_after_this_order": round(remaining_capacity - order_weight, 2) if can_fit else None,
        })

    # Đưa các đơn vừa tải lên đầu
    eligible_candidates.sort(key=lambda x: (not x["can_fit_in_remaining_capacity"], x["created_at"] or ""))

    return {
        "workflow": "POST_ANALYZE",
        "route_id": route_id,
        "primary_order": {
            "order_id": primary_order.id,
            "package_label": primary_order.package_label,
            "payload_kg": primary_payload,
            "origin_hub_id": primary_order.origin_hub_id,
            "origin_hub_name": origin_hub.name if origin_hub else None,
            "destination_hub_id": primary_order.destination_hub_id,
            "destination_hub_name": dest_hub.name if dest_hub else None,
            "payment_status": primary_order.payment_status.value if primary_order.payment_status else "PENDING",
            "is_paid": primary_order.payment_status == PaymentStatus.PAID,
        },
        "drone": {
            "id": drone_obj.id,
            "name": drone_obj.name,
            "model": drone_obj.model,
            "payload_capacity_kg": drone_capacity,
        },
        "capacity_summary": {
            "drone_capacity_kg": drone_capacity,
            "primary_order_payload_kg": primary_payload,
            "remaining_capacity_kg": remaining_capacity,
            "is_full": remaining_capacity <= 0,
        },
        "available_bundle_candidates": eligible_candidates,
        "total_available_candidates": len(eligible_candidates),
        "fittable_candidates_count": sum(1 for c in eligible_candidates if c["can_fit_in_remaining_capacity"]),
    }
