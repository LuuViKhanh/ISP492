from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.models import Order, Mission, OrderStatus, MissionStatus
from app.modules.orders.customer.router import compute_delivery_status
from app.modules.dashboard.schemas import CustomerDashboardResponse
from app.modules.dashboard.service import COMPLETED_STATUSES

router = APIRouter(prefix="/customer/dashboard", tags=["Customer - Dashboard"])
allow_customer = RoleChecker([UserRole.CUSTOMER, UserRole.ADMIN])


@router.get("", response_model=CustomerDashboardResponse)
async def customer_dashboard(
    user: CurrentUser = Depends(allow_customer),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Customer] Dashboard thống kê giao hàng của tôi.**

    Tổng hợp số đơn đang giao / đã giao / đã hủy, tổng chi tiêu (từ phí giao của các
    chuyến đã hoàn tất) và vài chỉ số giao hàng.
    """
    orders_rows = (await db.execute(
        select(Order.id, Order.status, Order.payment_status, Order.payload_kg).where(
            Order.customer_id == user.id,
            Order.status != OrderStatus.CANCELLED,
        )
    )).all()
    order_ids = [o.id for o in orders_rows]

    # Mission mới nhất cho mỗi order (DISTINCT ON trực tiếp từ SQL, chỉ lấy 6 cột cần thiết)
    mission_by_order: dict = {}
    if order_ids:
        m_rows = (await db.execute(
            select(
                Mission.id,
                Mission.order_id,
                Mission.status,
                Mission.arrival_confirmed_at,
                Mission.arrived_at,
                Mission.delivery_fee,
                Mission.distance_m,
            )
            .distinct(Mission.order_id)
            .where(
                Mission.order_id.in_(order_ids),
                Mission.status != MissionStatus.CANCELLED,
            )
            .order_by(Mission.order_id, Mission.id.desc())
        )).all()
        for m in m_rows:
            mission_by_order[m.order_id] = m

    active = completed = cancelled = 0
    breakdown: dict[str, int] = {}
    for o in orders_rows:
        code, _ = compute_delivery_status(o, mission_by_order.get(o.id))
        if code == "CANCELLED":
            continue
        breakdown[code] = breakdown.get(code, 0) + 1
        if code == "DELIVERED":
            completed += 1
        else:
            active += 1

    cust_missions = list(mission_by_order.values())
    total_spent = round(sum(
        (m.delivery_fee or 0.0) for m in cust_missions if m.status in COMPLETED_STATUSES
    ), 2)
    total_distance = round(sum((m.distance_m or 0.0) for m in cust_missions), 2)
    payloads = [o.payload_kg for o in orders_rows if o.payload_kg]
    avg_payload = round(sum(payloads) / len(payloads), 2) if payloads else 0.0

    return CustomerDashboardResponse(
        active_deliveries=active,
        completed_deliveries=completed,
        cancelled_deliveries=cancelled,
        total_deliveries=len(orders_rows),
        total_spent=total_spent,
        avg_payload_kg=avg_payload,
        total_distance_m=total_distance,
        status_breakdown=breakdown,
    )
