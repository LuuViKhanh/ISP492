from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from datetime import timedelta
import math

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.models import Mission, MissionStatus, Location, LocationType, Incident, IncidentStatus, IncidentSeverity, TelemetryLog, MissionHubCheckpoint
from app.modules.system.models import Hub
from app.modules.fleet.models import MaintenanceAlert
from app.modules.missions.schemas import (
    MissionResponse, ApproveRejectRequest,
    TelemetryDataCreate, TelemetryDataResponse,
    LiveTrackingResponse, LiveTrackingDrone,
    HubCheckpointResponse, TelemetryIngestResponse,
)
from app.modules.fleet.models import Drone, Battery, DroneStatus, batteries_status
from app.modules.fleet.schemas import CheckAvailabilityRequest, AvailabilityResponse

router = APIRouter(prefix="/operator/missions", tags=["Operator - Missions"])

allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])


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

    return AvailabilityResponse(available_drones=available_drones, available_batteries=available_batteries)


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


@router.get("/{mission_id}", response_model=MissionResponse, summary="Lấy thông tin chi tiết một mission")
async def get_mission(
    mission_id: int,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """Trả về toàn bộ thông tin mission bao gồm pickup_location_id và dropoff_location_id."""
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


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


@router.post("/{mission_id}/telemetry", response_model=TelemetryIngestResponse)
async def collect_telemetry(
    mission_id: int,
    telemetry_data: list[TelemetryDataCreate],
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Thu thập và lưu trữ dữ liệu từ xa (telemetry) của nhiệm vụ bay.

    Ngoài việc lưu log tọa độ, endpoint này còn:
    - Tự động chuyển mission sang trạng thái **FLYING** khi nhận được telemetry đầu tiên
      (từ trạng thái APPROVED), đồng thời ghi `start_time` và `departed_at`.
    - **Tự động detect hub proximity**: với mỗi điểm tọa độ, hệ thống tính khoảng cách Haversine
      tới tất cả Hub và Mini-hub. Nếu drone vào trong bán kính **200m** của một hub mà chưa
      log checkpoint trong **60 giây** gần nhất → tạo `MissionHubCheckpoint`.
    - Trả về danh sách log đã lưu **và** các checkpoint mới được tạo trong batch này.

    Frontend dùng response này để:
    - Vẽ đường bay (polyline) theo `logs`
    - Hiển thị marker tại các `new_checkpoints`
    """

    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

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

    # ── 2. Lưu telemetry logs ──────────────────────────────────────────────────
    logs_to_insert: list[TelemetryLog] = []
    for data in telemetry_data:
        naive_timestamp = data.timestamp.replace(tzinfo=None) if data.timestamp.tzinfo else data.timestamp
        log_entry = TelemetryLog(
            mission_id=mission_id,
            timestamp=naive_timestamp,
            latitude=data.latitude,
            longitude=data.longitude,
            altitude=data.altitude,
            speed=data.speed,
            battery_voltage=data.battery_voltage,
            energy_consumed_wh=data.energy_consumed_wh,
            wind_speed=data.wind_speed,
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
        .where(MissionHubCheckpoint.mission_id == mission_id)
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
        .where(MissionHubCheckpoint.mission_id == mission_id)
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
                        MissionHubCheckpoint.mission_id == mission_id,
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
                mission_id=mission_id,
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


# ── Telemetry Read + Live Tracking ───────────────────────────────────────────────

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

    Mỗi item trong `drones` bao gồm:
    - Tọa độ mới nhất (`latest_lat`, `latest_lng`, `latest_altitude`, `latest_speed`)
    - Điện áp pin mới nhất
    - Thời điểm cập nhật cuối (`last_updated`)
    - Số checkpoint hub đã qua (`checkpoints_passed`)

    **Polling pattern**: Frontend gọi endpoint này mỗi **3–5 giây** để cập nhật vị trí
    marker trên bản đồ. Không cần WebSocket — server-side cronjob đã giám sát mất tín hiệu.

    Trả về `active_count = 0` và `drones = []` khi không có drone nào đang bay.
    """
    # Lấy tất cả missions đang FLYING
    flying_result = await db.execute(
        select(Mission).where(Mission.status == MissionStatus.FLYING)
    )
    flying_missions = flying_result.scalars().all()

    if not flying_missions:
        return LiveTrackingResponse(active_count=0, drones=[])

    mission_ids = [m.id for m in flying_missions]

    # Subquery: latest telemetry log id cho mỗi mission
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

    # Đếm checkpoints cho từng mission
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
            )
        )

    return LiveTrackingResponse(active_count=len(drones), drones=drones)


@router.get(
    "/{mission_id}/telemetry",
    response_model=list[TelemetryDataResponse],
    summary="Lấy lịch sử tọa độ của một chuyến bay (vẽ đường bay)",
)
async def get_mission_telemetry(
    mission_id: int,
    limit: int = Query(default=1000, ge=1, le=5000, description="Số điểm tối đa trả về"),
    offset: int = Query(default=0, ge=0, description="Bỏ qua N điểm đầu tiên"),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Trả về toàn bộ (hoặc một trang) lịch sử tọa độ của chuyến bay theo thứ tự thời gian.

    Frontend dùng endpoint này để:
    - **Vẽ polyline** đường bay trên bản đồ (replay hoặc realtime khi poll).
    - Hiển thị biểu đồ altitude / speed / battery theo thời gian.

    Kết quả được sắp xếp `timestamp ASC` — đúng thứ tự drone đã bay.
    """
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    result = await db.execute(
        select(TelemetryLog)
        .where(TelemetryLog.mission_id == mission_id)
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
    mission_id: int,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Trả về các mốc Hub mà drone đã đi qua trong chuyến bay, theo thứ tự `checkpoint_order`.

    Mỗi checkpoint được tạo tự động bởi `POST /telemetry` khi drone vào vùng **200m** xung quanh
    một Hub hoặc Mini-hub (cooldown 60s để tránh log trùng).

    Frontend dùng để:
    - Hiển thị **marker / icon hub** trên bản đồ với timestamp.
    - Render bảng log hành trình: Hub A → Hub B → Hub C...
    """
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    result = await db.execute(
        select(MissionHubCheckpoint)
        .where(MissionHubCheckpoint.mission_id == mission_id)
        .order_by(MissionHubCheckpoint.checkpoint_order.asc())
    )
    return result.scalars().all()


# ── Hubs & Locations ──────────────────────────────────────────────────────────────

@router.get("/hubs")
async def list_hubs(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy danh sách toàn bộ các trung tâm điều khiển (Hub) chính.
    
    Trích xuất dữ liệu của các Hub bao gồm mã, tên, địa chỉ, tọa độ địa lý và trạng thái hoạt động.
    """
    result = await db.execute(select(Hub).order_by(Hub.id))
    hubs = result.scalars().all()
    return [{"id": h.id, "code": h.code, "name": h.name, "address": h.address, "latitude": h.latitude, "longitude": h.longitude, "status": h.status} for h in hubs]


@router.get("/mini-hubs")
async def list_mini_hubs(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy danh sách các Hub phụ (Mini-hubs).
    
    API này truy vấn các địa điểm (Location) được đánh dấu là loại HUB, phục vụ cho việc 
    định tuyến và quản lý các trạm dừng đỗ nhỏ của Drone.
    """
    result = await db.execute(
        select(Location).where(Location.type == LocationType.HUB).order_by(Location.id)
    )
    locations = result.scalars().all()
    return [{"id": l.id, "name": l.name, "latitude": l.latitude, "longitude": l.longitude, "type": l.type.value} for l in locations]


# ── Incidents ────────────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class IncidentCreate(BaseModel):
    mission_id: Optional[int] = None
    drone_id: Optional[int] = None
    severity: IncidentSeverity
    description: str
    requires_technical_inspection: bool = False

class IncidentResponse(BaseModel):
    id: int
    mission_id: Optional[int]
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
