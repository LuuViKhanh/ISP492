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
    ActivityLogListResponse,
    SystemLogListResponse,
    HubCreate,
    HubUpdate,
    HubResponse,
    HubSummaryResponse,
    HubListItemResponse,
    HubDetailResponse,
    HubAssignedDroneItem,
    HubAssignedBatteryItem,
    HubAssignedTechnician,
    HubTechnicianAssignRequest,
    HubTechnicianTransferRequest,
    HubTechnicianAssignmentResponse,
    UnassignedTechnicianItem,
    KPIDashboardResponse,
    KPIChartData,
    SimulatorConfigResponse,
    SimulatorConfigUpdate,
    SimulatorTriggerIncidentRequest,
    SimulatorTriggerIncidentResponse,
)
from app.modules.system.models import Hub, Notification, NotificationType
from app.modules.fleet.models import Drone, Battery
from app.modules.auth.models import User
from app.modules.missions.models import Mission, MissionStatus
from app.modules.system import service
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.shared.drone_simulator import simulator_config, apply_simulator_config_update

router = APIRouter(tags=["System"])

require_admin = RoleChecker([UserRole.ADMIN])


def extract_area(address: Optional[str]) -> str:
    """Trích xuất khu vực (District / TP. Thủ Đức) từ chuỗi địa chỉ."""
    if not address:
        return "Ho Chi Minh City"
    # Match real Vietnamese hub addresses stored in the DB using Unicode escapes
    match = re.search(
        r"(TP\.\s*Th\u1ee7 \u0110\u1ee9c|Th\u1ee7 \u0110\u1ee9c|B\u00ecnh Th\u1ea1nh|Qu\u1eadn\s*\d+|District\s*\d+)",
        address,
        re.IGNORECASE,
    )
    if match:
        val = match.group(1).strip()
        if "th\u1ee7 \u0111\u1ee9c" in val.lower():
            return "Thu Duc City"
        if "b\u00ecnh th\u1ea1nh" in val.lower():
            return "Binh Thanh District"
        m_q = re.search(r"Qu\u1eadn\s*(\d+)", val, re.IGNORECASE)
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


@router.get("/admin/activity-logs", response_model=ActivityLogListResponse,
            dependencies=[Depends(require_admin)],
            summary="Activity Logs — User actions")
async def get_activity_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: Optional[str] = Query(None, description="Filter by user_id or email"),
    action: Optional[str] = Query(None, description="Filter by action (LOGIN, CREATE_ORDER...)"),
    role: Optional[str] = Query(None, description="ADMIN / OPERATOR / TECHNICIAN / CUSTOMER"),
    resource_type: Optional[str] = Query(None, description="order / mission / drone / user..."),
    status: Optional[str] = Query(None, description="success / failed"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] Activity Logs** — lịch sử hành động của tất cả người dùng.

    Ghi nhận: LOGIN, REGISTER, CREATE_ORDER, CANCEL_MISSION, APPROVE_REQUEST, STOCK_RECEIVE...
    """
    return await service.get_activity_logs(db, page, page_size, user_id, action, role, resource_type, status)


@router.get("/admin/system-logs", response_model=SystemLogListResponse,
            dependencies=[Depends(require_admin)],
            summary="System Logs — Automated system events")
async def get_system_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    source: Optional[str] = Query(None, description="Worker/module name"),
    level: Optional[str] = Query(None, description="INFO / WARNING / ERROR / CRITICAL"),
    event: Optional[str] = Query(None, description="WORKER_RUN / MISSION_AUTO_CANCEL..."),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Admin] System Logs** — sự kiện tự động: workers, cron, errors nền.

    Sources: run_critical_order_notifier, run_deadline_alert_watcher, run_pickup_reminder_watcher...
    """
    return await service.get_system_logs(db, page, page_size, source, level, event)


# ── Hubs Endpoints for Admin (Hỗ trợ cả prefix /admin/system/hubs và /admin/fleet/hubs) ──

@router.get("/admin/system/hubs/summary", response_model=HubSummaryResponse, dependencies=[Depends(require_admin)])
@router.get("/admin/fleet/hubs/summary", response_model=HubSummaryResponse, dependencies=[Depends(require_admin)], include_in_schema=False)
async def get_hubs_summary(db: AsyncSession = Depends(get_async_db)):
    """Trả về 4 KPI tổng quan đầu trang Hub network map."""
    row = (await db.execute(
        select(
            select(func.count(Hub.id)).scalar_subquery(),
            select(func.count(Hub.id)).where(func.lower(Hub.status) == "active").scalar_subquery(),
            select(func.count(Drone.id)).scalar_subquery(),
            select(func.count(Battery.id)).scalar_subquery(),
        )
    )).one()

    return HubSummaryResponse(
        total_hubs=int(row[0] or 0),
        active_hubs=int(row[1] or 0),
        total_drones=int(row[2] or 0),
        total_batteries=int(row[3] or 0),
    )


@router.get("/admin/system/hubs", response_model=list[HubListItemResponse], dependencies=[Depends(require_admin)])
@router.get("/admin/fleet/hubs", response_model=list[HubListItemResponse], dependencies=[Depends(require_admin)], include_in_schema=False)
async def get_admin_hubs(
    search: Optional[str] = Query(None, description="Search by name, code, or address"),
    status: Optional[str] = Query(None, description="Filter by status (Active, Inactive)"),
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

    # Lấy danh sách drones chi tiết cho từng hub (tính luôn drone_count_map trong RAM, bỏ 1 query GROUP BY dư)
    drones_res = await db.execute(
        select(Drone).where(Drone.current_hub_id.in_(hub_ids)).order_by(Drone.id.asc())
    )
    drones_by_hub: dict[int, list[HubAssignedDroneItem]] = {}
    for d in drones_res.scalars().all():
        drones_by_hub.setdefault(d.current_hub_id, []).append(
            HubAssignedDroneItem(
                id=d.id,
                name=d.name,
                model=d.model,
                status=d.status.value if hasattr(d.status, "value") else str(d.status),
                serial_number=d.serial_number,
                battery_level_pct=d.battery_level_pct,
                payload_capacity_kg=d.payload_capacity_kg,
            )
        )
    drone_count_map = {hid: len(lst) for hid, lst in drones_by_hub.items()}

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
            serial_number=d.serial_number,
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
            battery_model=b.battery_model,
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
    from app.modules.missions.bundle_service import invalidate_planning_metadata_caches
    invalidate_planning_metadata_caches()
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
    from app.modules.missions.bundle_service import invalidate_planning_metadata_caches
    invalidate_planning_metadata_caches()
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
    from app.modules.missions.bundle_service import invalidate_planning_metadata_caches
    invalidate_planning_metadata_caches()


# ── Hub Technician Assignment APIs ────────────────────────────────────────────

@router.get(
    "/admin/fleet/technicians/unassigned",
    response_model=list[UnassignedTechnicianItem],
    summary="List Technicians not yet assigned to any Hub",
)
@router.get(
    "/admin/system/technicians/unassigned",
    response_model=list[UnassignedTechnicianItem],
    include_in_schema=False,
)
async def get_unassigned_technicians(
    user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    stmt = (
        select(User)
        .where(
            User.role_id == "TECHNICIAN",
            User.is_active == True,
            User.hub_id.is_(None),
        )
        .order_by(User.created_at.asc())
    )
    res = await db.execute(stmt)
    return res.scalars().all()


@router.post(
    "/admin/fleet/hubs/{hub_id}/technicians",
    response_model=HubTechnicianAssignmentResponse,
    summary="Assign a Technician to a Hub",
)
@router.post(
    "/admin/system/hubs/{hub_id}/technicians",
    response_model=HubTechnicianAssignmentResponse,
    include_in_schema=False,
)
async def assign_technician_to_hub(
    hub_id: int,
    body: HubTechnicianAssignRequest,
    user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail=f"Hub #{hub_id} not found.")

    tech = await db.get(User, body.technician_id)
    if not tech:
        raise HTTPException(status_code=404, detail=f"Account '{body.technician_id}' not found.")
    if tech.role_id != "TECHNICIAN":
        raise HTTPException(status_code=400, detail=f"Account '{tech.email}' does not have the TECHNICIAN role.")
    if not tech.is_active:
        raise HTTPException(status_code=400, detail=f"Technician '{tech.full_name}' is currently deactivated.")

    if tech.hub_id == hub_id:
        raise HTTPException(status_code=400, detail=f"Technician '{tech.full_name}' is already assigned to Hub #{hub_id}.")
    if tech.hub_id is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Technician '{tech.full_name}' belongs to Hub #{tech.hub_id}. Please use the Transfer endpoint."
        )

    tech.hub_id = hub_id
    now_utc = datetime.now(timezone.utc)
    db.add(Notification(
        user_id=tech.id,
        title=f"Assigned to {hub.name}",
        message=f"Admin has assigned you as technician at {hub.name} (Hub #{hub.id}).",
        notifications_type=NotificationType.SYSTEM_ALERT,
        reference_id=f"HUB_TECH_ASSIGN_{hub.id}",
        created_at=now_utc,
    ))
    await db.commit()

    await service.log_activity(
        db,
        action="HUB_TECH_ASSIGN",
        user_id=user.id,
        role=user.role.value,
        resource_type="accounts",
        resource_id=tech.id,
        description=f"Assigned Technician {tech.full_name} ({tech.email}) to Hub #{hub.id} ({hub.name}).",
        details={"hub_id": hub.id, "hub_name": hub.name, "technician_id": tech.id},
    )

    return HubTechnicianAssignmentResponse(
        message=f"Assigned Technician '{tech.full_name}' to Hub #{hub.id} ({hub.name}).",
        technician_id=tech.id,
        full_name=tech.full_name,
        email=tech.email,
        hub_id=hub.id,
        hub_name=hub.name,
        previous_hub_id=None,
    )


@router.delete(
    "/admin/fleet/hubs/{hub_id}/technicians/{technician_id}",
    response_model=HubTechnicianAssignmentResponse,
    summary="Unassign a Technician from a Hub",
)
@router.delete(
    "/admin/system/hubs/{hub_id}/technicians/{technician_id}",
    response_model=HubTechnicianAssignmentResponse,
    include_in_schema=False,
)
async def unassign_technician_from_hub(
    hub_id: int,
    technician_id: str,
    user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    hub = await db.get(Hub, hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail=f"Hub #{hub_id} not found.")

    tech = await db.get(User, technician_id)
    if not tech:
        raise HTTPException(status_code=404, detail=f"Technician '{technician_id}' not found.")
    if tech.role_id != "TECHNICIAN":
        raise HTTPException(status_code=400, detail=f"Account '{tech.email}' does not have the TECHNICIAN role.")
    if tech.hub_id != hub_id:
        raise HTTPException(
            status_code=400,
            detail=f"Technician '{tech.full_name}' does not belong to Hub #{hub_id} (current Hub: {tech.hub_id})."
        )

    prev_hub_id = tech.hub_id
    tech.hub_id = None
    now_utc = datetime.now(timezone.utc)
    db.add(Notification(
        user_id=tech.id,
        title=f"Unassigned from {hub.name}",
        message=f"Admin has removed your assignment from {hub.name} (Hub #{hub.id}).",
        notifications_type=NotificationType.SYSTEM_ALERT,
        reference_id=f"HUB_TECH_UNASSIGN_{hub.id}",
        created_at=now_utc,
    ))
    await db.commit()

    await service.log_activity(
        db,
        action="HUB_TECH_UNASSIGN",
        user_id=user.id,
        role=user.role.value,
        resource_type="accounts",
        resource_id=tech.id,
        description=f"Unassigned Technician {tech.full_name} from Hub #{hub.id} ({hub.name}).",
        details={"previous_hub_id": prev_hub_id, "technician_id": tech.id},
    )

    return HubTechnicianAssignmentResponse(
        message=f"Unassigned Technician '{tech.full_name}' from Hub #{hub.id} ({hub.name}).",
        technician_id=tech.id,
        full_name=tech.full_name,
        email=tech.email,
        hub_id=None,
        hub_name=None,
        previous_hub_id=prev_hub_id,
    )


@router.put(
    "/admin/fleet/hubs/{hub_id}/technicians/{technician_id}/transfer",
    response_model=HubTechnicianAssignmentResponse,
    summary="Transfer a Technician to another Hub",
)
@router.put(
    "/admin/system/hubs/{hub_id}/technicians/{technician_id}/transfer",
    response_model=HubTechnicianAssignmentResponse,
    include_in_schema=False,
)
async def transfer_technician_hub(
    hub_id: int,
    technician_id: str,
    body: HubTechnicianTransferRequest,
    user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if body.new_hub_id == hub_id:
        raise HTTPException(status_code=400, detail="New Hub cannot be the same as the current Hub.")

    old_hub = await db.get(Hub, hub_id)
    new_hub = await db.get(Hub, body.new_hub_id)
    if not old_hub:
        raise HTTPException(status_code=404, detail=f"Source Hub #{hub_id} not found.")
    if not new_hub:
        raise HTTPException(status_code=404, detail=f"Destination Hub #{body.new_hub_id} not found.")

    tech = await db.get(User, technician_id)
    if not tech:
        raise HTTPException(status_code=404, detail=f"Technician '{technician_id}' not found.")
    if tech.role_id != "TECHNICIAN":
        raise HTTPException(status_code=400, detail=f"Account '{tech.email}' does not have the TECHNICIAN role.")
    if tech.hub_id != hub_id:
        raise HTTPException(
            status_code=400,
            detail=f"Technician '{tech.full_name}' does not belong to source Hub #{hub_id} (current Hub: {tech.hub_id})."
        )

    tech.hub_id = new_hub.id
    now_utc = datetime.now(timezone.utc)
    db.add(Notification(
        user_id=tech.id,
        title=f"Transferred to {new_hub.name}",
        message=f"Admin has transferred you from {old_hub.name} to {new_hub.name} (Hub #{new_hub.id}).",
        notifications_type=NotificationType.SYSTEM_ALERT,
        reference_id=f"HUB_TECH_TRANSFER_{new_hub.id}",
        created_at=now_utc,
    ))
    await db.commit()

    await service.log_activity(
        db,
        action="HUB_TECH_TRANSFER",
        user_id=user.id,
        role=user.role.value,
        resource_type="accounts",
        resource_id=tech.id,
        description=f"Transferred Technician {tech.full_name} from Hub #{old_hub.id} ({old_hub.name}) to Hub #{new_hub.id} ({new_hub.name}).",
        details={"previous_hub_id": old_hub.id, "new_hub_id": new_hub.id, "technician_id": tech.id},
    )

    return HubTechnicianAssignmentResponse(
        message=f"Transferred Technician '{tech.full_name}' to Hub #{new_hub.id} ({new_hub.name}).",
        technician_id=tech.id,
        full_name=tech.full_name,
        email=tech.email,
        hub_id=new_hub.id,
        hub_name=new_hub.name,
        previous_hub_id=old_hub.id,
    )


@router.get("/admin/dashboard/kpi", response_model=KPIDashboardResponse, dependencies=[Depends(require_admin)])
async def get_dashboard_kpi(db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import case
    comp_statuses = [MissionStatus.MISSION_COMPLETED, MissionStatus.ARRIVAL_COMPLETED]
    # Gộp toàn bộ scalar KPIs + tổng doanh thu vào 1 câu SQL
    kpi_row = (await db.execute(
        select(
            select(func.count(Drone.id)).scalar_subquery(),
            select(func.count(Battery.id)).scalar_subquery(),
            select(func.count(Mission.id)).where(Mission.status.in_(comp_statuses)).scalar_subquery(),
            select(func.count(Mission.id)).where(Mission.status == MissionStatus.INCIDENT_RETURN).scalar_subquery(),
            select(func.coalesce(func.sum(Mission.delivery_fee), 0.0)).where(Mission.status.in_(comp_statuses)).scalar_subquery(),
        )
    )).one()
    total_drones = int(kpi_row[0] or 0)
    total_batteries = int(kpi_row[1] or 0)
    successful_flights = int(kpi_row[2] or 0)
    failed_flights = int(kpi_row[3] or 0)
    gross_revenue = float(kpi_row[4] or 0.0)

    from app.modules.system.service import compute_total_procurement_expenses
    procurement_expenses = await compute_total_procurement_expenses(db)
    total_revenue = round(gross_revenue - procurement_expenses, 2)

    # Revenue Chart (group by date)
    chart_res = await db.execute(
        select(
            cast(func.coalesce(Mission.arrived_at, Mission.departed_at), Date).label("date"),
            func.sum(Mission.delivery_fee).label("revenue")
        )
        .where(Mission.status.in_(comp_statuses))
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


@router.get("/system/simulator-config", response_model=SimulatorConfigResponse, summary="Get current Drone Simulator configuration")
async def get_simulator_config(
    user: CurrentUser = Depends(allow_config_access),
):
    return SimulatorConfigResponse(**simulator_config.to_dict())


@router.patch("/system/simulator-config", response_model=SimulatorConfigResponse, summary="Update Drone Simulator configuration (toggle incidents, adjust probability)")
async def update_simulator_config(
    body: SimulatorConfigUpdate,
    user: CurrentUser = Depends(allow_config_access),
):
    try:
        cfg = apply_simulator_config_update(
            incident_enabled=body.incident_enabled,
            incident_probability=body.incident_probability,
            incident_min_step=body.incident_min_step,
            incident_severity=body.incident_severity,
            interval_sec=body.interval_sec,
            steps_per_leg=body.steps_per_leg,
            next_flight_incident=body.next_flight_incident,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return SimulatorConfigResponse(**cfg)


@router.post(
    "/system/simulator-config/trigger-next-incident",
    response_model=SimulatorTriggerIncidentResponse,
    summary="Trigger a one-time incident for the next flight (auto-resets to SUCCESS afterward)",
)
async def trigger_next_flight_incident_admin(
    body: Optional[SimulatorTriggerIncidentRequest] = None,
    user: CurrentUser = Depends(allow_config_access),
):
    """
    When enabled, only the next dispatched flight will encounter an incident,
    and subsequent flights will automatically reset to normal operation (100% SUCCESS).
    """
    req = body or SimulatorTriggerIncidentRequest()
    try:
        cfg = simulator_config.arm_next_flight_incident(
            enabled=req.enabled,
            severity=req.severity,
            trigger_step=req.trigger_step,
            incident_type=req.incident_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    msg = (
        f"Armed incident scenario ({cfg['next_flight_incident_type']} • {cfg['next_flight_incident_severity']}) "
        f"for the next flight only; subsequent flights will automatically return to normal SUCCESS."
        if req.enabled
        else "Disarmed next-flight incident scenario; upcoming flights will run normally with SUCCESS."
    )
    return SimulatorTriggerIncidentResponse(message=msg, config=SimulatorConfigResponse(**cfg))

