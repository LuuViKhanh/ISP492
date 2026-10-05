from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.models import Mission, MissionStatus, Incident, IncidentStatus
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

_COMPLETED = (MissionStatus.COMPLETED, MissionStatus.MISSION_COMPLETED)
_ACTIVE = (MissionStatus.FLYING, MissionStatus.IN_PROGRESS, MissionStatus.ACTIVE_MISSION)


@router.get("", response_model=OperatorDashboardResponse)
async def operator_dashboard(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator] Dashboard điều hành:** Mission KPI, phân bố trạng thái, chuyến đang bay,
    Energy KPI, Route Efficiency và Alerts (bảo trì / sự cố / duyệt quá hạn).
    """
    missions = (await db.execute(select(Mission))).scalars().all()

    total = len(missions)
    completed = sum(1 for m in missions if m.status in _COMPLETED)
    cancelled = sum(1 for m in missions if m.status == MissionStatus.CANCELLED)
    rejected = sum(1 for m in missions if m.status == MissionStatus.REJECTED)
    failed = sum(1 for m in missions if m.status == MissionStatus.FAILED)
    terminal = completed + cancelled + rejected + failed
    success_rate = round(completed / terminal * 100, 2) if terminal else 0.0

    distances = [m.distance_m for m in missions if m.distance_m]
    payloads = [m.payload_weight for m in missions if m.payload_weight]
    revenues = [m.delivery_fee for m in missions if m.delivery_fee and m.status in _COMPLETED]
    energies = [m.estimated_energy_wh for m in missions if m.estimated_energy_wh]
    durations = [m.predicted_duration_min for m in missions if m.predicted_duration_min]

    total_distance = sum(distances) if distances else 0.0
    total_energy = sum(energies) if energies else 0.0
    total_distance_km = total_distance / 1000.0 if total_distance else 0.0
    wh_per_km = round(total_energy / total_distance_km, 2) if total_distance_km else 0.0

    status_breakdown: dict[str, int] = {}
    for m in missions:
        key = m.status.value if m.status else "UNKNOWN"
        status_breakdown[key] = status_breakdown.get(key, 0) + 1

    active_flights = sum(1 for m in missions if m.status in _ACTIVE)

    now = now_naive()
    overdue_approvals = sum(
        1 for m in missions
        if m.status == MissionStatus.PENDING_APPROVAL and m.scheduled_time and m.scheduled_time <= now
    )

    pending_alerts = (await db.execute(
        select(func.count()).select_from(MaintenanceAlert).where(MaintenanceAlert.status == "Pending")
    )).scalar() or 0
    open_incidents = (await db.execute(
        select(func.count()).select_from(Incident).where(Incident.status == IncidentStatus.OPEN)
    )).scalar() or 0

    return OperatorDashboardResponse(
        mission_kpi=OperatorMissionKPI(
            total=total,
            completed=completed,
            cancelled=cancelled,
            rejected=rejected,
            success_rate=success_rate,
            avg_distance_m=round(sum(distances) / len(distances), 2) if distances else 0.0,
            avg_payload_kg=round(sum(payloads) / len(payloads), 2) if payloads else 0.0,
            total_revenue=round(sum(revenues), 2) if revenues else 0.0,
        ),
        status_breakdown=status_breakdown,
        active_flights=active_flights,
        energy_kpi=OperatorEnergyKPI(
            total_estimated_energy_wh=round(total_energy, 2),
            avg_estimated_energy_wh=round(sum(energies) / len(energies), 2) if energies else 0.0,
            wh_per_km=wh_per_km,
        ),
        route_efficiency=OperatorRouteEfficiency(
            total_distance_m=round(total_distance, 2),
            avg_distance_m=round(sum(distances) / len(distances), 2) if distances else 0.0,
            avg_predicted_duration_min=round(sum(durations) / len(durations), 2) if durations else 0.0,
        ),
        alerts=OperatorAlerts(
            pending_maintenance_alerts=int(pending_alerts),
            open_incidents=int(open_incidents),
            overdue_approvals=overdue_approvals,
        ),
    )
