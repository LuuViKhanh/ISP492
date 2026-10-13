from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
import uuid

from app.database.db import get_async_db
from app.shared.dependencies import get_current_user, CurrentUser
from app.modules.system.models import Hub
from app.modules.missions.models import Order, OrderStatus, DeliveryMode
from app.modules.orders.customer.schemas import (
    HubResponse,
    OrderEstimateRequest,
    OrderEstimateResponse,
    CreateOrderRequest,
    OrderCreateResponse
)
from app.modules.ai_predictions.service import predict_flight_energy, haversine_distance

router = APIRouter(prefix="/customer", tags=["Orders"])

@router.get("/hubs", response_model=list[HubResponse])
async def get_hubs(db: AsyncSession = Depends(get_async_db)):
    """
    BE - GET /customer/hubs: Lấy danh sách các điểm giao nhận của hệ thống để khách hàng chọn.
    """
    result = await db.execute(select(Hub).where(Hub.status == 'Active'))
    hubs = result.scalars().all()
    # Fallback in case no hubs are marked 'Active'
    if not hubs:
        result = await db.execute(select(Hub))
        hubs = result.scalars().all()
    return hubs

def calculate_delivery_cost(dist_km: float, payload_kg: float, package_type: str, delivery_mode: DeliveryMode, has_insurance: bool = False):
    # Determine drone size based on payload
    if payload_kg <= 1:
        base_fee = 20000
        rate_per_km = 4000
        rate_per_kg = 3000
    elif payload_kg <= 3:
        base_fee = 30000
        rate_per_km = 5000
        rate_per_kg = 4000
    elif payload_kg <= 5:
        base_fee = 45000
        rate_per_km = 6000
        rate_per_kg = 5000
    else: # XL
        base_fee = 65000
        rate_per_km = 8000
        rate_per_kg = 6000
        
    distance_fee = dist_km * rate_per_km
    payload_fee = payload_kg * rate_per_kg
    
    core_fee = base_fee + distance_fee + payload_fee
    
    # Surcharges
    is_fragile = (package_type and package_type.lower() == 'fragile')
    fragile_surcharge = core_fee * 0.15 if is_fragile else 0
    
    is_express = (delivery_mode == DeliveryMode.EXPRESS)
    express_surcharge = core_fee * 0.25 if is_express else 0
    
    # Insurance 10%
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

@router.post("/orders/estimate", response_model=OrderEstimateResponse)
async def estimate_order(
    body: OrderEstimateRequest,
    db: AsyncSession = Depends(get_async_db)
):
    """
    BE - POST /customer/orders/estimate: Tính toán và trả về báo giá dựa trên năng lượng dự đoán tiêu thụ.
    """
    if body.payload_kg <= 0 or body.payload_kg > 10.0:
        raise HTTPException(
            status_code=400,
            detail="Package weight must be between 0.1 kg and 10.0 kg (Size XL limit)."
        )

    origin_hub = await db.get(Hub, body.origin_hub_id)
    dest_hub = await db.get(Hub, body.destination_hub_id)
    if not origin_hub or not dest_hub:
        raise HTTPException(status_code=404, detail="Hub not found")
        
    # Calculate distance using haversine
    lat1, lon1 = origin_hub.latitude or 0.0, origin_hub.longitude or 0.0
    lat2, lon2 = dest_hub.latitude or 0.0, dest_hub.longitude or 0.0
    dist_km = haversine_distance(lat1, lon1, lat2, lon2)
    
    # Predict energy consumption
    prediction = predict_flight_energy(dist_km, body.payload_kg)
    energy_wh = prediction.get("energy_wh", 0.0)
    
    # Calculate fees using new logic
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
        estimated_departure=estimated_departure,
        estimated_arrival=estimated_arrival,
        handover_deadline=handover_deadline
    )

@router.post("/orders", response_model=OrderCreateResponse)
async def create_order(
    body: CreateOrderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    BE - POST /customer/orders: Xử lý tạo đơn hàng mới.
    """
    if body.payload_kg <= 0 or body.payload_kg > 10.0:
        raise HTTPException(
            status_code=400,
            detail="Package weight must be between 0.1 kg and 10.0 kg (Size XL limit)."
        )

    from app.shared.id_generator import generate_sequential_id
    from app.modules.missions.models import Order
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


# ==============================================================================
# CUSTOMER ORDERS GET & CANONICAL REFERENCE / HELP ENDPOINTS (FE/BE SYNC SPEC)
# ==============================================================================

@router.get(
    "/orders/reference/package-rules",
    summary="Quy định danh mục hàng hóa và kích thước kiện hàng (Canonical Package Rules)",
)
async def get_customer_package_rules():
    """
    BE - GET /api/v1/customer/orders/reference/package-rules: Nguồn chuẩn duy nhất cho FE Pricing/Create Delivery & Chatbot.
    """
    from app.modules.chatbot.customer.reference_service import CustomerReferenceService
    return CustomerReferenceService.get_package_rules()


@router.get(
    "/orders/reference/pricing-rules",
    summary="Quy định bảng giá và tỷ lệ phụ phí (Canonical Pricing Rules)",
)
async def get_customer_pricing_rules():
    """
    BE - GET /api/v1/customer/orders/reference/pricing-rules: Hiển thị bảng giá theo size (S/M/L/XL) và công thức tính phụ phí.
    """
    from app.modules.chatbot.customer.reference_service import CustomerReferenceService
    return CustomerReferenceService.get_pricing_rules()


@router.get(
    "/orders/reference/delivery-modes",
    summary="Danh sách chế độ giao hàng Scheduled & Express (Canonical Delivery Modes)",
)
async def get_customer_delivery_modes():
    """
    BE - GET /api/v1/customer/orders/reference/delivery-modes: Mô tả Scheduled (0%) và Express (+25% Core Fee).
    """
    from app.modules.chatbot.customer.reference_service import CustomerReferenceService
    return CustomerReferenceService.get_delivery_modes()


@router.get(
    "/orders/help/topics",
    summary="Danh sách chủ đề Help & Policies chính thức cho Khách hàng",
)
async def get_customer_help_topics():
    """
    BE - GET /api/v1/customer/orders/help/topics: Nguồn dữ liệu cho trang Help & Policies trên FE và Chatbot FAQ.
    """
    from app.modules.chatbot.customer.reference_service import CustomerHelpService
    return CustomerHelpService.get_all_help_topics()


@router.get(
    "/orders/help/topics/{topic}",
    summary="Chi tiết một chủ đề Help / Policy theo mã",
)
async def get_customer_help_topic_detail(topic: str):
    """
    BE - GET /api/v1/customer/orders/help/topics/{topic}: Trả về chi tiết policy hoặc mã `POLICY_NOT_DEFINED` nếu chưa công bố.
    """
    from app.modules.chatbot.customer.reference_service import CustomerHelpService
    return CustomerHelpService.get_help_policy(topic)


@router.get("/orders", summary="Lấy danh sách đơn hàng của khách hàng hiện tại")
async def get_my_customer_orders(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    BE - GET /api/v1/customer/orders: Luôn filter theo `current_user.id` từ JWT Token.
    """
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
    """
    BE - GET /api/v1/customer/orders/{order_id}: Kiểm tra quyền sở hữu `order.customer_id == current_user.id`.
    """
    from app.modules.chatbot.customer.tools import tool_get_order_detail
    res = await tool_get_order_detail(db=db, order_id=order_id, current_user_id=str(current_user.id))
    if not res.get("success"):
        code = res.get("code", "ORDER_NOT_FOUND")
        status_code = 403 if code == "ORDER_ACCESS_DENIED" else 404
        raise HTTPException(status_code=status_code, detail=res)
    res.pop("_actions", None)
    return res

