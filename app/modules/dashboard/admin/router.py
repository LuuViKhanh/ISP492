from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_, case, cast, Float
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.auth.models import User, ROLE_ID_MAP
from app.modules.missions.models import Mission, MissionStatus, Incident, IncidentStatus, Order, PaymentStatus, DeliveryRating
from app.modules.fleet.models import Drone, Battery, MaintenanceAlert
from app.modules.system.models import ActivityLog
from app.modules.supplies.models import SupplyTransaction, SupplyTxnReason, PartCategory
from app.modules.ai_predictions.models import AIPrediction
from app.modules.dashboard.schemas import (
    AdminDashboardResponse,
    AdminRevenue,
    AdminUsers,
    AdminMissions,
    AdminFleet,
    AdminBatteries,
    AdminAI,
    AdminAlerts,
    AdminRatings,
    AdminOrders,
)
from app.modules.dashboard.service import COMPLETED_STATUSES, now_naive

router = APIRouter(prefix="/admin/dashboard", tags=["Admin - Dashboard"])
allow_admin = RoleChecker([UserRole.ADMIN])


@router.get("", response_model=AdminDashboardResponse)
async def admin_dashboard(
    user: CurrentUser = Depends(allow_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] Dashboard tổng quan hệ thống.**

    Doanh thu (từ `Mission.delivery_fee` của chuyến đã hoàn tất), thống kê người dùng,
    mission, fleet, pin, AI và các cảnh báo hệ thống.
    """
    now = now_naive()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1. Missions + Revenue gộp trong 1 query GROUP BY status
    comp = Mission.status.in_(COMPLETED_STATUSES)
    comp_ts = func.coalesce(Mission.arrival_confirmed_at, Mission.end_time, Mission.departed_at)
    m_rows = (await db.execute(
        select(
            Mission.status,
            func.count(),
            func.coalesce(func.sum(case((comp, Mission.delivery_fee), else_=0.0)), 0.0),
            func.coalesce(func.sum(case((and_(comp, comp_ts >= today_start), Mission.delivery_fee), else_=0.0)), 0.0),
            func.coalesce(func.sum(case((and_(comp, comp_ts >= month_start), Mission.delivery_fee), else_=0.0)), 0.0),
            func.count(case((comp, 1))),
        ).group_by(Mission.status)
    )).all()

    total_rev = 0.0
    today_rev = 0.0
    month_rev = 0.0
    paid_orders = 0
    mission_by_status: dict[str, int] = {}
    for st, cnt, rev_s, rev_t, rev_m, paid_c in m_rows:
        st_key = st.value if hasattr(st, "value") else (str(st) if st is not None else "UNKNOWN")
        if st_key != MissionStatus.CANCELLED.value:
            mission_by_status[st_key] = int(cnt)
        total_rev += float(rev_s or 0.0)
        today_rev += float(rev_t or 0.0)
        month_rev += float(rev_m or 0.0)
        paid_orders += int(paid_c or 0)

    total_missions = sum(mission_by_status.values())
    m_completed = mission_by_status.get(MissionStatus.ARRIVAL_COMPLETED.value, 0) + mission_by_status.get(MissionStatus.MISSION_COMPLETED.value, 0)
    m_cancelled = 0
    m_failed = mission_by_status.get(MissionStatus.FAILED.value, 0)
    m_terminal = m_completed + m_cancelled + m_failed
    m_success = round(m_completed / m_terminal * 100, 2) if m_terminal else 0.0
    missions = AdminMissions(total=total_missions, by_status=mission_by_status, success_rate=m_success)

    # 2. Users gộp role + active trong 1 query
    user_rows = (await db.execute(
        select(User.role_id, User.is_active, func.count()).group_by(User.role_id, User.is_active)
    )).all()
    by_role: dict[str, int] = {}
    active_users = 0
    total_users = 0
    for rid, is_act, cnt in user_rows:
        cnt_i = int(cnt)
        role = ROLE_ID_MAP.get(rid)
        key = role.value if role else "UNKNOWN"
        by_role[key] = by_role.get(key, 0) + cnt_i
        total_users += cnt_i
        if is_act:
            active_users += cnt_i
    users = AdminUsers(
        total=total_users,
        by_role=by_role,
        active=active_users,
        inactive=total_users - active_users,
    )

    # 3. Fleet gộp status + avg_utilization trong 1 query
    drone_rows = (await db.execute(
        select(
            Drone.status,
            func.count(),
            func.coalesce(func.sum(Drone.utilization_pct), 0.0),
            func.count(Drone.utilization_pct),
        ).group_by(Drone.status)
    )).all()
    fleet_by_status: dict[str, int] = {}
    util_sum = 0.0
    util_cnt = 0
    for st, cnt, u_s, u_c in drone_rows:
        st_key = st.value if hasattr(st, "value") else (str(st) if st is not None else "UNKNOWN")
        fleet_by_status[st_key] = int(cnt)
        util_sum += float(u_s or 0.0)
        util_cnt += int(u_c or 0)
    fleet = AdminFleet(
        total_drones=sum(fleet_by_status.values()),
        by_status=fleet_by_status,
        avg_utilization_pct=round(util_sum / util_cnt, 2) if util_cnt else 0.0,
    )

    # 4. Batteries gộp status + avg_charge trong 1 query
    batt_rows = (await db.execute(
        select(
            Battery.status,
            func.count(),
            func.coalesce(func.sum(Battery.charge_level_pct), 0.0),
            func.count(Battery.charge_level_pct),
        ).group_by(Battery.status)
    )).all()
    batt_by_status: dict[str, int] = {}
    chg_sum = 0.0
    chg_cnt = 0
    for st, cnt, c_s, c_c in batt_rows:
        st_key = st.value if hasattr(st, "value") else (str(st) if st is not None else "UNKNOWN")
        batt_by_status[st_key] = int(cnt)
        chg_sum += float(c_s or 0.0)
        chg_cnt += int(c_c or 0)
    batteries = AdminBatteries(
        total=sum(batt_by_status.values()),
        by_status=batt_by_status,
        avg_charge_pct=round(chg_sum / chg_cnt, 2) if chg_cnt else 0.0,
    )

    # 5. AI risk breakdown + avg energy gộp trong 1 query
    ai_rows = (await db.execute(
        select(
            AIPrediction.risk_level,
            func.count(),
            func.coalesce(func.sum(AIPrediction.estimated_energy_wh), 0.0),
            func.count(AIPrediction.estimated_energy_wh),
        ).group_by(AIPrediction.risk_level)
    )).all()
    risk_breakdown: dict[str, int] = {}
    ai_e_sum = 0.0
    ai_e_cnt = 0
    for rl, cnt, e_s, e_c in ai_rows:
        rl_key = rl.value if hasattr(rl, "value") else (str(rl) if rl is not None else "UNKNOWN")
        risk_breakdown[rl_key] = int(cnt)
        ai_e_sum += float(e_s or 0.0)
        ai_e_cnt += int(e_c or 0)

    # 6. Gộp toàn bộ scalar counts & sums (Orders pending/refund, Expenses, AI distinct missions, Alerts, Incidents, Audit, Order stats) vào 1 câu SQL duy nhất
    scalars_row = (await db.execute(
        select(
            select(func.coalesce(func.sum(Order.delivery_fee), 0.0))
            .where(Order.payment_status == PaymentStatus.PENDING)
            .scalar_subquery(),
            select(func.coalesce(func.sum(Order.refund_amount), 0.0))
            .where(Order.payment_status == PaymentStatus.REFUNDED)
            .scalar_subquery(),
            select(func.coalesce(func.sum(cast(ActivityLog.details_json["acquisition_cost"].as_string(), Float)), 0.0))
            .where(
                ActivityLog.action.in_(["DRONE_ACQUISITION", "BATTERY_BATCH_IMPORT", "BATTERY_ADD"]),
                ActivityLog.details_json.isnot(None),
            )
            .scalar_subquery(),
            select(func.coalesce(func.sum(SupplyTransaction.quantity_delta * PartCategory.unit_cost), 0.0))
            .join(PartCategory, SupplyTransaction.part_category_id == PartCategory.id)
            .where(SupplyTransaction.reason == SupplyTxnReason.RECEIVE)
            .scalar_subquery(),
            select(func.count(func.distinct(AIPrediction.mission_id)))
            .where(AIPrediction.estimated_energy_wh.isnot(None))
            .scalar_subquery(),
            select(func.count())
            .select_from(MaintenanceAlert)
            .where(MaintenanceAlert.status == "Pending")
            .scalar_subquery(),
            select(func.count())
            .select_from(Incident)
            .where(Incident.status == IncidentStatus.OPEN)
            .scalar_subquery(),
            select(func.count())
            .select_from(ActivityLog)
            .scalar_subquery(),
            select(func.count())
            .select_from(Order)
            .scalar_subquery(),
            select(func.count())
            .select_from(Order)
            .where(Order.status == OrderStatus.DELIVERED_TO_CUSTOMER)
            .scalar_subquery(),
            select(func.count())
            .select_from(Order)
            .where(Order.payment_status == PaymentStatus.REFUNDED)
            .scalar_subquery(),
            select(func.count())
            .select_from(Order)
            .where(Order.status == OrderStatus.CANCELLED)
            .scalar_subquery(),
        )
    )).one()

    pending_payments_result = float(scalars_row[0] or 0.0)
    refunds_result = float(scalars_row[1] or 0.0)
    total_expenses = round(float(scalars_row[2] or 0.0) + float(scalars_row[3] or 0.0), 2)
    ai_count = int(scalars_row[4] or 0)
    pending_alerts = int(scalars_row[5] or 0)
    open_incidents = int(scalars_row[6] or 0)
    total_audit = int(scalars_row[7] or 0)
    total_orders = int(scalars_row[8] or 0)
    successful_orders = int(scalars_row[9] or 0)
    refunded_orders = int(scalars_row[10] or 0)
    cancelled_orders = int(scalars_row[11] or 0)

    gross_total = round(float(total_rev), 2)
    net_total = round(gross_total - total_expenses, 2)
    revenue = AdminRevenue(
        total=net_total,
        today=round(float(today_rev), 2),
        this_month=round(float(month_rev), 2),
        paid_orders=int(paid_orders),
        pending_payments=round(pending_payments_result, 2),
        refunds=round(refunds_result, 2),
        gross_revenue=gross_total,
        total_expenses=total_expenses,
    )
    ai = AdminAI(
        missions_with_prediction=ai_count,
        avg_estimated_energy_wh=round(ai_e_sum / ai_e_cnt, 2) if ai_e_cnt else 0.0,
        risk_breakdown=risk_breakdown,
    )
    alerts = AdminAlerts(
        pending_maintenance_alerts=pending_alerts,
        open_incidents=open_incidents,
        total_audit_logs=total_audit,
    )

    # Delivery ratings stats
    rating_stats = (await db.execute(
        select(
            func.count(DeliveryRating.id).label("total"),
            func.avg(DeliveryRating.rating).label("avg"),
            func.count(case((DeliveryRating.rating == 5, 1))).label("five_star"),
            func.count(case((DeliveryRating.rating <= 2, 1))).label("low"),
        )
    )).one()
    r_total = rating_stats.total or 0
    r_avg = round(float(rating_stats.avg), 2) if rating_stats.avg else 0.0
    r_five_pct = round(rating_stats.five_star / r_total * 100, 1) if r_total else 0.0
    ratings = AdminRatings(
        total_ratings=r_total,
        avg_rating=r_avg,
        five_star_pct=r_five_pct,
        low_rating_count=rating_stats.low or 0,
    )
    
    orders = AdminOrders(
        total=total_orders,
        successful_orders_count=successful_orders,
        refunded_orders_count=refunded_orders,
        cancelled_orders_count=cancelled_orders,
    )

    return AdminDashboardResponse(
        revenue=revenue,
        users=users,
        missions=missions,
        fleet=fleet,
        batteries=batteries,
        ai=ai,
        alerts=alerts,
        ratings=ratings,
        orders=orders,
    )
