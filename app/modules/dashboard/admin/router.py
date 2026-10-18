from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.auth.models import User, ROLE_ID_MAP
from app.modules.missions.models import Mission, MissionStatus, Incident, IncidentStatus
from app.modules.fleet.models import Drone, Battery, MaintenanceAlert
from app.modules.dashboard.schemas import (
    AdminDashboardResponse,
    AdminRevenue,
    AdminUsers,
    AdminMissions,
    AdminFleet,
    AdminBatteries,
    AdminAI,
    AdminAlerts,
)
from app.modules.dashboard.service import COMPLETED_STATUSES, now_naive, group_counts

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
    Lưu ý: không có module thanh toán nên `pending_payments` và `refunds` luôn = 0.
    """
    now = now_naive()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ----- Revenue -----
    comp = Mission.status.in_(COMPLETED_STATUSES)
    comp_ts = func.coalesce(Mission.arrival_confirmed_at, Mission.end_time, Mission.departed_at)

    total_rev = (await db.execute(
        select(func.coalesce(func.sum(Mission.delivery_fee), 0.0)).where(comp)
    )).scalar() or 0.0
    today_rev = (await db.execute(
        select(func.coalesce(func.sum(Mission.delivery_fee), 0.0)).where(and_(comp, comp_ts >= today_start))
    )).scalar() or 0.0
    month_rev = (await db.execute(
        select(func.coalesce(func.sum(Mission.delivery_fee), 0.0)).where(and_(comp, comp_ts >= month_start))
    )).scalar() or 0.0
    paid_orders = (await db.execute(
        select(func.count()).select_from(Mission).where(comp)
    )).scalar() or 0

    revenue = AdminRevenue(
        total=round(float(total_rev), 2),
        today=round(float(today_rev), 2),
        this_month=round(float(month_rev), 2),
        paid_orders=int(paid_orders),
        pending_payments=0.0,
        refunds=0.0,
    )

    # ----- Users -----
    role_rows = (await db.execute(select(User.role_id, func.count()).group_by(User.role_id))).all()
    by_role: dict[str, int] = {}
    for rid, cnt in role_rows:
        role = ROLE_ID_MAP.get(rid)
        key = role.value if role else "UNKNOWN"
        by_role[key] = by_role.get(key, 0) + int(cnt)
    total_users = sum(by_role.values())
    active_users = (await db.execute(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    )).scalar() or 0
    users = AdminUsers(
        total=total_users,
        by_role=by_role,
        active=int(active_users),
        inactive=total_users - int(active_users),
    )

    # ----- Missions -----
    mission_by_status = await group_counts(db, Mission.status)
    total_missions = sum(mission_by_status.values())
    m_completed = mission_by_status.get(MissionStatus.COMPLETED.value, 0) + mission_by_status.get(MissionStatus.MISSION_COMPLETED.value, 0)
    m_cancelled = mission_by_status.get(MissionStatus.CANCELLED.value, 0)
    m_rejected = mission_by_status.get(MissionStatus.REJECTED.value, 0)
    m_failed = mission_by_status.get(MissionStatus.FAILED.value, 0)
    m_terminal = m_completed + m_cancelled + m_rejected + m_failed
    m_success = round(m_completed / m_terminal * 100, 2) if m_terminal else 0.0
    missions = AdminMissions(total=total_missions, by_status=mission_by_status, success_rate=m_success)

    # ----- Fleet -----
    fleet_by_status = await group_counts(db, Drone.status)
    avg_util = (await db.execute(select(func.avg(Drone.utilization_pct)))).scalar()
    fleet = AdminFleet(
        total_drones=sum(fleet_by_status.values()),
        by_status=fleet_by_status,
        avg_utilization_pct=round(float(avg_util), 2) if avg_util is not None else 0.0,
    )

    # ----- Batteries -----
    batt_by_status = await group_counts(db, Battery.status)
    avg_charge = (await db.execute(select(func.avg(Battery.charge_level_pct)))).scalar()
    batteries = AdminBatteries(
        total=sum(batt_by_status.values()),
        by_status=batt_by_status,
        avg_charge_pct=round(float(avg_charge), 2) if avg_charge is not None else 0.0,
    )

    # ----- AI -----
    from app.modules.ai_predictions.models import AIPrediction
    ai_count = (await db.execute(
        select(func.count(func.distinct(AIPrediction.mission_id))).where(AIPrediction.estimated_energy_wh.isnot(None))
    )).scalar() or 0
    ai_avg = (await db.execute(select(func.avg(AIPrediction.estimated_energy_wh)))).scalar()
    risk_breakdown = await group_counts(db, AIPrediction.risk_level)
    ai = AdminAI(
        missions_with_prediction=int(ai_count),
        avg_estimated_energy_wh=round(float(ai_avg), 2) if ai_avg is not None else 0.0,
        risk_breakdown=risk_breakdown,
    )

    # ----- Alerts -----
    pending_alerts = (await db.execute(
        select(func.count()).select_from(MaintenanceAlert).where(MaintenanceAlert.status == "Pending")
    )).scalar() or 0
    open_incidents = (await db.execute(
        select(func.count()).select_from(Incident).where(Incident.status == IncidentStatus.OPEN)
    )).scalar() or 0
    try:
        total_audit = (await db.execute(text("SELECT COUNT(*) FROM public.audit_logs"))).scalar() or 0
    except Exception:
        total_audit = 0
    alerts = AdminAlerts(
        pending_maintenance_alerts=int(pending_alerts),
        open_incidents=int(open_incidents),
        total_audit_logs=int(total_audit),
    )

    return AdminDashboardResponse(
        revenue=revenue,
        users=users,
        missions=missions,
        fleet=fleet,
        batteries=batteries,
        ai=ai,
        alerts=alerts,
    )
