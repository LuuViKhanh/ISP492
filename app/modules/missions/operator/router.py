from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from datetime import timedelta, datetime, timezone
import math

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.core.config import settings
from app.modules.missions.models import Mission, MissionStatus, HandlingStatus, Location, LocationType, Incident, IncidentStatus, IncidentSeverity, TelemetryLog, MissionHubCheckpoint
from app.modules.system.models import Hub
from app.modules.fleet.models import MaintenanceAlert
import heapq
from app.modules.missions.schemas import (
    MissionResponse, ApproveRejectRequest,
    TelemetryDataCreate, TelemetryDataResponse,
    LiveTrackingResponse, LiveTrackingDrone,
    HubCheckpointResponse, TelemetryIngestResponse,
)
from app.modules.fleet.models import Drone, Battery, DroneStatus, batteries_status
from app.modules.fleet.schemas import CheckAvailabilityRequest, AvailabilityResponse, AvailableBatterySchema

router = APIRouter(prefix="/operator/missions", tags=["Missions"])

allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])


'''
@router.post(
    "/check-availability",
    response_model=AvailabilityResponse,
    summary="Kiểm tra Drone và Pin khả dụng cho chuyến bay",
)
async def check_fleet_availability(
    request: CheckAvailabilityRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Kiểm tra và trả về danh sách các Drone và Pin (Battery) khả dụng cho một chuyến bay dự kiến.
    
    API này đánh giá thời gian bắt đầu, tải trọng, và năng lượng tiêu thụ ước tính của chuyến bay 
    để lọc ra các thiết bị đang không bị bận trong khoảng thời gian đó, và đáp ứng đủ yêu cầu kỹ thuật.
    """
    estimated_duration = timedelta(hours=1)
    requested_start = request.scheduled_time.replace(tzinfo=None)
    requested_end = requested_start + estimated_duration

    busy_missions_query = await db.execute(
        select(Mission.drone_id, Mission.battery_id).where(
            and_(
                Mission.status.in_([MissionStatus.APPROVED, MissionStatus.FLYING]),
                Mission.scheduled_time.is_not(None),
                Mission.scheduled_time < requested_end,
                or_(
                    and_(Mission.end_time.is_not(None), Mission.end_time > requested_start),
                    and_(Mission.end_time.is_(None), Mission.scheduled_time + timedelta(hours=1) > requested_start)
                )
            )
        )
    )
    busy_rows = busy_missions_query.all()
    busy_drone_ids = [row[0] for row in busy_rows if row[0] is not None]
    busy_battery_ids = [row[1] for row in busy_rows if row[1] is not None]

    drones_query = select(Drone).where(
        and_(
            Drone.status == DroneStatus.AVAILABLE,
            Drone.payload_capacity_kg >= request.payload_weight_kg,
            Drone.id.not_in(busy_drone_ids) if busy_drone_ids else True
        )
    )
    available_drones = (await db.execute(drones_query)).scalars().all()

    battery_conditions = [
        Battery.status == batteries_status.ACTIVE,
        Battery.id.not_in(busy_battery_ids) if busy_battery_ids else True
    ]
    if request.estimated_energy_wh:
        battery_conditions.append(Battery.capacity_wh >= request.estimated_energy_wh * 1.2)

    available_batteries = (await db.execute(select(Battery).where(and_(*battery_conditions)))).scalars().all()

    return AvailabilityResponse(
        available_drones=available_drones,
        available_batteries=[AvailableBatterySchema.from_battery(b) for b in available_batteries],
    )


@router.get("/planned", response_model=list[MissionResponse])
async def get_planned_missions(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """Lấy danh sách các nhiệm vụ bay đang chờ phê duyệt."""
    result = await db.execute(
        select(Mission)
        .where(Mission.status == MissionStatus.PENDING_APPROVAL)
        .order_by(Mission.scheduled_time)
    )
    return result.scalars().all()
'''


@router.get("/hubs")
async def list_hubs(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """Lấy danh sách toàn bộ các trung tâm điều khiển (Hub) chính."""
    result = await db.execute(select(Hub).order_by(Hub.id))
    hubs = result.scalars().all()
    return [{"id": h.id, "code": h.code, "name": h.name, "address": h.address, "latitude": h.latitude, "longitude": h.longitude, "status": h.status} for h in hubs]


@router.get("/mini-hubs")
async def list_mini_hubs(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """Lấy danh sách các Hub phụ (Mini-hubs)."""
    result = await db.execute(
        select(Location).where(Location.type == LocationType.HUB).order_by(Location.id)
    )
    locations = result.scalars().all()
    return [{"id": l.id, "name": l.name, "latitude": l.latitude, "longitude": l.longitude, "type": l.type.value} for l in locations]


@router.get(
    "/live-tracking",
    response_model=LiveTrackingResponse,
    summary="Live map — tất cả drone đang bay theo thời gian thực",
)
async def get_live_tracking(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Trả về snapshot thời gian thực của **toàn bộ drone đang ở trạng thái FLYING**.
    **Polling pattern**: Frontend gọi endpoint này mỗi **3–5 giây**.
    Trả về `active_count = 0` và `drones = []` khi không có drone nào đang bay.
    """
    flying_result = await db.execute(
        select(Mission).where(Mission.status == MissionStatus.FLYING)
    )
    flying_missions = flying_result.scalars().all()

    if not flying_missions:
        return LiveTrackingResponse(active_count=0, drones=[])

    mission_ids = [m.id for m in flying_missions]

    latest_log_subq = (
        select(
            TelemetryLog.mission_id,
            func.max(TelemetryLog.id).label("max_id"),
        )
        .where(TelemetryLog.mission_id.in_(mission_ids))
        .group_by(TelemetryLog.mission_id)
        .subquery()
    )

    latest_logs_result = await db.execute(
        select(TelemetryLog).join(
            latest_log_subq,
            and_(
                TelemetryLog.mission_id == latest_log_subq.c.mission_id,
                TelemetryLog.id == latest_log_subq.c.max_id,
            ),
        )
    )
    latest_logs = {log.mission_id: log for log in latest_logs_result.scalars().all()}

    cp_counts_result = await db.execute(
        select(
            MissionHubCheckpoint.mission_id,
            func.count(MissionHubCheckpoint.id).label("cnt"),
        )
        .where(MissionHubCheckpoint.mission_id.in_(mission_ids))
        .group_by(MissionHubCheckpoint.mission_id)
    )
    cp_counts = {row.mission_id: row.cnt for row in cp_counts_result.all()}

    drones = []
    for mission in flying_missions:
        log = latest_logs.get(mission.id)
        drones.append(
            LiveTrackingDrone(
                mission_id=mission.id,
                drone_id=mission.drone_id,
                status=mission.status,
                latest_lat=log.latitude if log else None,
                latest_lng=log.longitude if log else None,
                latest_altitude=log.altitude if log else None,
                latest_speed=log.speed if log else None,
                latest_battery_voltage=log.battery_voltage if log else None,
                last_updated=log.timestamp if log else None,
                checkpoints_passed=cp_counts.get(mission.id, 0),
                # Weather từ telemetry log mới nhất
                weather_temperature=log.weather_temperature if log else None,
                weather_apparent_temp=log.weather_apparent_temp if log else None,
                weather_dew_point=log.weather_dew_point if log else None,
                weather_humidity=log.weather_humidity if log else None,
                weather_wind_speed=log.weather_wind_speed if log else None,
                weather_wind_gust=log.weather_wind_gust if log else None,
                weather_wind_direction=log.weather_wind_direction if log else None,
                weather_precipitation=log.weather_precipitation if log else None,
                weather_pressure=log.weather_pressure if log else None,
                weather_cloud_cover=log.weather_cloud_cover if log else None,
            )
        )

    return LiveTrackingResponse(active_count=len(drones), drones=drones)


# ── Incidents ────────────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class IncidentCreate(BaseModel):
    mission_id: Optional[str] = None
    drone_id: Optional[int] = None
    severity: IncidentSeverity
    description: str
    requires_technical_inspection: bool = False

class IncidentResponse(BaseModel):
    id: int
    mission_id: Optional[str]
    drone_id: Optional[int]
    reporter_id: Optional[str]
    severity: IncidentSeverity
    description: str
    status: IncidentStatus
    reported_at: Optional[datetime]
    requires_technical_inspection: bool

    class Config:
        from_attributes = True


@router.get("/incidents", response_model=list[IncidentResponse])
async def list_incidents(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(select(Incident).order_by(Incident.id.desc()))
    return result.scalars().all()


@router.get("/incidents/{incident_id}", response_model=IncidentResponse)
async def get_incident(
    incident_id: int,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.post("/incidents", response_model=IncidentResponse, status_code=201)
async def create_incident(
    body: IncidentCreate,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    from datetime import timezone
    now = datetime.now(timezone.utc)
    incident = Incident(
        mission_id=body.mission_id,
        drone_id=body.drone_id,
        reporter_id=user.id,
        severity=body.severity,
        description=body.description,
        status=IncidentStatus.OPEN,
        reported_at=now,
        requires_technical_inspection=body.requires_technical_inspection,
    )
    db.add(incident)
    await db.flush()

    # Tự động tạo maintenance_alert nếu cần kiểm tra kỹ thuật
    if body.requires_technical_inspection:
        alert = MaintenanceAlert(
            drone_id=body.drone_id,
            source="INCIDENT",
            incident_id=incident.id,
            title=f"Incident #{incident.id}: {body.description[:50]}",
            status="PENDING",
            created_at=now.replace(tzinfo=None),
        )
        db.add(alert)

    await db.commit()
    await db.refresh(incident)
    return incident


@router.patch("/incidents/{incident_id}/status", response_model=IncidentResponse)
async def update_incident_status(
    incident_id: int,
    status: IncidentStatus,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    incident = await db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.status = status
    await db.commit()
    await db.refresh(incident)
    return incident


@router.get("/{mission_id}", response_model=MissionResponse, summary="Lấy thông tin chi tiết một mission")
async def get_mission(
    mission_id: str,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """Trả về toàn bộ thông tin mission kèm chi tiết tuyến đường đã lên lịch (giống cấu trúc mission-planning/analyze)."""
    mission = await db.get(Mission, mission_id)

    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return await build_mission_detail_response(db, mission)


# ── Transit Confirm ───────────────────────────────────────────────────────────

class TransitConfirmRequest(BaseModel):
    note: str = ""


class TransitConfirmResponse(BaseModel):
    mission_id: str
    confirmed_leg_id: int
    confirmed_leg_sequence: int
    next_leg_id: int
    next_leg_sequence: int
    relay_hub_id: int
    relay_hub_name: str
    mission_status: str
    handling_status: str
    message: str


@router.post("/{mission_id}/transit-confirm", response_model=TransitConfirmResponse)
async def transit_confirm(
    mission_id: str,
    body: TransitConfirmRequest = TransitConfirmRequest(),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator] Xác nhận transit relay hub — cho phép chuyến bay tiếp tục chặng kế tiếp.**

    Đây là bước Operator authorize sau khi Technician đã hoàn tất chuẩn bị tại relay hub
    (confirm-relay-arrival → swap-battery → dispatch đã do Technician thực hiện, hoặc
    Operator có thể tự kích hoạt toàn bộ luồng này nếu không cần swap pin).

    **Điều kiện:** Mission có ít nhất 2 MissionLegs (multi-hop route), và có leg đang
    PENDING chờ bay tiếp.

    **Luồng xử lý:**
    1. Validate: mission phải đang FLYING / IN_PROGRESS / AT_HUB (handling).
    2. Tìm leg COMPLETED gần nhất để biết relay hub hiện tại.
    3. Tìm leg PENDING tiếp theo để kích hoạt.
    4. Cập nhật next_leg → IN_PROGRESS, started_at = now.
    5. Cập nhật mission.status → FLYING, handling_status → INCOMING.
    6. Cập nhật drone.status → In Flight, drone.current_hub_id → None.
    7. Ghi AuditLog + Notify Operators.
    """
    from app.modules.missions.models import MissionLeg, MissionLegStatus
    from app.modules.fleet.models import DroneStatus
    from app.modules.system.models import Notification, NotificationType
    from app.modules.auth.models import User, ROLE_NAME_TO_ID
    from app.shared.roles import UserRole as _Role

    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    # Validate trạng thái mission
    valid_statuses = {
        MissionStatus.FLYING,
        MissionStatus.IN_PROGRESS,
        MissionStatus.ACTIVE_MISSION,
        MissionStatus.APPROVED,
        MissionStatus.SCHEDULED,
    }
    if mission.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Mission đang ở trạng thái '{mission.status.value}' — không thể transit-confirm. "
                f"Chỉ áp dụng cho mission đang hoạt động."
            ),
        )

    # Lấy tất cả legs theo thứ tự
    legs_result = await db.execute(
        select(MissionLeg)
        .where(MissionLeg.mission_id == mission.id)
        .order_by(MissionLeg.sequence_no)
    )
    legs = legs_result.scalars().all()

    if len(legs) < 2:
        raise HTTPException(
            status_code=400,
            detail="Đây là tuyến bay thẳng (1 leg) — không có relay hub cần transit-confirm. "
                   "Dùng confirm-arrival để kết thúc chuyến bay.",
        )

    # Tìm next_leg PENDING đầu tiên
    next_leg = next(
        (leg for leg in legs if leg.status == MissionLegStatus.PENDING), None
    )
    if next_leg is None:
        raise HTTPException(
            status_code=400,
            detail="Không tìm thấy leg PENDING nào. Tất cả các chặng đã hoàn thành "
                   "hoặc mission đã kết thúc.",
        )

    # Leg vừa completed (chặng ngay trước next_leg)
    completed_leg = next(
        (leg for leg in reversed(legs) if leg.status == MissionLegStatus.COMPLETED),
        None,
    )
    if completed_leg is None:
        raise HTTPException(
            status_code=400,
            detail="Chưa có chặng nào được hoàn thành. Technician cần confirm-relay-arrival trước.",
        )

    relay_hub_id = completed_leg.to_hub_id
    if not relay_hub_id:
        raise HTTPException(status_code=400, detail="Không xác định được relay hub.")

    relay_hub = await db.get(Hub, relay_hub_id)
    relay_hub_name = relay_hub.name if relay_hub else str(relay_hub_id)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # ── 1. Kích hoạt leg kế tiếp ─────────────────────────────────────────────
    next_leg.status = MissionLegStatus.IN_PROGRESS
    next_leg.started_at = now

    # ── 2. Cập nhật mission → FLYING ─────────────────────────────────────────
    mission.status = MissionStatus.FLYING
    mission.handling_status = HandlingStatus.INCOMING  # drone đang bay, rời relay hub

    # ── 3. Cập nhật drone → In Flight ────────────────────────────────────────
    if mission.drone_id:
        drone = await db.get(Drone, mission.drone_id)
        if drone:
            drone.status = DroneStatus.IN_FLIGHT
            drone.current_hub_id = None  # đang bay, không còn ở hub

    # ── 4. Notify Operators ───────────────────────────────────────────────────
    op_role_id = ROLE_NAME_TO_ID.get(_Role.OPERATOR)
    op_result = await db.execute(select(User.id).where(User.role_id == op_role_id))
    operator_ids = op_result.scalars().all()

    notif_msg = (
        f"Operator {user.id} đã xác nhận transit tại {relay_hub_name}. "
        f"Mission #{mission_id} đang bay chặng {next_leg.sequence_no}/{len(legs)} "
        f"đến hub #{next_leg.to_hub_id}."
        + (f" Ghi chú: {body.note}" if body.note else "")
    )
    for op_id in operator_ids:
        db.add(Notification(
            user_id=op_id,
            title=f"✈️ Transit Confirmed — Mission #{mission_id}",
            message=notif_msg,
            notifications_type=NotificationType.MISSION_STATUS,
            mission_id=str(mission.id),
            created_at=now,
        ))

    await db.commit()

    return TransitConfirmResponse(
        mission_id=str(mission.id),
        confirmed_leg_id=completed_leg.id,
        confirmed_leg_sequence=completed_leg.sequence_no or 0,
        next_leg_id=next_leg.id,
        next_leg_sequence=next_leg.sequence_no or 0,
        relay_hub_id=relay_hub_id,
        relay_hub_name=relay_hub_name,
        mission_status=mission.status.value,
        handling_status=mission.handling_status.value if mission.handling_status else "",
        message=(
            f"Transit confirmed. Mission #{mission_id} đang bay chặng "
            f"{next_leg.sequence_no}/{len(legs)} từ {relay_hub_name} → Hub #{next_leg.to_hub_id}."
        ),
    )


# ── Emergency Land ────────────────────────────────────────────────────────────

class EmergencyLandRequest(BaseModel):
    reason: str = "Operator yêu cầu hạ cánh khẩn cấp"


class EmergencyLandResponse(BaseModel):
    mission_id: str
    previous_status: str
    current_status: str
    incident_id: int
    alert_id: int
    message: str


@router.post("/{mission_id}/emergency-land", response_model=EmergencyLandResponse)
async def emergency_land(
    mission_id: str,
    body: EmergencyLandRequest = EmergencyLandRequest(),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator] Kích hoạt hạ cánh khẩn cấp cho một chuyến bay đang hoạt động.**

    Luồng xử lý:
    1. Validate: mission phải đang ở trạng thái FLYING / IN_PROGRESS / ACTIVE_MISSION.
    2. Chuyển mission status → `INCIDENT_RETURN`.
    3. Tạo Incident (severity=HIGH, status=OPEN) gắn với mission và drone.
    4. Tạo MaintenanceAlert yêu cầu kiểm tra kỹ thuật drone.
    5. Đặt drone về trạng thái MAINTENANCE.
    6. Gửi thông báo cho tất cả Operators và Customer của đơn hàng.
    """
    from pydantic import BaseModel as _BM
    from app.modules.system.models import Notification, NotificationType
    from app.modules.auth.models import User, ROLE_NAME_TO_ID
    from app.shared.roles import UserRole as _Role

    # ── 1. Lấy và validate mission ──────────────────────────────────────────
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    active_statuses = {
        MissionStatus.FLYING,
        MissionStatus.IN_PROGRESS,
        MissionStatus.ACTIVE_MISSION,
        MissionStatus.APPROVED,
    }
    if mission.status not in active_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Không thể emergency-land mission đang ở trạng thái '{mission.status.value}'. "
                   f"Chỉ áp dụng cho mission đang hoạt động (Flying/In Progress/Approved)."
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    previous_status = mission.status.value

    # ── 2. Chuyển mission → INCIDENT_RETURN ─────────────────────────────────
    mission.status = MissionStatus.INCIDENT_RETURN
    mission.end_time = now

    # ── 3. Tạo Incident HIGH severity ───────────────────────────────────────
    incident = Incident(
        mission_id=mission.id,
        drone_id=mission.drone_id,
        reporter_id=user.id,
        severity=IncidentSeverity.HIGH,
        description=f"[EMERGENCY LAND] Mission #{mission_id} — {body.reason}",
        status=IncidentStatus.OPEN,
        reported_at=now,
        requires_technical_inspection=True,
    )
    db.add(incident)
    await db.flush()  # lấy incident.id

    # ── 4. Tạo MaintenanceAlert yêu cầu kiểm tra drone ──────────────────────
    alert = MaintenanceAlert(
        drone_id=mission.drone_id,
        source="EmergencyLand",
        incident_id=incident.id,
        mission_id=str(mission.id),
        title=f"Hạ cánh khẩn cấp — Drone #{mission.drone_id} cần kiểm tra ngay",
        status="Pending",
        created_at=now,
    )
    db.add(alert)
    await db.flush()  # lấy alert.id

    # ── 5. Đặt drone MAINTENANCE ─────────────────────────────────────────────
    if mission.drone_id:
        drone = await db.get(Drone, mission.drone_id)
        if drone:
            drone.status = DroneStatus.MAINTENANCE

    # ── 6. Notifications ─────────────────────────────────────────────────────
    # Lấy danh sách Operator IDs
    op_role_id = ROLE_NAME_TO_ID.get(_Role.OPERATOR)
    op_result = await db.execute(
        select(User.id).where(User.role_id == op_role_id)
    )
    operator_ids = op_result.scalars().all()

    notif_title = f"🚨 HẠ CÁNH KHẨN CẤP — Mission #{mission_id}"
    notif_msg = (
        f"Operator {user.id} đã kích hoạt emergency-land cho Mission #{mission_id} "
        f"(Drone #{mission.drone_id}). Lý do: {body.reason}. "
        f"Drone đã được chuyển sang chế độ MAINTENANCE."
    )

    for op_id in operator_ids:
        db.add(Notification(
            user_id=op_id,
            title=notif_title,
            message=notif_msg,
            notifications_type=NotificationType.SYSTEM_ALERT,
            mission_id=str(mission.id),
            reference_id=str(incident.id),
            created_at=now,
        ))

    # Notify Customer nếu có
    if mission.customer_id:
        db.add(Notification(
            user_id=mission.customer_id,
            title="Chuyến bay của bạn gặp sự cố",
            message=(
                f"Chuyến bay #{mission_id} đang xử lý sự cố khẩn cấp. "
                f"Đội ngũ của chúng tôi đang xử lý và sẽ cập nhật sớm nhất có thể."
            ),
            notifications_type=NotificationType.MISSION_STATUS,
            mission_id=str(mission.id),
            created_at=now,
        ))

    await db.commit()

    return EmergencyLandResponse(
        mission_id=str(mission.id),
        previous_status=previous_status,
        current_status=MissionStatus.INCIDENT_RETURN.value,
        incident_id=incident.id,
        alert_id=alert.id,
        message=(
            f"Emergency landing kích hoạt thành công. "
            f"Incident #{incident.id} và MaintenanceAlert #{alert.id} đã được tạo. "
            f"Drone #{mission.drone_id} chuyển sang MAINTENANCE."
        ),
    )



'''
@router.post("/{mission_id}/approve", response_model=MissionResponse)
async def approve_mission(
    mission_id: int,
    body: ApproveRejectRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Phê duyệt một nhiệm vụ bay đang chờ xử lý.
    
    API này cho phép người điều hành chấp thuận chuyến bay (chuyển sang trạng thái APPROVED). 
    Có thể đồng thời cập nhật thông tin thiết bị (drone, pin) và người điều hành chính (operator_id) 
    chịu trách nhiệm cho nhiệm vụ này.
    """
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.status != MissionStatus.PENDING_APPROVAL:
        raise HTTPException(status_code=400, detail=f"Cannot approve mission with status '{mission.status.value}'")
    mission.status = MissionStatus.APPROVED
    if body.drone_id:
        mission.drone_id = body.drone_id
    if body.battery_id:
        mission.battery_id = body.battery_id
    if body.operator_id:
        mission.operator_id = body.operator_id
    await db.commit()
    await db.refresh(mission)
    return mission


@router.post("/{mission_id}/reject", response_model=MissionResponse)
async def reject_mission(
    mission_id: int,
    body: ApproveRejectRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Từ chối một nhiệm vụ bay đang chờ xử lý.
    
    API này được sử dụng khi người điều hành phát hiện yêu cầu bay không hợp lệ hoặc thiếu 
    an toàn, chuyển trạng thái chuyến bay sang REJECTED.
    """
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.status != MissionStatus.PENDING_APPROVAL:
        raise HTTPException(status_code=400, detail=f"Cannot reject mission with status '{mission.status.value}'")
    mission.status = MissionStatus.REJECTED
    await db.commit()
    await db.refresh(mission)
    return mission
'''


@router.post("/{mission_id}/telemetry", response_model=TelemetryIngestResponse)
async def collect_telemetry(
    mission_id: str,
    telemetry_data: list[TelemetryDataCreate],
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    mission = await db.get(Mission, mission_id)

    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
        
    actual_mission_id = mission.id

    if mission.status not in (MissionStatus.APPROVED, MissionStatus.FLYING):
        raise HTTPException(
            status_code=400,
            detail=f"Không thể nhận telemetry cho mission trạng thái '{mission.status.value}'. "
                   f"Chỉ chấp nhận APPROVED hoặc FLYING."
        )

    # ── 1. Xử lý chuyển trạng thái APPROVED → FLYING ───────────────────────────
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if mission.status == MissionStatus.APPROVED:
        mission.status = MissionStatus.FLYING
        mission.start_time = now
        mission.departed_at = now

        # ── Cập nhật Order status → IN_DELIVERY ────────────────────────────────
        try:
            from app.modules.missions.models import Order as OrderModel
            if mission.order_id:
                order_result = await db.execute(
                    select(OrderModel).where(OrderModel.id == mission.order_id)
                )
                order_obj = order_result.scalar_one_or_none()
                if order_obj:
                    order_obj.status = OrderStatus.IN_DELIVERY
                    order_obj.updated_at = now
        except Exception as email_err:
            print(f"[ORDER] Lỗi khi cập nhật order status IN_DELIVERY: {email_err}")

    # ── 2. Fetch weather + Lưu telemetry logs ─────────────────────────────────
    from app.shared.weather import fetch_weather

    logs_to_insert: list[TelemetryLog] = []
    for data in telemetry_data:
        naive_timestamp = data.timestamp.replace(tzinfo=None) if data.timestamp.tzinfo else data.timestamp

        # Fetch weather theo tọa độ điểm này (timeout 3s, không throw nếu lỗi)
        weather = await fetch_weather(data.latitude, data.longitude)

        log_entry = TelemetryLog(
            mission_id=actual_mission_id,
            timestamp=naive_timestamp,
            latitude=data.latitude,
            longitude=data.longitude,
            altitude=data.altitude,
            speed=data.speed,
            battery_voltage=data.battery_voltage,
            energy_consumed_wh=data.energy_consumed_wh,
            wind_speed=data.wind_speed,
            # Weather fields từ Open-Meteo
            weather_temperature=weather.temperature if weather else None,
            weather_apparent_temp=weather.apparent_temperature if weather else None,
            weather_dew_point=weather.dew_point if weather else None,
            weather_humidity=weather.humidity if weather else None,
            weather_wind_speed=weather.wind_speed if weather else None,
            weather_wind_gust=weather.wind_gust if weather else None,
            weather_wind_direction=weather.wind_direction if weather else None,
            weather_precipitation=weather.precipitation if weather else None,
            weather_pressure=weather.pressure if weather else None,
            weather_cloud_cover=weather.cloud_cover if weather else None,
        )
        logs_to_insert.append(log_entry)
        db.add(log_entry)

    # ── 3. Tải hubs + mini-hubs để detect proximity ────────────────────────────
    hubs_result = await db.execute(
        select(Hub).where(and_(Hub.latitude.isnot(None), Hub.longitude.isnot(None)))
    )
    all_hubs = hubs_result.scalars().all()  # [(id, lat, lng, name, is_mini=False)]

    mini_hubs_result = await db.execute(
        select(Location).where(Location.type == LocationType.HUB)
    )
    all_mini_hubs = mini_hubs_result.scalars().all()

    # ── 4. Đếm số checkpoint đã có để gán thứ tự tiếp theo ─────────────────────
    cp_count_result = await db.execute(
        select(func.count()).select_from(MissionHubCheckpoint)
        .where(MissionHubCheckpoint.mission_id == actual_mission_id)
    )
    existing_cp_count = cp_count_result.scalar_one() or 0
    next_order = existing_cp_count + 1

    # ── 5. Detect hub proximity cho từng điểm telemetry ────────────────────────
    HUB_PROXIMITY_RADIUS_M = 200.0   # Bán kính tính từ tâm hub (m)
    COOLDOWN_SECONDS = 60             # Tránh log trùng cho cùng 1 hub liên tiếp

    new_checkpoints: list[MissionHubCheckpoint] = []

    # Lấy checkpoint gần nhất để cooldown check
    last_cp_result = await db.execute(
        select(MissionHubCheckpoint)
        .where(MissionHubCheckpoint.mission_id == actual_mission_id)
        .order_by(MissionHubCheckpoint.passed_at.desc())
        .limit(1)
    )
    last_cp = last_cp_result.scalar_one_or_none()

    def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Tính khoảng cách Haversine (mét) giữa hai toạ độ WGS-84."""
        R = 6_371_000  # Bán kính Trái Đất (m)
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    for data in telemetry_data:
        pt_ts = data.timestamp.replace(tzinfo=None) if data.timestamp.tzinfo else data.timestamp

        # Gom tất cả hub candidates: (hub_id, location_id, name, lat, lng)
        candidates = [
            (h.id, None, h.name or f"Hub #{h.id}", h.latitude, h.longitude)
            for h in all_hubs
        ] + [
            (None, loc.id, loc.name or f"Mini-hub #{loc.id}", loc.latitude, loc.longitude)
            for loc in all_mini_hubs
        ]

        for hub_id, loc_id, hub_name, hub_lat, hub_lng in candidates:
            dist = haversine_m(data.latitude, data.longitude, hub_lat, hub_lng)
            if dist > HUB_PROXIMITY_RADIUS_M:
                continue  # Không trong vùng

            # Cooldown: kiểm tra xem hub này đã được log trong 60s qua chưa
            recent_cp_result = await db.execute(
                select(MissionHubCheckpoint).where(
                    and_(
                        MissionHubCheckpoint.mission_id == actual_mission_id,
                        MissionHubCheckpoint.hub_id == hub_id if hub_id else MissionHubCheckpoint.location_id == loc_id,
                        MissionHubCheckpoint.passed_at >= (
                            pt_ts.replace(tzinfo=None) - timedelta(seconds=COOLDOWN_SECONDS)
                        ),
                    )
                ).limit(1)
            )
            if recent_cp_result.scalar_one_or_none():
                continue  # Đã log gần đây, bỏ qua

            cp = MissionHubCheckpoint(
                mission_id=actual_mission_id,
                hub_id=hub_id,
                location_id=loc_id,
                hub_name=hub_name,
                hub_latitude=hub_lat,
                hub_longitude=hub_lng,
                drone_latitude=data.latitude,
                drone_longitude=data.longitude,
                distance_to_hub_m=round(dist, 2),
                passed_at=pt_ts,
                checkpoint_order=next_order,
            )
            db.add(cp)
            new_checkpoints.append(cp)
            next_order += 1

    # ── 6. Commit tất cả ────────────────────────────────────────────────────────
    await db.commit()

    for log in logs_to_insert:
        await db.refresh(log)
    for cp in new_checkpoints:
        await db.refresh(cp)
    await db.refresh(mission)

    return TelemetryIngestResponse(
        saved_count=len(logs_to_insert),
        logs=logs_to_insert,
        new_checkpoints=new_checkpoints,
        mission_status=mission.status,
    )


# ── Telemetry Read ───────────────────────────────────────────────────────────────


@router.get(
    "/{mission_id}/telemetry",
    response_model=list[TelemetryDataResponse],
    summary="Lấy lịch sử tọa độ của một chuyến bay (vẽ đường bay)",
)
async def get_mission_telemetry(
    mission_id: str,
    limit: int = Query(default=1000, ge=1, le=5000, description="Số điểm tối đa trả về"),
    offset: int = Query(default=0, ge=0, description="Bỏ qua N điểm đầu tiên"),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    mission = await db.get(Mission, mission_id)

    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    result = await db.execute(
        select(TelemetryLog)
        .where(TelemetryLog.mission_id == mission.id)
        .order_by(TelemetryLog.timestamp.asc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


@router.get(
    "/{mission_id}/hub-checkpoints",
    response_model=list[HubCheckpointResponse],
    summary="Lấy danh sách các Hub trung gian drone đã đi qua",
)
async def get_hub_checkpoints(
    mission_id: str,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    mission = await db.get(Mission, mission_id)

    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    result = await db.execute(
        select(MissionHubCheckpoint)
        .where(MissionHubCheckpoint.mission_id == mission.id)
        .order_by(MissionHubCheckpoint.checkpoint_order.asc())
    )
    return result.scalars().all()


# ── Hubs & Locations ──────────────────────────────────────────────────────────────


from app.modules.missions.schemas import (
    MissionPlanningAnalyzeRequest, MissionPlanningAnalyzeResponse,
    RouteOptionSchema, RouteLegSchema, MissionCreateRequest, HubBatteryStatus
)
from app.modules.missions.models import Order, OrderStatus, PaymentStatus, MissionLeg, MissionLegStatus
from app.modules.ai_predictions.models import AIPrediction, AIRecommendation
from app.modules.ai_predictions.service import predict_flight_energy, haversine_distance
from app.modules.missions.technician.router import DRONE_BATTERY_COMPATIBILITY, get_compatible_battery_models
from app.shared.id_generator import generate_sequential_id
from typing import Optional


DEFAULT_DRONE_BATTERY_CAPACITY_WH = {
    "DJI Matrice 100": 130.0,
    "Aurelia X6 Pro": 400.0,
    "DJI FlyCart 15 Heavy": 2000.0,
}


async def check_relay_hub_battery_availability(
    db: AsyncSession,
    relay_hub_ids: list[int],
    hub_dict: dict,
    all_batteries: list,
    compatible_models: list[str],
    drone_id: Optional[int],
    drone_capacity_wh: float,
    legs_detail: list[dict],
) -> tuple[bool, Optional[str], list[HubBatteryStatus]]:
    """
    Kiểm tra tính khả thi của Relay Route:
    1. Kiểm tra năng lượng từng chặng (legs_detail), bảo đảm mức dự phòng tối thiểu 20%
       (mỗi chặng tiêu thụ <= 80% dung lượng bộ pin thực tế `drone_capacity_wh`).
    2. Kiểm tra Battery Inventory tại từng Relay Hub:
       - status ACTIVE
       - drone_id IS NULL (chưa gắn vào drone khác)
       - charge_level_pct >= 100 (sạc đầy 100%, trạng thái Ready)
       - battery_model tương thích với model của drone
       - chưa bị giữ chỗ (reserved) bởi Mission đang active khác (bao gồm cả giữ chỗ theo
         Mission.battery_id/MissionLeg.battery_id VÀ giữ chỗ theo số lượng chặng trung gian PENDING
         của các Mission active cùng dòng pin).

    Returns: (feasible, infeasible_reason, relay_battery_status_list)
    """
    active_statuses = [
        MissionStatus.SCHEDULED,
        MissionStatus.IN_PROGRESS,
        MissionStatus.APPROVED,
        MissionStatus.FLYING,
        MissionStatus.ACTIVE_MISSION,
    ]

    # 1. Lấy set battery_id đang bị giữ trực tiếp bởi mission active hoặc mission_leg active
    reserved_result = await db.execute(
        select(Mission.battery_id).where(
            Mission.status.in_(active_statuses),
            Mission.battery_id.is_not(None),
        )
    )
    reserved_battery_ids = {row[0] for row in reserved_result.all() if row[0]}

    leg_reserved_res = await db.execute(
        select(MissionLeg.battery_id)
        .join(Mission, MissionLeg.mission_id == Mission.id)
        .where(
            Mission.status.in_(active_statuses),
            MissionLeg.status.in_([MissionLegStatus.PENDING, MissionLegStatus.IN_PROGRESS]),
            MissionLeg.battery_id.is_not(None),
        )
    )
    for row in leg_reserved_res.all():
        if row[0]:
            reserved_battery_ids.add(row[0])

    # 2. Đếm số lượng pin đang được "giữ chỗ ảo" (virtual hold) tại mỗi Relay Hub bởi các Mission active
    # có chặng bay tiếp theo xuất phát từ Relay Hub đó (sequence_no > 1, status == PENDING, battery_id IS NULL)
    held_count_by_hub: dict[int, int] = {}
    if relay_hub_ids:
        pending_relay_legs_res = await db.execute(
            select(MissionLeg.from_hub_id, Drone.model)
            .join(Mission, MissionLeg.mission_id == Mission.id)
            .outerjoin(Drone, Mission.drone_id == Drone.id)
            .where(
                Mission.status.in_(active_statuses),
                MissionLeg.sequence_no > 1,
                MissionLeg.status == MissionLegStatus.PENDING,
                MissionLeg.battery_id.is_(None),
                MissionLeg.from_hub_id.in_(relay_hub_ids),
            )
        )
        for from_hub_id, other_drone_model in pending_relay_legs_res.all():
            if from_hub_id is None:
                continue
            other_compat = DRONE_BATTERY_COMPATIBILITY.get(other_drone_model or "", [other_drone_model] if other_drone_model else [])
            # Nếu cùng dùng chung dòng pin tương thích thì tính là đang giữ 1 suất pin tại Relay Hub đó
            if not compatible_models or not other_compat or bool(set(compatible_models) & set(other_compat)):
                held_count_by_hub[from_hub_id] = held_count_by_hub.get(from_hub_id, 0) + 1

    infeasible_reasons: list[str] = []
    max_safe_energy_wh = round(drone_capacity_wh * 0.8, 2)
    model_str = f" ({', '.join(compatible_models)})" if compatible_models else ""

    # 3. Kiểm tra năng lượng cho từng chặng (bảo đảm dự phòng tối thiểu 20% mỗi chặng)
    for idx, leg in enumerate(legs_detail):
        leg_energy = float(leg.get("energy_wh") or 0.0)
        if leg_energy <= 0 and leg.get("distance_km") is not None:
            leg_energy = float(predict_flight_energy(float(leg["distance_km"]), float(leg.get("payload_kg") or 0.0))["energy_wh"])
        if drone_capacity_wh > 0 and leg_energy > max_safe_energy_wh:
            leg_pct = int(round((leg_energy / drone_capacity_wh) * 100))
            infeasible_reasons.append(
                f"Chặng {idx + 1} ({leg.get('from_hub_code', '?')} -> {leg.get('to_hub_code', '?')}) "
                f"tiêu thụ {leg_energy:.1f} Wh (~{leg_pct}% pin), vượt mức an toàn 80% "
                f"({max_safe_energy_wh:.1f} Wh / {drone_capacity_wh:.1f} Wh{model_str}, cần dự phòng tối thiểu 20%)"
            )

    # 4. Kiểm tra Battery Inventory tại từng Hub trung gian
    # Trường hợp tuyến qua cùng 1 hub nhiều lần (hiếm gặp), vẫn trừ dần số pin cần dùng
    hub_needed_so_far: dict[int, int] = {}
    relay_statuses: list[HubBatteryStatus] = []

    for hub_id in relay_hub_ids:
        hub = hub_dict.get(hub_id)
        hub_code = (hub.code or hub.name) if hub else str(hub_id)
        hub_name = hub.name if hub else str(hub_id)

        hub_bats = [
            b for b in all_batteries
            if b.current_hub_id == hub_id
            and b.drone_id is None
            and (b.charge_level_pct or 0) >= 100
            and b.id not in reserved_battery_ids
            and (
                not compatible_models
                or (b.battery_model and b.battery_model in compatible_models)
            )
        ]

        raw_ready_count = len(hub_bats)
        held_by_others = held_count_by_hub.get(hub_id, 0)
        already_used_in_route = hub_needed_so_far.get(hub_id, 0)
        effective_ready_count = max(0, raw_ready_count - held_by_others - already_used_in_route)
        hub_needed_so_far[hub_id] = already_used_in_route + 1

        hub_feasible = effective_ready_count > 0

        relay_statuses.append(HubBatteryStatus(
            hub_id=hub_id,
            hub_code=hub_code,
            hub_name=hub_name,
            ready_count=effective_ready_count,
            compatible_models=compatible_models,
            feasible=hub_feasible,
        ))

        if not hub_feasible:
            if raw_ready_count > 0 and held_by_others > 0:
                infeasible_reasons.append(
                    f"Hub {hub_name} có {raw_ready_count} pin{model_str} sạc 100% nhưng đã được giữ chỗ ({held_by_others} pin) cho nhiệm vụ khác"
                )
            else:
                infeasible_reasons.append(
                    f"Hub {hub_name} không có pin tương thích{model_str} ở trạng thái Ready (sạc 100%, chưa gắn drone và chưa bị giữ chỗ)"
                )

    if infeasible_reasons:
        return False, " | ".join(infeasible_reasons), relay_statuses
    return True, None, relay_statuses


async def _resolve_drone_battery_capacity_wh(
    db: AsyncSession,
    drone_obj: Optional[Drone],
    all_active_batteries: list[Battery],
) -> tuple[list[str], float]:
    """
    Xác định danh sách model pin tương thích và dung lượng 1 viên pin thực tế (Wh) của Drone.
    Tuyệt đối KHÔNG lấy nhầm dung lượng pin của dòng Drone khác tại cùng Hub
    (ví dụ: Drone #30 DJI Matrice 100 dùng pin TB48D 130 Wh, không lấy pin 1000-2000 Wh của drone tải nặng).
    """
    compatible_models: list[str] = []
    if drone_obj and drone_obj.model:
        compatible_models = await get_compatible_battery_models(db, drone_obj.model)

    # 1. Ưu tiên lấy từ viên pin đang gắn trực tiếp trên drone_obj (nếu tương thích)
    if drone_obj:
        installed_bats = [
            b for b in all_active_batteries
            if b.drone_id == drone_obj.id
            and b.capacity_wh
            and (not compatible_models or (b.battery_model and b.battery_model in compatible_models))
        ]
        if installed_bats:
            return compatible_models, float(max(b.capacity_wh for b in installed_bats))

    # 2. Lấy từ bất kỳ viên pin nào trong hệ thống có battery_model thuộc compatible_models
    if compatible_models:
        model_bats = [
            b for b in all_active_batteries
            if b.battery_model in compatible_models and b.capacity_wh
        ]
        if model_bats:
            return compatible_models, float(max(b.capacity_wh for b in model_bats))

    # 3. Tra cứu bảng dung lượng chuẩn theo model của Drone
    default_cap = DEFAULT_DRONE_BATTERY_CAPACITY_WH.get(
        drone_obj.model if drone_obj else "",
        130.0
    )
    return compatible_models, float(default_cap)


async def compute_mission_routes_internal(
    db: AsyncSession,
    order: Order,
    drone_id: Optional[int] = None
) -> tuple[list[RouteOptionSchema], list[dict]]:
    origin_hub = await db.get(Hub, order.origin_hub_id)
    dest_hub = await db.get(Hub, order.destination_hub_id)
    if not origin_hub or not dest_hub:
        raise HTTPException(status_code=400, detail="Order is missing valid Hubs")

    # 1. Get all active hubs
    result = await db.execute(select(Hub).where(Hub.status == 'ACTIVE'))
    hubs = result.scalars().all()
    if not hubs:
        result = await db.execute(select(Hub))
        hubs = result.scalars().all()
    hub_dict = {h.id: h for h in hubs}

    # 2. Suggested drone + load drone object for battery compatibility
    suggested_drone_id = drone_id
    drone_obj = None
    if drone_id:
        drone_obj = await db.get(Drone, drone_id)
    if not suggested_drone_id:
        result = await db.execute(
            select(Drone).where(
                and_(
                    Drone.status == DroneStatus.AVAILABLE,
                    Drone.current_hub_id == origin_hub.id,
                    Drone.payload_capacity_kg >= float(order.payload_kg or 0)
                )
            )
        )
        available_drones = result.scalars().all()
        if available_drones:
            suggested_drone_id = available_drones[0].id
            if not drone_obj:
                drone_obj = available_drones[0]

    # 3. Lấy toàn bộ pin ACTIVE (cả pin rời và pin đang gắn trên drone để tính đúng capacity_wh)
    result = await db.execute(
        select(Battery).where(Battery.status == batteries_status.ACTIVE)
    )
    all_active_batteries = result.scalars().all()
    all_batteries = [b for b in all_active_batteries if b.drone_id is None]

    # Xác định chính xác dòng pin tương thích và dung lượng 1 viên pin của Drone (ví dụ TB48D = 130 Wh)
    compatible_models, drone_capacity_wh = await _resolve_drone_battery_capacity_wh(
        db, drone_obj, all_active_batteries
    )

    # Tính thời gian chờ pin tại từng Hub dựa trên pin TƯƠNG THÍCH với Drone
    hub_wait_times = {}
    for h in hubs:
        h_bats = [
            b for b in all_batteries
            if b.current_hub_id == h.id
            and (not compatible_models or (b.battery_model and b.battery_model in compatible_models))
        ]
        if not h_bats:
            hub_wait_times[h.id] = float('inf')
        else:
            best_charge = max([b.charge_level_pct or 0 for b in h_bats])
            if best_charge >= 100:
                hub_wait_times[h.id] = 0
            else:
                hub_wait_times[h.id] = max(1, 100 - best_charge)

    payload = float(order.payload_kg or 0)

    def plan_single_path(mode="energy", exclude_nodes=None):
        if exclude_nodes is None:
            exclude_nodes = set()

        distances = {h.id: float('inf') for h in hubs}
        distances[origin_hub.id] = 0
        previous_nodes = {h.id: None for h in hubs}
        explanations = {h.id: "" for h in hubs}

        pq = [(0, origin_hub.id)]

        while pq:
            current_cost, current_node = heapq.heappop(pq)
            if current_node == dest_hub.id:
                break
            if current_cost > distances[current_node]:
                continue

            curr_h = hub_dict[current_node]

            for n_hub in hubs:
                if n_hub.id == current_node or n_hub.id in exclude_nodes:
                    continue

                dist_km = haversine_distance(
                    curr_h.latitude or 0, curr_h.longitude or 0,
                    n_hub.latitude or 0, n_hub.longitude or 0
                )
                pred = predict_flight_energy(dist_km, payload)
                energy_wh = pred["energy_wh"]

                # Check drone capacity constraint (max 80% usage per leg -> minimum 20% reserve)
                if energy_wh > drone_capacity_wh * 0.8:
                    continue

                flight_time = dist_km * 2  # ~2 mins per km
                wait_time = 0
                exp = f"{n_hub.name} (Tốn {energy_wh:.1f}Wh)"

                if n_hub.id != dest_hub.id:
                    wait_time = hub_wait_times.get(n_hub.id, float('inf'))
                    if mode == "zero_wait" and wait_time > 0:
                        continue
                    if wait_time == float('inf'):
                        continue
                    if wait_time == 0:
                        exp += " - Đổi pin nhanh (0p)"
                    else:
                        exp += f" - Đợi sạc {wait_time}p"

                if mode == "energy":
                    edge_cost = energy_wh
                elif mode == "shortest":
                    edge_cost = dist_km
                elif mode == "zero_wait":
                    edge_cost = flight_time
                else:  # balanced
                    edge_cost = flight_time + wait_time

                new_cost = distances[current_node] + edge_cost
                if new_cost < distances[n_hub.id]:
                    distances[n_hub.id] = new_cost
                    previous_nodes[n_hub.id] = current_node
                    explanations[n_hub.id] = exp
                    heapq.heappush(pq, (new_cost, n_hub.id))

        if distances[dest_hub.id] == float('inf'):
            return None

        path = []
        curr = dest_hub.id
        while curr is not None:
            path.append(curr)
            curr = previous_nodes[curr]
        path.reverse()

        relay_hubs_info = [explanations[n] for n in path if n not in (origin_hub.id, dest_hub.id)]

        total_dist = 0.0
        total_energy = 0.0
        total_wait = 0
        legs_detail = []
        route_codes = []

        for i in range(len(path) - 1):
            h1 = hub_dict[path[i]]
            h2 = hub_dict[path[i + 1]]
            route_codes.append(h1.code or h1.name)

            d = haversine_distance(
                h1.latitude or 0, h1.longitude or 0,
                h2.latitude or 0, h2.longitude or 0
            )
            e = predict_flight_energy(d, payload)["energy_wh"]
            total_dist += d
            total_energy += e

            legs_detail.append({
                "from_hub_id": h1.id,
                "to_hub_id": h2.id,
                "from_hub_code": h1.code or h1.name,
                "to_hub_code": h2.code or h2.name,
                "distance_km": round(d, 2),
                "energy_wh": round(e, 2),
                "payload_kg": payload,
            })

            if path[i + 1] != dest_hub.id:
                total_wait += hub_wait_times.get(path[i + 1], 0)

        last_hub = hub_dict[path[-1]]
        route_codes.append(last_hub.code or last_hub.name)
        route_string = " -> ".join(route_codes)
        total_time = int(total_dist * 2) + total_wait

        return {
            "path": path,
            "relayHubs": relay_hubs_info,
            "distanceKm": round(total_dist, 2),
            "predictedEnergyWh": round(total_energy, 2),
            "predictedDurationMin": int(total_time),
            "wait_time": total_wait,
            "legs_detail": legs_detail,
            "route_string": route_string
        }

    raw_routes = []
    seen_route_strings = set()

    # 1. Tuyến Tối ưu Năng lượng (Energy-Optimal: Dijkstra với cost = CatBoost energy_wh)
    r1 = plan_single_path("energy")
    if r1:
        raw_routes.append({"key": "ENG", "type": "energy", "data": r1})
        seen_route_strings.add(r1["route_string"])

    # 2. Tuyến Nhanh nhất / Không chờ sạc (Fastest / Zero Wait)
    r2 = plan_single_path("zero_wait")
    if r2 and r2["route_string"] in seen_route_strings:
        intermediates_r1 = [nid for nid in r1["path"] if nid not in (origin_hub.id, dest_hub.id)]
        if intermediates_r1:
            r2_alt = plan_single_path("zero_wait", exclude_nodes={intermediates_r1[0]})
            if r2_alt and r2_alt["route_string"] not in seen_route_strings:
                r2 = r2_alt
    if r2 and r2["route_string"] not in seen_route_strings:
        raw_routes.append({"key": "FAST", "type": "fast", "data": r2})
        seen_route_strings.add(r2["route_string"])
    elif not r2:
        r_bal = plan_single_path("balanced")
        if r_bal and r_bal["route_string"] not in seen_route_strings:
            raw_routes.append({"key": "FAST", "type": "fast", "data": r_bal})
            seen_route_strings.add(r_bal["route_string"])

    # 3. Tuyến Quãng đường ngắn nhất (Shortest Distance)
    r3 = plan_single_path("shortest")
    if r3 and r3["route_string"] in seen_route_strings:
        used_intermediates = set()
        for rr in raw_routes:
            for nid in rr["data"]["path"]:
                if nid not in (origin_hub.id, dest_hub.id):
                    used_intermediates.add(nid)
        if used_intermediates:
            r3_alt = plan_single_path("shortest", exclude_nodes=used_intermediates)
            if r3_alt and r3_alt["route_string"] not in seen_route_strings:
                r3 = r3_alt
    if r3 and r3["route_string"] not in seen_route_strings:
        raw_routes.append({"key": "SHORT", "type": "shortest", "data": r3})
        seen_route_strings.add(r3["route_string"])

    # 4. Dự phòng nếu chưa đủ 3 tuyến: Tuyến cân bằng (Balanced)
    if len(raw_routes) < 3:
        r_bal = plan_single_path("balanced")
        if r_bal and r_bal["route_string"] not in seen_route_strings:
            raw_routes.append({"key": "BAL", "type": "balanced", "data": r_bal})
            seen_route_strings.add(r_bal["route_string"])

    # 5. Dự phòng nếu vẫn chưa đủ 3 tuyến: Tuyến bay thẳng (Direct Flight)
    if len(raw_routes) < 3:
        dist = haversine_distance(
            origin_hub.latitude or 0, origin_hub.longitude or 0,
            dest_hub.latitude or 0, dest_hub.longitude or 0
        )
        e = predict_flight_energy(dist, payload)["energy_wh"]
        dir_string = f"{origin_hub.code or origin_hub.name} -> {dest_hub.code or dest_hub.name}"
        if dir_string not in seen_route_strings:
            r_dir = {
                "path": [origin_hub.id, dest_hub.id],
                "relayHubs": [],
                "distanceKm": round(dist, 2),
                "predictedEnergyWh": round(e, 2),
                "predictedDurationMin": int(dist * 2),
                "wait_time": 0,
                "legs_detail": [{
                    "from_hub_id": origin_hub.id,
                    "to_hub_id": dest_hub.id,
                    "from_hub_code": origin_hub.code or origin_hub.name,
                    "to_hub_code": dest_hub.code or dest_hub.name,
                    "distance_km": round(dist, 2),
                    "energy_wh": round(e, 2),
                    "payload_kg": payload,
                }],
                "route_string": dir_string
            }
            raw_routes.append({"key": "DIR", "type": "direct", "data": r_dir})
            seen_route_strings.add(dir_string)

    # 6. Dự phòng để ĐẢM BẢO LUÔN CÓ ĐỦ 3 TUYẾN ĐƯỜNG: Tìm tuyến trung gian qua các Hub lân cận
    if len(raw_routes) < 3:
        candidates = []
        for h in hubs:
            if h.id not in (origin_hub.id, dest_hub.id):
                d1 = haversine_distance(origin_hub.latitude or 0, origin_hub.longitude or 0, h.latitude or 0, h.longitude or 0)
                d2 = haversine_distance(h.latitude or 0, h.longitude or 0, dest_hub.latitude or 0, dest_hub.longitude or 0)
                candidates.append((d1 + d2, h.id))
        candidates.sort(key=lambda x: x[0])

        for _, mid_hub_id in candidates:
            if len(raw_routes) >= 3:
                break
            d_leg1 = haversine_distance(origin_hub.latitude or 0, origin_hub.longitude or 0, hub_dict[mid_hub_id].latitude or 0, hub_dict[mid_hub_id].longitude or 0)
            d_leg2 = haversine_distance(hub_dict[mid_hub_id].latitude or 0, hub_dict[mid_hub_id].longitude or 0, dest_hub.latitude or 0, dest_hub.longitude or 0)
            e_leg1 = predict_flight_energy(d_leg1, payload)["energy_wh"]
            e_leg2 = predict_flight_energy(d_leg2, payload)["energy_wh"]

            cand_path = [origin_hub.id, mid_hub_id, dest_hub.id]
            cand_route_codes = [origin_hub.code or origin_hub.name, hub_dict[mid_hub_id].code or hub_dict[mid_hub_id].name, dest_hub.code or dest_hub.name]
            cand_route_string = " -> ".join(cand_route_codes)

            if cand_route_string not in seen_route_strings:
                w_mid = hub_wait_times.get(mid_hub_id, 0)
                w_val = w_mid if w_mid != float('inf') else 15
                tot_dist = d_leg1 + d_leg2
                tot_energy = e_leg1 + e_leg2
                cand_data = {
                    "path": cand_path,
                    "relayHubs": [f"{hub_dict[mid_hub_id].name} (Tốn {e_leg1:.1f}Wh)"],
                    "distanceKm": round(tot_dist, 2),
                    "predictedEnergyWh": round(tot_energy, 2),
                    "predictedDurationMin": int(tot_dist * 2) + int(w_val),
                    "wait_time": int(w_val),
                    "legs_detail": [
                        {
                            "from_hub_id": origin_hub.id,
                            "to_hub_id": mid_hub_id,
                            "from_hub_code": origin_hub.code or origin_hub.name,
                            "to_hub_code": hub_dict[mid_hub_id].code or hub_dict[mid_hub_id].name,
                            "distance_km": round(d_leg1, 2),
                            "energy_wh": round(e_leg1, 2),
                            "payload_kg": payload,
                        },
                        {
                            "from_hub_id": mid_hub_id,
                            "to_hub_id": dest_hub.id,
                            "from_hub_code": hub_dict[mid_hub_id].code or hub_dict[mid_hub_id].name,
                            "to_hub_code": dest_hub.code or dest_hub.name,
                            "distance_km": round(d_leg2, 2),
                            "energy_wh": round(e_leg2, 2),
                            "payload_kg": payload,
                        }
                    ],
                    "route_string": cand_route_string
                }
                raw_routes.append({"key": f"ALT{len(raw_routes)+1}", "type": "alternative", "data": cand_data})
                seen_route_strings.add(cand_route_string)

    # Đóng gói Schema
    routes_schemas: list[RouteOptionSchema] = []
    raw_data_list: list[dict] = []
    for entry in raw_routes:
        k = entry["key"]
        d = entry["data"]
        legs_raw = d.get("legs_detail", [])
        # Tính % tiêu hao pin theo chặng cao nhất so với dung lượng 1 viên pin thực tế (drone_capacity_wh)
        # vì tại mỗi Relay Hub drone được thay viên pin mới 100%
        if legs_raw:
            max_leg_energy_wh = max(float(leg.get("energy_wh") or 0.0) for leg in legs_raw)
        else:
            max_leg_energy_wh = float(d["predictedEnergyWh"])

        batt_pct = int(round((max_leg_energy_wh / drone_capacity_wh) * 100)) if drone_capacity_wh else 50
        rem_batt_pct = max(0, 100 - batt_pct)
        risk = "LOW" if max_leg_energy_wh < drone_capacity_wh * 0.6 else ("MEDIUM" if max_leg_energy_wh <= drone_capacity_wh * 0.8 else "HIGH")

        schema = RouteOptionSchema(
            routeId=f"R-{order.id}-{k}",
            route_string=d["route_string"],
            legs_detail=[
                RouteLegSchema(
                    from_hub_code=leg["from_hub_code"],
                    to_hub_code=leg["to_hub_code"],
                    distance_km=leg["distance_km"],
                    sequence_no=idx + 1,
                    from_hub_id=leg.get("from_hub_id"),
                    to_hub_id=leg.get("to_hub_id"),
                    from_hub_name=hub_dict[leg["from_hub_id"]].name if leg.get("from_hub_id") in hub_dict else None,
                    to_hub_name=hub_dict[leg["to_hub_id"]].name if leg.get("to_hub_id") in hub_dict else None,
                    from_latitude=hub_dict[leg["from_hub_id"]].latitude if leg.get("from_hub_id") in hub_dict else None,
                    from_longitude=hub_dict[leg["from_hub_id"]].longitude if leg.get("from_hub_id") in hub_dict else None,
                    to_latitude=hub_dict[leg["to_hub_id"]].latitude if leg.get("to_hub_id") in hub_dict else None,
                    to_longitude=hub_dict[leg["to_hub_id"]].longitude if leg.get("to_hub_id") in hub_dict else None,
                ) for idx, leg in enumerate(legs_raw)
            ],
            distanceKm=d["distanceKm"],
            relayHubs=d["relayHubs"],
            predictedDurationMin=d["predictedDurationMin"],
            predictedEnergyWh=d["predictedEnergyWh"],
            batteryConsumptionPct=batt_pct,
            remainingBatteryPct=rem_batt_pct,
            confidencePct=90 if risk == "LOW" else (85 if risk == "MEDIUM" else 75),
            risk=risk,
            recommended=False,
            reason="",
            suggestedDroneId=suggested_drone_id
        )
        routes_schemas.append(schema)
        raw_data_list.append(d)

    # ── Battery feasibility & 20% reserve check cho từng tuyến ──────────────────
    max_safe_energy_wh = round(drone_capacity_wh * 0.8, 2)
    compat_label = f" ({', '.join(compatible_models)})" if compatible_models else ""

    for schema, raw in zip(routes_schemas, raw_data_list):
        path = raw.get("path", [])
        relay_hub_ids = [nid for nid in path if nid not in (origin_hub.id, dest_hub.id)]

        if not relay_hub_ids:
            # Direct route — kiểm tra năng lượng chặng duy nhất với mức dự phòng tối thiểu 20% (<= 80% capacity)
            if schema.predictedEnergyWh > drone_capacity_wh:
                schema.feasible = False
                schema.infeasible_reason = (
                    f"Năng lượng cần ({schema.predictedEnergyWh:.1f} Wh, ~{schema.batteryConsumptionPct}% pin) "
                    f"vượt dung lượng 1 viên pin{compat_label} ({drone_capacity_wh:.1f} Wh). "
                    f"Drone không đủ pin để bay trực tiếp (tối đa {max_safe_energy_wh:.1f} Wh để giữ dự phòng 20%)."
                )
            elif schema.predictedEnergyWh > max_safe_energy_wh:
                schema.feasible = False
                schema.infeasible_reason = (
                    f"Năng lượng cần ({schema.predictedEnergyWh:.1f} Wh, ~{schema.batteryConsumptionPct}% pin) "
                    f"vượt ngưỡng an toàn 80% của pin{compat_label} ({max_safe_energy_wh:.1f} Wh / {drone_capacity_wh:.1f} Wh). "
                    f"Không bảo đảm mức dự phòng tối thiểu 20%."
                )
            else:
                schema.feasible = True
                schema.infeasible_reason = None
            schema.relay_battery_status = []
            continue

        feasible, reason, hub_statuses = await check_relay_hub_battery_availability(
            db=db,
            relay_hub_ids=relay_hub_ids,
            hub_dict=hub_dict,
            all_batteries=all_batteries,
            compatible_models=compatible_models,
            drone_id=suggested_drone_id,
            drone_capacity_wh=drone_capacity_wh,
            legs_detail=raw.get("legs_detail", []),
        )
        schema.feasible = feasible
        schema.infeasible_reason = reason
        schema.relay_battery_status = hub_statuses

    # ĐÁNH DẤU RECOMMEND CHO TUYẾN KHẢ THI TỐI ƯU HIỆU SUẤT NĂNG LƯỢNG NHẤT
    if routes_schemas:
        best_idx = None
        min_energy = float('inf')
        for idx, s in enumerate(routes_schemas):
            if s.feasible and s.predictedEnergyWh < min_energy:
                min_energy = s.predictedEnergyWh
                best_idx = idx

        for idx, (s, d) in enumerate(zip(routes_schemas, raw_data_list)):
            if not s.feasible:
                s.recommended = False
                s.reason = f"Tuyến không khả thi: {s.infeasible_reason}"
            elif idx == best_idx:
                s.recommended = True
                s.reason = (
                    f"Được AI đề xuất: Tuyến đường khả thi tối ưu hiệu suất năng lượng nhất "
                    f"(tổng {s.predictedEnergyWh:.1f} Wh, tiêu thụ tối đa ~{s.batteryConsumptionPct}% pin/chặng, "
                    f"dự phòng {s.remainingBatteryPct}%)."
                )
            else:
                s.recommended = False
                if "FAST" in s.routeId:
                    s.reason = f"Tuyến bay nhanh nhất: Dự kiến {s.predictedDurationMin} phút, thời gian chờ sạc {d.get('wait_time', 0)} phút."
                elif "SHORT" in s.routeId:
                    s.reason = f"Quãng đường ngắn nhất: Cự ly {s.distanceKm} km, tiêu thụ {s.predictedEnergyWh:.1f} Wh."
                elif "DIR" in s.routeId:
                    s.reason = f"Tuyến bay thẳng trực tiếp: Cự ly {s.distanceKm} km, không qua trạm sạc trung gian."
                else:
                    s.reason = f"Tuyến cân bằng: Cự ly {s.distanceKm} km, thời gian {s.predictedDurationMin} phút."

    return routes_schemas, raw_data_list


async def build_missions_response_list(
    db: AsyncSession,
    missions: list[Mission]
) -> list[MissionResponse]:
    """
    Đóng gói danh sách Mission kèm thông tin chi tiết tuyến đường đã lên lịch
    (giống cấu trúc RouteOptionSchema của POST /api/v1/operator/missions/mission-planning/analyze).
    """
    if not missions:
        return []

    mission_ids = [m.id for m in missions]

    # 1. Lấy tất cả Hubs
    hub_res = await db.execute(select(Hub))
    hub_dict = {h.id: h for h in hub_res.scalars().all()}

    # 2. Lấy tất cả MissionLeg của các mission
    legs_res = await db.execute(
        select(MissionLeg)
        .where(MissionLeg.mission_id.in_(mission_ids))
        .order_by(MissionLeg.mission_id, MissionLeg.sequence_no.asc(), MissionLeg.id.asc())
    )
    legs_by_mission: dict[str, list[MissionLeg]] = {}
    for leg in legs_res.scalars().all():
        if leg.mission_id not in legs_by_mission:
            legs_by_mission[leg.mission_id] = []
        legs_by_mission[leg.mission_id].append(leg)

    # 3. Lấy AIPrediction & AIRecommendation mới nhất của từng mission
    preds_res = await db.execute(
        select(AIPrediction)
        .where(AIPrediction.mission_id.in_(mission_ids))
        .order_by(AIPrediction.id.desc())
    )
    pred_by_mission: dict[str, AIPrediction] = {}
    for p in preds_res.scalars().all():
        if p.mission_id not in pred_by_mission:
            pred_by_mission[p.mission_id] = p

    recs_res = await db.execute(
        select(AIRecommendation)
        .where(AIRecommendation.mission_id.in_(mission_ids))
        .order_by(AIRecommendation.id.desc())
    )
    rec_by_mission: dict[str, AIRecommendation] = {}
    for r in recs_res.scalars().all():
        if r.mission_id not in rec_by_mission:
            rec_by_mission[r.mission_id] = r

    # 4. Lấy danh sách Pin & Drone để tính chính xác % tiêu hao pin theo model Drone
    bats_res = await db.execute(select(Battery))
    all_batteries = bats_res.scalars().all()
    battery_by_id = {b.id: b for b in all_batteries}

    drone_ids = {m.drone_id for m in missions if m.drone_id is not None}
    drone_by_id: dict[int, Drone] = {}
    if drone_ids:
        drones_res = await db.execute(select(Drone).where(Drone.id.in_(drone_ids)))
        drone_by_id = {d.id: d for d in drones_res.scalars().all()}

    responses: list[MissionResponse] = []
    for m in missions:
        m_dict = {c.name: getattr(m, c.name) for c in m.__table__.columns}
        payload = float(m.payload_weight or 0.0)
        drone_obj = drone_by_id.get(m.drone_id) if m.drone_id else None

        # Xác định capacity_wh của 1 viên pin tương thích với Drone (không lấy nhầm pin của dòng drone khác)
        drone_capacity_wh = DEFAULT_DRONE_BATTERY_CAPACITY_WH.get(
            drone_obj.model if drone_obj else "",
            130.0
        )
        if m.battery_id and str(m.battery_id) in battery_by_id:
            b_obj = battery_by_id[str(m.battery_id)]
            if b_obj.capacity_wh:
                drone_capacity_wh = float(b_obj.capacity_wh)
        elif drone_obj:
            _, drone_capacity_wh = await _resolve_drone_battery_capacity_wh(db, drone_obj, all_batteries)

        m_legs = legs_by_mission.get(m.id, [])
        legs_detail: list[RouteLegSchema] = []
        relay_hubs: list[str] = []
        leg_energies: list[float] = []

        if m_legs:
            for idx, leg in enumerate(m_legs):
                h1 = hub_dict.get(leg.from_hub_id)
                h2 = hub_dict.get(leg.to_hub_id)
                from_code = (h1.code or h1.name) if h1 else str(leg.from_hub_id)
                to_code = (h2.code or h2.name) if h2 else str(leg.to_hub_id)

                d = haversine_distance(
                    h1.latitude or 0, h1.longitude or 0,
                    h2.latitude or 0, h2.longitude or 0
                ) if (h1 and h2) else 0.0
                e = float(predict_flight_energy(d, payload)["energy_wh"])
                leg_energies.append(e)

                legs_detail.append(
                    RouteLegSchema(
                        from_hub_code=from_code,
                        to_hub_code=to_code,
                        distance_km=round(d, 2),
                        sequence_no=leg.sequence_no or (idx + 1),
                        from_hub_id=leg.from_hub_id,
                        to_hub_id=leg.to_hub_id,
                        from_hub_name=h1.name if h1 else None,
                        to_hub_name=h2.name if h2 else None,
                        from_latitude=h1.latitude if h1 else None,
                        from_longitude=h1.longitude if h1 else None,
                        to_latitude=h2.latitude if h2 else None,
                        to_longitude=h2.longitude if h2 else None,
                        status=leg.status.value if hasattr(leg.status, "value") else (str(leg.status) if leg.status else None),
                    )
                )

                if idx < len(m_legs) - 1:
                    hub_label = h2.name if h2 else to_code
                    relay_hubs.append(f"{hub_label} (Tốn {e:.1f}Wh) - Đổi pin nhanh (0p)")

            route_codes = [legs_detail[0].from_hub_code] + [l.to_hub_code for l in legs_detail]
            route_string = " -> ".join(route_codes)
        else:
            h1 = hub_dict.get(m.origin_hub_id)
            h2 = hub_dict.get(m.destination_hub_id)
            from_code = (h1.code or h1.name) if h1 else (str(m.origin_hub_id) if m.origin_hub_id else "?")
            to_code = (h2.code or h2.name) if h2 else (str(m.destination_hub_id) if m.destination_hub_id else "?")
            route_string = f"{from_code} -> {to_code}"

            if h1 or h2:
                d = (m.distance_m / 1000.0) if m.distance_m else (
                    haversine_distance(
                        h1.latitude or 0, h1.longitude or 0,
                        h2.latitude or 0, h2.longitude or 0
                    ) if (h1 and h2) else 0.0
                )
                e = float(predict_flight_energy(d, payload)["energy_wh"])
                leg_energies.append(e)
                legs_detail.append(
                    RouteLegSchema(
                        from_hub_code=from_code,
                        to_hub_code=to_code,
                        distance_km=round(d, 2),
                        sequence_no=1,
                        from_hub_id=m.origin_hub_id,
                        to_hub_id=m.destination_hub_id,
                        from_hub_name=h1.name if h1 else None,
                        to_hub_name=h2.name if h2 else None,
                        from_latitude=h1.latitude if h1 else None,
                        from_longitude=h1.longitude if h1 else None,
                        to_latitude=h2.latitude if h2 else None,
                        to_longitude=h2.longitude if h2 else None,
                        status="PENDING",
                    )
                )

        dist_km = round(m.distance_m / 1000.0, 2) if m.distance_m else round(sum(l.distance_km for l in legs_detail), 2)
        ai_pred = pred_by_mission.get(m.id)
        ai_rec = rec_by_mission.get(m.id)

        if ai_pred and ai_pred.estimated_energy_wh is not None:
            pred_energy = round(float(ai_pred.estimated_energy_wh), 2)
        else:
            pred_energy = round(sum(leg_energies), 2)

        if ai_pred and ai_pred.estimated_duration_m is not None:
            pred_duration = int(ai_pred.estimated_duration_m)
        else:
            pred_duration = int(dist_km * 2)

        max_leg_energy_wh = max(leg_energies) if len(leg_energies) > 1 else pred_energy
        batt_pct = int(round((max_leg_energy_wh / drone_capacity_wh) * 100)) if drone_capacity_wh else 50
        rem_batt_pct = max(0, 100 - batt_pct)

        if ai_pred and ai_pred.risk_level:
            risk = str(ai_pred.risk_level).upper()
        else:
            risk = "LOW" if max_leg_energy_wh < drone_capacity_wh * 0.6 else ("MEDIUM" if max_leg_energy_wh <= drone_capacity_wh * 0.8 else "HIGH")

        conf_pct = 90 if risk == "LOW" else (85 if risk == "MEDIUM" else 75)
        reason = (
            ai_rec.content
            if (ai_rec and ai_rec.content)
            else f"Lộ trình {route_string} được chọn theo phân tích AI ({pred_energy:.1f} Wh)."
        )
        route_id = f"R-{m.order_id or m.id}-SCH"

        scheduled_route = RouteOptionSchema(
            routeId=route_id,
            route_string=route_string,
            legs_detail=legs_detail,
            distanceKm=dist_km,
            relayHubs=relay_hubs,
            predictedDurationMin=pred_duration,
            predictedEnergyWh=pred_energy,
            batteryConsumptionPct=batt_pct,
            remainingBatteryPct=rem_batt_pct,
            confidencePct=conf_pct,
            risk=risk,
            recommended=True,
            reason=reason,
            suggestedDroneId=m.drone_id,
        )

        m_dict["route_path_string"] = route_string
        m_dict["routeId"] = route_id
        m_dict["route_string"] = route_string
        m_dict["legs_detail"] = legs_detail
        m_dict["distanceKm"] = dist_km
        m_dict["relayHubs"] = relay_hubs
        m_dict["predictedDurationMin"] = pred_duration
        m_dict["predictedEnergyWh"] = pred_energy
        m_dict["batteryConsumptionPct"] = batt_pct
        m_dict["remainingBatteryPct"] = rem_batt_pct
        m_dict["confidencePct"] = conf_pct
        m_dict["risk"] = risk
        m_dict["recommended"] = True
        m_dict["reason"] = reason
        m_dict["suggestedDroneId"] = m.drone_id
        m_dict["scheduled_route"] = scheduled_route
        m_dict["routes"] = [scheduled_route]

        responses.append(MissionResponse(**m_dict))

    return responses


async def build_mission_detail_response(
    db: AsyncSession,
    mission: Mission
) -> MissionResponse:
    res = await build_missions_response_list(db, [mission])
    return res[0]


@router.post("/mission-planning/analyze", response_model=MissionPlanningAnalyzeResponse)
async def analyze_mission_planning(
    request: MissionPlanningAnalyzeRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db)
):
    order = await db.get(Order, request.orderId.strip() if request.orderId else request.orderId)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    routes_schemas, _ = await compute_mission_routes_internal(db, order, request.droneId)
    return MissionPlanningAnalyzeResponse(routes=routes_schemas)


@router.post("", response_model=MissionResponse)
async def create_mission(
    request: MissionCreateRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db)
):
    """
    **[Operator API] Tạo nhiệm vụ bay (Create Mission)**
    
    - **Mục đích**: Chính thức xác nhận tạo chuyến bay để giao một Order cụ thể.
    - **Hoạt động**:
        1. Validate `Order` bắt buộc phải đang ở trạng thái `AT_HUB` (Technician đã nhận hàng).
        2. Chặn tạo trùng lặp nếu Order hoặc Drone đã có Mission đang chờ hoặc đang bay.
        3. Khớp thông tin tuyến đường Operator đã chọn (`routeId`) và kiểm tra lại tính khả thi
           (năng lượng từng chặng <= 80% dung lượng pin thực tế + pin sẵn sàng tại Relay Hub).
        4. Giữ chỗ nguồn pin tại Origin Hub (qua `Mission.battery_id` & `MissionLeg[0].battery_id`)
           và giữ chỗ công suất pin tại các Relay Hub (qua các `MissionLeg` trung gian `PENDING`).
        5. Tạo bản ghi `Mission`, `MissionLeg`, `AIPrediction`, `AIRecommendation`.
    """
    clean_order_id = request.orderId.strip() if request.orderId else request.orderId
    order = await db.get(Order, clean_order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Order chưa được thanh toán")

    if order.status != OrderStatus.AT_HUB:
        raise HTTPException(
            status_code=400,
            detail=f"Order đang ở trạng thái '{order.status.value}'. Chỉ tạo mission khi Technician đã xác nhận nhận hàng tại hub (AT_HUB)."
        )

    active_statuses = [
        MissionStatus.SCHEDULED,
        MissionStatus.IN_PROGRESS,
        MissionStatus.APPROVED,
        MissionStatus.FLYING,
        MissionStatus.ACTIVE_MISSION,
    ]

    # Kiểm tra xem đơn hàng đã có Mission active chưa
    existing_mission_res = await db.execute(
        select(Mission).where(
            Mission.order_id == order.id,
            Mission.status.in_(active_statuses)
        )
    )
    if existing_mission_res.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="Đơn hàng này đã có nhiệm vụ bay đang được lên lịch hoặc đang thực hiện"
        )

    drone = await db.get(Drone, request.droneId)
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")

    # Kiểm tra Drone chưa bị gán cho một Mission active khác
    busy_drone_res = await db.execute(
        select(Mission).where(
            Mission.drone_id == drone.id,
            Mission.status.in_(active_statuses)
        )
    )
    busy_mission = busy_drone_res.scalars().first()
    if busy_mission:
        raise HTTPException(
            status_code=400,
            detail=f"Drone #{drone.id} ({drone.name}) hiện đã được phân công cho nhiệm vụ {busy_mission.id}."
        )

    # Tái hiện các tùy chọn tuyến đường và chạy lại bước kiểm tra battery feasibility + 20% reserve
    routes_schemas, raw_data_list = await compute_mission_routes_internal(db, order, request.droneId)

    selected_schema = None
    selected_data = None
    if request.routeId:
        for s, d in zip(routes_schemas, raw_data_list):
            if s.routeId == request.routeId:
                selected_schema = s
                selected_data = d
                break

    # Nếu không truyền routeId hoặc routeId không nằm trong danh sách, ưu tiên tuyến recommended khả thi
    if not selected_data and routes_schemas:
        for s, d in zip(routes_schemas, raw_data_list):
            if s.recommended and s.feasible:
                selected_schema = s
                selected_data = d
                break
        if not selected_data:
            selected_schema = routes_schemas[0]
            selected_data = raw_data_list[0]

    # ── Guard: chặn tạo mission nếu tuyến không khả thi về năng lượng hoặc pin Relay Hub ──
    if selected_schema and not selected_schema.feasible:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Không thể tạo mission: Tuyến '{selected_schema.route_string}' không khả thi. "
                f"{selected_schema.infeasible_reason or 'Thiếu pin tương thích tại Hub trung gian hoặc vượt mức dự phòng 20%.'} "
                f"Vui lòng chọn tuyến khả thi khác hoặc đảm bảo Hub có pin sẵn sàng trước khi Assign."
            )
        )

    # Tìm và giữ chỗ pin tương thích khả dụng của Drone hoặc pin tại Origin Hub
    compatible_models = await get_compatible_battery_models(db, drone.model)
    reserved_bat_res = await db.execute(
        select(Mission.battery_id).where(
            Mission.status.in_(active_statuses),
            Mission.battery_id.is_not(None),
        )
    )
    reserved_bat_ids = {row[0] for row in reserved_bat_res.all() if row[0]}

    # 1. Ưu tiên pin đang lắp sẵn trên Drone (nếu tương thích, sạc >= 100% và chưa bị giữ bởi mission khác)
    result = await db.execute(
        select(Battery).where(
            Battery.drone_id == drone.id,
            Battery.status == batteries_status.ACTIVE,
        )
    )
    drone_batteries = [
        b for b in result.scalars().all()
        if b.id not in reserved_bat_ids
        and (not compatible_models or (b.battery_model and b.battery_model in compatible_models))
        and (b.charge_level_pct or 0) >= 100
    ]
    assigned_battery = drone_batteries[0] if drone_batteries else None

    # 2. Nếu Drone chưa gắn sẵn pin 100% tương thích, tìm pin 100% tương thích rảnh tại Origin Hub
    if not assigned_battery:
        result = await db.execute(
            select(Battery).where(
                Battery.current_hub_id == order.origin_hub_id,
                Battery.status == batteries_status.ACTIVE,
                Battery.drone_id.is_(None),
            ).order_by(Battery.id)
        )
        hub_batteries = [
            b for b in result.scalars().all()
            if b.id not in reserved_bat_ids
            and (not compatible_models or (b.battery_model and b.battery_model in compatible_models))
            and (b.charge_level_pct or 0) >= 100
        ]
        assigned_battery = hub_batteries[0] if hub_batteries else None

    battery_id = assigned_battery.id if assigned_battery else None

    mission_id = await generate_sequential_id(db, Mission, "MSN")

    total_dist_km = selected_schema.distanceKm if selected_schema else 0.0
    total_energy_wh = selected_schema.predictedEnergyWh if selected_schema else 0.0
    duration_min = selected_schema.predictedDurationMin if selected_schema else 0
    route_str = selected_schema.route_string if selected_schema else ""

    # 1. Tạo bản ghi Mission
    mission = Mission(
        id=mission_id,
        order_id=order.id,
        customer_id=order.customer_id,
        drone_id=request.droneId,
        battery_id=battery_id,
        origin_hub_id=order.origin_hub_id,
        destination_hub_id=order.destination_hub_id,
        payload_weight=order.payload_kg,
        distance_m=round(total_dist_km * 1000, 2),
        delivery_fee=order.delivery_fee,
        scheduled_time=order.requested_delivery_at or datetime.now(),
        status=MissionStatus.SCHEDULED,
        handling_status=HandlingStatus.AT_HUB,
        operator_id=user.id
    )
    db.add(mission)
    await db.flush()

    # 2. Tạo các chặng bay (MissionLeg)
    legs = selected_data.get("legs_detail", []) if selected_data else []
    for idx, leg_item in enumerate(legs):
        m_leg = MissionLeg(
            mission_id=mission_id,
            sequence_no=idx + 1,
            from_hub_id=leg_item["from_hub_id"],
            to_hub_id=leg_item["to_hub_id"],
            battery_id=battery_id if idx == 0 else None,
            status=MissionLegStatus.PENDING,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        db.add(m_leg)

    # 3. Lưu AI Snapshot
    ai_pred = AIPrediction(
        mission_id=mission_id,
        estimated_energy_wh=round(total_energy_wh, 2),
        estimated_duration_m=float(duration_min),
        risk_level=selected_schema.risk.capitalize() if (selected_schema and selected_schema.risk) else "Low",
        predicted_at=datetime.now(timezone.utc)
    )
    db.add(ai_pred)

    ai_rec = AIRecommendation(
        mission_id=mission_id,
        category="Mission Planning",
        content=f"Lộ trình {route_str} được chọn theo phân tích AI ({total_energy_wh:.1f} Wh).",
        is_applied=True,
        created_at=datetime.now(timezone.utc)
    )
    db.add(ai_rec)

    # 4. Ghi nhận thời gian planning trên Order
    order.planning_at = datetime.now()

    await db.commit()
    await db.refresh(mission)

    # Gửi email thông báo cho người nhận hàng
    try:
        from app.core.email import send_mission_scheduled_email
        from app.core.config import settings
        if order.receiver_email:
            await send_mission_scheduled_email(
                to_email=order.receiver_email,
                order_id=order.id,
                mission_id=mission.id,
                frontend_url=settings.FRONTEND_URL,
            )
    except Exception as e:
        print(f"[EMAIL] Lỗi gửi mission scheduled email: {e}")

    return await build_mission_detail_response(db, mission)


