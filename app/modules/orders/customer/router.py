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

router = APIRouter(prefix="/customer", tags=["Customer - Orders"])

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

@router.post("/orders/estimate", response_model=OrderEstimateResponse)
async def estimate_order(
    body: OrderEstimateRequest,
    db: AsyncSession = Depends(get_async_db)
):
    """
    BE - POST /customer/orders/estimate: Tính toán và trả về báo giá dựa trên năng lượng dự đoán tiêu thụ.
    """
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
    
    # Calculate fees based on energy consumed
    express_multiplier = 1.5 if body.delivery_mode == DeliveryMode.EXPRESS else 1.0
    
    # Base calculation: e.g., 1000 VND per Wh for delivery, 200 VND for service
    delivery_fee = (energy_wh * 1000) * express_multiplier
    service_fee = energy_wh * 200
    
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
    order_id = f"DRO-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    new_order = Order(
        id=order_id,
        customer_id=current_user.id,
        package_type=body.package_type,
        payload_kg=body.payload_kg,
        origin_hub_id=body.origin_hub_id,
        destination_hub_id=body.destination_hub_id,
        delivery_mode=body.delivery_mode,
        requested_delivery_at=body.requested_delivery_at.replace(tzinfo=None) if body.requested_delivery_at else None,
        status=OrderStatus.PENDING, 
        created_at=now,
        updated_at=now
    )
    
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)
    
    return new_order
