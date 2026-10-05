from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date, and_
from typing import Optional
from datetime import datetime, timezone

from app.database.db import get_async_db
from app.modules.system.schemas import AuditLogListResponse, HubCreate, HubUpdate, HubResponse, KPIDashboardResponse, KPIChartData
from app.modules.system.models import Hub
from app.modules.fleet.models import Drone, Battery
from app.modules.missions.models import Mission, MissionStatus
from app.modules.system import service
from app.shared.dependencies import RoleChecker
from app.shared.roles import UserRole

router = APIRouter(tags=["System"])

require_admin = RoleChecker([UserRole.ADMIN])


@router.get("/system/audit-logs", response_model=AuditLogListResponse, dependencies=[Depends(require_admin)])
async def get_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    resource_table: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    return await service.get_audit_logs(db, page, page_size, user_id, action, resource_table)


@router.post("/admin/system/hubs", response_model=HubResponse, dependencies=[Depends(require_admin)], status_code=201)
async def create_hub(body: HubCreate, db: AsyncSession = Depends(get_async_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hub = Hub(
        code=body.code,
        name=body.name,
        address=body.address,
        latitude=body.latitude,
        longitude=body.longitude,
        status=body.status,
        created_at=now,
        updated_at=now
    )
    db.add(hub)
    await db.commit()
    await db.refresh(hub)
    return hub


@router.put("/admin/system/hubs/{hub_id}", response_model=HubResponse, dependencies=[Depends(require_admin)])
async def update_hub(hub_id: int, body: HubUpdate, db: AsyncSession = Depends(get_async_db)):
    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found")
    
    if body.code is not None: hub.code = body.code
    if body.name is not None: hub.name = body.name
    if body.address is not None: hub.address = body.address
    if body.latitude is not None: hub.latitude = body.latitude
    if body.longitude is not None: hub.longitude = body.longitude
    if body.status is not None: hub.status = body.status
    
    hub.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    await db.commit()
    await db.refresh(hub)
    return hub


@router.delete("/admin/system/hubs/{hub_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_hub(hub_id: int, db: AsyncSession = Depends(get_async_db)):
    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found")
    await db.delete(hub)
    await db.commit()


@router.get("/admin/dashboard/kpi", response_model=KPIDashboardResponse, dependencies=[Depends(require_admin)])
async def get_dashboard_kpi(db: AsyncSession = Depends(get_async_db)):
    # Total Drones
    drones_res = await db.execute(select(func.count(Drone.id)))
    total_drones = drones_res.scalar() or 0
    
    # Total Batteries
    batt_res = await db.execute(select(func.count(Battery.id)))
    total_batteries = batt_res.scalar() or 0
    
    # Successful flights (MISSION_COMPLETED)
    succ_res = await db.execute(select(func.count(Mission.id)).where(Mission.status == MissionStatus.MISSION_COMPLETED))
    successful_flights = succ_res.scalar() or 0
    
    # Failed flights (INCIDENT_RETURN)
    fail_res = await db.execute(select(func.count(Mission.id)).where(Mission.status == MissionStatus.INCIDENT_RETURN))
    failed_flights = fail_res.scalar() or 0
    
    # Total Revenue (sum of delivery_fee where status is completed)
    rev_res = await db.execute(select(func.sum(Mission.delivery_fee)).where(Mission.status == MissionStatus.MISSION_COMPLETED))
    total_revenue = rev_res.scalar() or 0.0
    
    # Revenue Chart (group by date)
    chart_res = await db.execute(
        select(
            cast(Mission.arrived_at, Date).label("date"),
            func.sum(Mission.delivery_fee).label("revenue")
        )
        .where(Mission.status == MissionStatus.MISSION_COMPLETED)
        .where(Mission.arrived_at.isnot(None))
        .group_by(cast(Mission.arrived_at, Date))
        .order_by(cast(Mission.arrived_at, Date))
        .limit(30)
    )
    
    revenue_chart = []
    for row in chart_res.all():
        revenue_chart.append(KPIChartData(
            date=row.date.strftime("%Y-%m-%d") if row.date else "",
            revenue=float(row.revenue or 0.0)
        ))
        
    return KPIDashboardResponse(
        total_drones=total_drones,
        total_batteries=total_batteries,
        successful_flights=successful_flights,
        failed_flights=failed_flights,
        total_revenue=float(total_revenue),
        revenue_chart=revenue_chart
    )
