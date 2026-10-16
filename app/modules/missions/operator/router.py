from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from datetime import timedelta, datetime, timezone
import math

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.core.config import settings
from app.modules.missions.models import Mission, MissionStatus, Location, LocationType, Incident, IncidentStatus, IncidentSeverity, TelemetryLog, MissionHubCheckpoint
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
from app.modules.fleet.schemas import CheckAvailabilityRequest, AvailabilityResponse

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
    """Trả về toàn bộ thông tin mission bao gồm pickup_location_id và dropoff_location_id."""
    mission = await db.get(Mission, mission_id)

    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


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
    RouteOptionSchema, RouteLegSchema, MissionCreateRequest
)
from app.modules.missions.models import Order, OrderStatus, PaymentStatus, MissionLeg, MissionLegStatus
from app.modules.ai_predictions.models import AIPrediction, AIRecommendation
from app.modules.ai_predictions.service import predict_flight_energy, haversine_distance
from app.shared.id_generator import generate_sequential_id
from typing import Optional


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

    # 2. Suggested drone
    suggested_drone_id = drone_id
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

    # 3. Get battery wait times at each hub
    result = await db.execute(
        select(Battery).where(
            and_(Battery.status == batteries_status.ACTIVE, Battery.drone_id.is_(None))
        )
    )
    all_batteries = result.scalars().all()

    hub_wait_times = {}
    for h in hubs:
        h_bats = [b for b in all_batteries if b.current_hub_id == h.id]
        if not h_bats:
            hub_wait_times[h.id] = float('inf')
        else:
            best_charge = max([b.charge_level_pct or 0 for b in h_bats])
            if best_charge >= 90:
                hub_wait_times[h.id] = 0
            else:
                hub_wait_times[h.id] = 90 - best_charge

    origin_bats = [b for b in all_batteries if b.current_hub_id == origin_hub.id]
    drone_capacity_wh = max([b.capacity_wh for b in origin_bats]) if origin_bats else 100.0
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

                # Check drone capacity constraint (max 80% usage per leg)
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
                "distance_km": round(d, 2)
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
                    "distance_km": round(dist, 2)
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
                    "relayHubs": [hub_dict[mid_hub_id].name],
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
                            "distance_km": round(d_leg1, 2)
                        },
                        {
                            "from_hub_id": mid_hub_id,
                            "to_hub_id": dest_hub.id,
                            "from_hub_code": hub_dict[mid_hub_id].code or hub_dict[mid_hub_id].name,
                            "to_hub_code": dest_hub.code or dest_hub.name,
                            "distance_km": round(d_leg2, 2)
                        }
                    ],
                    "route_string": cand_route_string
                }
                raw_routes.append({"key": f"ALT{len(raw_routes)+1}", "type": "alternative", "data": cand_data})
                seen_route_strings.add(cand_route_string)


    # Đóng gói Schema
    routes_schemas = []
    raw_data_list = []
    for entry in raw_routes:
        k = entry["key"]
        d = entry["data"]
        batt_pct = int((d["predictedEnergyWh"] / drone_capacity_wh) * 100) if drone_capacity_wh else 50
        rem_batt_pct = max(0, 100 - batt_pct)
        risk = "LOW" if d["predictedEnergyWh"] < drone_capacity_wh * 0.6 else ("MEDIUM" if d["predictedEnergyWh"] < drone_capacity_wh * 0.8 else "HIGH")

        schema = RouteOptionSchema(
            routeId=f"R-{order.id}-{k}",
            route_string=d["route_string"],
            legs_detail=[
                RouteLegSchema(
                    from_hub_code=leg["from_hub_code"],
                    to_hub_code=leg["to_hub_code"],
                    distance_km=leg["distance_km"]
                ) for leg in d["legs_detail"]
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

    # ĐÁNH DẤU RECOMMEND CHO TUYẾN TỐI ƯU HIỆU SUẤT NĂNG LƯỢNG NHẤT
    if routes_schemas:
        best_idx = 0
        min_energy = float('inf')
        for idx, s in enumerate(routes_schemas):
            if s.predictedEnergyWh < min_energy:
                min_energy = s.predictedEnergyWh
                best_idx = idx

        for idx, (s, d) in enumerate(zip(routes_schemas, raw_data_list)):
            if idx == best_idx:
                s.recommended = True
                s.reason = f"Được AI đề xuất: Tuyến đường tối ưu hiệu suất năng lượng nhất (chỉ tiêu thụ {s.predictedEnergyWh:.1f} Wh, ~{s.batteryConsumptionPct}% pin), tiết kiệm pin tối đa."
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


@router.post("/mission-planning/analyze", response_model=MissionPlanningAnalyzeResponse)
async def analyze_mission_planning(
    request: MissionPlanningAnalyzeRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db)
):
    order = await db.get(Order, request.orderId)
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
        1. Validate `Order` bắt buộc phải đang ở trạng thái `PENDING`.
        2. Chặn tạo trùng lặp nếu Order đã có Mission đang chờ hoặc đang bay.
        3. Khớp thông tin tuyến đường Operator đã chọn (`routeId`).
        4. Tạo bản ghi `Mission` trong Database với trạng thái `SCHEDULED` và cự ly `distance_m`.
        5. Tạo chi tiết các chặng bay `MissionLeg` (`sequence_no`, `from_hub_id`, `to_hub_id`, `status='PENDING'`).
        6. Lưu snapshot dự đoán AI vào `AIPrediction` và `AIRecommendation`.
    """
    order = await db.get(Order, request.orderId)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Order chưa được thanh toán")

    if order.status != OrderStatus.AT_HUB:
        raise HTTPException(
            status_code=400,
            detail=f"Order đang ở trạng thái '{order.status.value}'. Chỉ tạo mission khi hàng đã có mặt tại hub (AT_HUB)."
        )

    # Kiểm tra xem đơn hàng đã có Mission active chưa
    existing_mission_res = await db.execute(
        select(Mission).where(
            Mission.order_id == request.orderId,
            Mission.status.in_([MissionStatus.SCHEDULED, MissionStatus.IN_PROGRESS])
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

    # Tái hiện các tùy chọn tuyến đường để khớp với routeId mà Operator đã chọn
    routes_schemas, raw_data_list = await compute_mission_routes_internal(db, order, request.droneId)

    selected_schema = None
    selected_data = None
    for s, d in zip(routes_schemas, raw_data_list):
        if s.routeId == request.routeId:
            selected_schema = s
            selected_data = d
            break

    # Nếu không khớp chính xác routeId, chọn tuyến recommended hoặc tuyến đầu tiên
    if not selected_data and routes_schemas:
        for s, d in zip(routes_schemas, raw_data_list):
            if s.recommended:
                selected_schema = s
                selected_data = d
                break
        if not selected_data:
            selected_schema = routes_schemas[0]
            selected_data = raw_data_list[0]

    # Tìm pin khả dụng của Drone hoặc pin tại Origin Hub
    result = await db.execute(
        select(Battery).where(
            Battery.drone_id == drone.id,
            Battery.status == batteries_status.ACTIVE
        )
    )
    assigned_battery = result.scalars().first()
    if not assigned_battery:
        result = await db.execute(
            select(Battery).where(
                Battery.current_hub_id == order.origin_hub_id,
                Battery.status == batteries_status.ACTIVE,
                Battery.drone_id.is_(None)
            )
        )
        assigned_battery = result.scalars().first()
    battery_id = assigned_battery.id if assigned_battery else None

    mission_id = await generate_sequential_id(db, Mission, "MSN")

    total_dist_km = selected_schema.distanceKm if selected_schema else 0.0
    total_energy_wh = selected_schema.predictedEnergyWh if selected_schema else 0.0
    duration_min = selected_schema.predictedDurationMin if selected_schema else 0
    route_str = selected_schema.route_string if selected_schema else ""

    # 1. Tạo bản ghi Mission
    mission = Mission(
        id=mission_id,
        order_id=request.orderId,
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
        risk_level=selected_schema.risk if selected_schema else "Low",
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

    m_dict = {c.name: getattr(mission, c.name) for c in mission.__table__.columns}
    m_dict["route_path_string"] = route_str
    return MissionResponse(**m_dict)

