from fastapi import APIRouter, Depends
from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.models import Mission, MissionStatus, Incident, IncidentStatus
from app.modules.ai_predictions.models import AIPrediction
from app.modules.fleet.models import MaintenanceAlert
from app.modules.dashboard.schemas import (
    OperatorDashboardResponse,
    OperatorMissionKPI,
    OperatorEnergyKPI,
    OperatorRouteEfficiency,
    OperatorAlerts,
)
from app.modules.dashboard.service import now_naive

router = APIRouter(prefix="/operator/dashboard", tags=["Operator - Dashboard"])
allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])

_COMPLETED = (MissionStatus.ARRIVAL_COMPLETED, MissionStatus.MISSION_COMPLETED)
_ACTIVE = (MissionStatus.FLYING,)


@router.get("", response_model=OperatorDashboardResponse)
async def operator_dashboard(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator] Dashboard điều hành:** Mission KPI, phân bố trạng thái, chuyến đang bay,
    Energy KPI, Route Efficiency và Alerts (bảo trì / sự cố / duyệt quá hạn).
    """
    now = now_naive()
    comp_cond = Mission.status.in_(_COMPLETED)
    dist_cond = and_(Mission.distance_m.isnot(None), Mission.distance_m != 0)
    pay_cond = and_(Mission.payload_weight.isnot(None), Mission.payload_weight != 0)
    overdue_cond = and_(
        Mission.status == MissionStatus.SCHEDULED,
        Mission.scheduled_time.isnot(None),
        Mission.scheduled_time <= now,
    )

    # 1. Tổng hợp toàn bộ KPI Mission ngay trên PostgreSQL (GROUP BY status)
    m_rows = (await db.execute(
        select(
            Mission.status,
            func.count(),
            func.coalesce(func.sum(case((dist_cond, Mission.distance_m), else_=0.0)), 0.0),
            func.count(case((dist_cond, 1))),
            func.coalesce(func.sum(case((pay_cond, Mission.payload_weight), else_=0.0)), 0.0),
            func.count(case((pay_cond, 1))),
            func.coalesce(func.sum(case((and_(comp_cond, Mission.delivery_fee.isnot(None)), Mission.delivery_fee), else_=0.0)), 0.0),
            func.count(case((overdue_cond, 1))),
        )
        .where(Mission.status != MissionStatus.CANCELLED)
        .group_by(Mission.status)
    )).all()

    total = completed = rejected = failed = active_flights = overdue_approvals = 0
    total_distance = dist_cnt = 0.0
    total_payload = pay_cnt = 0.0
    total_revenue = 0.0
    status_breakdown: dict[str, int] = {}

    for st, cnt, d_sum, d_c, p_sum, p_c, rev_s, ov_c in m_rows:
        cnt_i = int(cnt)
        total += cnt_i
        if st in _COMPLETED:
            completed += cnt_i
        if st == MissionStatus.FAILED:
            failed += cnt_i
        if st in _ACTIVE:
            active_flights += cnt_i
        overdue_approvals += int(ov_c or 0)
        total_distance += float(d_sum or 0.0)
        dist_cnt += int(d_c or 0)
        total_payload += float(p_sum or 0.0)
        pay_cnt += int(p_c or 0)
        total_revenue += float(rev_s or 0.0)

        val = st.value if st else "UNKNOWN"
        if val != "Cancelled":
            key = MissionStatus.FLYING.value if val in ("In Progress", "Active mission") else val
            status_breakdown[key] = status_breakdown.get(key, 0) + cnt_i

    cancelled = 0
    terminal = completed + cancelled + rejected + failed
    success_rate = round(completed / terminal * 100, 2) if terminal else 0.0
    avg_distance = round(total_distance / dist_cnt, 2) if dist_cnt else 0.0
    avg_payload = round(total_payload / pay_cnt, 2) if pay_cnt else 0.0

    # 2. Latest AIPrediction per non-cancelled mission + Alerts/Incidents gộp trong 1 câu SQL
    latest_pred_subq = (
        select(
            AIPrediction.mission_id,
            AIPrediction.estimated_energy_wh,
            AIPrediction.estimated_duration_m,
        )
        .distinct(AIPrediction.mission_id)
        .join(Mission, Mission.id == AIPrediction.mission_id)
        .where(Mission.status != MissionStatus.CANCELLED)
        .order_by(AIPrediction.mission_id, AIPrediction.id.desc())
        .subquery()
    )
    ai_alert_row = (await db.execute(
        select(
            func.coalesce(func.sum(latest_pred_subq.c.estimated_energy_wh), 0.0),
            func.count(latest_pred_subq.c.estimated_energy_wh),
            func.coalesce(func.sum(latest_pred_subq.c.estimated_duration_m), 0.0),
            func.count(latest_pred_subq.c.estimated_duration_m),
            select(func.count()).select_from(MaintenanceAlert).where(MaintenanceAlert.status == "Pending").scalar_subquery(),
            select(func.count()).select_from(Incident).where(Incident.status == IncidentStatus.OPEN).scalar_subquery(),
        )
    )).one()

    total_energy = float(ai_alert_row[0] or 0.0)
    energy_cnt = int(ai_alert_row[1] or 0)
    total_duration = float(ai_alert_row[2] or 0.0)
    duration_cnt = int(ai_alert_row[3] or 0)
    pending_alerts = int(ai_alert_row[4] or 0)
    open_incidents = int(ai_alert_row[5] or 0)

    total_distance_km = total_distance / 1000.0 if total_distance else 0.0
    wh_per_km = round(total_energy / total_distance_km, 2) if total_distance_km else 0.0

    return OperatorDashboardResponse(
        mission_kpi=OperatorMissionKPI(
            total=total,
            completed=completed,
            cancelled=cancelled,
            rejected=rejected,
            success_rate=success_rate,
            avg_distance_m=avg_distance,
            avg_payload_kg=avg_payload,
            total_revenue=round(total_revenue, 2),
        ),
        status_breakdown=status_breakdown,
        active_flights=active_flights,
        energy_kpi=OperatorEnergyKPI(
            total_estimated_energy_wh=round(total_energy, 2),
            avg_estimated_energy_wh=round(total_energy / energy_cnt, 2) if energy_cnt else 0.0,
            wh_per_km=wh_per_km,
        ),
        route_efficiency=OperatorRouteEfficiency(
            total_distance_m=round(total_distance, 2),
            avg_distance_m=avg_distance,
            avg_predicted_duration_min=round(total_duration / duration_cnt, 2) if duration_cnt else 0.0,
        ),
        alerts=OperatorAlerts(
            pending_maintenance_alerts=pending_alerts,
            open_incidents=open_incidents,
            overdue_approvals=overdue_approvals,
        ),
    )
