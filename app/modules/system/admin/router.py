from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date, and_, or_
from typing import Optional
from datetime import datetime, timezone
import re

from app.core.timezone import get_vn_naive_datetime
from app.database.db import get_async_db
from app.modules.system.schemas import (
    AuditLogListResponse,
    HubCreate,
    HubUpdate,
    HubResponse,
    HubSummaryResponse,
    HubListItemResponse,
    HubDetailResponse,
    HubAssignedDroneItem,
    HubAssignedBatteryItem,
    HubAssignedTechnician,
    KPIDashboardResponse,
    KPIChartData,
    SimulatorConfigResponse,
    SimulatorConfigUpdate,
)
from app.modules.system.models import Hub
from app.modules.fleet.models import Drone, Battery
from app.modules.auth.models import User
from app.modules.missions.models import Mission, MissionStatus
from app.modules.system import service
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.shared.drone_simulator import simulator_config

router = APIRouter(tags=["System"])

require_admin = RoleChecker([UserRole.ADMIN])


def extract_area(address: Optional[str]) -> str:
    """Trích xuất khu vực (District / TP. Thủ Đức) từ chuỗi địa chỉ."""
    if not address:
        return "Ho Chi Minh City"
    match = re.search(r"(TP\.\s*Thủ Đức|Thủ Đức|Bình Thạnh|Quận\s*\d+|District\s*\d+)", address, re.IGNORECASE)
    if match:
        val = match.group(1).strip()
        if "Thủ Đức" in val or "thủ đức" in val.lower():
            return "Thu Duc"
        m_q = re.search(r"Quận\s*(\d+)", val, re.IGNORECASE)
        if m_q:
            return f"District {m_q.group(1)}"
        m_d = re.search(r"District\s*(\d+)", val, re.IGNORECASE)
        if m_d:
            return f"District {m_d.group(1)}"
        return val
    return "Ho Chi Minh City"


def compute_battery_ui_status(battery: Battery) -> str:
    """Quy đổi trạng thái pin hiển thị trên giao diện chi tiết Hub."""
    if battery.drone_id:
        return "Installed"
    if battery.work_order_id:
        return "Maintenance"
    if (battery.charge_level_pct or 0) >= 100:
        return "Ready"
    return "Charging"


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


# ── Hubs Endpoints for Admin (Hỗ trợ cả prefix /admin/system/hubs và /admin/fleet/hubs) ──

@router.get("/admin/system/hubs/summary", response_model=HubSummaryResponse, dependencies=[Depends(require_admin)])
@router.get("/admin/fleet/hubs/summary", response_model=HubSummaryResponse, dependencies=[Depends(require_admin)], include_in_schema=False)
async def get_hubs_summary(db: AsyncSession = Depends(get_async_db)):
    """Trả về 4 KPI tổng quan đầu trang Hub network map."""
    total_hubs = (await db.execute(select(func.count(Hub.id)))).scalar() or 0
    active_hubs = (await db.execute(select(func.count(Hub.id)).where(func.lower(Hub.status) == "active"))).scalar() or 0
    total_drones = (await db.execute(select(func.count(Drone.id)))).scalar() or 0
    total_batteries = (await db.execute(select(func.count(Battery.id)))).scalar() or 0

    return HubSummaryResponse(
        total_hubs=int(total_hubs),
        active_hubs=int(active_hubs),
        total_drones=int(total_drones),
        total_batteries=int(total_batteries),
    )


@router.get("/admin/system/hubs", response_model=list[HubListItemResponse], dependencies=[Depends(require_admin)])
@router.get("/admin/fleet/hubs", response_model=list[HubListItemResponse], dependencies=[Depends(require_admin)], include_in_schema=False)
async def get_admin_hubs(
    search: Optional[str] = Query(None, description="Tìm theo tên, mã code hoặc địa chỉ"),
    status: Optional[str] = Query(None, description="Lọc theo trạng thái (Active, Inactive)"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Danh sách Hubs kèm số lượng Drones, Batteries, Technicians và danh sách Drones tại Hub.
    """
    query = select(Hub)
    if status:
        query = query.where(func.lower(Hub.status) == status.lower())
    if search:
        s = f"%{search.strip()}%"
        query = query.where(
            or_(
                Hub.name.ilike(s),
                Hub.code.ilike(s),
                Hub.address.ilike(s),
            )
        )
    query = query.order_by(Hub.id.asc())
    hubs = (await db.execute(query)).scalars().all()
    if not hubs:
        return []

    hub_ids = [h.id for h in hubs]

    # Đếm Drones theo hub
    drone_counts_res = await db.execute(
        select(Drone.current_hub_id, func.count(Drone.id))
        .where(Drone.current_hub_id.in_(hub_ids))
        .group_by(Drone.current_hub_id)
    )
    drone_count_map = {row[0]: row[1] for row in drone_counts_res.all()}

    # Lấy danh sách drones chi tiết cho từng hub
    drones_res = await db.execute(
        select(Drone).where(Drone.current_hub_id.in_(hub_ids)).order_by(Drone.id.asc())
    )
    drones_by_hub: dict[int, list[HubAssignedDroneItem]] = {}
    for d in drones_res.scalars().all():
        if d.current_hub_id not in drones_by_hub:
            drones_by_hub[d.current_hub_id] = []
        drones_by_hub[d.current_hub_id].append(
            HubAssignedDroneItem(
                id=d.id,
                name=d.name,
                model=d.model,
                status=d.status.value if hasattr(d.status, "value") else str(d.status),
                serial_number=d.id,
                battery_level_pct=d.battery_level_pct,
                payload_capacity_kg=d.payload_capacity_kg,
            )
        )

    # Đếm Batteries theo hub
    bat_counts_res = await db.execute(
        select(Battery.current_hub_id, func.count(Battery.id))
        .where(Battery.current_hub_id.in_(hub_ids))
        .group_by(Battery.current_hub_id)
    )
    bat_count_map = {row[0]: row[1] for row in bat_counts_res.all()}

    # Đếm Technicians theo hub
    tech_counts_res = await db.execute(
        select(User.hub_id, func.count(User.id))
        .where(User.hub_id.in_(hub_ids), User.role_id == "TECHNICIAN")
        .group_by(User.hub_id)
    )
    tech_count_map = {row[0]: row[1] for row in tech_counts_res.all()}

    items = []
    for h in hubs:
        d_cnt = drone_count_map.get(h.id, 0)
        b_cnt = bat_count_map.get(h.id, 0)
        t_cnt = tech_count_map.get(h.id, 0)
        assigned_d = drones_by_hub.get(h.id, [])
        items.append(
            HubListItemResponse(
                id=h.id,
                code=h.code,
                name=h.name,
                area=extract_area(h.address),
                address=h.address,
                latitude=h.latitude,
                longitude=h.longitude,
                status=h.status,
                capacity=24,
                drones=d_cnt,
                drones_count=d_cnt,
                batteries=b_cnt,
                batteries_count=b_cnt,
                technicians=t_cnt,
                technicians_count=t_cnt,
                assigned_drones=assigned_d,
                created_at=h.created_at,
                updated_at=h.updated_at,
            )
        )
    return items


@router.get("/admin/system/hubs/{hub_id}", response_model=HubDetailResponse, dependencies=[Depends(require_admin)])
@router.get("/admin/fleet/hubs/{hub_id}", response_model=HubDetailResponse, dependencies=[Depends(require_admin)], include_in_schema=False)
async def get_admin_hub_detail(hub_id: int, db: AsyncSession = Depends(get_async_db)):
    """
    Chi tiết Hub kèm Assigned Technician, danh sách Drones và Batteries tại Hub.
    """
    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found")

    # Kỹ thuật viên phụ trách từ bảng accounts
    tech_res = await db.execute(
        select(User)
        .where(User.hub_id == hub.id, User.role_id == "TECHNICIAN")
        .order_by(User.created_at.asc())
    )
    tech_users = tech_res.scalars().all()
    technicians = [
        HubAssignedTechnician(
            id=u.id,
            full_name=u.full_name or "Technician",
            email=u.email,
        )
        for u in tech_users
    ]
    primary_tech = technicians[0] if technicians else None

    # Drones tại Hub
    drones_res = await db.execute(
        select(Drone).where(Drone.current_hub_id == hub.id).order_by(Drone.id.asc())
    )
    assigned_drones = [
        HubAssignedDroneItem(
            id=d.id,
            name=d.name,
            model=d.model,
            status=d.status.value if hasattr(d.status, "value") else str(d.status),
            serial_number=d.id,
            battery_level_pct=d.battery_level_pct,
            payload_capacity_kg=d.payload_capacity_kg,
        )
        for d in drones_res.scalars().all()
    ]

    # Batteries tại Hub
    bat_res = await db.execute(
        select(Battery).where(Battery.current_hub_id == hub.id).order_by(Battery.id.asc())
    )
    assigned_batteries = [
        HubAssignedBatteryItem(
            id=b.id,
            serial_number=b.serial_number or b.id,
            battery_model=b.battery_model or "FC30 Battery Pack",
            status=compute_battery_ui_status(b),
            capacity_wh=b.capacity_wh,
            charge_level_pct=b.charge_level_pct,
            drone_id=b.drone_id,
        )
        for b in bat_res.scalars().all()
    ]

    return HubDetailResponse(
        id=hub.id,
        code=hub.code,
        name=hub.name,
        area=extract_area(hub.address),
        address=hub.address,
        capacity=24,
        latitude=hub.latitude,
        longitude=hub.longitude,
        status=hub.status,
        created_at=hub.created_at,
        updated_at=hub.updated_at,
        assigned_technician=primary_tech,
        assigned_technicians=technicians,
        assigned_drones=assigned_drones,
        assigned_batteries=assigned_batteries,
    )


@router.post("/admin/system/hubs", response_model=HubResponse, dependencies=[Depends(require_admin)], status_code=201)
async def create_hub(body: HubCreate, db: AsyncSession = Depends(get_async_db)):
    now = get_vn_naive_datetime()
    addr = body.address or ""
    if body.area and body.area not in addr:
        addr = f"{addr}, {body.area}".strip(", ")
    hub = Hub(
        code=body.code,
        name=body.name,
        address=addr or body.address,
        latitude=body.latitude,
        longitude=body.longitude,
        status=body.status,
        created_at=now,
        updated_at=now,
    )
    db.add(hub)
    await db.commit()
    await db.refresh(hub)
    res = HubResponse.model_validate(hub)
    res.area = extract_area(hub.address)
    res.capacity = body.capacity or 24
    return res


@router.put("/admin/system/hubs/{hub_id}", response_model=HubResponse, dependencies=[Depends(require_admin)])
async def update_hub(hub_id: int, body: HubUpdate, db: AsyncSession = Depends(get_async_db)):
    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Hub not found")
    
    if body.code is not None: hub.code = body.code
    if body.name is not None: hub.name = body.name
    if body.address is not None: hub.address = body.address
    if body.area is not None and body.address is None:
        if hub.address and body.area not in hub.address:
            hub.address = f"{hub.address}, {body.area}"
    if body.latitude is not None: hub.latitude = body.latitude
    if body.longitude is not None: hub.longitude = body.longitude
    if body.status is not None: hub.status = body.status
    
    hub.updated_at = get_vn_naive_datetime()
    
    await db.commit()
    await db.refresh(hub)
    res = HubResponse.model_validate(hub)
    res.area = extract_area(hub.address)
    res.capacity = body.capacity or 24
    return res


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
    
    # Successful flights (MISSION_COMPLETED hoặc COMPLETED)
    succ_res = await db.execute(
        select(func.count(Mission.id)).where(
            Mission.status.in_([MissionStatus.MISSION_COMPLETED, MissionStatus.COMPLETED])
        )
    )
    successful_flights = succ_res.scalar() or 0
    
    # Failed flights (INCIDENT_RETURN)
    fail_res = await db.execute(select(func.count(Mission.id)).where(Mission.status == MissionStatus.INCIDENT_RETURN))
    failed_flights = fail_res.scalar() or 0
    
    # Total Revenue (sum of delivery_fee where status is completed)
    rev_res = await db.execute(
        select(func.coalesce(func.sum(Mission.delivery_fee), 0.0)).where(
            Mission.status.in_([MissionStatus.MISSION_COMPLETED, MissionStatus.COMPLETED])
        )
    )
    total_revenue = rev_res.scalar() or 0.0
    
    # Revenue Chart (group by date)
    chart_res = await db.execute(
        select(
            cast(func.coalesce(Mission.arrived_at, Mission.departed_at), Date).label("date"),
            func.sum(Mission.delivery_fee).label("revenue")
        )
        .where(Mission.status.in_([MissionStatus.MISSION_COMPLETED, MissionStatus.COMPLETED]))
        .where(or_(Mission.arrived_at.isnot(None), Mission.departed_at.isnot(None)))
        .group_by(cast(func.coalesce(Mission.arrived_at, Mission.departed_at), Date))
        .order_by(cast(func.coalesce(Mission.arrived_at, Mission.departed_at), Date))
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


allow_config_access = RoleChecker([UserRole.ADMIN, UserRole.OPERATOR])


@router.get("/system/simulator-config", response_model=SimulatorConfigResponse, summary="Lấy cấu hình Drone Simulator hiện tại")
async def get_simulator_config(
    user: CurrentUser = Depends(allow_config_access),
):
    return SimulatorConfigResponse(**simulator_config.to_dict())


@router.patch("/system/simulator-config", response_model=SimulatorConfigResponse, summary="Cập nhật cấu hình Drone Simulator (bật/tắt sự cố, chỉnh xác suất)")
async def update_simulator_config(
    body: SimulatorConfigUpdate,
    user: CurrentUser = Depends(allow_config_access),
):
    if body.incident_enabled is not None:
        simulator_config.incident_enabled = body.incident_enabled
    if body.incident_probability is not None:
        simulator_config.incident_probability = max(0.0, min(1.0, float(body.incident_probability)))
    if body.incident_min_step is not None:
        simulator_config.incident_min_step = max(1, body.incident_min_step)
    if body.incident_severity is not None:
        simulator_config.incident_severity = body.incident_severity
    if body.interval_sec is not None:
        simulator_config.interval_sec = max(1, body.interval_sec)
    if body.steps_per_leg is not None:
        simulator_config.steps_per_leg = max(5, body.steps_per_leg)
    return SimulatorConfigResponse(**simulator_config.to_dict())

