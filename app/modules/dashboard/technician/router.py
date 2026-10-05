from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.fleet.models import Drone, DroneStatus, Battery, WorkOrder, MaintenanceAlert
from app.modules.dashboard.schemas import TechnicianDashboardResponse
from app.modules.dashboard.service import group_counts

router = APIRouter(prefix="/technician/dashboard", tags=["Technician - Dashboard"])
allow_technician = RoleChecker([UserRole.TECHNICIAN, UserRole.ADMIN])


@router.get("", response_model=TechnicianDashboardResponse)
async def technician_dashboard(
    user: CurrentUser = Depends(allow_technician),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Technician] Dashboard thiết bị:** tình trạng đội drone, pin, mức sẵn sàng,
    cảnh báo bảo trì đang chờ và work order theo trạng thái.
    """
    fleet_status = await group_counts(db, Drone.status)
    total_drones = sum(fleet_status.values())
    available_drones = fleet_status.get(DroneStatus.AVAILABLE.value, 0)
    drone_availability_pct = round(available_drones / total_drones * 100, 2) if total_drones else 0.0

    avg_util = (await db.execute(select(func.avg(Drone.utilization_pct)))).scalar()
    avg_util = round(float(avg_util), 2) if avg_util is not None else 0.0

    battery_status = await group_counts(db, Battery.status)
    total_batteries = sum(battery_status.values())
    avg_charge = (await db.execute(select(func.avg(Battery.charge_level_pct)))).scalar()
    avg_charge = round(float(avg_charge), 2) if avg_charge is not None else 0.0
    low_battery = (await db.execute(
        select(func.count()).select_from(Battery).where(Battery.charge_level_pct <= 20)
    )).scalar() or 0

    pending_alerts = (await db.execute(
        select(func.count()).select_from(MaintenanceAlert).where(MaintenanceAlert.status == "Pending")
    )).scalar() or 0

    work_order_status = await group_counts(db, WorkOrder.status)

    return TechnicianDashboardResponse(
        fleet_status=fleet_status,
        total_drones=total_drones,
        available_drones=available_drones,
        drone_availability_pct=drone_availability_pct,
        avg_utilization_pct=avg_util,
        battery_status=battery_status,
        total_batteries=total_batteries,
        avg_battery_charge_pct=avg_charge,
        low_battery_count=int(low_battery),
        pending_maintenance_alerts=int(pending_alerts),
        work_order_status=work_order_status,
    )
