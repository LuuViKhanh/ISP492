from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.models import Order, Mission
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
    orders = (await db.execute(
        select(Order).where(Order.customer_id == user.id)
    )).scalars().all()
    order_ids = [o.id for o in orders]

    # Mission mới nhất cho mỗi order
    mission_by_order: dict[str, Mission] = {}
    if order_ids:
        missions = (await db.execute(
            select(Mission).where(Mission.order_id.in_(order_ids))
        )).scalars().all()
        for m in missions:
            cur = mission_by_order.get(m.order_id)
            if cur is None or (m.id or 0) > (cur.id or 0):
                mission_by_order[m.order_id] = m

    active = completed = cancelled = 0
    breakdown: dict[str, int] = {}
    for o in orders:
        code, _ = compute_delivery_status(o, mission_by_order.get(o.id))
        breakdown[code] = breakdown.get(code, 0) + 1
        if code == "DELIVERED":
            completed += 1
        elif code == "CANCELLED":
            cancelled += 1
        else:
            active += 1

    cust_missions = list(mission_by_order.values())
    total_spent = round(sum(
        (m.delivery_fee or 0.0) for m in cust_missions if m.status in COMPLETED_STATUSES
    ), 2)
    total_distance = round(sum((m.distance_m or 0.0) for m in cust_missions), 2)
    payloads = [o.payload_kg for o in orders if o.payload_kg]
    avg_payload = round(sum(payloads) / len(payloads), 2) if payloads else 0.0

    return CustomerDashboardResponse(
        active_deliveries=active,
        completed_deliveries=completed,
        cancelled_deliveries=cancelled,
        total_deliveries=len(orders),
        total_spent=total_spent,
        avg_payload_kg=avg_payload,
        total_distance_m=total_distance,
        status_breakdown=breakdown,
    )
