from fastapi import APIRouter, Depends
from sqlalchemy import select, func, case
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
    # 1. Drones: gộp GROUP BY status + utilization trung bình trong 1 query
    drone_rows = (await db.execute(
        select(
            Drone.status,
            func.count(),
            func.coalesce(func.sum(Drone.utilization_pct), 0.0),
            func.count(Drone.utilization_pct),
        ).group_by(Drone.status)
    )).all()
    fleet_status: dict[str, int] = {}
    util_sum = 0.0
    util_cnt = 0
    for st, cnt, u_s, u_c in drone_rows:
        st_key = st.value if hasattr(st, "value") else (str(st) if st is not None else "UNKNOWN")
        fleet_status[st_key] = int(cnt)
        util_sum += float(u_s or 0.0)
        util_cnt += int(u_c or 0)
    total_drones = sum(fleet_status.values())
    available_drones = fleet_status.get(DroneStatus.AVAILABLE.value, 0)
    drone_availability_pct = round(available_drones / total_drones * 100, 2) if total_drones else 0.0
    avg_util = round(util_sum / util_cnt, 2) if util_cnt else 0.0

    # 2. Batteries: gộp GROUP BY status + avg_charge + low_battery_count + pending_alerts trong 1 query
    batt_rows = (await db.execute(
        select(
            Battery.status,
            func.count(),
            func.coalesce(func.sum(Battery.charge_level_pct), 0.0),
            func.count(Battery.charge_level_pct),
            func.count(case((Battery.charge_level_pct <= 20, 1))),
        ).group_by(Battery.status)
    )).all()
    battery_status: dict[str, int] = {}
    chg_sum = 0.0
    chg_cnt = 0
    low_battery = 0
    for st, cnt, c_s, c_c, low_c in batt_rows:
        st_key = st.value if hasattr(st, "value") else (str(st) if st is not None else "UNKNOWN")
        battery_status[st_key] = int(cnt)
        chg_sum += float(c_s or 0.0)
        chg_cnt += int(c_c or 0)
        low_battery += int(low_c or 0)
    total_batteries = sum(battery_status.values())
    avg_charge = round(chg_sum / chg_cnt, 2) if chg_cnt else 0.0

    # 3. Pending maintenance alerts + Work orders
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
        low_battery_count=low_battery,
        pending_maintenance_alerts=int(pending_alerts),
        work_order_status=work_order_status,
    )
