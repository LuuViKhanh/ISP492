from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel, Field as PydanticField, field_validator
import uuid
import math

from app.database.db import get_async_db
from app.shared.dependencies import get_current_user, CurrentUser
from app.modules.system.models import Hub
from app.modules.missions.models import (
    Order, OrderStatus, PaymentStatus, DeliveryMode,
    Mission, MissionStatus, TelemetryLog,
    MissionLeg, MissionLegStatus,
)
from app.modules.fleet.models import Drone, Battery
from app.modules.orders.resource_readiness_service import evaluate_hub_resource_readiness
from app.modules.orders.customer.schemas import (
    HubResponse,
    OrderEstimateRequest,
    OrderEstimateResponse,
    CreateOrderRequest,
    OrderCreateResponse
)
from app.modules.orders.schemas import (
    TrackingResponse,
    DroneLocationSchema,
    RoutePointSchema,
    TrackingHubSchema,
    TrackingLegSchema,
    TrackingCurrentLegSchema,
    TrailPointSchema,
    TrackingTimelineStepSchema,
    MissionReportResponse,
    ReportHubSchema,
    ReportRouteSchema,
    ReportTimelineSchema,
    ReportPredictionSchema,
    ReportDroneSchema,
    ReportActualsSchema,
)
from app.modules.ai_predictions.service import haversine_distance
from app.modules.missions.bundle_service import get_all_hubs_cached, predict_flight_energy_cached

router = APIRouter(prefix="/customer", tags=["Orders"])

MAX_PATH_POINTS = 2000


# ── Helper: suy ra trạng thái giao hàng thân thiện cho Customer ──────
def compute_delivery_status(order: Order, mission: Optional[Mission]) -> tuple[str, str]:
    """
    Trả về (code, nhãn tiếng Việt) cho trạng thái giao hàng.
    Được dùng bởi dashboard/customer và tracking endpoint.
    """
    if order.status == OrderStatus.CANCELLED:
        return "CANCELLED", "Cancelled"
    if mission is None:
        if order.status == OrderStatus.AWAITING_DROP_OFF:
            return "AWAITING_DROP_OFF", "Awaiting Hub Drop-off"
        if order.status == OrderStatus.AT_HUB:
            return "AT_HUB", "Received at Hub, Awaiting Flight Schedule"
        if order.payment_status == PaymentStatus.PAID:
            return "PAID", "Paid, Awaiting Drop-off"
        return "PENDING", "Pending"
    
    ms_str = (mission.status.value if hasattr(mission.status, "value") else str(mission.status or "")).upper().replace(" ", "_")
    if mission.arrival_confirmed_at is not None or ms_str in ("MISSION_COMPLETED", "COMPLETED"):
        return "DELIVERED", "Delivered"
    if mission.arrived_at is not None:
        return "ARRIVED_AT_HUB", "Arrived at Destination Hub, Ready for Pickup"
    if ms_str in ("FLYING", "IN_PROGRESS", "ACTIVE_MISSION"):
        return "IN_DELIVERY", "In Delivery"
    if ms_str in ("SCHEDULED", "APPROVED", "PENDING_APPROVAL"):
        return "SCHEDULED", "Scheduled"
    if ms_str in ("CANCELLED", "REJECTED", "FAILED"):
        return "CANCELLED", "Cancelled"
    return "PROCESSING", "Processing"


def calculate_delivery_cost(
    dist_km: float, payload_kg: float, package_type: str,
    delivery_mode: DeliveryMode, has_insurance: bool = False
):
    if payload_kg <= 1:
        base_fee, rate_per_km, rate_per_kg = 20000, 4000, 3000
    elif payload_kg <= 3:
        base_fee, rate_per_km, rate_per_kg = 30000, 5000, 4000
    elif payload_kg <= 5:
        base_fee, rate_per_km, rate_per_kg = 45000, 6000, 5000
    else:
        base_fee, rate_per_km, rate_per_kg = 65000, 8000, 6000

    distance_fee = dist_km * rate_per_km
    payload_fee = payload_kg * rate_per_kg
    core_fee = base_fee + distance_fee + payload_fee

    is_fragile = (package_type and package_type.lower() == 'fragile')
    fragile_surcharge = core_fee * 0.15 if is_fragile else 0
    is_express = (delivery_mode == DeliveryMode.EXPRESS)
    express_surcharge = core_fee * 0.25 if is_express else 0
    # NORMAL: không phụ phí Express (flexible scheduling)
    insurance_fee = core_fee * 0.10 if has_insurance else 0

    total_fee = core_fee + fragile_surcharge + express_surcharge + insurance_fee
    service_fee_total = fragile_surcharge + express_surcharge + insurance_fee

    fee_details = {
        "base_fee": base_fee,
        "distance_fee": distance_fee,
        "payload_fee": payload_fee,
        "insurance_fee": insurance_fee,
        "express_surcharge": express_surcharge,
        "fragile_surcharge": fragile_surcharge,
    }
    return core_fee, service_fee_total, total_fee, fee_details


async def _latest_mission_for_order(db: AsyncSession, order_id: str) -> Optional[Mission]:
    res = await db.execute(
        select(Mission)
        .where(
            Mission.order_id == order_id,
            Mission.status != MissionStatus.CANCELLED,
        )
        .order_by(Mission.id.desc())
    )
    return res.scalars().first()


# ══════════════════════════════════════════════════════════════════════
# HUB LIST
# ══════════════════════════════════════════════════════════════════════
@router.get("/hubs", response_model=list[HubResponse])
async def get_hubs(db: AsyncSession = Depends(get_async_db)):
    """
    BE - GET /customer/hubs: Lấy danh sách các điểm giao nhận của hệ thống để khách hàng chọn.
    [PERF NOTE] Dùng get_all_hubs_cached(db) thay vì query select(Hub) lặp lại trên DB remote.
    """
    all_hubs = await get_all_hubs_cached(db)
    hubs = [h for h in all_hubs if str(h.status or "").lower() == "active"]
    return hubs or all_hubs


# ══════════════════════════════════════════════════════════════════════
# ESTIMATE
# ══════════════════════════════════════════════════════════════════════
@router.post("/orders/estimate", response_model=OrderEstimateResponse)
async def estimate_order(
    body: OrderEstimateRequest,
    db: AsyncSession = Depends(get_async_db)
):
    """BE - POST /customer/orders/estimate: Tính toán và trả về báo giá dựa trên năng lượng dự đoán tiêu thụ."""
    if body.payload_kg <= 0 or body.payload_kg > 10.0:
        raise HTTPException(status_code=400, detail="Package weight must be between 0.1 kg and 10.0 kg (Size XL limit).")

    hub_map = {h.id: h for h in await get_all_hubs_cached(db)}
    origin_hub = hub_map.get(body.origin_hub_id)
    dest_hub = hub_map.get(body.destination_hub_id)
    if not origin_hub or not dest_hub:
        raise HTTPException(status_code=404, detail="Hub not found")

    lat1, lon1 = origin_hub.latitude or 0.0, origin_hub.longitude or 0.0
    lat2, lon2 = dest_hub.latitude or 0.0, dest_hub.longitude or 0.0
    dist_km = haversine_distance(lat1, lon1, lat2, lon2)

    prediction = predict_flight_energy_cached(dist_km, body.payload_kg)
    core_fee, service_fee, total_fee, fee_details = calculate_delivery_cost(
        dist_km=dist_km,
        payload_kg=body.payload_kg,
        package_type=getattr(body, "package_type", ""),
        delivery_mode=body.delivery_mode,
        has_insurance=getattr(body, "has_insurance", False)
    )
    delivery_fee = core_fee
    now = datetime.now(timezone.utc)

    # Tính thời gian bay từ AI model (vận tốc chuẩn 10 m/s = 36 km/h)
    flight_duration_sec = dist_km * 1000 / 10.0
    flight_mins = max(math.ceil(flight_duration_sec / 60.0), 10)

    if body.delivery_mode == DeliveryMode.EXPRESS:
        handover_deadline = now + timedelta(hours=3)
        estimated_departure = handover_deadline + timedelta(minutes=30)
        estimated_arrival = estimated_departure + timedelta(minutes=flight_mins)
    elif body.delivery_mode == DeliveryMode.NORMAL:
        # NORMAL: SLA 4h — hệ thống tự lập lịch, FE không cần gửi requested_delivery_at.
        NORMAL_SLA_HOURS = 4
        handover_deadline = now + timedelta(hours=2)          # khách phải bàn giao hàng trong 2h
        estimated_departure = handover_deadline + timedelta(minutes=30)
        estimated_arrival = estimated_departure + timedelta(minutes=flight_mins)
    else:
        if not body.requested_delivery_at:
            raise HTTPException(status_code=400, detail="requested_delivery_at is required for SCHEDULED mode")
        req_time = body.requested_delivery_at
        if req_time.tzinfo is None:
            req_time = req_time.replace(tzinfo=timezone.utc)
        handover_deadline = req_time - timedelta(hours=3)
        estimated_departure = req_time
        estimated_arrival = estimated_departure + timedelta(minutes=flight_mins)

    # Đánh giá tài nguyên Hub và thời gian chờ bổ sung (Hàng chờ T_queue, Sạc pin T_charge, Máy bay T_dispatch)
    readiness = await evaluate_hub_resource_readiness(
        db=db,
        origin_hub_id=body.origin_hub_id,
        destination_hub_id=body.destination_hub_id,
        payload_kg=body.payload_kg,
    )
    extra_wait_td = timedelta(minutes=readiness["total_extra_wait_minutes"])
    adjusted_departure = estimated_departure + extra_wait_td if estimated_departure else None
    adjusted_arrival = estimated_arrival + extra_wait_td if estimated_arrival else None

    return OrderEstimateResponse(
        delivery_fee=round(delivery_fee, 2),
        service_fee=round(service_fee, 2),
        total_fee=round(delivery_fee + service_fee, 2),
        fee_details=fee_details,
        distance_km=round(dist_km, 2),
        estimated_departure=adjusted_departure,
        estimated_arrival=adjusted_arrival,
        handover_deadline=handover_deadline,
        resource_readiness=readiness,
        customer_notice=readiness["customer_notice"],
    )


# ══════════════════════════════════════════════════════════════════════
# CREATE ORDER
# ══════════════════════════════════════════════════════════════════════
@router.post("/orders", response_model=OrderCreateResponse)
async def create_order(
    body: CreateOrderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """BE - POST /customer/orders: Xử lý tạo đơn hàng mới."""
    if body.payload_kg <= 0 or body.payload_kg > 10.0:
        raise HTTPException(status_code=400, detail="Package weight must be between 0.1 kg and 10.0 kg (Size XL limit).")

    from app.shared.id_generator import generate_sequential_id
    from app.core.timezone import get_vn_naive_datetime
    order_id = await generate_sequential_id(db, Order, "ORD")
    now = get_vn_naive_datetime()

    hub_map = {h.id: h for h in await get_all_hubs_cached(db)}
    origin_hub = hub_map.get(body.origin_hub_id)
    dest_hub = hub_map.get(body.destination_hub_id)
    if not origin_hub or not dest_hub:
        raise HTTPException(status_code=404, detail="Hub not found")

    lat1, lon1 = origin_hub.latitude or 0.0, origin_hub.longitude or 0.0
    lat2, lon2 = dest_hub.latitude or 0.0, dest_hub.longitude or 0.0
    dist_km = haversine_distance(lat1, lon1, lat2, lon2)

    core_fee, service_fee, total_fee, fee_details = calculate_delivery_cost(
        dist_km=dist_km,
        payload_kg=body.payload_kg,
        package_type=body.package_type,
        delivery_mode=body.delivery_mode,
        has_insurance=getattr(body, "has_insurance", False)
    )

    # Tính thời gian bay từ AI model (vận tốc chuẩn 10 m/s = 36 km/h)
    flight_duration_sec = dist_km * 1000 / 10.0
    flight_mins = max(math.ceil(flight_duration_sec / 60.0), 10)

    # Đánh giá tài nguyên Hub và bù thời gian chờ thực tế vào khung giờ giao hàng
    readiness = await evaluate_hub_resource_readiness(
        db=db,
        origin_hub_id=body.origin_hub_id,
        destination_hub_id=body.destination_hub_id,
        payload_kg=body.payload_kg,
    )
    extra_wait_td = timedelta(minutes=readiness["total_extra_wait_minutes"])

    # Tính toán thời gian cam kết theo delivery mode (có bù độ trễ tài nguyên nếu Hub đông đúc / sạc pin)
    req_at_naive = body.requested_delivery_at.replace(tzinfo=None) if body.requested_delivery_at else None

    if body.delivery_mode == DeliveryMode.EXPRESS:
        window_start = now + timedelta(hours=3) + extra_wait_td
        window_end   = window_start + timedelta(minutes=flight_mins + 30)
        order_aging_deadline = None
    elif body.delivery_mode == DeliveryMode.NORMAL:
        NORMAL_SLA_HOURS = 4
        window_start = now + extra_wait_td
        window_end   = now + timedelta(hours=NORMAL_SLA_HOURS) + extra_wait_td
        order_aging_deadline = window_end - timedelta(hours=1)
    else:  # SCHEDULED
        if not req_at_naive:
            raise HTTPException(status_code=400, detail="requested_delivery_at is required for SCHEDULED mode.")
        window_start = req_at_naive + extra_wait_td
        window_end   = req_at_naive + timedelta(minutes=flight_mins + 15) + extra_wait_td
        order_aging_deadline = None

    new_order = Order(
        id=order_id,
        customer_id=current_user.id,
        package_type=body.package_type,
        package_label=body.package_label,
        payload_kg=body.payload_kg,
        origin_hub_id=body.origin_hub_id,
        destination_hub_id=body.destination_hub_id,
        delivery_mode=body.delivery_mode,
        requested_delivery_at=req_at_naive,
        estimated_window_start=window_start,
        estimated_window_end=window_end,
        aging_deadline=order_aging_deadline,
        is_aging_priority=False,
        status=OrderStatus.PENDING,
        delivery_fee=total_fee,
        sender_phone=body.sender_phone,
        sender_email=body.sender_email,
        receiver_name=body.receiver_name,
        receiver_phone=body.receiver_phone,
        receiver_email=body.receiver_email,
        created_at=now,
        updated_at=now
    )
    db.add(new_order)

    # ── Notify Customer & tất cả Operators trong cùng 1 transaction (bỏ commit thứ 2 & refresh thừa) ──
    try:
        from app.modules.auth.models import User as _User, ROLE_NAME_TO_ID as _ROLE_MAP
        from app.modules.system.models import Notification as _Notif, NotificationType as _NT
        from app.shared.roles import UserRole as _Role
        from app.core.timezone import get_vn_datetime as _get_vn_dt

        _mode_str = body.delivery_mode.value if hasattr(body.delivery_mode, "value") else str(body.delivery_mode)
        _vn_now = _get_vn_dt()
        _time_str = _vn_now.strftime("%H:%M:%S %d/%m/%Y")

        db.add(_Notif(
            user_id=str(current_user.id),
            title=f"Order #{new_order.id} Created Successfully",
            message=(
                f"Order #{new_order.id} ({body.package_label or 'Package'}, {body.payload_kg} kg) "
                f"was created at {_time_str}. Route: {origin_hub.name} -> {dest_hub.name}. "
                f"Please proceed with payment to continue."
            ),
            notifications_type=_NT.MISSION_STATUS,
            reference_id=f"ORDER_CREATED_{new_order.id}",
            created_at=_vn_now,
        ))

        _op_role_id = _ROLE_MAP.get(_Role.OPERATOR)
        if _op_role_id:
            _ops = (await db.execute(select(_User.id).where(_User.role_id == _op_role_id))).scalars().all()
            for _op_id in _ops:
                db.add(_Notif(
                    user_id=_op_id,
                    title=f"New Order #{new_order.id}",
                    message=(
                        f"Customer placed order #{new_order.id} at {_time_str} "
                        f"({_mode_str}, {body.payload_kg} kg). "
                        f"ETA: {window_end.strftime('%H:%M %d/%m') if window_end else 'N/A'}. "
                        f"Order is awaiting customer payment and hub drop-off."
                    ),
                    notifications_type=_NT.MISSION_STATUS,
                    reference_id=f"NEW_ORDER_{new_order.id}",
                    created_at=_vn_now,
                ))
    except Exception as _e:
        print(f"[NOTIFY] Lỗi gửi thông báo đơn mới: {_e}")

    await db.commit()

    # ── Gửi email xác nhận đơn hàng cho customer (non-blocking) ──────────────
    _sender_email_addr = (new_order.sender_email or "").strip()
    if _sender_email_addr:
        import asyncio as _asyncio
        from app.core.email import send_order_confirmation_email as _send_order_confirm
        from app.core.config import settings as _settings

        _eta_str = None
        if new_order.estimated_window_end:
            _eta_str = new_order.estimated_window_end.strftime("%H:%M %d/%m/%Y")

        _asyncio.create_task(
            _send_order_confirm(
                to_email=_sender_email_addr,
                order_id=new_order.id,
                package_label=new_order.package_label,
                payload_kg=new_order.payload_kg,
                delivery_mode=new_order.delivery_mode.value if new_order.delivery_mode else None,
                origin_hub_name=origin_hub.name if origin_hub else None,
                destination_hub_name=dest_hub.name if dest_hub else None,
                delivery_fee=new_order.delivery_fee,
                estimated_window_end=_eta_str,
                frontend_url=_settings.FRONTEND_URL,
            )
        )

    return OrderCreateResponse(
        id=new_order.id,
        status=new_order.status,
        delivery_mode=new_order.delivery_mode,
        origin_hub_id=new_order.origin_hub_id,
        destination_hub_id=new_order.destination_hub_id,
        requested_delivery_at=new_order.requested_delivery_at,
        estimated_window_start=new_order.estimated_window_start,
        estimated_window_end=new_order.estimated_window_end,
        created_at=new_order.created_at,
        resource_readiness=readiness,
        customer_notice=readiness["customer_notice"],
    )


# ══════════════════════════════════════════════════════════════════════
# REFERENCE & HELP (Canonical data endpoints)
# ══════════════════════════════════════════════════════════════════════
@router.get("/orders/reference/package-rules", summary="Quy định danh mục hàng hóa và kích thước kiện hàng")
async def get_customer_package_rules():
    from app.modules.chatbot.customer.reference_service import CustomerReferenceService
    return CustomerReferenceService.get_package_rules()


@router.get("/orders/reference/pricing-rules", summary="Quy định bảng giá và tỷ lệ phụ phí")
async def get_customer_pricing_rules():
    from app.modules.chatbot.customer.reference_service import CustomerReferenceService
    return CustomerReferenceService.get_pricing_rules()


@router.get("/orders/reference/delivery-modes", summary="Danh sách chế độ giao hàng Scheduled & Express")
async def get_customer_delivery_modes():
    from app.modules.chatbot.customer.reference_service import CustomerReferenceService
    return CustomerReferenceService.get_delivery_modes()


@router.get("/orders/help/topics", summary="Danh sách chủ đề Help & Policies chính thức cho Khách hàng")
async def get_customer_help_topics():
    from app.modules.chatbot.customer.reference_service import CustomerHelpService
    return CustomerHelpService.get_all_help_topics()


@router.get("/orders/help/topics/{topic}", summary="Chi tiết một chủ đề Help / Policy theo mã")
async def get_customer_help_topic_detail(topic: str):
    from app.modules.chatbot.customer.reference_service import CustomerHelpService
    return CustomerHelpService.get_help_policy(topic)


# ══════════════════════════════════════════════════════════════════════
# MY ORDERS (list + detail via chatbot tools)
# ══════════════════════════════════════════════════════════════════════
@router.get("/orders", summary="Lấy danh sách đơn hàng của khách hàng hiện tại")
async def get_my_customer_orders(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """BE - GET /api/v1/customer/orders: Luôn filter theo `current_user.id` từ JWT Token."""
    from app.modules.chatbot.customer.tools import tool_get_my_orders
    res = await tool_get_my_orders(
        db=db, limit=limit, current_user_id=str(current_user.id), include_cancelled=True
    )
    res.pop("_actions", None)
    return res


@router.get("/orders/{order_id}", summary="Lấy chi tiết đơn hàng của khách hàng hiện tại")
async def get_my_customer_order_detail(
    order_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """BE - GET /api/v1/customer/orders/{order_id}: Kiểm tra quyền sở hữu `order.customer_id == current_user.id`."""
    from app.modules.chatbot.customer.tools import tool_get_order_detail
    res = await tool_get_order_detail(
        db=db, order_id=order_id, current_user_id=str(current_user.id), include_cancelled=True
    )
    if not res.get("success"):
        code = res.get("code", "ORDER_NOT_FOUND")
        status_code = 403 if code == "ORDER_ACCESS_DENIED" else 404
        raise HTTPException(status_code=status_code, detail=res)
    res.pop("_actions", None)
    return res


# ══════════════════════════════════════════════════════════════════════
# CANCEL ORDER WITH REASON
# ══════════════════════════════════════════════════════════════════════

_CANCELLATION_REASONS = [
    "changed_my_mind",
    "wrong_information",
    "price_too_high",
    "found_alternative",
    "delivery_too_slow",
    "other",
]


class CancelOrderRequest(BaseModel):
    reason: str = PydanticField(
        ...,
        description=f"Lý do hủy đơn. Các giá trị hợp lệ: {_CANCELLATION_REASONS}"
    )
    note: Optional[str] = PydanticField(default=None, max_length=500, description="Ghi chú thêm (tùy chọn)")


@router.patch(
    "/orders/{order_id}/cancel",
    summary="Hủy đơn hàng kèm lý do",
)
async def cancel_order_with_reason(
    order_id: str,
    body: CancelOrderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Customer API] Hủy đơn hàng kèm lý do.**

    Chỉ được hủy khi:
    - Order đang ở trạng thái `PENDING`.
    - Chưa thanh toán (UNPAID/PENDING payment).
    - Lý do phải thuộc danh sách: `changed_my_mind`, `wrong_information`, `price_too_high`,
      `found_alternative`, `delivery_too_slow`, `other`.
    """
    if body.reason not in _CANCELLATION_REASONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid reason. Must be one of: {_CANCELLATION_REASONS}"
        )

    order = await db.get(Order, order_id)
    if not order or order.status == OrderStatus.CANCELLED:
        raise HTTPException(status_code=404, detail="Order not found")
    if str(order.customer_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your order")
    if order.payment_status == PaymentStatus.PAID:
        raise HTTPException(
            status_code=400,
            detail="Paid orders cannot be cancelled directly. Contact support for a refund."
        )
    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel order in '{order.status.value}' status. Only PENDING orders can be cancelled."
        )

    # Đã lập mission (bất kỳ trạng thái nào chưa hủy) thì không cho customer hủy nữa
    existing_mission = (await db.execute(
        select(Mission.id).where(
            Mission.order_id == order_id,
            Mission.status != MissionStatus.CANCELLED,
        ).limit(1)
    )).scalars().first()
    if existing_mission:
        raise HTTPException(
            status_code=400,
            detail="A flight mission has already been created for this order, so it can no longer be cancelled by the customer. Contact an operator."
        )

    from app.core.timezone import get_vn_naive_datetime
    now = get_vn_naive_datetime()

    full_reason = body.reason
    if body.note:
        full_reason = f"{body.reason}: {body.note.strip()}"

    order.status = OrderStatus.CANCELLED
    order.cancelled_at = now
    order.cancellation_reason = full_reason
    order.updated_at = now

    # Notify admins and operators (same transaction, never breaks the cancel)
    try:
        from app.modules.system.models import Notification as _Notif, NotificationType as _NT
        from app.shared.workers import get_operator_and_admin_ids
        async with db.begin_nested():
            for _uid in await get_operator_and_admin_ids(db):
                db.add(_Notif(
                    user_id=str(_uid),
                    title=f"Order #{order_id} cancelled by customer",
                    message=f"Customer cancelled order #{order_id}. Reason: {full_reason}.",
                    notifications_type=_NT.MISSION_STATUS,
                    reference_id=f"ORDER_CANCELLED_BY_CUSTOMER_{order_id}",
                    created_at=now,
                ))
    except Exception as _e:
        print(f"[NOTIFY] Failed to send order cancellation notification: {_e}")

    await db.commit()

    return {
        "message": "Order cancelled successfully.",
        "order_id": order_id,
        "reason": full_reason,
        "cancelled_at": now.isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════
# DELETE ORDER (chỉ khi còn PENDING và chưa có mission)
# ══════════════════════════════════════════════════════════════════════
@router.delete("/orders/{order_id}", status_code=204, summary="Xóa đơn hàng (chỉ khi chưa xử lý)")
async def delete_order(
    order_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Customer xóa đơn hàng của mình.
    - Chỉ được xóa khi order đang ở trạng thái **PENDING** và chưa có Mission nào.
    - Nếu đã có Mission hoặc đã thanh toán → không cho xóa.
    """
    order = await db.get(Order, order_id)
    if not order or order.status == OrderStatus.CANCELLED:
        raise HTTPException(status_code=404, detail="Order not found")

    if str(order.customer_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="You do not have permission to delete this order")

    if order.payment_status == PaymentStatus.PAID:
        raise HTTPException(
            status_code=400,
            detail="Paid orders cannot be deleted. Contact an operator to cancel."
        )

    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete order in '{order.status.value}' status. Only PENDING orders can be deleted."
        )

    # Kiểm tra đã có Mission chưa
    mission_result = await db.execute(
        select(Mission).where(Mission.order_id == order_id).limit(1)
    )
    if mission_result.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="A mission has already been created for this order, so it cannot be deleted. Contact an operator to cancel."
        )

    await db.delete(order)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Cannot delete order due to linked records. Contact an operator to cancel."
        )


# ══════════════════════════════════════════════════════════════════════
# TRACKING (drone location + route + ETA + delivery status)
# ══════════════════════════════════════════════════════════════════════
@router.get("/orders/{order_id}/tracking", response_model=TrackingResponse)
async def track_delivery(
    order_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Customer API] Theo dõi đơn giao hàng (Tracking).**

    Trả về: trạng thái giao, ETA (= `order.estimated_window_end`), vị trí drone hiện tại,
    điểm đầu/cuối tuyến, đường đã bay (polyline) và số checkpoint đã qua.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.customer_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your order")

    mission = await _latest_mission_for_order(db, order_id)
    code, label = compute_delivery_status(order, mission)

    current_mission_status = None
    if mission:
        if mission.status == MissionStatus.FLYING:
            current_mission_status = MissionStatus.FLYING.value
        else:
            current_mission_status = mission.status.value if hasattr(mission.status, "value") else str(mission.status)

    from app.core.timezone import get_vn_naive_datetime
    now_vn = get_vn_naive_datetime()
    dep_at = (mission.departed_at or mission.start_time) if mission else None
    arr_at = (mission.arrived_at or mission.end_time) if mission else None
    # Chuẩn hóa nếu arrived_at từng bị simulator lưu bằng UTC trong khi departed_at là giờ VN (+7)
    if dep_at and arr_at:
        _d0 = dep_at.replace(tzinfo=None) if dep_at.tzinfo else dep_at
        _a0 = arr_at.replace(tzinfo=None) if arr_at.tzinfo else arr_at
        if (_a0 - _d0).total_seconds() < -3600:
            arr_at = arr_at + timedelta(hours=7)
    conf_at = mission.arrival_confirmed_at if mission else None
    deliv_at = order.destination_received_at or conf_at

    elapsed_sec = None
    if dep_at:
        end_ref = arr_at or now_vn
        d_start = dep_at.replace(tzinfo=None) if dep_at.tzinfo else dep_at
        d_end = end_ref.replace(tzinfo=None) if end_ref.tzinfo else end_ref
        elapsed_sec = max(0, int((d_end - d_start).total_seconds()))

    remaining_sec = None
    if order.estimated_window_end and not deliv_at and not arr_at:
        eta_naive = order.estimated_window_end.replace(tzinfo=None) if order.estimated_window_end.tzinfo else order.estimated_window_end
        remaining_sec = max(0, int((eta_naive - now_vn).total_seconds()))

    timeline_steps = [
        TrackingTimelineStepSchema(
            step_key="ORDER_CREATED",
            label="Order Created",
            completed=order.created_at is not None,
            timestamp=order.created_at,
            description="Order has been initialized in the system",
        ),
        TrackingTimelineStepSchema(
            step_key="PAYMENT_CONFIRMED",
            label="Payment Confirmed",
            completed=(order.payment_status == PaymentStatus.PAID or order.paid_at is not None),
            timestamp=order.paid_at,
            description="Shipping fee payment has been confirmed",
        ),
        TrackingTimelineStepSchema(
            step_key="PACKAGE_RECEIVED",
            label="Package Received at Origin Hub",
            completed=(
                order.origin_received_at is not None
                or order.status in (OrderStatus.AT_HUB, OrderStatus.IN_DELIVERY, OrderStatus.DELIVERED_TO_HUB)
            ),
            timestamp=order.origin_received_at,
            description="Origin Hub technician has inspected and received the package",
        ),
        TrackingTimelineStepSchema(
            step_key="IN_FLIGHT",
            label="Drone Departed for Delivery",
            completed=(dep_at is not None),
            timestamp=dep_at,
            description="Drone is currently en route delivering the package",
        ),
        TrackingTimelineStepSchema(
            step_key="ARRIVED_AT_HUB",
            label="Drone Landed at Destination Hub",
            completed=(arr_at is not None),
            timestamp=arr_at,
            description="Drone has landed at the destination hub",
        ),
        TrackingTimelineStepSchema(
            step_key="DELIVERED",
            label="Delivered at Hub",
            completed=(deliv_at is not None or conf_at is not None or order.status == OrderStatus.DELIVERED_TO_HUB),
            timestamp=deliv_at,
            description="Package is ready for recipient pickup at the destination hub",
        ),
    ]

    resp = TrackingResponse(
        order_id=order.id,
        delivery_status=code,
        delivery_status_label=label,
        eta=order.estimated_window_end,
        estimated_window_start=order.estimated_window_start,
        order_created_at=order.created_at,
        paid_at=order.paid_at,
        package_received_at=order.origin_received_at,
        departed_at=dep_at,
        arrived_at=arr_at,
        arrival_confirmed_at=conf_at,
        delivered_at=deliv_at,
        elapsed_seconds=elapsed_sec,
        remaining_seconds=remaining_sec,
        timeline=timeline_steps,
        mission_id=mission.id if mission else None,
        mission_status=current_mission_status,
        drone_id=mission.drone_id if mission else None,
        order_status=order.status.value if hasattr(order.status, "value") else str(order.status or ""),
        cancelled_at=order.cancelled_at,
        cancellation_reason=order.cancellation_reason,
    )

    # Thu thập toàn bộ Hub ID (từ order và mission legs)
    all_hub_ids = set()
    if order.origin_hub_id:
        all_hub_ids.add(order.origin_hub_id)
    if order.destination_hub_id:
        all_hub_ids.add(order.destination_hub_id)

    mission_legs = []
    if mission:
        legs_res = await db.execute(
            select(MissionLeg)
            .where(MissionLeg.mission_id == mission.id)
            .order_by(MissionLeg.sequence_no.asc())
        )
        mission_legs = legs_res.scalars().all()
        for leg in mission_legs:
            if leg.from_hub_id:
                all_hub_ids.add(leg.from_hub_id)
            if leg.to_hub_id:
                all_hub_ids.add(leg.to_hub_id)

    hubs = {}
    if all_hub_ids:
        hub_map_all = {h.id: h for h in await get_all_hubs_cached(db)}
        hubs = {hid: hub_map_all[hid] for hid in all_hub_ids if hid in hub_map_all}
        o_hub = hubs.get(order.origin_hub_id)
        d_hub = hubs.get(order.destination_hub_id)
        if o_hub and o_hub.latitude is not None and o_hub.longitude is not None:
            resp.origin = RoutePointSchema(latitude=o_hub.latitude, longitude=o_hub.longitude, name=o_hub.name)
        if d_hub and d_hub.latitude is not None and d_hub.longitude is not None:
            resp.destination = RoutePointSchema(latitude=d_hub.latitude, longitude=d_hub.longitude, name=d_hub.name)

    if mission:
        # 1 & 2 & 4. Gộp 3 lần quét telemetry_logs (latest, path_logs, trail) thành 1 câu SQL nhẹ (chỉ 6 cột)
        path_rows = (await db.execute(
            select(
                TelemetryLog.latitude,
                TelemetryLog.longitude,
                TelemetryLog.altitude,
                TelemetryLog.speed,
                TelemetryLog.battery_voltage,
                TelemetryLog.timestamp,
            )
            .where(TelemetryLog.mission_id == mission.id)
            .order_by(TelemetryLog.timestamp.asc())
            .limit(MAX_PATH_POINTS)
        )).all()

        if len(path_rows) >= MAX_PATH_POINTS:
            trail_rows = list(reversed((await db.execute(
                select(
                    TelemetryLog.latitude,
                    TelemetryLog.longitude,
                    TelemetryLog.altitude,
                    TelemetryLog.speed,
                    TelemetryLog.battery_voltage,
                    TelemetryLog.timestamp,
                )
                .where(TelemetryLog.mission_id == mission.id)
                .order_by(TelemetryLog.timestamp.desc())
                .limit(50)
            )).all()))
        else:
            trail_rows = path_rows[-50:]

        latest = trail_rows[-1] if trail_rows else None

        bat_pct = None
        if mission.drone_id:
            drone_obj = await db.get(Drone, mission.drone_id)
            if drone_obj and drone_obj.battery_level_pct is not None:
                bat_pct = drone_obj.battery_level_pct
        if bat_pct is None and mission.battery_id:
            bat_obj = await db.get(Battery, mission.battery_id)
            if bat_obj and bat_obj.charge_level_pct is not None:
                bat_pct = bat_obj.charge_level_pct

        if latest:
            resp.drone_location = DroneLocationSchema(
                latitude=latest.latitude, longitude=latest.longitude,
                altitude=latest.altitude, speed=latest.speed,
                battery_voltage=latest.battery_voltage,
                battery_pct=bat_pct,
                last_updated=latest.timestamp,
            )

        resp.travelled_path = [RoutePointSchema(latitude=r.latitude, longitude=r.longitude) for r in path_rows]
        resp.checkpoints_passed = sum(
            1 for cp in (mission.checkpoints_json or [])
            if isinstance(cp, dict) and cp.get("drone_latitude") is not None and cp.get("passed_at") is not None
        )

        # 3. Legs & Current Leg
        legs_list = []
        current_leg_item = None
        for leg in mission_legs:
            f_hub = hubs.get(leg.from_hub_id)
            t_hub = hubs.get(leg.to_hub_id)
            leg_status_val = leg.status.value if hasattr(leg.status, "value") else str(leg.status or "")
            if str(leg_status_val).upper() in ("IN_PROGRESS", "IN PROGRESS", "IN-PROGRESS"):
                leg_status_val = "FLYING"
            leg_started = leg.started_at
            leg_completed = leg.completed_at
            if leg_started and leg_completed:
                _ls = leg_started.replace(tzinfo=None) if leg_started.tzinfo else leg_started
                _lc = leg_completed.replace(tzinfo=None) if leg_completed.tzinfo else leg_completed
                if (_lc - _ls).total_seconds() < -3600:
                    leg_completed = leg_completed + timedelta(hours=7)
            leg_schema = TrackingLegSchema(
                id=leg.id,
                sequence_no=leg.sequence_no or 1,
                status=leg_status_val,
                from_hub=TrackingHubSchema(
                    id=f_hub.id, name=f_hub.name,
                    latitude=f_hub.latitude, longitude=f_hub.longitude
                ) if f_hub else None,
                to_hub=TrackingHubSchema(
                    id=t_hub.id, name=t_hub.name,
                    latitude=t_hub.latitude, longitude=t_hub.longitude
                ) if t_hub else None,
                battery_id=leg.battery_id,
                started_at=leg_started,
                completed_at=leg_completed,
            )
            legs_list.append(leg_schema)
            if leg.status == MissionLegStatus.IN_PROGRESS or str(leg.status).upper() in ("IN_PROGRESS", "FLYING"):
                current_leg_item = leg_schema

        if not current_leg_item and legs_list:
            pending = [l for l in legs_list if str(l.status).upper() == "PENDING"]
            if pending:
                current_leg_item = pending[0]
            else:
                current_leg_item = legs_list[-1]

        if current_leg_item:
            resp.current_leg = TrackingCurrentLegSchema(
                id=current_leg_item.id,
                sequence_no=current_leg_item.sequence_no,
                status=current_leg_item.status,
                from_hub=current_leg_item.from_hub,
                to_hub=current_leg_item.to_hub,
                assigned_battery_id=current_leg_item.battery_id,
                started_at=current_leg_item.started_at,
                completed_at=current_leg_item.completed_at,
            )
        resp.legs = legs_list

        # 4. Trail: 50 điểm telemetry gần nhất (đã tách sẵn từ path_rows/trail_rows)
        resp.trail = [
            TrailPointSchema(
                latitude=t.latitude,
                longitude=t.longitude,
                altitude=t.altitude,
                speed=t.speed,
                timestamp=t.timestamp,
            )
            for t in trail_rows
        ]

    return resp


# ══════════════════════════════════════════════════════════════════════
# MISSION REPORT (download)
# ══════════════════════════════════════════════════════════════════════
@router.get("/orders/{order_id}/report", response_model=MissionReportResponse)
async def get_mission_report(
    order_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Customer API] Báo cáo chuyến bay (Mission Report).**

    Tổng hợp thông tin đơn + chuyến bay để Customer tải về: tuyến đường, mốc thời gian,
    dự đoán vs thực tế (năng lượng/thời lượng), thông tin drone và trạng thái cuối.
    """
    order = await db.get(Order, order_id)
    if not order or order.status == OrderStatus.CANCELLED:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.customer_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your order")

    mission = await _latest_mission_for_order(db, order_id)
    _, label = compute_delivery_status(order, mission)

    hub_ids = [i for i in (order.origin_hub_id, order.destination_hub_id) if i is not None]
    hubs = {}
    if hub_ids:
        hub_map_all = {h.id: h for h in await get_all_hubs_cached(db)}
        hubs = {hid: hub_map_all[hid] for hid in hub_ids if hid in hub_map_all}

    def hub_schema(hub_id):
        h = hubs.get(hub_id)
        if not h:
            return None
        return ReportHubSchema(id=h.id, name=h.name, latitude=h.latitude, longitude=h.longitude)

    route = ReportRouteSchema(
        origin=hub_schema(order.origin_hub_id),
        destination=hub_schema(order.destination_hub_id),
        distance_m=mission.distance_m if mission else None,
    )

    timeline = ReportTimelineSchema()
    prediction = ReportPredictionSchema()
    drone_schema = None
    actuals = ReportActualsSchema()

    if mission:
        from app.modules.ai_predictions.models import AIPrediction
        ai_pred = (await db.execute(
            select(AIPrediction)
            .where(AIPrediction.mission_id == mission.id)
            .order_by(AIPrediction.id.desc())
            .limit(1)
        )).scalar_one_or_none()

        timeline = ReportTimelineSchema(
            created_at=order.created_at,
            planned_start_at=mission.scheduled_time,
            departed_at=mission.departed_at or mission.start_time,
            arrived_at=mission.arrived_at or mission.end_time,
            arrival_confirmed_at=mission.arrival_confirmed_at,
        )
        pred_energy = float(ai_pred.estimated_energy_wh) if (ai_pred and ai_pred.estimated_energy_wh is not None) else None
        pred_dur = int(ai_pred.estimated_duration_m) if (ai_pred and ai_pred.estimated_duration_m is not None) else None
        risk_lvl = ai_pred.risk_level if ai_pred else None
        prediction = ReportPredictionSchema(
            predicted_duration_min=pred_dur,
            estimated_energy_wh=pred_energy,
            battery_consumption_pct=None,
            predicted_remaining_battery_pct=None,
            confidence_pct=90 if risk_lvl and str(risk_lvl).upper() == "LOW" else (85 if risk_lvl and str(risk_lvl).upper() == "MEDIUM" else (75 if risk_lvl else None)),
            risk_level=risk_lvl,
        )
        if mission.drone_id:
            drone = await db.get(Drone, mission.drone_id)
            if drone:
                drone_schema = ReportDroneSchema(id=drone.id, name=drone.name, model=drone.model)

        tel_agg = (await db.execute(
            select(func.max(TelemetryLog.energy_consumed_wh), func.count(TelemetryLog.id))
            .where(TelemetryLog.mission_id == mission.id)
        )).one()
        actual_energy = tel_agg[0]
        tel_points = int(tel_agg[1] or 0)
        start_ts = mission.departed_at or mission.start_time
        end_ts = mission.arrival_confirmed_at or mission.end_time or mission.arrived_at
        actual_duration = None
        if start_ts and end_ts and end_ts >= start_ts:
            actual_duration = round((end_ts - start_ts).total_seconds() / 60.0, 1)
        actuals = ReportActualsSchema(
            actual_duration_min=actual_duration,
            actual_energy_wh=actual_energy,
            telemetry_points=tel_points,
        )

    return MissionReportResponse(
        order_id=order.id,
        mission_id=mission.id if mission else None,
        mission_code=mission.id if mission else None,
        package_label=order.package_label,
        package_type=order.package_type,
        payload_kg=order.payload_kg,
        delivery_mode=order.delivery_mode,
        status=order.status,
        delivery_status_label=label,
        route=route,
        timeline=timeline,
        prediction=prediction,
        drone=drone_schema,
        actuals=actuals,
        generated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


# ══════════════════════════════════════════════════════════════════════
# DELIVERY RATING
# ══════════════════════════════════════════════════════════════════════

from app.modules.missions.models import DeliveryRating


class RatingRequest(BaseModel):
    rating: int = PydanticField(..., ge=1, le=5, description="Rating from 1 to 5 stars")
    comment: Optional[str] = PydanticField(default=None, max_length=1000, description="Optional feedback comment")

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError("Rating must be between 1 and 5")
        return v


class RatingResponse(BaseModel):
    id: int
    order_id: str
    rating: int
    comment: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


@router.post(
    "/orders/{order_id}/rating",
    response_model=RatingResponse,
    status_code=201,
    summary="Đánh giá trải nghiệm giao hàng (1-5 sao)",
)
async def submit_delivery_rating(
    order_id: str,
    body: RatingRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Customer API] Gửi đánh giá trải nghiệm sau khi nhận hàng.**

    - Chỉ được đánh giá khi order ở trạng thái `DELIVERED_TO_HUB`.
    - Mỗi đơn chỉ được đánh giá **1 lần**.
    - `rating`: 1-5 sao. `comment`: tùy chọn, tối đa 1000 ký tự.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.customer_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your order")
    if order.status not in (OrderStatus.DELIVERED_TO_HUB, OrderStatus.DELIVERED_TO_CUSTOMER):
        raise HTTPException(
            status_code=400,
            detail=f"Can only rate delivered orders (DELIVERED_TO_HUB or DELIVERED_TO_CUSTOMER). Current: {order.status.value if order.status else 'unknown'}"
        )

    # Check already rated
    existing = (await db.execute(
        select(DeliveryRating).where(DeliveryRating.order_id == order_id)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="You have already rated this order")

    from app.core.timezone import get_vn_naive_datetime
    now = get_vn_naive_datetime()

    rating_obj = DeliveryRating(
        order_id=order_id,
        customer_id=str(current_user.id),
        rating=body.rating,
        comment=body.comment,
        created_at=now,
    )
    db.add(rating_obj)
    await db.commit()
    await db.refresh(rating_obj)
    return rating_obj


@router.get(
    "/orders/{order_id}/rating",
    response_model=RatingResponse,
    summary="Xem đánh giá của đơn hàng",
)
async def get_delivery_rating(
    order_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """**[Customer API] Xem đánh giá đã gửi cho đơn hàng."""
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.customer_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your order")

    rating_obj = (await db.execute(
        select(DeliveryRating).where(DeliveryRating.order_id == order_id)
    )).scalar_one_or_none()
    if not rating_obj:
        raise HTTPException(status_code=404, detail="No rating found for this order")
    return rating_obj


# ══════════════════════════════════════════════════════════════════════
# LIVE POSITION (lightweight real-time GPS endpoint)
# ══════════════════════════════════════════════════════════════════════

class LivePositionResponse(BaseModel):
    order_id: str
    mission_id: Optional[str]
    mission_status: Optional[str]
    # GPS
    latitude: Optional[float]
    longitude: Optional[float]
    altitude_m: Optional[float]
    speed_mps: Optional[float]
    battery_pct: Optional[float]
    # Timing
    last_update: Optional[datetime]
    elapsed_sec: Optional[int]      # Giây đã bay
    eta_sec: Optional[int]          # Giây còn lại tới ETA
    # Human-readable
    is_flying: bool
    status_label: str


@router.get(
    "/orders/{order_id}/live-position",
    response_model=LivePositionResponse,
    summary="Vị trí GPS thời gian thực của drone đang giao hàng",
)
async def get_live_position(
    order_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Customer API] Vị trí GPS live của drone đang giao đơn hàng.**

    Endpoint nhẹ, chỉ trả về vị trí mới nhất từ telemetry. Gọi mỗi 3-5s từ FE để
    update marker trên bản đồ mà không cần load full tracking response.

    - Trả về `is_flying: false` nếu mission chưa cất cánh hoặc đã hoàn thành.
    - `eta_sec`: giây còn lại tới `estimated_window_end` (null nếu đã giao xong).
    - `last_update`: timestamp của telemetry log cuối cùng.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.customer_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your order")

    mission = await _latest_mission_for_order(db, order_id)

    flying_statuses = {MissionStatus.FLYING}
    is_flying = bool(mission and mission.status in flying_statuses)

    # Latest telemetry log
    lat = lng = alt = speed = bat_pct = None
    last_update = None
    if mission:
        tlog = (await db.execute(
            select(TelemetryLog)
            .where(TelemetryLog.mission_id == str(mission.id))
            .order_by(TelemetryLog.id.desc())
            .limit(1)
        )).scalar_one_or_none()
        if tlog:
            lat, lng, alt = tlog.latitude, tlog.longitude, tlog.altitude
            speed = tlog.speed
            bat_pct = tlog.battery_pct
            last_update = tlog.timestamp

    # ETA
    from app.core.timezone import get_vn_naive_datetime
    now_vn = get_vn_naive_datetime()
    eta_sec = None
    if order.estimated_window_end and is_flying:
        eta_naive = order.estimated_window_end.replace(tzinfo=None) if order.estimated_window_end.tzinfo else order.estimated_window_end
        eta_sec = max(0, int((eta_naive - now_vn).total_seconds()))

    # Elapsed
    elapsed_sec = None
    dep_at = (mission.departed_at or mission.start_time) if mission else None
    if dep_at and is_flying:
        d_start = dep_at.replace(tzinfo=None) if dep_at.tzinfo else dep_at
        elapsed_sec = max(0, int((now_vn - d_start).total_seconds()))

    # Status label
    code, label = compute_delivery_status(order, mission)

    return LivePositionResponse(
        order_id=order_id,
        mission_id=str(mission.id) if mission else None,
        mission_status=mission.status.value if mission and hasattr(mission.status, "value") else None,
        latitude=lat,
        longitude=lng,
        altitude_m=alt,
        speed_mps=speed,
        battery_pct=bat_pct,
        last_update=last_update,
        elapsed_sec=elapsed_sec,
        eta_sec=eta_sec,
        is_flying=is_flying,
        status_label=label,
    )
