from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from datetime import timedelta, datetime, timezone
from typing import Optional, List, Union
import math

from app.core.timezone import get_vn_naive_datetime, get_vn_datetime
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
    drone_id: Optional[Union[str, int]] = None
    severity: IncidentSeverity
    description: str
    requires_technical_inspection: bool = False

class IncidentResponse(BaseModel):
    id: int
    mission_id: Optional[str]
    drone_id: Optional[Union[str, int]] = None
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
    now = get_vn_datetime()
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
    RouteOptionSchema, RouteLegSchema, MissionCreateRequest, HubBatteryStatus,
    RelayHubBatteryOptionSchema,
)
from app.modules.missions.models import Order, OrderStatus, PaymentStatus, MissionLeg, MissionLegStatus
from app.modules.ai_predictions.models import AIPrediction, AIRecommendation
from app.modules.ai_predictions.service import predict_flight_energy, haversine_distance
from app.modules.missions.technician.router import DRONE_BATTERY_COMPATIBILITY, get_compatible_battery_models
from app.shared.id_generator import generate_sequential_id
from app.core.timezone import get_vn_naive_datetime
from typing import Optional


DEFAULT_DRONE_BATTERY_CAPACITY_WH = {
    "DJI Matrice 100": 130.0,
    "Aurelia X6 Pro": 444.0,
    "DJI FlyCart 15 Heavy": 980.0,
}


def _compute_battery_energy_wh(battery: Optional[Battery], default_capacity_wh: float = 130.0) -> tuple[float, int, float]:
    """
    Tính toán (capacity_wh, charge_level_pct, available_energy_wh) thực tế của 1 viên pin từ database.
    Năng lượng thực tế khả dụng = capacity_wh * (charge_level_pct / 100).
    Tuyệt đối KHÔNG giả định 100% nếu trong DB có giá trị thực tế.
    """
    if not battery:
        cap = float(default_capacity_wh or 130.0)
        return cap, 0, 0.0
    cap = float(battery.capacity_wh if battery.capacity_wh is not None else (default_capacity_wh or 130.0))
    charge_pct = int(battery.charge_level_pct if battery.charge_level_pct is not None else 0)
    energy_wh = round(cap * (charge_pct / 100.0), 2)
    return cap, charge_pct, energy_wh



async def _get_reserved_batteries_and_virtual_holds(
    db: AsyncSession,
    relay_hub_ids: Optional[list[int]] = None,
    compatible_models: Optional[list[str]] = None,
    exclude_order_id: Optional[str] = None,
    exclude_mission_id: Optional[str] = None,
) -> tuple[set[str], dict[int, int]]:
    """
    Trả về:
    - reserved_battery_ids: tập hợp battery_id đã được gán trực tiếp trên Mission.battery_id
      hoặc MissionLeg.battery_id của các Mission đang active.
    - held_count_by_hub: số lượng chặng trung gian PENDING cũ (legacy) chưa gán cụ thể battery_id
      đang giữ chỗ ảo tại từng Relay Hub.
    """
    active_statuses = [
        MissionStatus.SCHEDULED,
        MissionStatus.IN_PROGRESS,
        MissionStatus.APPROVED,
        MissionStatus.FLYING,
        MissionStatus.ACTIVE_MISSION,
    ]

    m_stmt = select(Mission.battery_id).where(
        Mission.status.in_(active_statuses),
        Mission.battery_id.is_not(None),
    )
    if exclude_order_id:
        m_stmt = m_stmt.where(Mission.order_id != exclude_order_id)
    if exclude_mission_id:
        m_stmt = m_stmt.where(Mission.id != exclude_mission_id)

    reserved_result = await db.execute(m_stmt)
    reserved_battery_ids = {row[0] for row in reserved_result.all() if row[0]}

    leg_stmt = (
        select(MissionLeg.battery_id)
        .join(Mission, MissionLeg.mission_id == Mission.id)
        .where(
            Mission.status.in_(active_statuses),
            MissionLeg.status.in_([MissionLegStatus.PENDING, MissionLegStatus.IN_PROGRESS]),
            MissionLeg.battery_id.is_not(None),
        )
    )
    if exclude_order_id:
        leg_stmt = leg_stmt.where(Mission.order_id != exclude_order_id)
    if exclude_mission_id:
        leg_stmt = leg_stmt.where(Mission.id != exclude_mission_id)

    leg_reserved_res = await db.execute(leg_stmt)
    for row in leg_reserved_res.all():
        if row[0]:
            reserved_battery_ids.add(row[0])

    held_count_by_hub: dict[int, int] = {}
    if relay_hub_ids:
        hold_stmt = (
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
        if exclude_order_id:
            hold_stmt = hold_stmt.where(Mission.order_id != exclude_order_id)
        if exclude_mission_id:
            hold_stmt = hold_stmt.where(Mission.id != exclude_mission_id)

        pending_relay_legs_res = await db.execute(hold_stmt)
        for from_hub_id, other_drone_model in pending_relay_legs_res.all():
            if from_hub_id is None:
                continue
            other_compat = DRONE_BATTERY_COMPATIBILITY.get(
                other_drone_model or "",
                [other_drone_model] if other_drone_model else [],
            )
            if not compatible_models or not other_compat or bool(set(compatible_models) & set(other_compat)):
                held_count_by_hub[from_hub_id] = held_count_by_hub.get(from_hub_id, 0) + 1

    return reserved_battery_ids, held_count_by_hub


async def _resolve_drone_battery_capacity_wh(
    db: AsyncSession,
    drone_obj: Optional[Drone],
    all_active_batteries: list[Battery],
) -> tuple[list[str], float]:
    """
    Xác định danh sách model pin tương thích và dung lượng chuẩn 1 viên pin thực tế (Wh) của Drone.
    Tuyệt đối KHÔNG lấy nhầm dung lượng pin của dòng Drone khác tại cùng Hub.
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


async def _resolve_assigned_drone_battery(
    db: AsyncSession,
    origin_hub_id: int,
    drone_obj: Optional[Drone],
    requested_battery_id: Optional[str],
    all_active_batteries: list[Battery],
    reserved_battery_ids: set[str],
    strict: bool = False,
) -> tuple[list[str], Optional[Battery], float, int, float, float]:
    """
    Xác định viên pin được chọn/assign cho Drone tại Hub xuất phát và năng lượng thực tế (Wh) của viên pin đó.
    Returns:
        (compatible_models, assigned_battery, bat_capacity_wh, bat_charge_pct, bat_energy_wh, model_default_capacity_wh)
    """
    compatible_models, model_default_cap_wh = await _resolve_drone_battery_capacity_wh(
        db, drone_obj, all_active_batteries
    )

    clean_req_bat_id = (requested_battery_id or "").strip() or None
    if clean_req_bat_id:
        bat = next((b for b in all_active_batteries if b.id == clean_req_bat_id), None)
        if not bat:
            bat = await db.get(Battery, clean_req_bat_id)
        if not bat:
            raise HTTPException(
                status_code=404,
                detail=f"Không tìm thấy viên pin '{clean_req_bat_id}' trong hệ thống."
            )
        if bat.status != batteries_status.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {bat.id} hiện không ở trạng thái sẵn sàng hoạt động (Trạng thái: {bat.status})."
            )
        if bat.drone_id is not None and (not drone_obj or bat.drone_id != drone_obj.id):
            raise HTTPException(
                status_code=400,
                detail=f"Pin {bat.id} hiện đang được gắn trên Drone #{bat.drone_id}, không thể gán cho Drone #{drone_obj.id if drone_obj else '?'}."
            )
        if bat.current_hub_id != origin_hub_id and (not drone_obj or bat.drone_id != drone_obj.id):
            raise HTTPException(
                status_code=400,
                detail=f"Pin {bat.id} hiện đang ở Hub #{bat.current_hub_id}, không thuộc Hub xuất phát #{origin_hub_id}."
            )
        if compatible_models and bat.battery_model and bat.battery_model not in compatible_models:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Pin {bat.id} (Model: {bat.battery_model}) không tương thích với "
                    f"Drone {drone_obj.name if drone_obj else ''} ({drone_obj.model if drone_obj else ''}). "
                    f"Yêu cầu dòng pin: {', '.join(compatible_models)}."
                )
            )
        if bat.id in reserved_battery_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Pin {bat.id} hiện đã được giữ chỗ cho một nhiệm vụ bay đang hoạt động khác."
            )
        cap_wh, charge_pct, energy_wh = _compute_battery_energy_wh(bat, model_default_cap_wh)
        return compatible_models, bat, cap_wh, charge_pct, energy_wh, model_default_cap_wh

    # Nếu không truyền battery_id cụ thể:
    # 1. Tìm viên pin đang gắn sẵn trên Drone (nếu tương thích và chưa bị giữ chỗ)
    candidate_bats: list[Battery] = []
    if drone_obj:
        drone_installed = [
            b for b in all_active_batteries
            if b.drone_id == drone_obj.id
            and b.id not in reserved_battery_ids
            and (not compatible_models or (b.battery_model and b.battery_model in compatible_models))
        ]
        candidate_bats.extend(drone_installed)

    # 2. Tìm viên pin tương thích rảnh tại Origin Hub
    hub_spare = [
        b for b in all_active_batteries
        if b.current_hub_id == origin_hub_id
        and b.drone_id is None
        and b.id not in reserved_battery_ids
        and (not compatible_models or (b.battery_model and b.battery_model in compatible_models))
    ]

    # Ưu tiên theo năng lượng thực tế (Wh = capacity_wh * charge_pct / 100) cao nhất từ DB;
    # Nếu năng lượng tương đương, ưu tiên pin đang gắn sẵn trên Drone để tiết kiệm thời gian tháo lắp
    all_origin_candidates = candidate_bats + hub_spare
    all_origin_candidates.sort(
        key=lambda b: (
            -(_compute_battery_energy_wh(b, model_default_cap_wh)[2]),
            0 if (drone_obj and b.drone_id == drone_obj.id) else 1,
            -(b.charge_level_pct or 0),
            -float(b.capacity_wh or 0.0),
            b.id,
        )
    )
    assigned_bat = all_origin_candidates[0] if all_origin_candidates else None

    if assigned_bat:
        cap_wh, charge_pct, energy_wh = _compute_battery_energy_wh(assigned_bat, model_default_cap_wh)
        return compatible_models, assigned_bat, cap_wh, charge_pct, energy_wh, model_default_cap_wh

    if strict:
        model_label = f" ({', '.join(compatible_models)})" if compatible_models else ""
        raise HTTPException(
            status_code=400,
            detail=f"Tại Hub xuất phát #{origin_hub_id} hiện không có viên pin tương thích{model_label} nào khả dụng để gán cho Drone #{drone_obj.id if drone_obj else '?'}."
        )

    return compatible_models, None, model_default_cap_wh, 0, 0.0, model_default_cap_wh


async def check_relay_hub_battery_availability(
    db: AsyncSession,
    relay_hub_ids: list[int],
    hub_dict: dict,
    all_batteries: list,
    compatible_models: list[str],
    drone_id: Optional[Union[str, int]],
    drone_capacity_wh: float,
    legs_detail: list[dict],
    assigned_battery: Optional[Battery] = None,
    assigned_battery_energy_wh: Optional[float] = None,
    reserved_battery_ids: Optional[set[str]] = None,
    held_count_by_hub: Optional[dict[int, int]] = None,
) -> tuple[bool, Optional[str], list[HubBatteryStatus]]:
    """
    Kiểm tra tính khả thi của Relay Route & chỉ định chính xác viên pin thay thế tại từng Hub trung gian:
    1. Chặng 1 (xuất phát từ Origin Hub) kiểm tra trực tiếp dựa trên năng lượng của viên pin
       được assign cho Drone (`assigned_battery` / `assigned_battery_energy_wh`), bảo đảm dự phòng >= 20%
       (tiêu thụ <= 80% năng lượng pin).
    2. Tại mỗi Relay Hub trung gian (`relay_hub_ids[i]`):
       - Tìm các viên pin ACTIVE, chưa gắn vào drone khác (`drone_id IS NULL`), `charge_level_pct >= 100`,
         `battery_model` tương thích với Drone, và chưa bị giữ chỗ (`reserved_battery_ids`).
       - Chỉ định chính xác viên pin (`selected_battery`) sẽ được dùng để thay tại Relay Hub đó cho chặng kế tiếp
         (`legs_detail[i + 1]`).
       - Kiểm tra năng lượng của chặng kế tiếp (`legs_detail[i + 1]`) dựa trên năng lượng của chính viên pin
         được chỉ định thay thế (`selected_battery_energy_wh * 0.8`).

    Returns: (feasible, infeasible_reason, relay_battery_status_list)
    """
    if reserved_battery_ids is None or held_count_by_hub is None:
        res_ids, holds = await _get_reserved_batteries_and_virtual_holds(
            db=db,
            relay_hub_ids=relay_hub_ids,
            compatible_models=compatible_models,
        )
        if reserved_battery_ids is None:
            reserved_battery_ids = res_ids
        if held_count_by_hub is None:
            held_count_by_hub = holds

    infeasible_reasons: list[str] = []
    model_str = f" ({', '.join(compatible_models)})" if compatible_models else ""

    # ── 1. Kiểm tra Chặng 1 dựa trên năng lượng viên pin được assign cho Drone tại Origin Hub ──
    origin_cap_wh, origin_charge_pct, origin_energy_wh = _compute_battery_energy_wh(
        assigned_battery, drone_capacity_wh
    )
    if assigned_battery_energy_wh is not None and assigned_battery_energy_wh > 0:
        origin_energy_wh = float(assigned_battery_energy_wh)

    max_safe_origin_wh = round(origin_energy_wh * 0.8, 2)

    if legs_detail:
        leg0 = legs_detail[0]
        leg0_energy = float(leg0.get("energy_wh") or 0.0)
        if leg0_energy <= 0 and leg0.get("distance_km") is not None:
            leg0_energy = float(
                predict_flight_energy(float(leg0["distance_km"]), float(leg0.get("payload_kg") or 0.0))["energy_wh"]
            )

        if assigned_battery is not None:
            leg0["battery_id"] = assigned_battery.id
            leg0["battery_serial"] = assigned_battery.serial_number
            leg0["battery_model"] = assigned_battery.battery_model
            leg0["battery_capacity_wh"] = round(origin_cap_wh, 2)
            leg0["battery_charge_pct"] = origin_charge_pct
            leg0["battery_energy_wh"] = round(origin_energy_wh, 2)
            leg0_cons_pct = int(round((leg0_energy / origin_energy_wh) * 100)) if origin_energy_wh > 0 else 100
            leg0["battery_consumption_pct"] = leg0_cons_pct
            leg0["remaining_battery_pct"] = max(0, 100 - leg0_cons_pct)

            if leg0_energy > max_safe_origin_wh:
                infeasible_reasons.append(
                    f"Chặng 1 ({leg0.get('from_hub_code', '?')} -> {leg0.get('to_hub_code', '?')}) "
                    f"tiêu thụ {leg0_energy:.1f} Wh (~{leg0_cons_pct}% năng lượng pin {assigned_battery.id}), "
                    f"vượt mức an toàn 80% ({max_safe_origin_wh:.1f} Wh / {origin_energy_wh:.1f} Wh, "
                    f"sạc {origin_charge_pct}%, cần dự phòng tối thiểu 20%)"
                )
        else:
            leg0_cons_pct = int(round((leg0_energy / origin_energy_wh) * 100)) if origin_energy_wh > 0 else 100
            leg0["battery_consumption_pct"] = leg0_cons_pct
            leg0["remaining_battery_pct"] = max(0, 100 - leg0_cons_pct)
            infeasible_reasons.append(
                f"Hub xuất phát không có viên pin tương thích{model_str} khả dụng để gán cho Drone"
            )
            if origin_energy_wh > 0 and leg0_energy > max_safe_origin_wh:
                infeasible_reasons.append(
                    f"Chặng 1 ({leg0.get('from_hub_code', '?')} -> {leg0.get('to_hub_code', '?')}) "
                    f"tiêu thụ {leg0_energy:.1f} Wh (~{leg0_cons_pct}% pin), vượt mức an toàn 80% "
                    f"({max_safe_origin_wh:.1f} Wh / {origin_energy_wh:.1f} Wh{model_str}, cần dự phòng tối thiểu 20%)"
                )

    # ── 2. Kiểm tra Battery Inventory & chỉ định chính xác pin thay thế tại từng Hub trung gian ──
    used_relay_bat_ids: set[str] = set()
    if assigned_battery:
        used_relay_bat_ids.add(assigned_battery.id)

    relay_statuses: list[HubBatteryStatus] = []

    for relay_idx, hub_id in enumerate(relay_hub_ids):
        hub = hub_dict.get(hub_id)
        hub_code = (hub.code or hub.name) if hub else str(hub_id)
        hub_name = hub.name if hub else str(hub_id)

        next_leg = legs_detail[relay_idx + 1] if (relay_idx + 1) < len(legs_detail) else None
        next_leg_energy = 0.0
        if next_leg is not None:
            next_leg_energy = float(next_leg.get("energy_wh") or 0.0)
            if next_leg_energy <= 0 and next_leg.get("distance_km") is not None:
                next_leg_energy = float(
                    predict_flight_energy(float(next_leg["distance_km"]), float(next_leg.get("payload_kg") or 0.0))["energy_wh"]
                )

        # Lấy tất cả pin tương thích tại Relay Hub chưa bị giữ chỗ và có mức pin > 0%
        hub_bats = [
            b for b in all_batteries
            if b.current_hub_id == hub_id
            and b.drone_id is None
            and (b.charge_level_pct or 0) > 0
            and b.id not in reserved_battery_ids
            and (
                not compatible_models
                or (b.battery_model and b.battery_model in compatible_models)
            )
        ]

        # Sắp xếp ưu tiên:
        # 1. Viên pin có năng lượng thực tế đủ an toàn cho chặng kế tiếp (available_energy_wh * 0.8 >= next_leg_energy)
        # 2. Năng lượng thực tế (Wh) từ database cao nhất
        # 3. Mức sạc (%) từ database cao nhất
        # 4. Dung lượng danh định (capacity_wh) từ database cao nhất
        # 5. ID tăng dần
        hub_bats.sort(
            key=lambda b: (
                0 if (_compute_battery_energy_wh(b, drone_capacity_wh)[2] * 0.8 >= next_leg_energy) else 1,
                -(_compute_battery_energy_wh(b, drone_capacity_wh)[2]),
                -(b.charge_level_pct or 0),
                -float(b.capacity_wh or 0.0),
                b.id,
            )
        )

        raw_ready_count = len(hub_bats)
        held_by_others = held_count_by_hub.get(hub_id, 0)
        unheld_bats = hub_bats[held_by_others:] if held_by_others < len(hub_bats) else []
        available_for_this_stop = [b for b in unheld_bats if b.id not in used_relay_bat_ids]
        effective_ready_count = len(available_for_this_stop)

        selected_bat: Optional[Battery] = available_for_this_stop[0] if available_for_this_stop else None
        if selected_bat:
            used_relay_bat_ids.add(selected_bat.id)
            sel_cap_wh, sel_charge_pct, sel_energy_wh = _compute_battery_energy_wh(selected_bat, drone_capacity_wh)
            sel_max_safe_wh = round(sel_energy_wh * 0.8, 2)

            if next_leg is not None:
                next_leg["battery_id"] = selected_bat.id
                next_leg["battery_serial"] = selected_bat.serial_number
                next_leg["battery_model"] = selected_bat.battery_model
                next_leg["battery_capacity_wh"] = round(sel_cap_wh, 2)
                next_leg["battery_charge_pct"] = sel_charge_pct
                next_leg["battery_energy_wh"] = round(sel_energy_wh, 2)
                leg_cons_pct = int(round((next_leg_energy / sel_energy_wh) * 100)) if sel_energy_wh > 0 else 100
                next_leg["battery_consumption_pct"] = leg_cons_pct
                next_leg["remaining_battery_pct"] = max(0, 100 - leg_cons_pct)

            if next_leg is not None and next_leg_energy > sel_max_safe_wh:
                leg_pct = int(round((next_leg_energy / sel_energy_wh) * 100)) if sel_energy_wh > 0 else 100
                infeasible_reasons.append(
                    f"Chặng {relay_idx + 2} ({next_leg.get('from_hub_code', '?')} -> {next_leg.get('to_hub_code', '?')}) "
                    f"tiêu thụ {next_leg_energy:.1f} Wh (~{leg_pct}% pin thay thế {selected_bat.id}), "
                    f"vượt mức an toàn 80% ({sel_max_safe_wh:.1f} Wh / {sel_energy_wh:.1f} Wh{model_str}, "
                    f"cần dự phòng tối thiểu 20%)"
                )
                hub_feasible = False
            else:
                hub_feasible = True

            relay_statuses.append(HubBatteryStatus(
                hub_id=hub_id,
                hub_code=hub_code,
                hub_name=hub_name,
                ready_count=effective_ready_count,
                compatible_models=compatible_models,
                feasible=hub_feasible,
                selected_battery_id=selected_bat.id,
                selected_battery_serial=selected_bat.serial_number,
                selected_battery_model=selected_bat.battery_model,
                selected_battery_capacity_wh=round(sel_cap_wh, 2),
                selected_battery_charge_pct=sel_charge_pct,
                selected_battery_energy_wh=round(sel_energy_wh, 2),
                swap_battery_id=selected_bat.id,
                swap_battery_serial=selected_bat.serial_number,
                available_batteries=[
                    RelayHubBatteryOptionSchema(
                        id=b.id,
                        serial_number=b.serial_number,
                        battery_model=b.battery_model,
                        capacity_wh=float(b.capacity_wh or drone_capacity_wh),
                        charge_level_pct=int(b.charge_level_pct or 0),
                        available_energy_wh=_compute_battery_energy_wh(b, drone_capacity_wh)[2],
                    )
                    for b in available_for_this_stop
                ],
            ))
        else:
            hub_feasible = False
            fallback_max_safe_wh = round(drone_capacity_wh * 0.8, 2)
            if next_leg is not None:
                leg_cons_pct = int(round((next_leg_energy / drone_capacity_wh) * 100)) if drone_capacity_wh > 0 else 100
                next_leg["battery_consumption_pct"] = leg_cons_pct
                next_leg["remaining_battery_pct"] = max(0, 100 - leg_cons_pct)
                if drone_capacity_wh > 0 and next_leg_energy > fallback_max_safe_wh:
                    infeasible_reasons.append(
                        f"Chặng {relay_idx + 2} ({next_leg.get('from_hub_code', '?')} -> {next_leg.get('to_hub_code', '?')}) "
                        f"tiêu thụ {next_leg_energy:.1f} Wh (~{leg_cons_pct}% pin), vượt mức an toàn 80% "
                        f"({fallback_max_safe_wh:.1f} Wh / {drone_capacity_wh:.1f} Wh{model_str}, cần dự phòng tối thiểu 20%)"
                    )

            if raw_ready_count > 0 and held_by_others > 0:
                infeasible_reasons.append(
                    f"Hub {hub_name} có {raw_ready_count} pin tương thích{model_str} nhưng toàn bộ đã được giữ chỗ ({held_by_others} pin) cho nhiệm vụ khác"
                )
            else:
                infeasible_reasons.append(
                    f"Hub {hub_name} không có pin tương thích{model_str} khả dụng trong kho (đủ năng lượng, chưa gắn drone và chưa bị giữ chỗ)"
                )

            relay_statuses.append(HubBatteryStatus(
                hub_id=hub_id,
                hub_code=hub_code,
                hub_name=hub_name,
                ready_count=0,
                compatible_models=compatible_models,
                feasible=False,
                selected_battery_id=None,
                available_batteries=[],
            ))

    if infeasible_reasons:
        return False, " | ".join(infeasible_reasons), relay_statuses
    return True, None, relay_statuses


async def compute_mission_routes_internal(
    db: AsyncSession,
    order: Order,
    drone_id: Optional[Union[str, int]] = None,
    battery_id: Optional[str] = None,
    strict_battery: bool = False,
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
            ).order_by(Drone.id)
        )
        available_drones = result.scalars().all()
        if available_drones:
            suggested_drone_id = available_drones[0].id
            if not drone_obj:
                drone_obj = available_drones[0]

    # 3. Lấy toàn bộ pin ACTIVE (cả pin rời và pin đang gắn trên drone)
    result = await db.execute(
        select(Battery).where(Battery.status == batteries_status.ACTIVE).order_by(Battery.id)
    )
    all_active_batteries = result.scalars().all()
    all_batteries = [b for b in all_active_batteries if b.drone_id is None]

    # 4. Lấy danh sách pin đã bị giữ chỗ bởi các Mission/MissionLeg đang hoạt động khác
    all_hub_ids = [h.id for h in hubs]
    compatible_models_prelim, _ = await _resolve_drone_battery_capacity_wh(db, drone_obj, all_active_batteries)
    reserved_battery_ids, held_count_by_hub = await _get_reserved_batteries_and_virtual_holds(
        db=db,
        relay_hub_ids=all_hub_ids,
        compatible_models=compatible_models_prelim,
        exclude_order_id=order.id,
    )

    # 5. Xác định viên pin được assign cho Drone tại Origin Hub và năng lượng thực tế (Wh) của pin đó
    (
        compatible_models,
        assigned_battery,
        origin_bat_capacity_wh,
        origin_bat_charge_pct,
        origin_bat_energy_wh,
        drone_capacity_wh,
    ) = await _resolve_assigned_drone_battery(
        db=db,
        origin_hub_id=origin_hub.id,
        drone_obj=drone_obj,
        requested_battery_id=battery_id,
        all_active_batteries=all_active_batteries,
        reserved_battery_ids=reserved_battery_ids,
        strict=strict_battery,
    )

    # 6. Tính thời gian chờ pin & năng lượng pin thay thế tốt nhất tại từng Hub trung gian
    hub_wait_times: dict[int, float] = {}
    hub_best_energy_wh: dict[int, float] = {}
    for h in hubs:
        h_bats = [
            b for b in all_batteries
            if b.current_hub_id == h.id
            and b.id not in reserved_battery_ids
            and (not assigned_battery or b.id != assigned_battery.id)
            and (not compatible_models or (b.battery_model and b.battery_model in compatible_models))
        ]
        if not h_bats:
            hub_wait_times[h.id] = float('inf')
            hub_best_energy_wh[h.id] = 0.0
        else:
            ready_h_bats = [b for b in h_bats if (b.charge_level_pct or 0) > 0]
            held_cnt = held_count_by_hub.get(h.id, 0)
            effective_ready_bats = ready_h_bats[held_cnt:] if held_cnt < len(ready_h_bats) else []
            if effective_ready_bats:
                hub_wait_times[h.id] = 0
                hub_best_energy_wh[h.id] = max(
                    _compute_battery_energy_wh(b, drone_capacity_wh)[2] for b in effective_ready_bats
                )
            else:
                hub_wait_times[h.id] = float('inf')
                hub_best_energy_wh[h.id] = 0.0

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
            # Chặng 1 (xuất phát từ Origin Hub) dùng năng lượng thực tế của viên pin được assign cho Drone;
            # Các chặng tiếp theo (xuất phát từ Relay Hub) dùng năng lượng của pin thay thế tại Relay Hub đó.
            if current_node == origin_hub.id:
                leg_available_energy_wh = origin_bat_energy_wh if origin_bat_energy_wh > 0 else drone_capacity_wh
            else:
                leg_available_energy_wh = hub_best_energy_wh.get(current_node, drone_capacity_wh)

            for n_hub in hubs:
                if n_hub.id == current_node or n_hub.id in exclude_nodes:
                    continue

                dist_km = haversine_distance(
                    curr_h.latitude or 0, curr_h.longitude or 0,
                    n_hub.latitude or 0, n_hub.longitude or 0
                )
                pred = predict_flight_energy(dist_km, payload)
                energy_wh = pred["energy_wh"]

                # Kiểm tra giới hạn năng lượng của viên pin ở chặng hiện tại (tối đa 80% -> dự phòng >= 20%)
                if energy_wh > leg_available_energy_wh * 0.8:
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

    # ── 7. Kiểm tra Battery feasibility, gán pin xuất phát & chỉ định chính xác pin thay thế ở từng Relay Hub ──
    max_safe_origin_energy_wh = round(origin_bat_energy_wh * 0.8, 2)
    compat_label = f" ({', '.join(compatible_models)})" if compatible_models else ""

    routes_schemas: list[RouteOptionSchema] = []
    raw_data_list: list[dict] = []

    for entry in raw_routes:
        k = entry["key"]
        d = entry["data"]
        path = d.get("path", [])
        legs_raw = d.get("legs_detail", [])
        relay_hub_ids = [nid for nid in path if nid not in (origin_hub.id, dest_hub.id)]

        if not relay_hub_ids:
            # Direct route — kiểm tra năng lượng chặng duy nhất dựa trên năng lượng của pin được assign cho Drone
            leg0_energy = float(legs_raw[0].get("energy_wh") or d["predictedEnergyWh"]) if legs_raw else float(d["predictedEnergyWh"])
            batt_pct = int(round((leg0_energy / origin_bat_energy_wh) * 100)) if origin_bat_energy_wh > 0 else 100
            rem_batt_pct = max(0, 100 - batt_pct)

            if legs_raw:
                legs_raw[0]["battery_id"] = assigned_battery.id if assigned_battery else None
                legs_raw[0]["battery_serial"] = assigned_battery.serial_number if assigned_battery else None
                legs_raw[0]["battery_model"] = assigned_battery.battery_model if assigned_battery else None
                legs_raw[0]["battery_capacity_wh"] = round(origin_bat_capacity_wh, 2) if assigned_battery else round(drone_capacity_wh, 2)
                legs_raw[0]["battery_charge_pct"] = origin_bat_charge_pct if assigned_battery else None
                legs_raw[0]["battery_energy_wh"] = round(origin_bat_energy_wh, 2)
                legs_raw[0]["battery_consumption_pct"] = batt_pct
                legs_raw[0]["remaining_battery_pct"] = rem_batt_pct

            if assigned_battery is None:
                feasible = False
                infeasible_reason = (
                    f"Hub xuất phát không có viên pin tương thích{compat_label} khả dụng để gán cho Drone."
                )
            elif leg0_energy > origin_bat_energy_wh:
                feasible = False
                infeasible_reason = (
                    f"Năng lượng cần ({leg0_energy:.1f} Wh, ~{batt_pct}% năng lượng pin {assigned_battery.id}) "
                    f"vượt năng lượng hiện có của viên pin được gán{compat_label} "
                    f"({origin_bat_energy_wh:.1f} Wh / {origin_bat_capacity_wh:.1f} Wh, sạc {origin_bat_charge_pct}%). "
                    f"Drone không đủ pin để bay trực tiếp (tối đa {max_safe_origin_energy_wh:.1f} Wh để giữ dự phòng 20%)."
                )
            elif leg0_energy > max_safe_origin_energy_wh:
                feasible = False
                infeasible_reason = (
                    f"Năng lượng cần ({leg0_energy:.1f} Wh, ~{batt_pct}% năng lượng pin {assigned_battery.id}) "
                    f"vượt ngưỡng an toàn 80% của viên pin được gán{compat_label} "
                    f"({max_safe_origin_energy_wh:.1f} Wh / {origin_bat_energy_wh:.1f} Wh, sạc {origin_bat_charge_pct}%). "
                    f"Không bảo đảm mức dự phòng tối thiểu 20%."
                )
            else:
                feasible = True
                infeasible_reason = None
            hub_statuses: list[HubBatteryStatus] = []
        else:
            # Multi-hub Relay Route — kiểm tra năng lượng từng chặng & chỉ định chính xác pin thay thế ở từng Relay Hub
            feasible, infeasible_reason, hub_statuses = await check_relay_hub_battery_availability(
                db=db,
                relay_hub_ids=relay_hub_ids,
                hub_dict=hub_dict,
                all_batteries=all_batteries,
                compatible_models=compatible_models,
                drone_id=suggested_drone_id,
                drone_capacity_wh=drone_capacity_wh,
                legs_detail=legs_raw,
                assigned_battery=assigned_battery,
                assigned_battery_energy_wh=origin_bat_energy_wh,
                reserved_battery_ids=reserved_battery_ids,
                held_count_by_hub=held_count_by_hub,
            )
            # Cập nhật lại chuỗi hiển thị relayHubs kèm mã pin thay thế cụ thể tại từng Hub trung gian
            updated_relay_labels: list[str] = []
            for r_idx, st in enumerate(hub_statuses):
                prev_leg_e = float(legs_raw[r_idx].get("energy_wh") or 0.0) if r_idx < len(legs_raw) else 0.0
                if st.selected_battery_id:
                    updated_relay_labels.append(
                        f"{st.hub_name} (Tốn {prev_leg_e:.1f}Wh) - Thay pin {st.selected_battery_id} "
                        f"({st.selected_battery_serial or st.selected_battery_model or 'Ready'}, "
                        f"{st.selected_battery_energy_wh or st.selected_battery_capacity_wh or 0:.0f}Wh, "
                        f"{st.selected_battery_charge_pct or 0}%)"
                    )
                else:
                    updated_relay_labels.append(
                        f"{st.hub_name} (Tốn {prev_leg_e:.1f}Wh) - Thiếu pin khả dụng tương thích"
                    )
            d["relayHubs"] = updated_relay_labels

            leg_pcts = [int(leg.get("battery_consumption_pct") or 0) for leg in legs_raw if leg.get("battery_consumption_pct") is not None]
            batt_pct = max(leg_pcts) if leg_pcts else (int(round((float(d["predictedEnergyWh"]) / origin_bat_energy_wh) * 100)) if origin_bat_energy_wh > 0 else 50)
            rem_batt_pct = max(0, 100 - batt_pct)

        risk = "LOW" if batt_pct < 60 else ("MEDIUM" if batt_pct <= 80 else "HIGH")

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
                    energy_wh=leg.get("energy_wh"),
                    battery_id=leg.get("battery_id"),
                    battery_serial=leg.get("battery_serial"),
                    battery_model=leg.get("battery_model"),
                    battery_capacity_wh=leg.get("battery_capacity_wh"),
                    battery_charge_pct=leg.get("battery_charge_pct"),
                    battery_energy_wh=leg.get("battery_energy_wh"),
                    battery_consumption_pct=leg.get("battery_consumption_pct"),
                    remaining_battery_pct=leg.get("remaining_battery_pct"),
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
            suggestedDroneId=suggested_drone_id,
            assignedBatteryId=assigned_battery.id if assigned_battery else None,
            assignedBatterySerial=assigned_battery.serial_number if assigned_battery else None,
            assignedBatteryModel=assigned_battery.battery_model if assigned_battery else None,
            assignedBatteryCapacityWh=round(origin_bat_capacity_wh, 2) if assigned_battery else None,
            assignedBatteryChargePct=origin_bat_charge_pct if assigned_battery else None,
            assignedBatteryEnergyWh=round(origin_bat_energy_wh, 2) if assigned_battery else None,
            feasible=feasible,
            infeasible_reason=infeasible_reason,
            relay_battery_status=hub_statuses,
        )
        routes_schemas.append(schema)
        raw_data_list.append(d)

    # ĐÁNH DẤU RECOMMEND CHO TUYẾN KHẢ THI TỐI ƯU HIỆU SUẤT NĂNG LƯỢNG NHẤT
    if routes_schemas:
        best_idx = None
        min_energy = float('inf')
        for idx, s in enumerate(routes_schemas):
            if s.feasible and s.predictedEnergyWh < min_energy:
                min_energy = s.predictedEnergyWh
                best_idx = idx

        origin_bat_note = (
            f"Pin xuất phát: {assigned_battery.id} ({assigned_battery.serial_number}, {origin_bat_energy_wh:.1f} Wh)."
            if assigned_battery else ""
        )

        for idx, (s, d) in enumerate(zip(routes_schemas, raw_data_list)):
            swap_notes = [
                f"{st.hub_name}: thay pin {st.selected_battery_id} ({st.selected_battery_serial})"
                for st in s.relay_battery_status
                if st.selected_battery_id
            ]
            swap_summary = f" Kế hoạch thay pin trung gian: {'; '.join(swap_notes)}." if swap_notes else ""

            if not s.feasible:
                s.recommended = False
                s.reason = f"Tuyến không khả thi: {s.infeasible_reason}"
            elif idx == best_idx:
                s.recommended = True
                s.reason = (
                    f"Được AI đề xuất: Tuyến đường khả thi tối ưu hiệu suất năng lượng nhất "
                    f"(tổng {s.predictedEnergyWh:.1f} Wh, tiêu thụ tối đa ~{s.batteryConsumptionPct}% pin/chặng, "
                    f"dự phòng {s.remainingBatteryPct}%). {origin_bat_note}{swap_summary}"
                ).strip()
            else:
                s.recommended = False
                if "FAST" in s.routeId:
                    s.reason = f"Tuyến bay nhanh nhất: Dự kiến {s.predictedDurationMin} phút, thời gian chờ sạc {d.get('wait_time', 0)} phút. {origin_bat_note}{swap_summary}".strip()
                elif "SHORT" in s.routeId:
                    s.reason = f"Quãng đường ngắn nhất: Cự ly {s.distanceKm} km, tiêu thụ {s.predictedEnergyWh:.1f} Wh. {origin_bat_note}{swap_summary}".strip()
                elif "DIR" in s.routeId:
                    s.reason = f"Tuyến bay thẳng trực tiếp: Cự ly {s.distanceKm} km, không qua trạm sạc trung gian. {origin_bat_note}".strip()
                else:
                    s.reason = f"Tuyến cân bằng: Cự ly {s.distanceKm} km, thời gian {s.predictedDurationMin} phút. {origin_bat_note}{swap_summary}".strip()

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

        # Xác định capacity_wh và viên pin xuất phát của Mission
        drone_capacity_wh = DEFAULT_DRONE_BATTERY_CAPACITY_WH.get(
            drone_obj.model if drone_obj else "",
            130.0
        )
        if drone_obj:
            _, drone_capacity_wh = await _resolve_drone_battery_capacity_wh(db, drone_obj, all_batteries)

        m_legs = legs_by_mission.get(m.id, [])
        origin_bat_id = str(m.battery_id) if m.battery_id else (
            str(m_legs[0].battery_id) if (m_legs and m_legs[0].battery_id) else None
        )
        origin_bat_obj = battery_by_id.get(origin_bat_id) if origin_bat_id else None
        if origin_bat_obj and origin_bat_obj.capacity_wh:
            drone_capacity_wh = float(origin_bat_obj.capacity_wh)

        origin_bat_cap, origin_bat_charge, origin_bat_energy = _compute_battery_energy_wh(
            origin_bat_obj, drone_capacity_wh
        )

        legs_detail: list[RouteLegSchema] = []
        relay_hubs: list[str] = []
        relay_battery_status_list: list[HubBatteryStatus] = []
        leg_energies: list[float] = []
        leg_consumption_pcts: list[int] = []

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

                leg_bat_id = str(leg.battery_id) if leg.battery_id else (origin_bat_id if idx == 0 else None)
                leg_bat_obj = battery_by_id.get(leg_bat_id) if leg_bat_id else None
                leg_cap, leg_charge, leg_energy_avail = _compute_battery_energy_wh(
                    leg_bat_obj, drone_capacity_wh
                )
                leg_pct = int(round((e / leg_energy_avail) * 100)) if leg_energy_avail > 0 else 50
                leg_rem_pct = max(0, 100 - leg_pct)
                leg_consumption_pcts.append(leg_pct)

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
                        energy_wh=round(e, 2),
                        battery_id=leg_bat_id,
                        battery_serial=leg_bat_obj.serial_number if leg_bat_obj else None,
                        battery_model=leg_bat_obj.battery_model if leg_bat_obj else None,
                        battery_capacity_wh=round(leg_cap, 2),
                        battery_charge_pct=leg_charge if leg_bat_obj else None,
                        battery_energy_wh=round(leg_energy_avail, 2),
                        battery_consumption_pct=leg_pct,
                        remaining_battery_pct=leg_rem_pct,
                    )
                )

                if idx < len(m_legs) - 1:
                    hub_label = h2.name if h2 else to_code
                    next_leg_obj = m_legs[idx + 1]
                    swap_bat_id = str(next_leg_obj.battery_id) if next_leg_obj.battery_id else None
                    swap_bat_obj = battery_by_id.get(swap_bat_id) if swap_bat_id else None
                    if swap_bat_obj:
                        s_cap, s_charge, s_energy = _compute_battery_energy_wh(swap_bat_obj, drone_capacity_wh)
                        relay_hubs.append(
                            f"{hub_label} (Tốn {e:.1f}Wh) - Thay pin {swap_bat_obj.id} "
                            f"({swap_bat_obj.serial_number or swap_bat_obj.battery_model or 'Ready'}, "
                            f"{s_energy:.0f}Wh, {s_charge}%)"
                        )
                        relay_battery_status_list.append(
                            HubBatteryStatus(
                                hub_id=leg.to_hub_id,
                                hub_code=h2.code if h2 else to_code,
                                hub_name=hub_label,
                                ready_count=1,
                                compatible_models=[swap_bat_obj.battery_model] if swap_bat_obj.battery_model else [],
                                feasible=True,
                                selected_battery_id=swap_bat_obj.id,
                                selected_battery_serial=swap_bat_obj.serial_number,
                                selected_battery_model=swap_bat_obj.battery_model,
                                selected_battery_capacity_wh=round(s_cap, 2),
                                selected_battery_charge_pct=s_charge,
                                selected_battery_energy_wh=round(s_energy, 2),
                                swap_battery_id=swap_bat_obj.id,
                                swap_battery_serial=swap_bat_obj.serial_number,
                                available_batteries=[
                                    RelayHubBatteryOptionSchema(
                                        id=swap_bat_obj.id,
                                        serial_number=swap_bat_obj.serial_number,
                                        battery_model=swap_bat_obj.battery_model,
                                        capacity_wh=round(s_cap, 2),
                                        charge_level_pct=s_charge,
                                        available_energy_wh=round(s_energy, 2),
                                        current_hub_id=swap_bat_obj.current_hub_id,
                                    )
                                ],
                            )
                        )
                    else:
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
                leg_pct = int(round((e / origin_bat_energy) * 100)) if origin_bat_energy > 0 else 50
                leg_rem_pct = max(0, 100 - leg_pct)
                leg_consumption_pcts.append(leg_pct)
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
                        energy_wh=round(e, 2),
                        battery_id=origin_bat_id,
                        battery_serial=origin_bat_obj.serial_number if origin_bat_obj else None,
                        battery_model=origin_bat_obj.battery_model if origin_bat_obj else None,
                        battery_capacity_wh=round(origin_bat_cap, 2),
                        battery_charge_pct=origin_bat_charge if origin_bat_obj else None,
                        battery_energy_wh=round(origin_bat_energy, 2),
                        battery_consumption_pct=leg_pct,
                        remaining_battery_pct=leg_rem_pct,
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
        if leg_consumption_pcts:
            batt_pct = max(leg_consumption_pcts)
        else:
            batt_pct = int(round((max_leg_energy_wh / origin_bat_energy) * 100)) if origin_bat_energy else 50
        rem_batt_pct = max(0, 100 - batt_pct)

        if ai_pred and ai_pred.risk_level:
            risk = str(ai_pred.risk_level).upper()
        else:
            risk = "LOW" if batt_pct < 60 else ("MEDIUM" if batt_pct <= 80 else "HIGH")

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
            assignedBatteryId=origin_bat_id,
            assignedBatterySerial=origin_bat_obj.serial_number if origin_bat_obj else None,
            assignedBatteryModel=origin_bat_obj.battery_model if origin_bat_obj else None,
            assignedBatteryCapacityWh=round(origin_bat_cap, 2) if origin_bat_obj else None,
            assignedBatteryChargePct=origin_bat_charge if origin_bat_obj else None,
            assignedBatteryEnergyWh=round(origin_bat_energy, 2) if origin_bat_obj else None,
            relay_battery_status=relay_battery_status_list,
        )

        m_dict["battery_id"] = origin_bat_id or m.battery_id
        m_dict["battery_serial"] = origin_bat_obj.serial_number if origin_bat_obj else None
        m_dict["battery_model"] = origin_bat_obj.battery_model if origin_bat_obj else None
        m_dict["battery_capacity_wh"] = round(origin_bat_cap, 2) if origin_bat_obj else None
        m_dict["battery_charge_pct"] = origin_bat_charge if origin_bat_obj else None
        m_dict["battery_energy_wh"] = round(origin_bat_energy, 2) if origin_bat_obj else None
        m_dict["assignedBatteryId"] = origin_bat_id or m.battery_id
        m_dict["assignedBatterySerial"] = origin_bat_obj.serial_number if origin_bat_obj else None
        m_dict["assignedBatteryModel"] = origin_bat_obj.battery_model if origin_bat_obj else None
        m_dict["assignedBatteryCapacityWh"] = round(origin_bat_cap, 2) if origin_bat_obj else None
        m_dict["assignedBatteryChargePct"] = origin_bat_charge if origin_bat_obj else None
        m_dict["assignedBatteryEnergyWh"] = round(origin_bat_energy, 2) if origin_bat_obj else None
        m_dict["relay_battery_status"] = relay_battery_status_list
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


@router.get("/eligible-batteries", summary="Lấy danh sách Pin khả dụng tương thích với Drone khi tạo Mission")
async def get_eligible_batteries_for_mission_planning(
    drone_id: str = Query(..., description="ID của Drone được chọn thực hiện nhiệm vụ"),
    order_id: Optional[str] = Query(None, description="ID đơn hàng để lọc theo Hub xuất phát"),
    hub_id: Optional[int] = Query(None, description="ID Hub xuất phát (nếu không truyền order_id)"),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API] Lấy danh sách Pin khả dụng tương thích với Drone được chọn**
    - Trả về các viên pin tại Hub xuất phát (hoặc đang gắn sẵn trên Drone) tương thích với model của Drone,
      kèm `capacity_wh`, `charge_level_pct`, và `available_energy_wh` thực tế.
    """
    drone = await db.get(Drone, drone_id)
    if not drone:
        raise HTTPException(status_code=404, detail=f"Drone #{drone_id} not found")

    target_hub_id = hub_id
    if not target_hub_id and order_id:
        order = await db.get(Order, order_id.strip())
        if not order:
            raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")
        target_hub_id = order.origin_hub_id
    if not target_hub_id:
        target_hub_id = drone.current_hub_id

    reserved_battery_ids, _ = await _get_reserved_batteries_and_virtual_holds(db)
    compatible_models = await get_compatible_battery_models(db, drone.model)
    default_cap = DEFAULT_DRONE_BATTERY_CAPACITY_WH.get(drone.model or "", 130.0)

    bats_res = await db.execute(
        select(Battery).where(
            Battery.status == batteries_status.ACTIVE,
            or_(
                Battery.current_hub_id == target_hub_id,
                Battery.drone_id == drone.id,
            )
        ).order_by(Battery.charge_level_pct.desc(), Battery.id.asc())
    )
    all_bats = bats_res.scalars().all()

    eligible_list = []
    for b in all_bats:
        if b.id in reserved_battery_ids:
            continue
        if target_hub_id is not None and b.current_hub_id != target_hub_id and b.drone_id != drone.id:
            continue
        if b.drone_id is not None and b.drone_id != drone.id:
            continue
        if compatible_models and b.battery_model and b.battery_model not in compatible_models:
            continue
        cap_wh, charge_pct, avail_energy_wh = _compute_battery_energy_wh(b, default_cap)
        eligible_list.append({
            "id": b.id,
            "serial_number": b.serial_number,
            "battery_model": b.battery_model,
            "capacity_wh": round(cap_wh, 2),
            "charge_level_pct": charge_pct,
            "available_energy_wh": round(avail_energy_wh, 2),
            "current_hub_id": b.current_hub_id,
            "drone_id": b.drone_id,
            "is_installed_on_drone": b.drone_id == drone.id,
            "available_status": "READY",
        })

    eligible_list.sort(
        key=lambda item: (
            1 if item["is_installed_on_drone"] else 0,
            item["available_energy_wh"],
            item["charge_level_pct"],
        ),
        reverse=True,
    )

    return {
        "order_id": order_id.strip() if order_id else None,
        "origin_hub_id": target_hub_id,
        "drone_id": drone.id,
        "drone_name": drone.name,
        "drone_model": drone.model,
        "compatible_battery_models": compatible_models,
        "default_battery_id": eligible_list[0]["id"] if eligible_list else None,
        "batteries": eligible_list,
    }


@router.post("/mission-planning/analyze", response_model=MissionPlanningAnalyzeResponse)
async def analyze_mission_planning(
    request: MissionPlanningAnalyzeRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db)
):
    order = await db.get(Order, request.orderId.strip() if request.orderId else request.orderId)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    requested_battery_id = (request.batteryId or request.battery_id or "").strip() or None
    routes_schemas, _ = await compute_mission_routes_internal(
        db=db,
        order=order,
        drone_id=request.droneId,
        battery_id=requested_battery_id,
        strict_battery=bool(requested_battery_id),
    )
    first_route = routes_schemas[0] if routes_schemas else None
    return MissionPlanningAnalyzeResponse(
        orderId=order.id,
        droneId=first_route.suggestedDroneId if first_route else request.droneId,
        assignedBatteryId=first_route.assignedBatteryId if first_route else requested_battery_id,
        assignedBatterySerial=first_route.assignedBatterySerial if first_route else None,
        assignedBatteryModel=first_route.assignedBatteryModel if first_route else None,
        assignedBatteryCapacityWh=first_route.assignedBatteryCapacityWh if first_route else None,
        assignedBatteryChargePct=first_route.assignedBatteryChargePct if first_route else None,
        assignedBatteryEnergyWh=first_route.assignedBatteryEnergyWh if first_route else None,
        routes=routes_schemas,
    )


@router.post("", response_model=MissionResponse)
async def create_mission(
    request: MissionCreateRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db)
):
    """
    **[Operator API] Tạo nhiệm vụ bay (Create Mission)**
    
    - **Mục đích**: Chính thức xác nhận tạo chuyến bay để giao một Order cụ thể kèm lựa chọn Drone và Pin.
    - **Hoạt động**:
        1. Validate `Order` bắt buộc phải đang ở trạng thái `AT_HUB` (Technician đã nhận hàng).
        2. Chặn tạo trùng lặp nếu Order hoặc Drone đã có Mission đang chờ hoặc đang bay.
        3. Validate viên pin được chọn cho Drone (`batteryId` / `battery_id`) hoặc tự động chọn viên pin tương thích tốt nhất tại Origin Hub.
        4. Phân tích lại tuyến đường dựa trên năng lượng thực tế của viên pin được gán (`available_energy_wh = capacity_wh * charge_level_pct / 100`) và ngưỡng dự phòng an toàn 20%.
        5. Ở các tuyến Multi-Hub (Relay), chỉ định và giữ chỗ chính xác từng viên pin cụ thể tại mỗi Hub trung gian (`MissionLeg.battery_id`) để dùng thay thế.
        6. Tạo bản ghi `Mission`, `MissionLeg`, `AIPrediction`, `AIRecommendation`.
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

    # Xác định mã pin xuất phát được chọn cho Drone (từ batteryId / battery_id hoặc legs_detail[0].battery_id)
    requested_battery_id = (request.batteryId or request.battery_id or "").strip() or None
    if not requested_battery_id and request.legs_detail:
        first_leg_bat = getattr(request.legs_detail[0], "battery_id", None)
        if first_leg_bat:
            requested_battery_id = str(first_leg_bat).strip() or None

    # Tái hiện các tùy chọn tuyến đường dựa trên năng lượng của pin được gán + chỉ định pin tại các Relay Hub
    routes_schemas, raw_data_list = await compute_mission_routes_internal(
        db=db,
        order=order,
        drone_id=request.droneId,
        battery_id=requested_battery_id,
        strict_battery=True,
    )

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

    # ── Guard: chặn tạo mission nếu tuyến không khả thi về năng lượng pin được gán hoặc pin Relay Hub ──
    if selected_schema and not selected_schema.feasible:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Không thể tạo mission: Tuyến '{selected_schema.route_string}' không khả thi. "
                f"{selected_schema.infeasible_reason or 'Thiếu pin tương thích tại Hub trung gian hoặc vượt mức dự phòng 20%.'} "
                f"Vui lòng chọn tuyến khả thi khác hoặc chọn viên pin có đủ năng lượng trước khi Assign."
            )
        )

    battery_id = selected_schema.assignedBatteryId if selected_schema else requested_battery_id
    assigned_battery = await db.get(Battery, battery_id) if battery_id else None
    if not assigned_battery:
        raise HTTPException(
            status_code=400,
            detail=f"Tại Hub xuất phát #{order.origin_hub_id} hiện không có viên pin tương thích khả dụng để gán cho Drone #{drone.id} ({drone.model})."
        )

    # Gắn liên kết viên pin được chọn với Drone thực hiện nhiệm vụ
    if assigned_battery.drone_id != drone.id:
        # Nếu drone đang gắn viên pin khác không dùng cho mission này, giải phóng liên kết cũ
        old_drone_bats_res = await db.execute(
            select(Battery).where(Battery.drone_id == drone.id, Battery.id != assigned_battery.id)
        )
        for ob in old_drone_bats_res.scalars().all():
            ob.drone_id = None
        assigned_battery.drone_id = drone.id

    compatible_models = await get_compatible_battery_models(db, drone.model)
    reserved_bat_ids, _ = await _get_reserved_batteries_and_virtual_holds(db)

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
        battery_id=assigned_battery.id,
        origin_hub_id=order.origin_hub_id,
        destination_hub_id=order.destination_hub_id,
        payload_weight=order.payload_kg,
        distance_m=round(total_dist_km * 1000, 2),
        delivery_fee=order.delivery_fee,
        scheduled_time=order.requested_delivery_at or get_vn_naive_datetime(),
        status=MissionStatus.SCHEDULED,
        handling_status=HandlingStatus.AT_HUB,
        operator_id=user.id
    )
    db.add(mission)
    await db.flush()

    # 2. Tạo các chặng bay (MissionLeg) và chỉ định chính xác viên pin cho từng chặng (kể cả Relay Hub)
    legs = selected_data.get("legs_detail", []) if selected_data else []
    used_mission_bat_ids: set[str] = {assigned_battery.id}
    relay_swap_summary_parts: list[str] = []

    for idx, leg_item in enumerate(legs):
        if idx == 0:
            leg_battery_id = assigned_battery.id
        else:
            relay_hub_id = leg_item.get("from_hub_id")
            relay_hub_code = leg_item.get("from_hub_code") or str(relay_hub_id)
            # Kiểm tra xem Operator có truyền override pin thay thế tại Relay Hub không
            override_bat_id = None
            if request.relay_battery_ids:
                override_bat_id = (
                    request.relay_battery_ids.get(str(relay_hub_id))
                    or request.relay_battery_ids.get(str(relay_hub_code))
                )
            if not override_bat_id and request.legs_detail and idx < len(request.legs_detail):
                req_leg_bat = getattr(request.legs_detail[idx], "battery_id", None)
                if req_leg_bat:
                    override_bat_id = str(req_leg_bat).strip() or None

            candidate_bat_id = (override_bat_id or leg_item.get("battery_id") or "").strip() or None
            if override_bat_id:
                relay_bat = await db.get(Battery, candidate_bat_id) if candidate_bat_id else None
                if not relay_bat:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Không tìm thấy viên pin thay thế '{candidate_bat_id}' cho Hub trung gian {relay_hub_code}."
                    )
                if relay_bat.current_hub_id != relay_hub_id:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pin thay thế {relay_bat.id} không nằm tại Hub trung gian {relay_hub_code} (Hub #{relay_hub_id})."
                    )
                if relay_bat.status != batteries_status.ACTIVE or (relay_bat.charge_level_pct or 0) <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pin thay thế {relay_bat.id} tại Hub trung gian {relay_hub_code} không sẵn sàng (mức sạc: {relay_bat.charge_level_pct or 0}%)."
                    )
                if compatible_models and relay_bat.battery_model and relay_bat.battery_model not in compatible_models:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pin thay thế {relay_bat.id} ({relay_bat.battery_model}) tại Hub {relay_hub_code} không tương thích với {drone.model}."
                    )
                if relay_bat.id in reserved_bat_ids or relay_bat.id in used_mission_bat_ids:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pin thay thế {relay_bat.id} tại Hub trung gian {relay_hub_code} đã được giữ chỗ cho chặng/nhiệm vụ khác."
                    )
                _, _, relay_bat_energy = _compute_battery_energy_wh(relay_bat)
                leg_req_energy = float(leg_item.get("energy_wh") or 0.0)
                if leg_req_energy > relay_bat_energy * 0.8:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Pin thay thế {relay_bat.id} ({relay_bat_energy:.1f} Wh, sạc {relay_bat.charge_level_pct}%) tại Hub {relay_hub_code} "
                            f"không đủ năng lượng an toàn (tối đa {relay_bat_energy * 0.8:.1f} Wh) cho chặng {idx + 1} ({leg_req_energy:.1f} Wh, yêu cầu dự phòng tối thiểu 20%)."
                        )
                    )
                leg_battery_id = relay_bat.id
            else:
                leg_battery_id = candidate_bat_id

            if leg_battery_id:
                used_mission_bat_ids.add(leg_battery_id)
                relay_swap_summary_parts.append(f"{relay_hub_code}: pin {leg_battery_id}")

        m_leg = MissionLeg(
            mission_id=mission_id,
            sequence_no=idx + 1,
            from_hub_id=leg_item["from_hub_id"],
            to_hub_id=leg_item["to_hub_id"],
            battery_id=leg_battery_id,
            status=MissionLegStatus.PENDING,
            created_at=get_vn_naive_datetime(),
            updated_at=get_vn_naive_datetime()
        )
        db.add(m_leg)

    # 3. Lưu AI Snapshot
    ai_pred = AIPrediction(
        mission_id=mission_id,
        estimated_energy_wh=round(total_energy_wh, 2),
        estimated_duration_m=float(duration_min),
        risk_level=selected_schema.risk.capitalize() if (selected_schema and selected_schema.risk) else "Low",
        predicted_at=get_vn_datetime()
    )
    db.add(ai_pred)

    rec_content = (
        selected_schema.reason
        if (selected_schema and selected_schema.reason)
        else (
            f"Lộ trình {route_str} được chọn theo phân tích AI ({total_energy_wh:.1f} Wh). "
            f"Pin xuất phát: {assigned_battery.id} ({assigned_battery.serial_number})."
            + (f" Pin thay thế trung gian: {', '.join(relay_swap_summary_parts)}." if relay_swap_summary_parts else "")
        )
    )
    ai_rec = AIRecommendation(
        mission_id=mission_id,
        category="Mission Planning",
        content=rec_content,
        is_applied=True,
        created_at=get_vn_datetime()
    )
    db.add(ai_rec)

    # 4. Ghi nhận thời gian planning trên Order
    order.planning_at = get_vn_naive_datetime()

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


# ── Battery Swap (Operator) ───────────────────────────────────────────────────

class BatterySwapOperatorRequest(BaseModel):
    new_battery_id: str


class BatterySwapOperatorResponse(BaseModel):
    mission_id: str
    drone_id: Union[str, int]
    old_battery_id: str
    new_battery_id: str
    hub_id: int
    hub_name: str
    swap_history_id: int
    mission_battery_id: str
    message: str


@router.post("/{mission_id}/battery-swap", response_model=BatterySwapOperatorResponse)
async def operator_battery_swap(
    mission_id: str,
    body: BatterySwapOperatorRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator] Yêu cầu thay pin cho drone tại hub hiện tại.**

    Endpoint này cho phép Operator chủ động override pin cho mission đang dừng tại hub
    (trạng thái AT_HUB hoặc READY), bao gồm cả origin hub lẫn relay hub trung gian.
    Khác với endpoint của Technician (chỉ relay hub + auto-select), endpoint này:
    - Operator **chọn thẳng** battery_id cần dùng.
    - Hoạt động ở **mọi hub** (origin hoặc relay).
    - Không bắt buộc pin phải 100% nhưng phải ≥ 80% (đủ để bay an toàn).
    - Ghi `MissionBatterySwapHistory` để audit.

    **Điều kiện:**
    - Mission phải đang dừng tại hub: `handling_status ∈ {AT_HUB, READY, INCOMING}`.
    - Pin mới phải: ACTIVE, tại đúng hub của drone, chưa gắn drone khác, charge ≥ 80%.
    """
    from app.modules.missions.models import MissionBatterySwapHistory
    from app.modules.fleet.models import Battery, batteries_status
    from app.modules.system.models import Notification, NotificationType

    # ── 1. Lấy mission và validate ────────────────────────────────────────────
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    swappable_handling = {
        HandlingStatus.AT_HUB,
        HandlingStatus.READY,
        HandlingStatus.INCOMING,
    }
    if mission.handling_status not in swappable_handling:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Mission phải đang dừng tại hub (AT_HUB / READY / INCOMING). "
                f"Hiện tại: {mission.handling_status.value if mission.handling_status else 'None'}."
            ),
        )

    if not mission.drone_id:
        raise HTTPException(status_code=400, detail="Mission chưa được gán drone.")

    drone = await db.get(Drone, str(mission.drone_id))
    if not drone:
        raise HTTPException(status_code=404, detail="Drone không tìm thấy.")

    # Hub hiện tại của drone
    hub_id = drone.current_hub_id
    if not hub_id:
        raise HTTPException(
            status_code=400,
            detail="Drone không đang ở hub nào (current_hub_id is None). Không thể swap pin.",
        )

    # ── 2. Validate pin mới ───────────────────────────────────────────────────
    new_battery = await db.get(Battery, body.new_battery_id)
    if not new_battery:
        raise HTTPException(status_code=404, detail=f"Pin '{body.new_battery_id}' không tìm thấy.")

    if new_battery.status != batteries_status.ACTIVE:
        raise HTTPException(status_code=400, detail=f"Pin {new_battery.id} không ở trạng thái Active.")

    if new_battery.current_hub_id != hub_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Pin {new_battery.id} không thuộc hub hiện tại của drone "
                f"(Hub #{hub_id}). Pin đang ở Hub #{new_battery.current_hub_id}."
            ),
        )

    if new_battery.drone_id is not None and str(new_battery.drone_id) != str(mission.drone_id):
        raise HTTPException(
            status_code=400,
            detail=f"Pin {new_battery.id} đang gắn trên drone khác ({new_battery.drone_id}).",
        )

    charge = new_battery.charge_level_pct or 0
    if charge < 80:
        raise HTTPException(
            status_code=400,
            detail=f"Pin {new_battery.id} chỉ còn {charge}% — không đủ để bay an toàn (yêu cầu ≥ 80%).",
        )

    now = get_vn_naive_datetime()
    old_battery_id = mission.battery_id or ""

    # ── 3. Tháo pin cũ ────────────────────────────────────────────────────────
    if old_battery_id and old_battery_id != new_battery.id:
        old_battery = await db.get(Battery, old_battery_id)
        if old_battery:
            old_battery.drone_id = None
            old_battery.current_hub_id = hub_id  # pin cũ ở lại hub để sạc

    # ── 4. Gán pin mới ────────────────────────────────────────────────────────
    new_battery.drone_id = str(mission.drone_id)
    new_battery.current_hub_id = hub_id
    mission.battery_id = new_battery.id

    # ── 5. Ghi lịch sử swap ──────────────────────────────────────────────────
    swap_record = MissionBatterySwapHistory(
        mission_id=mission.id,
        hub_id=hub_id,
        old_battery_id=old_battery_id or None,
        new_battery_id=new_battery.id,
        swapped_at=now,
    )
    db.add(swap_record)
    await db.flush()

    # ── 6. Notify ─────────────────────────────────────────────────────────────
    hub = await db.get(Hub, hub_id)
    hub_name = hub.name if hub else str(hub_id)

    from app.modules.auth.models import User as _User, ROLE_NAME_TO_ID as _ROLE_MAP
    from app.shared.roles import UserRole as _Role
    tech_role_id = _ROLE_MAP.get(_Role.TECHNICIAN)
    tech_result = await db.execute(
        select(_User.id).where(_User.role_id == tech_role_id)
    )
    technician_ids = tech_result.scalars().all()

    for tech_id in technician_ids:
        db.add(Notification(
            user_id=tech_id,
            title=f"🔋 Operator yêu cầu thay pin — Mission #{mission_id}",
            message=(
                f"Operator {user.id} đã swap pin cho Mission #{mission_id} tại {hub_name}. "
                f"Pin cũ: {old_battery_id or 'N/A'} → Pin mới: {new_battery.id} ({charge}%)."
            ),
            notifications_type=NotificationType.MISSION_STATUS,
            mission_id=str(mission.id),
            created_at=now,
        ))

    await db.commit()

    return BatterySwapOperatorResponse(
        mission_id=str(mission.id),
        drone_id=mission.drone_id,
        old_battery_id=old_battery_id or "N/A",
        new_battery_id=new_battery.id,
        hub_id=hub_id,
        hub_name=hub_name,
        swap_history_id=swap_record.id,
        mission_battery_id=mission.battery_id,
        message=(
            f"Swap pin thành công. Mission #{mission_id} tại {hub_name}: "
            f"pin {old_battery_id or 'cũ'} → {new_battery.id} ({charge}%)."
        ),
    )


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

    now = get_vn_naive_datetime()

    # ── 1. Kích hoạt leg kế tiếp ─────────────────────────────────────────────
    next_leg.status = MissionLegStatus.IN_PROGRESS
    next_leg.started_at = now

    # ── 2. Cập nhật mission → FLYING ─────────────────────────────────────────
    mission.status = MissionStatus.FLYING
    mission.handling_status = HandlingStatus.INCOMING  # drone đang bay, rời relay hub

    # ── 3. Cập nhật drone → In Flight ────────────────────────────────────────
    if mission.drone_id:
        drone = await db.get(Drone, str(mission.drone_id))
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

    now = get_vn_naive_datetime()
    previous_status = mission.status.value

    # ── 2. Chuyển mission → INCIDENT_RETURN ─────────────────────────────────
    mission.status = MissionStatus.INCIDENT_RETURN
    mission.end_time = now

    # ── 3. Tạo Incident HIGH severity ───────────────────────────────────────
    incident = Incident(
        mission_id=mission.id,
        drone_id=str(mission.drone_id) if mission.drone_id else None,
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
        drone_id=str(mission.drone_id) if mission.drone_id else None,
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
        drone = await db.get(Drone, str(mission.drone_id))
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
