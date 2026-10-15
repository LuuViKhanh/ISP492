from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from app.database.db import get_async_db
from app.shared.dependencies import get_current_user, CurrentUser
from app.modules.system.models import Hub
from app.modules.missions.models import (
    Order, OrderStatus, PaymentStatus, DeliveryMode,
    Mission, MissionStatus, TelemetryLog, MissionHubCheckpoint,
)
from app.modules.fleet.models import Drone
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
    MissionReportResponse,
    ReportHubSchema,
    ReportRouteSchema,
    ReportTimelineSchema,
    ReportPredictionSchema,
    ReportDroneSchema,
    ReportActualsSchema,
)
from app.modules.ai_predictions.service import predict_flight_energy, haversine_distance

router = APIRouter(prefix="/customer", tags=["Orders"])

MAX_PATH_POINTS = 2000


# ── Helper: suy ra trạng thái giao hàng thân thiện cho Customer ──────
def compute_delivery_status(order: Order, mission: Optional[Mission]) -> tuple[str, str]:
    """
    Trả về (code, nhãn tiếng Việt) cho trạng thái giao hàng.
    Được dùng bởi dashboard/customer và tracking endpoint.
    """
    if order.status == OrderStatus.CANCELLED:
        return "CANCELLED", "Đã hủy"
    if mission is None:
        return "PENDING", "Chờ xử lý"
    ms = mission.status
    if mission.arrival_confirmed_at is not None or ms in (
        MissionStatus.MISSION_COMPLETED, MissionStatus.COMPLETED
    ):
        return "DELIVERED", "Đã giao"
    if mission.arrived_at is not None:
        return "ARRIVED_AT_HUB", "Đã tới hub đích, chờ nhận"
    if ms in (MissionStatus.FLYING, MissionStatus.IN_PROGRESS, MissionStatus.ACTIVE_MISSION):
        return "IN_DELIVERY", "Đang giao"
    if ms in (MissionStatus.SCHEDULED, MissionStatus.APPROVED, MissionStatus.PENDING_APPROVAL):
        return "SCHEDULED", "Đã lên lịch"
    if ms in (MissionStatus.CANCELLED, MissionStatus.REJECTED, MissionStatus.FAILED):
        return "CANCELLED", "Đã hủy"
    return "PROCESSING", "Đang xử lý"


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
        select(Mission).where(Mission.order_id == order_id).order_by(Mission.id.desc())
    )
    return res.scalars().first()


# ══════════════════════════════════════════════════════════════════════
# HUB LIST
# ══════════════════════════════════════════════════════════════════════
@router.get("/hubs", response_model=list[HubResponse])
async def get_hubs(db: AsyncSession = Depends(get_async_db)):
    """BE - GET /customer/hubs: Lấy danh sách các điểm giao nhận của hệ thống để khách hàng chọn."""
    result = await db.execute(select(Hub).where(Hub.status == 'Active'))
    hubs = result.scalars().all()
    if not hubs:
        result = await db.execute(select(Hub))
        hubs = result.scalars().all()
    return hubs


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

    origin_hub = await db.get(Hub, body.origin_hub_id)
    dest_hub = await db.get(Hub, body.destination_hub_id)
    if not origin_hub or not dest_hub:
        raise HTTPException(status_code=404, detail="Hub not found")

    lat1, lon1 = origin_hub.latitude or 0.0, origin_hub.longitude or 0.0
    lat2, lon2 = dest_hub.latitude or 0.0, dest_hub.longitude or 0.0
    dist_km = haversine_distance(lat1, lon1, lat2, lon2)

    prediction = predict_flight_energy(dist_km, body.payload_kg)
    core_fee, service_fee, total_fee, fee_details = calculate_delivery_cost(
        dist_km=dist_km,
        payload_kg=body.payload_kg,
        package_type=getattr(body, "package_type", ""),
        delivery_mode=body.delivery_mode,
        has_insurance=getattr(body, "has_insurance", False)
    )
    delivery_fee = core_fee
    now = datetime.now(timezone.utc)

    if body.delivery_mode == DeliveryMode.EXPRESS:
        handover_deadline = now + timedelta(hours=3)
        estimated_departure = handover_deadline + timedelta(minutes=30)
        estimated_arrival = estimated_departure + timedelta(hours=1)
    else:
        if not body.requested_delivery_at:
            raise HTTPException(status_code=400, detail="requested_delivery_at is required for SCHEDULED mode")
        req_time = body.requested_delivery_at
        if req_time.tzinfo is None:
            req_time = req_time.replace(tzinfo=timezone.utc)
        handover_deadline = req_time - timedelta(hours=3)
        estimated_departure = req_time
        estimated_arrival = estimated_departure + timedelta(hours=1)

    return OrderEstimateResponse(
        delivery_fee=round(delivery_fee, 2),
        service_fee=round(service_fee, 2),
        total_fee=round(delivery_fee + service_fee, 2),
        fee_details=fee_details,
        distance_km=round(dist_km, 2),
        estimated_departure=estimated_departure,
        estimated_arrival=estimated_arrival,
        handover_deadline=handover_deadline
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
    order_id = await generate_sequential_id(db, Order, "ORD")
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    origin_hub = await db.get(Hub, body.origin_hub_id)
    dest_hub = await db.get(Hub, body.destination_hub_id)
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

    new_order = Order(
        id=order_id,
        customer_id=current_user.id,
        package_type=body.package_type,
        package_label=body.package_label,
        payload_kg=body.payload_kg,
        origin_hub_id=body.origin_hub_id,
        destination_hub_id=body.destination_hub_id,
        delivery_mode=body.delivery_mode,
        requested_delivery_at=body.requested_delivery_at.replace(tzinfo=None) if body.requested_delivery_at else None,
        status=OrderStatus.PENDING,
        delivery_fee=total_fee,
        sender_phone=body.sender_phone,
        receiver_phone=body.receiver_phone,
        created_at=now,
        updated_at=now
    )
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)
    return new_order


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
    res = await tool_get_my_orders(db=db, limit=limit, current_user_id=str(current_user.id))
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
    res = await tool_get_order_detail(db=db, order_id=order_id, current_user_id=str(current_user.id))
    if not res.get("success"):
        code = res.get("code", "ORDER_NOT_FOUND")
        status_code = 403 if code == "ORDER_ACCESS_DENIED" else 404
        raise HTTPException(status_code=status_code, detail=res)
    res.pop("_actions", None)
    return res


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
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if str(order.customer_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Không có quyền xóa đơn hàng này")

    if order.payment_status == PaymentStatus.PAID:
        raise HTTPException(
            status_code=400,
            detail="Đơn hàng đã thanh toán, không thể xóa. Liên hệ operator nếu muốn hủy."
        )

    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Không thể xóa đơn hàng đang ở trạng thái '{order.status.value}'. Chỉ xóa được khi PENDING."
        )

    # Kiểm tra đã có Mission chưa
    mission_result = await db.execute(
        select(Mission).where(Mission.order_id == order_id).limit(1)
    )
    if mission_result.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="Đơn hàng đã được tạo mission, không thể xóa. Liên hệ operator để hủy."
        )

    await db.delete(order)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Không thể xóa đơn hàng do còn dữ liệu liên quan. Liên hệ operator để hủy."
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

    resp = TrackingResponse(
        order_id=order.id,
        delivery_status=code,
        delivery_status_label=label,
        eta=order.estimated_window_end,
        estimated_window_start=order.estimated_window_start,
        mission_id=mission.id if mission else None,
        mission_status=mission.status.value if mission else None,
        drone_id=mission.drone_id if mission else None,
    )

    # Điểm đầu/cuối tuyến từ hub
    hub_ids = [i for i in (order.origin_hub_id, order.destination_hub_id) if i is not None]
    if hub_ids:
        hubs = {h.id: h for h in (await db.execute(select(Hub).where(Hub.id.in_(hub_ids)))).scalars().all()}
        o_hub = hubs.get(order.origin_hub_id)
        d_hub = hubs.get(order.destination_hub_id)
        if o_hub and o_hub.latitude is not None:
            resp.origin = RoutePointSchema(latitude=o_hub.latitude, longitude=o_hub.longitude, name=o_hub.name)
        if d_hub and d_hub.latitude is not None:
            resp.destination = RoutePointSchema(latitude=d_hub.latitude, longitude=d_hub.longitude, name=d_hub.name)

    if mission:
        latest = (await db.execute(
            select(TelemetryLog)
            .where(TelemetryLog.mission_id == mission.id)
            .order_by(TelemetryLog.timestamp.desc())
            .limit(1)
        )).scalar_one_or_none()
        if latest:
            resp.drone_location = DroneLocationSchema(
                latitude=latest.latitude, longitude=latest.longitude,
                altitude=latest.altitude, speed=latest.speed,
                battery_voltage=latest.battery_voltage, last_updated=latest.timestamp,
            )
        path_logs = (await db.execute(
            select(TelemetryLog.latitude, TelemetryLog.longitude)
            .where(TelemetryLog.mission_id == mission.id)
            .order_by(TelemetryLog.timestamp.asc())
            .limit(MAX_PATH_POINTS)
        )).all()
        resp.travelled_path = [RoutePointSchema(latitude=lat, longitude=lng) for lat, lng in path_logs]
        cp_count = (await db.execute(
            select(MissionHubCheckpoint).where(MissionHubCheckpoint.mission_id == mission.id)
        )).scalars().all()
        resp.checkpoints_passed = len(cp_count)

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
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.customer_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your order")

    mission = await _latest_mission_for_order(db, order_id)
    _, label = compute_delivery_status(order, mission)

    hub_ids = [i for i in (order.origin_hub_id, order.destination_hub_id) if i is not None]
    hubs = {}
    if hub_ids:
        hubs = {h.id: h for h in (await db.execute(select(Hub).where(Hub.id.in_(hub_ids)))).scalars().all()}

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
        timeline = ReportTimelineSchema(
            created_at=mission.created_at or order.created_at,
            planned_start_at=mission.planned_start_at or mission.scheduled_time,
            departed_at=mission.departed_at or mission.actual_started_at,
            arrived_at=mission.arrived_at or mission.estimated_arrival_at,
            arrival_confirmed_at=mission.arrival_confirmed_at or mission.actual_completed_at,
        )
        prediction = ReportPredictionSchema(
            predicted_duration_min=mission.predicted_duration_min,
            estimated_energy_wh=mission.estimated_energy_wh,
            battery_consumption_pct=mission.battery_consumption_pct,
            predicted_remaining_battery_pct=mission.predicted_remaining_battery_pct,
            confidence_pct=mission.confidence_pct,
            risk_level=mission.risk_level,
        )
        if mission.drone_id:
            drone = await db.get(Drone, mission.drone_id)
            if drone:
                drone_schema = ReportDroneSchema(id=drone.id, name=drone.name, model=drone.model)

        logs = (await db.execute(
            select(TelemetryLog.energy_consumed_wh, TelemetryLog.timestamp)
            .where(TelemetryLog.mission_id == mission.id)
            .order_by(TelemetryLog.timestamp.asc())
        )).all()
        energies = [e for e, _ in logs if e is not None]
        actual_energy = max(energies) if energies else None
        start_ts = mission.departed_at or mission.actual_started_at
        end_ts = mission.arrival_confirmed_at or mission.actual_completed_at or mission.arrived_at
        actual_duration = None
        if start_ts and end_ts and end_ts >= start_ts:
            actual_duration = round((end_ts - start_ts).total_seconds() / 60.0, 1)
        actuals = ReportActualsSchema(
            actual_duration_min=actual_duration,
            actual_energy_wh=actual_energy,
            telemetry_points=len(logs),
        )

    return MissionReportResponse(
        order_id=order.id,
        mission_id=mission.id if mission else None,
        mission_code=mission.mission_code if mission else None,
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
