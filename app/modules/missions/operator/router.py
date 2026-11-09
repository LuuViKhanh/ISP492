from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from datetime import timedelta, datetime, timezone
from typing import Optional, List, Union
import math
import copy

from app.core.timezone import get_vn_naive_datetime, get_vn_datetime
from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.core.config import settings
from app.modules.missions.models import Mission, MissionStatus, HandlingStatus, Incident, IncidentStatus, IncidentSeverity, TelemetryLog, MissionLeg, MissionLegStatus
from app.modules.system.models import Hub
from app.modules.fleet.models import MaintenanceAlert
import heapq
from app.modules.missions.schemas import (
    MissionResponse, ApproveRejectRequest,
    TelemetryDataCreate, TelemetryDataResponse,
    LiveTrackingResponse, LiveTrackingDrone,
    HubCheckpointResponse, TelemetryIngestResponse,
    FailsafeEvaluateRequest, FailsafeEvaluateResponse,
    FailsafeExecuteRequest, FailsafeExecuteResponse, FailsafeHubInfo,
    PreAnalyzeBundleCandidatesRequest, ValidateBundleRequest, PostAnalyzeBundleCandidatesRequest,
)
from app.modules.missions.bundle_service import (
    get_pre_analyze_bundle_candidates,
    validate_bundle_orders,
    get_post_analyze_bundle_candidates,
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
    """Lấy danh sách các Hub (Mini-hubs và Hubs đã hợp nhất)."""
    result = await db.execute(
        select(Hub).order_by(Hub.id)
    )
    hubs = result.scalars().all()
    return [{"id": h.id, "name": h.name, "latitude": h.latitude, "longitude": h.longitude, "type": "Hub"} for h in hubs]


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
        select(Mission).where(
            or_(
                Mission.status == MissionStatus.FLYING,
                and_(
                    Mission.status == MissionStatus.INCIDENT_RETURN,
                    Mission.arrived_at.is_(None),
                    Mission.end_time.is_(None),
                )
            )
        )
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

    cp_counts = {m.id: len(m.checkpoints_json or []) for m in flying_missions}

    # Lấy thông tin Legs cho các mission đang bay
    legs_result = await db.execute(
        select(MissionLeg)
        .where(MissionLeg.mission_id.in_(mission_ids))
        .order_by(MissionLeg.sequence_no.asc())
    )
    all_legs = legs_result.scalars().all()
    legs_by_mid = {}
    hub_ids_to_fetch = set()
    for l in all_legs:
        legs_by_mid.setdefault(l.mission_id, []).append(l)
        if l.from_hub_id:
            hub_ids_to_fetch.add(l.from_hub_id)
        if l.to_hub_id:
            hub_ids_to_fetch.add(l.to_hub_id)

    hubs_map = {}
    if hub_ids_to_fetch:
        hubs_res = await db.execute(select(Hub).where(Hub.id.in_(list(hub_ids_to_fetch))))
        hubs_map = {h.id: h for h in hubs_res.scalars().all()}

    # Lấy drone & battery để lấy % pin
    drone_ids = [m.drone_id for m in flying_missions if m.drone_id]
    battery_ids = [m.battery_id for m in flying_missions if m.battery_id]
    drones_map = {}
    if drone_ids:
        d_res = await db.execute(select(Drone).where(Drone.id.in_(drone_ids)))
        drones_map = {d.id: d for d in d_res.scalars().all()}
    batteries_map = {}
    if battery_ids:
        b_res = await db.execute(select(Battery).where(Battery.id.in_(battery_ids)))
        batteries_map = {b.id: b for b in b_res.scalars().all()}

    # Lấy trail (50 điểm gần nhất theo thứ tự thời gian tăng dần)
    trails_by_mid = {}
    for mid in mission_ids:
        t_res = await db.execute(
            select(TelemetryLog)
            .where(TelemetryLog.mission_id == mid)
            .order_by(TelemetryLog.timestamp.desc())
            .limit(50)
        )
        recent_logs = list(reversed(t_res.scalars().all()))
        trails_by_mid[mid] = [
            {
                "latitude": t.latitude,
                "longitude": t.longitude,
                "altitude": t.altitude,
                "speed": t.speed,
                "timestamp": t.timestamp.isoformat() if t.timestamp else None,
            }
            for t in recent_logs
        ]

    # Lấy Incidents gần nhất cho các mission đang bay để phục vụ cảnh báo Failsafe
    incidents_res = await db.execute(
        select(Incident)
        .where(
            Incident.mission_id.in_(mission_ids),
            Incident.status.in_([IncidentStatus.OPEN, IncidentStatus.INVESTIGATING]),
        )
        .order_by(Incident.id.desc())
    )
    incidents_by_mid = {}
    for inc in incidents_res.scalars().all():
        if inc.mission_id not in incidents_by_mid:
            incidents_by_mid[inc.mission_id] = inc

    drones = []
    for mission in flying_missions:
        log = latest_logs.get(mission.id)
        m_legs = legs_by_mid.get(mission.id, [])
        legs_data = []
        curr_leg = None
        for leg in m_legs:
            f_hub = hubs_map.get(leg.from_hub_id)
            t_hub = hubs_map.get(leg.to_hub_id)
            leg_stat = leg.status.value if hasattr(leg.status, "value") else str(leg.status or "")
            item = {
                "id": leg.id,
                "sequence_no": leg.sequence_no or 1,
                "status": leg_stat,
                "from_hub": {
                    "id": f_hub.id,
                    "name": f_hub.name,
                    "latitude": f_hub.latitude,
                    "longitude": f_hub.longitude,
                } if f_hub else None,
                "to_hub": {
                    "id": t_hub.id,
                    "name": t_hub.name,
                    "latitude": t_hub.latitude,
                    "longitude": t_hub.longitude,
                } if t_hub else None,
                "battery_id": leg.battery_id,
            }
            legs_data.append(item)
            if leg.status == MissionLegStatus.IN_PROGRESS:
                curr_leg = {
                    "id": item["id"],
                    "sequence_no": item["sequence_no"],
                    "status": item["status"],
                    "from_hub": item["from_hub"],
                    "to_hub": item["to_hub"],
                    "assigned_battery_id": item["battery_id"],
                }

        if not curr_leg and legs_data:
            pending = [l for l in legs_data if str(l.get("status", "")).upper() == "PENDING"]
            if pending:
                p = pending[0]
                curr_leg = {
                    "id": p["id"],
                    "sequence_no": p["sequence_no"],
                    "status": p["status"],
                    "from_hub": p["from_hub"],
                    "to_hub": p["to_hub"],
                    "assigned_battery_id": p.get("battery_id"),
                }
            else:
                p = legs_data[-1]
                curr_leg = {
                    "id": p["id"],
                    "sequence_no": p["sequence_no"],
                    "status": p["status"],
                    "from_hub": p["from_hub"],
                    "to_hub": p["to_hub"],
                    "assigned_battery_id": p.get("battery_id"),
                }

        drone_obj = drones_map.get(mission.drone_id)
        bat_obj = batteries_map.get(mission.battery_id)
        bat_pct = drone_obj.battery_level_pct if drone_obj and drone_obj.battery_level_pct is not None else (
            bat_obj.charge_level_pct if bat_obj and bat_obj.charge_level_pct is not None else None
        )

        inc = incidents_by_mid.get(mission.id)
        failsafe_status = "SAFE"
        failsafe_alert = None
        is_diverted = False
        divert_hub_id = None
        divert_hub_name = None

        if inc and "[FAILSAFE DIVERT]" in (inc.description or ""):
            failsafe_status = "DIVERTED"
            failsafe_alert = inc.description
            is_diverted = True
            divert_hub_id = mission.destination_hub_id
            if divert_hub_id and divert_hub_id in hubs_map:
                divert_hub_name = hubs_map[divert_hub_id].name
        elif inc and "[FAILSAFE RTH]" in (inc.description or ""):
            failsafe_status = "RTH"
            failsafe_alert = inc.description
            is_diverted = True
            divert_hub_id = mission.origin_hub_id
            if divert_hub_id and divert_hub_id in hubs_map:
                divert_hub_name = hubs_map[divert_hub_id].name
        elif mission.status == MissionStatus.INCIDENT_RETURN:
            failsafe_status = "RTH"
            failsafe_alert = inc.description if inc else "Sự cố trong chuyến bay — Drone đang quay về Hub an toàn."
            is_diverted = True
            divert_hub_id = mission.origin_hub_id
            if divert_hub_id and divert_hub_id in hubs_map:
                divert_hub_name = hubs_map[divert_hub_id].name
        elif bat_pct is not None and bat_pct < 20:
            failsafe_status = "WARNING"
            failsafe_alert = f"Cảnh báo pin thấp: {bat_pct}% (tiệm cận ngưỡng an toàn 20%)"

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
                latest_battery_pct=bat_pct,
                last_updated=log.timestamp if log else None,
                checkpoints_passed=cp_counts.get(mission.id, 0),
                current_leg=curr_leg,
                legs=legs_data,
                trail=trails_by_mid.get(mission.id, []),
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
                # Failsafe in-flight fields
                failsafe_status=failsafe_status,
                failsafe_alert=failsafe_alert,
                is_diverted=is_diverted,
                divert_hub_id=divert_hub_id,
                divert_hub_name=divert_hub_name,
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
            battery_pct=data.battery_pct,
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

    # ── 3. Tải hubs để detect proximity ────────────────────────────
    hubs_result = await db.execute(
        select(Hub).where(and_(Hub.latitude.isnot(None), Hub.longitude.isnot(None)))
    )
    all_hubs = hubs_result.scalars().all()

    # ── 4. Detect hub proximity cho từng điểm telemetry ────────────────────────
    HUB_PROXIMITY_RADIUS_M = 200.0   # Bán kính tính từ tâm hub (m)
    COOLDOWN_SECONDS = 60             # Tránh log trùng cho cùng 1 hub liên tiếp

    current_checkpoints = list(mission.checkpoints_json or [])
    new_checkpoints: list[HubCheckpointResponse] = []
    next_order = len(current_checkpoints) + 1

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

        for h in all_hubs:
            dist = haversine_m(data.latitude, data.longitude, h.latitude, h.longitude)
            if dist > HUB_PROXIMITY_RADIUS_M:
                continue  # Không trong vùng

            # Cooldown: kiểm tra xem hub này đã được log trong 60s qua chưa
            in_cooldown = False
            for cp in reversed(current_checkpoints):
                if cp.get("hub_id") == h.id:
                    cp_ts_str = cp.get("passed_at")
                    if cp_ts_str:
                        try:
                            cp_ts = datetime.fromisoformat(str(cp_ts_str))
                            if abs((pt_ts - cp_ts).total_seconds()) < COOLDOWN_SECONDS:
                                in_cooldown = True
                                break
                        except Exception:
                            pass
            if in_cooldown:
                continue

            cp_dict = {
                "id": next_order,
                "mission_id": actual_mission_id,
                "hub_id": h.id,
                "location_id": None,
                "hub_name": h.name or f"Hub #{h.id}",
                "hub_latitude": h.latitude,
                "hub_longitude": h.longitude,
                "drone_latitude": data.latitude,
                "drone_longitude": data.longitude,
                "distance_to_hub_m": round(dist, 2),
                "passed_at": pt_ts.isoformat() if hasattr(pt_ts, "isoformat") else str(pt_ts),
                "checkpoint_order": next_order,
            }
            current_checkpoints.append(cp_dict)
            new_checkpoints.append(HubCheckpointResponse(**cp_dict))
            next_order += 1

    if new_checkpoints:
        mission.checkpoints_json = current_checkpoints

    # ── 6. Commit tất cả ────────────────────────────────────────────────────────
    await db.commit()

    for log in logs_to_insert:
        await db.refresh(log)
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

    return [HubCheckpointResponse(**cp) for cp in (mission.checkpoints_json or [])]


# ── Hubs & Locations ──────────────────────────────────────────────────────────────


from app.modules.missions.schemas import (
    MissionPlanningAnalyzeRequest, MissionPlanningAnalyzeResponse,
    RouteOptionSchema, RouteLegSchema, MissionCreateRequest, HubBatteryStatus,
    RelayHubBatteryOptionSchema,
)
from app.modules.missions.models import Order, OrderStatus, PaymentStatus, MissionLeg, MissionLegStatus
from app.modules.ai_predictions.models import AIPrediction
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



async def _get_reserved_battery_info_map(
    db: AsyncSession,
    exclude_order_id: Optional[str] = None,
    exclude_mission_id: Optional[str] = None,
) -> dict[str, dict]:
    """
    Trả về mapping thông tin chi tiết nhiệm vụ giữ chỗ cho từng viên pin:
    battery_id -> {
        "mission_id": str,
        "mission_code": str,
        "mission_status": str,
        "drone_id": str | None,
        "order_id": str | None,
        "reason": str,
    }
    """
    active_statuses = [
        MissionStatus.SCHEDULED,
        MissionStatus.IN_PROGRESS,
        MissionStatus.APPROVED,
        MissionStatus.FLYING,
        MissionStatus.ACTIVE_MISSION,
    ]
    info_map: dict[str, dict] = {}

    m_stmt = select(Mission).where(
        Mission.status.in_(active_statuses),
        Mission.battery_id.is_not(None),
    )
    if exclude_order_id:
        m_stmt = m_stmt.where(Mission.order_id != exclude_order_id)
    if exclude_mission_id:
        m_stmt = m_stmt.where(Mission.id != exclude_mission_id)

    m_res = await db.execute(m_stmt)
    for m in m_res.scalars().all():
        if m.battery_id:
            m_status = m.status.value if hasattr(m.status, "value") else str(m.status)
            info_map[m.battery_id] = {
                "mission_id": m.id,
                "mission_code": m.id,
                "mission_status": m_status,
                "drone_id": m.drone_id,
                "order_id": m.order_id,
                "reason": f"Đang được sử dụng bởi Nhiệm vụ #{m.id} ({m_status})",
            }

    leg_stmt = (
        select(MissionLeg, Mission)
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

    leg_res = await db.execute(leg_stmt)
    for leg, m in leg_res.all():
        if leg.battery_id:
            m_status = m.status.value if hasattr(m.status, "value") else str(m.status)
            seq_info = f"Chặng {leg.sequence_no}" if leg.sequence_no else "Chặng trung gian"
            info_map[leg.battery_id] = {
                "mission_id": m.id,
                "mission_code": m.id,
                "mission_status": m_status,
                "drone_id": m.drone_id,
                "order_id": m.order_id,
                "reason": f"Đang được sử dụng bởi Nhiệm vụ #{m.id} ({seq_info} - {m_status})",
            }

    return info_map


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
    is_cross_hub: bool = False,
    origin_hub_id: Optional[int] = None,
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
            leg0_cons_pct = int(round((leg0_energy / origin_cap_wh) * 100)) if origin_cap_wh > 0 else 100
            leg0["battery_consumption_pct"] = leg0_cons_pct
            leg0["remaining_battery_pct"] = max(0, origin_charge_pct - leg0_cons_pct)

            if leg0_energy > max_safe_origin_wh:
                infeasible_reasons.append(
                    f"Chặng 1 ({leg0.get('from_hub_code', '?')} -> {leg0.get('to_hub_code', '?')}) "
                    f"tiêu thụ {leg0_energy:.1f} Wh (~{leg0_cons_pct}% năng lượng pin {assigned_battery.id}), "
                    f"vượt mức an toàn 80% ({max_safe_origin_wh:.1f} Wh / {origin_energy_wh:.1f} Wh, "
                    f"sạc {origin_charge_pct}%, cần dự phòng tối thiểu 20%)"
                )
        else:
            leg0_cons_pct = int(round((leg0_energy / origin_cap_wh) * 100)) if origin_cap_wh > 0 else 100
            leg0["battery_consumption_pct"] = leg0_cons_pct
            leg0["remaining_battery_pct"] = max(0, origin_charge_pct - leg0_cons_pct)
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

        # Sắp xếp ưu tiên: Thuật toán Best-Fit (Vừa đủ năng lượng cho chặng kế tiếp)
        # 1. Nhóm 0: Viên pin có năng lượng thực tế đủ an toàn cho chặng kế tiếp (b_energy_wh * 0.8 >= next_leg_energy)
        #    -> Sắp xếp TĂNG DẦN theo năng lượng Wh để chọn viên pin "vừa đủ năng lượng an toàn",
        #       bảo tồn các viên pin 100% dung lượng lớn cho các nhiệm vụ đường dài khác.
        # 2. Nhóm 1: Viên pin KHÔNG đủ an toàn cho chặng kế tiếp
        #    -> Fallback sắp xếp GIẢM DẦN theo năng lượng Wh (chọn pin cao nhất hiện có).
        def _relay_battery_sort_key(b):
            b_energy_wh = _compute_battery_energy_wh(b, drone_capacity_wh)[2]
            is_sufficient = (b_energy_wh * 0.8 >= next_leg_energy)
            if is_sufficient:
                return (
                    0,
                    b_energy_wh,                # Năng lượng thực tế nhỏ nhất nhưng đủ an toàn
                    (b.charge_level_pct or 0),
                    float(b.capacity_wh or 0.0),
                    b.id,
                )
            else:
                return (
                    1,
                    -b_energy_wh,               # Fallback: năng lượng lớn nhất
                    -(b.charge_level_pct or 0),
                    -float(b.capacity_wh or 0.0),
                    b.id,
                )

        hub_bats.sort(key=_relay_battery_sort_key)

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
                leg_cons_pct = int(round((next_leg_energy / sel_cap_wh) * 100)) if sel_cap_wh > 0 else 100
                next_leg["battery_consumption_pct"] = leg_cons_pct
                next_leg["remaining_battery_pct"] = max(0, sel_charge_pct - leg_cons_pct)

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
            if is_cross_hub and hub_id == origin_hub_id and assigned_battery and (leg0_energy + next_leg_energy) <= max_safe_origin_wh:
                hub_feasible = True
                if next_leg is not None:
                    next_leg["battery_id"] = assigned_battery.id
                    next_leg["battery_serial"] = assigned_battery.serial_number
                    next_leg["battery_model"] = assigned_battery.battery_model
                    next_leg["battery_capacity_wh"] = round(origin_cap_wh, 2)
                    next_leg["battery_charge_pct"] = origin_charge_pct
                    next_leg["battery_energy_wh"] = round(origin_energy_wh, 2)
                    tot_cons_wh = leg0_energy + next_leg_energy
                    leg_cons_pct = int(round((tot_cons_wh / origin_energy_wh) * 100)) if origin_energy_wh > 0 else 100
                    next_leg["battery_consumption_pct"] = leg_cons_pct
                    next_leg["remaining_battery_pct"] = max(0, 100 - leg_cons_pct)

                relay_statuses.append(HubBatteryStatus(
                    hub_id=hub_id,
                    hub_code=hub_code,
                    hub_name=hub_name,
                    ready_count=effective_ready_count,
                    compatible_models=compatible_models,
                    feasible=True,
                    selected_battery_id=assigned_battery.id,
                    selected_battery_serial=assigned_battery.serial_number,
                    selected_battery_model=assigned_battery.battery_model,
                    selected_battery_capacity_wh=round(origin_cap_wh, 2),
                    selected_battery_charge_pct=origin_charge_pct,
                    selected_battery_energy_wh=round(origin_energy_wh, 2),
                    swap_battery_id=None,
                    swap_battery_serial=None,
                    available_batteries=[],
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
    override_payload_kg: Optional[float] = None,
    zone_tracker: Optional[dict] = None,
) -> tuple[list[RouteOptionSchema], list[dict]]:
    effective_payload = float(override_payload_kg) if override_payload_kg is not None else float(order.payload_kg or 0)
    def _set_zone(zone_name: str):
        if zone_tracker is not None:
            zone_tracker["current"] = zone_name
        try:
            print(f"[MISSION_PLANNING] {zone_name}")
        except Exception:
            pass

    _set_zone("ZONE 01: Kiểm tra Order & Thông tin Hub xuất phát - đích")
    if not order.origin_hub_id or not order.destination_hub_id:
        raise HTTPException(status_code=400, detail="Đơn hàng thiếu thông tin Trạm xuất phát hoặc Trạm đích")
    origin_hub = await db.get(Hub, order.origin_hub_id)
    dest_hub = await db.get(Hub, order.destination_hub_id)
    if not origin_hub or not dest_hub:
        raise HTTPException(status_code=400, detail="Trạm xuất phát hoặc Trạm đích không tồn tại trong hệ thống")

    _set_zone("ZONE 02: Nạp đồ thị mạng lưới Hubs & Kho Pin ACTIVE")
    # 1. Get all active hubs (hỗ trợ cả 'Active' và 'ACTIVE')
    result = await db.execute(select(Hub).where(func.lower(Hub.status) == 'active'))
    hubs = list(result.scalars().all())
    if not hubs:
        result = await db.execute(select(Hub))
        hubs = list(result.scalars().all())
    hub_dict = {h.id: h for h in hubs}

    # Đảm bảo origin_hub và dest_hub luôn có mặt trong hub_dict và hubs
    if origin_hub.id not in hub_dict:
        hub_dict[origin_hub.id] = origin_hub
        hubs.append(origin_hub)
    if dest_hub.id not in hub_dict:
        hub_dict[dest_hub.id] = dest_hub
        hubs.append(dest_hub)

    # 2. Lấy toàn bộ pin ACTIVE (cả pin rời và pin đang gắn trên drone)
    result = await db.execute(
        select(Battery).where(Battery.status == batteries_status.ACTIVE).order_by(Battery.id)
    )
    all_active_batteries = result.scalars().all()
    all_batteries = [b for b in all_active_batteries if b.drone_id is None]

    _set_zone("ZONE 03: Kiểm tra giữ chỗ Pin (Virtual Holds)")
    # 3. Lấy danh sách pin đã bị giữ chỗ bởi các Mission/MissionLeg đang hoạt động khác
    all_hub_ids = [h.id for h in hubs]
    reserved_battery_ids, held_count_by_hub = await _get_reserved_batteries_and_virtual_holds(
        db=db,
        relay_hub_ids=all_hub_ids,
        compatible_models=[],
        exclude_order_id=order.id,
    )

    async def _drone_has_ready_battery(d_cand):
        """Kiểm tra xem Drone tại trạm d_cand.current_hub_id có ít nhất 1 viên pin đạt 100% khả dụng không."""
        c_models = await get_compatible_battery_models(db, d_cand.model)
        cand_bats = [
            b for b in all_active_batteries
            if (b.drone_id == d_cand.id or (b.drone_id is None and b.current_hub_id == d_cand.current_hub_id))
            and b.id not in reserved_battery_ids
            and (b.charge_level_pct or 0) >= 100
            and (not c_models or (b.battery_model and b.battery_model in c_models))
        ]
        return len(cand_bats) > 0

    _set_zone("ZONE 04: Xác định Drone khả dụng & Cơ chế Cross-Hub Dispatch")
    # 4. Suggested drone + load drone object (ưu tiên Drone thực sự Mission-Ready có sẵn pin tại Hub)
    clean_drone_id = str(drone_id).strip() if drone_id is not None else None
    suggested_drone_id = clean_drone_id
    drone_obj = None
    if clean_drone_id:
        drone_obj = await db.get(Drone, clean_drone_id)
        if not drone_obj and clean_drone_id.isdigit():
            alt_id = f"DRN-{int(clean_drone_id):04d}"
            drone_obj = await db.get(Drone, alt_id)
            if drone_obj:
                suggested_drone_id = drone_obj.id

        if not drone_obj:
            raise HTTPException(
                status_code=404,
                detail=f"Không tìm thấy Drone '{drone_id}' trong hệ thống."
            )

    if not suggested_drone_id:
        result = await db.execute(
            select(Drone).where(
                and_(
                    Drone.status == DroneStatus.AVAILABLE,
                    Drone.current_hub_id == origin_hub.id,
                    Drone.payload_capacity_kg >= effective_payload
                )
            ).order_by(Drone.id)
        )
        local_drones = result.scalars().all()
        ready_local_drones = []
        for ld in local_drones:
            if await _drone_has_ready_battery(ld):
                ready_local_drones.append(ld)

        if ready_local_drones:
            suggested_drone_id = ready_local_drones[0].id
            drone_obj = ready_local_drones[0]
        else:
            # Hub xuất phát không có Drone HOẶC tất cả Drone tại Hub đều hết pin / pin bị giữ chỗ!
            # Tự động kích hoạt Cross-Hub Drone Dispatch: Tìm Drone tại Hub khác có sẵn pin đầy
            result_other = await db.execute(
                select(Drone).where(
                    and_(
                        Drone.status == DroneStatus.AVAILABLE,
                        Drone.current_hub_id != origin_hub.id,
                        Drone.payload_capacity_kg >= effective_payload
                    )
                ).order_by(Drone.id)
            )
            other_hub_drones = result_other.scalars().all()
            ready_other_drones = []
            for od in other_hub_drones:
                if await _drone_has_ready_battery(od):
                    ready_other_drones.append(od)

            if ready_other_drones:
                def _drone_dist(d_cand):
                    cand_hub = hub_dict.get(d_cand.current_hub_id)
                    if not cand_hub:
                        return float('inf')
                    return haversine_distance(
                        cand_hub.latitude or 0, cand_hub.longitude or 0,
                        origin_hub.latitude or 0, origin_hub.longitude or 0
                    )
                ready_other_drones.sort(key=_drone_dist)
                best_other_drone = ready_other_drones[0]
                suggested_drone_id = best_other_drone.id
                drone_obj = best_other_drone
            elif local_drones:
                # Nếu toàn hệ thống không có drone nào có pin, fallback về drone local đầu tiên để báo lỗi thiếu pin
                suggested_drone_id = local_drones[0].id
                drone_obj = local_drones[0]

    # Xác định điều phối Drone từ Hub khác (Cross-Hub Drone Dispatch)
    is_cross_hub = False
    drone_home_hub = None
    if drone_obj and drone_obj.current_hub_id and drone_obj.current_hub_id != origin_hub.id:
        is_cross_hub = True
        if drone_obj.current_hub_id not in hub_dict:
            d_hub = await db.get(Hub, drone_obj.current_hub_id)
            if d_hub:
                hub_dict[d_hub.id] = d_hub
                hubs.append(d_hub)
        drone_home_hub = hub_dict.get(drone_obj.current_hub_id)

    departure_hub = drone_home_hub if is_cross_hub and drone_home_hub else origin_hub

    _set_zone("ZONE 05: Gán Pin xuất phát & Xác định năng lượng Wh ban đầu")
    # 5. Xác định viên pin được assign cho Drone tại Hub xuất phát và năng lượng thực tế (Wh) của pin đó
    (
        compatible_models,
        assigned_battery,
        origin_bat_capacity_wh,
        origin_bat_charge_pct,
        origin_bat_energy_wh,
        drone_capacity_wh,
    ) = await _resolve_assigned_drone_battery(
        db=db,
        origin_hub_id=departure_hub.id,
        drone_obj=drone_obj,
        requested_battery_id=battery_id,
        all_active_batteries=all_active_batteries,
        reserved_battery_ids=reserved_battery_ids,
        strict=strict_battery,
    )
    max_safe_origin_energy_wh = round(origin_bat_energy_wh * 0.8, 2)

    _set_zone("ZONE 06: Đánh giá trạm đổi pin trung gian (Relay Hub Energy & Wait Times)")
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

    payload = effective_payload

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
                leg_available_energy_wh = origin_bat_energy_wh if origin_bat_energy_wh > 0 else (drone_capacity_wh if not assigned_battery else 0.0)
            else:
                leg_available_energy_wh = hub_best_energy_wh.get(current_node, 0.0)

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
                    if wait_time == float('inf') or hub_best_energy_wh.get(n_hub.id, 0.0) <= 0:
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

    _set_zone("ZONE 07: Lập 3 phương án lộ trình (Eco Direct, Fast Direct, Detour Swap Hub)")
    raw_routes = []
    seen_route_strings = set()

    # Tính cự ly bay thẳng & năng lượng bay thẳng
    dist_direct = haversine_distance(
        origin_hub.latitude or 0, origin_hub.longitude or 0,
        dest_hub.latitude or 0, dest_hub.longitude or 0
    )
    pred_direct_eco = predict_flight_energy(dist_direct, payload, speed=10.0, altitude=45.0)
    e_direct = pred_direct_eco["energy_wh"]
    route_str_eco = f"{origin_hub.code or origin_hub.name} -> {dest_hub.code or dest_hub.name} (Hành lang Eco 45m • 10m/s)"
    t_eco = max(3, int(math.ceil(dist_direct * 2.2)) + 2)

    # ── 1. Tuyến 1: Bay thẳng trực tiếp - Tối ưu năng lượng (Direct Flight - Eco Corridor) ──
    ai_exp_eco = dict(pred_direct_eco.get("ai_explanation") or {})
    ai_exp_eco["legs_breakdown"] = [{
        "leg": 1,
        "from_hub_code": origin_hub.code or origin_hub.name,
        "to_hub_code": dest_hub.code or dest_hub.name,
        "distance_km": round(dist_direct, 2),
        "energy_wh": round(e_direct, 2),
        "speed_mps": 10.0,
        "speed_kmh": 36.0,
        "action": f"Bay thẳng trực tiếp từ {origin_hub.name} tới {dest_hub.name} (Hành lang Eco 45m • 10 m/s)",
    }]
    r_direct_eco = {
        "path": [origin_hub.id, dest_hub.id],
        "relayHubs": [],
        "distanceKm": round(dist_direct, 2),
        "predictedEnergyWh": round(e_direct, 2),
        "predictedDurationMin": t_eco,
        "wait_time": 0,
        "flight_speed_mps": 10.0,
        "speed_profile_label": "Eco Corridor (10 m/s - 36 km/h • Độ cao 45m)",
        "shap_values": pred_direct_eco.get("shap_values"),
        "ai_explanation": ai_exp_eco,
        "legs_detail": [{
            "from_hub_id": origin_hub.id,
            "to_hub_id": dest_hub.id,
            "from_hub_code": origin_hub.code or origin_hub.name,
            "to_hub_code": dest_hub.code or dest_hub.name,
            "distance_km": round(dist_direct, 2),
            "energy_wh": round(e_direct, 2),
            "payload_kg": payload,
        }],
        "route_string": route_str_eco
    }
    raw_routes.append({"key": "ENG", "type": "energy", "data": r_direct_eco})

    # ── 2. Tuyến 2: Bay thẳng trực tiếp - Tốc độ hành trình cao (High-Speed Express Corridor) ──
    dist_fast = round(dist_direct * 1.04 + 0.08, 2)
    pred_direct_fast = predict_flight_energy(dist_fast, payload, speed=15.0, altitude=75.0)
    e_fast = round(max(float(pred_direct_fast["energy_wh"]), e_direct * 1.24 + 2.5), 2)
    t_fast = max(1, min(t_eco - 2, int(math.floor(dist_direct * 1.25)) + 1))
    route_str_fast = f"{origin_hub.code or origin_hub.name} -> {dest_hub.code or dest_hub.name} (Hành lang Tốc độ cao 75m • 15m/s)"
    ai_exp_fast = dict(pred_direct_fast.get("ai_explanation") or {})
    ai_exp_fast["legs_breakdown"] = [{
        "leg": 1,
        "from_hub_code": origin_hub.code or origin_hub.name,
        "to_hub_code": dest_hub.code or dest_hub.name,
        "distance_km": round(dist_fast, 2),
        "energy_wh": e_fast,
        "speed_mps": 15.0,
        "speed_kmh": 54.0,
        "action": f"Bay thẳng hành trình nhanh từ {origin_hub.name} tới {dest_hub.name} (Hành lang Express 75m • 15 m/s)",
    }]
    r_direct_fast = {
        "path": [origin_hub.id, dest_hub.id],
        "relayHubs": [],
        "distanceKm": round(dist_fast, 2),
        "predictedEnergyWh": e_fast,
        "predictedDurationMin": t_fast,
        "wait_time": 0,
        "flight_speed_mps": 15.0,
        "speed_profile_label": "Express Corridor (15 m/s - 54 km/h • Độ cao 75m)",
        "shap_values": pred_direct_fast.get("shap_values"),
        "ai_explanation": ai_exp_fast,
        "legs_detail": [{
            "from_hub_id": origin_hub.id,
            "to_hub_id": dest_hub.id,
            "from_hub_code": origin_hub.code or origin_hub.name,
            "to_hub_code": dest_hub.code or dest_hub.name,
            "distance_km": round(dist_fast, 2),
            "energy_wh": e_fast,
            "payload_kg": payload,
        }],
        "route_string": route_str_fast
    }
    raw_routes.append({"key": "FAST", "type": "fastest", "data": r_direct_fast})

    # ── 3. Tuyến 3: Tuyến đường vòng qua trạm trung gian đổi pin (Detour via Swap Hub) ──
    # Yêu cầu cốt lõi: Tuyến 3 mục đường vòng PHẢI LÀ TUYẾN CÓ THỂ ĐI ĐƯỢC (đủ pin để thay tại trạm trung gian và có thể gán trực tiếp vào drone),
    # trừ trường hợp thật sự không tồn tại tuyến nào khả thi nữa thì vẫn hiển thị 3 tuyến nhưng không cho chọn và kèm theo thông báo.
    detour_candidates = []
    other_hubs = [h for h in hubs if h.id not in (origin_hub.id, dest_hub.id)]

    for mid_h in other_hubs:
        mid_energy = hub_best_energy_wh.get(mid_h.id, 0.0)
        mid_wait = hub_wait_times.get(mid_h.id, float('inf'))
        if mid_energy <= 0 or mid_wait == float('inf'):
            continue

        d1 = haversine_distance(origin_hub.latitude or 0, origin_hub.longitude or 0, mid_h.latitude or 0, mid_h.longitude or 0)
        d2 = haversine_distance(mid_h.latitude or 0, mid_h.longitude or 0, dest_hub.latitude or 0, dest_hub.longitude or 0)
        pred_e1 = predict_flight_energy(d1, payload, speed=10.0, altitude=45.0)
        pred_e2 = predict_flight_energy(d2, payload, speed=10.0, altitude=45.0)
        e1 = pred_e1["energy_wh"]
        e2 = pred_e2["energy_wh"]

        # Điều kiện pin cho đường vòng:
        # 1. Chặng 1: Drone đủ pin xuất phát để bay tới trạm trung gian (an toàn <= 80% pin xuất phát)
        if origin_bat_energy_wh > 0 and e1 > max_safe_origin_energy_wh:
            continue
        # 2. Chặng 2: Pin tại trạm trung gian đủ để bay tới đích (an toàn <= 80% pin trạm trung gian)
        if e2 > mid_energy * 0.8:
            continue

        c_codes = [origin_hub.code or origin_hub.name, f"{mid_h.code or mid_h.name} (Đổi pin)", dest_hub.code or dest_hub.name]
        c_string = " -> ".join(c_codes)
        w_val = mid_wait if mid_wait != float('inf') else 0
        tot_d = d1 + d2
        tot_e = e1 + e2

        # Tổng hợp SHAP Values của toàn bộ các chặng trong tuyến Relay
        s1 = pred_e1.get("shap_values") or {}
        s2 = pred_e2.get("shap_values") or {}
        shap_speed_tot = 0.0
        shap_dist_tot = round(float(s1.get("distance", 0.0)) + float(s2.get("distance", 0.0)), 2)
        shap_payload_tot = round(float(s1.get("payload", 0.0)) + float(s2.get("payload", 0.0)), 2)
        shap_wind_tot = round(float(s1.get("relative_wind_angle", 0.0)) + float(s2.get("relative_wind_angle", 0.0)), 2)
        shap_alt_tot = round(float(s1.get("altitude", 0.0)) + float(s2.get("altitude", 0.0)), 2)
        aggregated_shap = {
            "speed": shap_speed_tot,
            "distance": shap_dist_tot,
            "payload": shap_payload_tot,
            "relative_wind_angle": shap_wind_tot,
            "altitude": shap_alt_tot,
        }

        diff_dist = round(tot_d - dist_direct, 2)
        diff_energy = round(tot_e - e_direct, 1)
        wind_impact = "Gió xuôi hỗ trợ tiết kiệm pin trên toàn tuyến" if shap_wind_tot <= 0 else "Gió ngược làm tăng lực cản di chuyển trên các chặng bay"

        detour_ai_explanation = {
            "flight_speed_mps": 10.0,
            "flight_speed_kmh": 36.0,
            "total_energy_wh": round(tot_e, 2),
            "feature_contributions": [
                {
                    "feature": "distance",
                    "label": "Cự ly hành trình (Distance)",
                    "value": f"{tot_d:.2f} km (Đường vòng qua {mid_h.name})",
                    "shap_contribution_wh": shap_dist_tot,
                    "impact": f"Cự ly dài hơn +{diff_dist:.2f} km so với bay thẳng làm phát sinh thêm +{diff_energy:.1f} Wh năng lượng cản di chuyển"
                },
                {
                    "feature": "payload",
                    "label": "Tải trọng hàng hóa (Payload)",
                    "value": f"{payload:.2f} kg",
                    "shap_contribution_wh": shap_payload_tot,
                    "impact": f"Tải trọng hàng hóa duy trì nâng đỡ suốt {tot_d:.2f} km làm tăng tiêu hao +{shap_payload_tot:.1f} Wh"
                },
                {
                    "feature": "speed",
                    "label": "Vận tốc bay (Flight Speed)",
                    "value": "10.0 m/s (36.0 km/h)",
                    "shap_contribution_wh": 0.0,
                    "impact": "Vận tốc hành trình kinh tế (10.0 m/s) xuyên suốt cả 2 chặng bay, tối ưu hóa mức tiêu hao công suất"
                },
                {
                    "feature": "wind",
                    "label": "Điều kiện đón gió (Wind Effect)",
                    "value": "Tổng hợp 2 chặng bay",
                    "shap_contribution_wh": shap_wind_tot,
                    "impact": wind_impact
                }
            ],
            "legs_breakdown": [
                {
                    "leg": 1,
                    "from_hub_code": origin_hub.code or origin_hub.name,
                    "to_hub_code": mid_h.code or mid_h.name,
                    "distance_km": round(d1, 2),
                    "energy_wh": round(e1, 2),
                    "speed_mps": 10.0,
                    "speed_kmh": 36.0,
                    "action": f"Bay từ {origin_hub.name} đến {mid_h.name} (Tốn {e1:.1f} Wh)",
                },
                {
                    "leg": 2,
                    "from_hub_code": mid_h.code or mid_h.name,
                    "to_hub_code": dest_hub.code or dest_hub.name,
                    "distance_km": round(d2, 2),
                    "energy_wh": round(e2, 2),
                    "speed_mps": 10.0,
                    "speed_kmh": 36.0,
                    "action": f"Thay pin tại {mid_h.name} và bay tiếp tới {dest_hub.name} (Tốn {e2:.1f} Wh)",
                }
            ],
            "summary": (
                f"Tuyến trung chuyển qua trạm đổi pin {mid_h.name}. Tổng cự ly {tot_d:.2f} km dài hơn đường thẳng +{diff_dist:.2f} km, "
                f"tiêu hao {tot_e:.1f} Wh (+{diff_energy:.1f} Wh). Phù hợp làm phương án dự phòng hành lang an toàn hoặc điều phối trạm."
            )
        }

        detour_candidates.append({
            "path": [origin_hub.id, mid_h.id, dest_hub.id],
            "relayHubs": [f"{mid_h.name} (Tốn {e1:.1f}Wh)"],
            "distanceKm": round(tot_d, 2),
            "predictedEnergyWh": round(tot_e, 2),
            "predictedDurationMin": int(math.ceil(tot_d * 2.2)) + 3 + int(w_val),
            "wait_time": int(w_val),
            "flight_speed_mps": 10.0,
            "speed_profile_label": "Safe Relay Corridor (10 m/s • Độ cao 45m)",
            "shap_values": aggregated_shap,
            "ai_explanation": detour_ai_explanation,
            "legs_detail": [
                {
                    "from_hub_id": origin_hub.id,
                    "to_hub_id": mid_h.id,
                    "from_hub_code": origin_hub.code or origin_hub.name,
                    "to_hub_code": mid_h.code or mid_h.name,
                    "distance_km": round(d1, 2),
                    "energy_wh": round(e1, 2),
                    "payload_kg": payload,
                },
                {
                    "from_hub_id": mid_h.id,
                    "to_hub_id": dest_hub.id,
                    "from_hub_code": mid_h.code or mid_h.name,
                    "to_hub_code": dest_hub.code or dest_hub.name,
                    "distance_km": round(d2, 2),
                    "energy_wh": round(e2, 2),
                    "payload_kg": payload,
                }
            ],
            "route_string": c_string,
            "cost_metric": tot_e + tot_d * 2,
        })

    # Sắp xếp các ứng viên đường vòng khả thi theo chi phí năng lượng + thời gian tối ưu nhất
    detour_candidates.sort(key=lambda x: x.get("cost_metric", 0))

    if detour_candidates:
        # Tìm thấy trạm trung gian có pin tương thích đủ năng lượng -> Tuyến 3 CÓ THỂ ĐI ĐƯỢC!
        best_detour = detour_candidates[0]
        raw_routes.append({"key": "DETOUR", "type": "detour", "data": best_detour})
    elif other_hubs:
        # Trường hợp thật sự không tồn tại tuyến nào khả thi (các trạm trung gian đều hết pin):
        # Vẫn tạo tuyến đường vòng qua trạm trung gian gần nhất để hiển thị 3 tuyến, nhưng đánh dấu không cho chọn
        nearest_mid = min(
            other_hubs,
            key=lambda h: haversine_distance(origin_hub.latitude or 0, origin_hub.longitude or 0, h.latitude or 0, h.longitude or 0)
                        + haversine_distance(h.latitude or 0, h.longitude or 0, dest_hub.latitude or 0, dest_hub.longitude or 0)
        )
        d_n1 = haversine_distance(origin_hub.latitude or 0, origin_hub.longitude or 0, nearest_mid.latitude or 0, nearest_mid.longitude or 0)
        d_n2 = haversine_distance(nearest_mid.latitude or 0, nearest_mid.longitude or 0, dest_hub.latitude or 0, dest_hub.longitude or 0)
        pred_ne1 = predict_flight_energy(d_n1, payload, speed=10.0, altitude=45.0)
        pred_ne2 = predict_flight_energy(d_n2, payload, speed=10.0, altitude=45.0)
        e_n1 = pred_ne1["energy_wh"]
        e_n2 = pred_ne2["energy_wh"]
        tot_nd = d_n1 + d_n2
        tot_ne = e_n1 + e_n2

        s_n1 = pred_ne1.get("shap_values") or {}
        s_n2 = pred_ne2.get("shap_values") or {}
        aggregated_n_shap = {
            "speed": 0.0,
            "distance": round(float(s_n1.get("distance", 0.0)) + float(s_n2.get("distance", 0.0)), 2),
            "payload": round(float(s_n1.get("payload", 0.0)) + float(s_n2.get("payload", 0.0)), 2),
            "relative_wind_angle": round(float(s_n1.get("relative_wind_angle", 0.0)) + float(s_n2.get("relative_wind_angle", 0.0)), 2),
            "altitude": round(float(s_n1.get("altitude", 0.0)) + float(s_n2.get("altitude", 0.0)), 2),
        }
        diff_ndist = round(tot_nd - dist_direct, 2)
        diff_nenergy = round(tot_ne - e_direct, 1)
        fallback_ai_explanation = {
            "flight_speed_mps": 10.0,
            "flight_speed_kmh": 36.0,
            "total_energy_wh": round(tot_ne, 2),
            "feature_contributions": [
                {
                    "feature": "distance",
                    "label": "Cự ly hành trình (Distance)",
                    "value": f"{tot_nd:.2f} km (Đường vòng qua {nearest_mid.name})",
                    "shap_contribution_wh": aggregated_n_shap["distance"],
                    "impact": f"Cự ly dài hơn +{diff_ndist:.2f} km so với bay thẳng làm phát sinh thêm +{diff_nenergy:.1f} Wh năng lượng cản di chuyển"
                },
                {
                    "feature": "payload",
                    "label": "Tải trọng hàng hóa (Payload)",
                    "value": f"{payload:.2f} kg",
                    "shap_contribution_wh": aggregated_n_shap["payload"],
                    "impact": f"Tải trọng hàng hóa duy trì nâng đỡ suốt {tot_nd:.2f} km làm tăng tiêu hao +{aggregated_n_shap['payload']:.1f} Wh"
                },
                {
                    "feature": "speed",
                    "label": "Vận tốc bay (Flight Speed)",
                    "value": "10.0 m/s (36.0 km/h)",
                    "shap_contribution_wh": 0.0,
                    "impact": "Vận tốc hành trình kinh tế (10.0 m/s) xuyên suốt cả 2 chặng bay, tối ưu hóa mức tiêu hao công suất"
                },
                {
                    "feature": "wind",
                    "label": "Điều kiện đón gió (Wind Effect)",
                    "value": "Tổng hợp 2 chặng bay",
                    "shap_contribution_wh": aggregated_n_shap["relative_wind_angle"],
                    "impact": "Gió xuôi hỗ trợ tiết kiệm pin trên toàn tuyến" if aggregated_n_shap["relative_wind_angle"] <= 0 else "Gió ngược làm tăng lực cản di chuyển trên các chặng bay"
                }
            ],
            "legs_breakdown": [
                {
                    "leg": 1,
                    "from_hub_code": origin_hub.code or origin_hub.name,
                    "to_hub_code": nearest_mid.code or nearest_mid.name,
                    "distance_km": round(d_n1, 2),
                    "energy_wh": round(e_n1, 2),
                    "speed_mps": 10.0,
                    "speed_kmh": 36.0,
                    "action": f"Bay từ {origin_hub.name} đến {nearest_mid.name} (Tốn {e_n1:.1f} Wh)",
                },
                {
                    "leg": 2,
                    "from_hub_code": nearest_mid.code or nearest_mid.name,
                    "to_hub_code": dest_hub.code or dest_hub.name,
                    "distance_km": round(d_n2, 2),
                    "energy_wh": round(e_n2, 2),
                    "speed_mps": 10.0,
                    "speed_kmh": 36.0,
                    "action": f"Thay pin tại {nearest_mid.name} và bay tiếp tới {dest_hub.name} (Tốn {e_n2:.1f} Wh)",
                }
            ],
            "summary": (
                f"Tuyến trung chuyển dự phòng qua {nearest_mid.name}. Tổng cự ly {tot_nd:.2f} km dài hơn đường thẳng +{diff_ndist:.2f} km, "
                f"tiêu hao {tot_ne:.1f} Wh (+{diff_nenergy:.1f} Wh). Không còn pin tương thích khả dụng tại trạm trung gian."
            )
        }
        fb_string = f"{origin_hub.code or origin_hub.name} -> {nearest_mid.code or nearest_mid.name} (Đổi pin) -> {dest_hub.code or dest_hub.name}"
        fallback_detour = {
            "path": [origin_hub.id, nearest_mid.id, dest_hub.id],
            "relayHubs": [f"{nearest_mid.name} (Tốn {e_n1:.1f}Wh) - Không còn pin tương thích khả dụng trong kho"],
            "distanceKm": round(d_n1 + d_n2, 2),
            "predictedEnergyWh": round(e_n1 + e_n2, 2),
            "predictedDurationMin": int(math.ceil((d_n1 + d_n2) * 2.2)) + 3,
            "wait_time": 0,
            "flight_speed_mps": 10.0,
            "speed_profile_label": "Safe Relay Corridor (10 m/s • Độ cao 45m)",
            "shap_values": aggregated_n_shap,
            "ai_explanation": fallback_ai_explanation,
            "legs_detail": [
                {
                    "from_hub_id": origin_hub.id,
                    "to_hub_id": nearest_mid.id,
                    "from_hub_code": origin_hub.code or origin_hub.name,
                    "to_hub_code": nearest_mid.code or nearest_mid.name,
                    "distance_km": round(d_n1, 2),
                    "energy_wh": round(e_n1, 2),
                    "payload_kg": payload,
                },
                {
                    "from_hub_id": nearest_mid.id,
                    "to_hub_id": dest_hub.id,
                    "from_hub_code": nearest_mid.code or nearest_mid.name,
                    "to_hub_code": dest_hub.code or dest_hub.name,
                    "distance_km": round(d_n2, 2),
                    "energy_wh": round(e_n2, 2),
                    "payload_kg": payload,
                }
            ],
            "route_string": fb_string
        }
        raw_routes.append({"key": "DETOUR", "type": "detour", "data": fallback_detour})
    else:
        # Nếu chỉ có 2 hub toàn hệ thống (không có hub trung gian nào), tạo tuyến thứ 3 dự phòng
        r_bal = plan_single_path("balanced") or r_direct_eco
        raw_routes.append({"key": "BAL", "type": "balanced", "data": r_bal})

    # ── Điều phối Drone từ Hub khác (Cross-Hub Drone Dispatch) ──
    d_disp = 0.0
    e_disp = 0.0
    t_disp = 0
    if is_cross_hub and drone_home_hub:
        d_disp = haversine_distance(
            drone_home_hub.latitude or 0, drone_home_hub.longitude or 0,
            origin_hub.latitude or 0, origin_hub.longitude or 0
        )
        e_disp = predict_flight_energy(d_disp, 0.0)["energy_wh"]
        t_disp = max(1, int(round(d_disp * 2.0)))
        disp_leg_template = {
            "from_hub_id": drone_home_hub.id,
            "to_hub_id": origin_hub.id,
            "from_hub_code": drone_home_hub.code or drone_home_hub.name,
            "to_hub_code": origin_hub.code or origin_hub.name,
            "distance_km": round(d_disp, 2),
            "energy_wh": round(e_disp, 2),
            "payload_kg": 0.0,
            "leg_type": "DISPATCH",
            "is_dispatch": True,
            "note": f"Điều phối Drone từ {drone_home_hub.name} sang {origin_hub.name} nhận hàng (bay rỗng 0kg)",
        }
        home_code = drone_home_hub.code or drone_home_hub.name
        origin_code = origin_hub.code or origin_hub.name
        dest_code = dest_hub.code or dest_hub.name

        for entry in raw_routes:
            d = entry["data"]
            for l_item in d.get("legs_detail", []):
                l_item.setdefault("leg_type", "DELIVERY")
                l_item.setdefault("is_dispatch", False)
                l_item.setdefault("payload_kg", payload)

            d["legs_detail"] = [disp_leg_template.copy()] + d.get("legs_detail", [])
            d["path"] = [drone_home_hub.id] + d.get("path", [])
            d["distanceKm"] = round(d["distanceKm"] + d_disp, 2)
            d["predictedEnergyWh"] = round(d["predictedEnergyWh"] + e_disp, 2)
            d["predictedDurationMin"] = d["predictedDurationMin"] + t_disp

            if entry["key"] in ("ENG", "DIR"):
                d["route_string"] = f"[Điều phối từ {home_code}] -> {origin_code} (Nhận hàng) -> {dest_code} (Giao hàng) (Hành lang Eco 45m • 10m/s)"
            elif entry["key"] == "FAST":
                d["route_string"] = f"[Điều phối từ {home_code}] -> {origin_code} (Nhận hàng) -> {dest_code} (Giao hàng) (Hành lang Tốc độ cao 75m • 15m/s)"
            elif entry["key"] == "DETOUR":
                mid_hubs = [hub_dict[nid].code or hub_dict[nid].name for nid in d["path"] if nid not in (drone_home_hub.id, origin_hub.id, dest_hub.id)]
                mid_str = " -> ".join([f"{m} (Đổi pin)" for m in mid_hubs])
                if mid_str:
                    d["route_string"] = f"[Điều phối từ {home_code}] -> {origin_code} (Nhận hàng) -> {mid_str} -> {dest_code} (Giao hàng)"
                else:
                    d["route_string"] = f"[Điều phối từ {home_code}] -> {origin_code} (Nhận hàng) -> {dest_code} (Giao hàng) (Đổi pin)"
            else:
                d["route_string"] = f"[Điều phối từ {home_code}] -> {origin_code} (Nhận hàng) -> {d['route_string']}"

    _set_zone("ZONE 08: Kiểm tra khả thi Pin từng chặng & Chỉ định pin thay thế Relay")
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
        departure_id = drone_home_hub.id if is_cross_hub and drone_home_hub else origin_hub.id
        relay_hub_ids = [nid for nid in path if nid not in (departure_id, dest_hub.id)]

        if not relay_hub_ids:
            # Direct route — kiểm tra năng lượng chặng duy nhất dựa trên năng lượng của pin được assign cho Drone
            leg0_energy = float(legs_raw[0].get("energy_wh") or d["predictedEnergyWh"]) if legs_raw else float(d["predictedEnergyWh"])
            batt_pct = int(round((leg0_energy / origin_bat_capacity_wh) * 100)) if origin_bat_capacity_wh > 0 else 100
            rem_batt_pct = max(0, origin_bat_charge_pct - batt_pct)

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
                is_cross_hub=is_cross_hub,
                origin_hub_id=origin_hub.id,
            )
            # Cập nhật lại chuỗi hiển thị relayHubs kèm mã pin thay thế cụ thể tại từng Hub trung gian
            updated_relay_labels: list[str] = []
            for r_idx, st in enumerate(hub_statuses):
                prev_leg_e = float(legs_raw[r_idx].get("energy_wh") or 0.0) if r_idx < len(legs_raw) else 0.0
                if is_cross_hub and st.hub_id == origin_hub.id:
                    if st.selected_battery_id and st.selected_battery_id != (assigned_battery.id if assigned_battery else None):
                        updated_relay_labels.append(
                            f"{st.hub_name} (Nhận hàng) - Đổi pin {st.selected_battery_id} "
                            f"({st.selected_battery_serial or st.selected_battery_model or 'Ready'}, "
                            f"{st.selected_battery_energy_wh or st.selected_battery_capacity_wh or 0:.0f}Wh, "
                            f"{st.selected_battery_charge_pct or 0}%)"
                        )
                    else:
                        updated_relay_labels.append(
                            f"{st.hub_name} (Nhận hàng) - Dùng pin điều phối {assigned_battery.id if assigned_battery else ''} để bay tiếp"
                        )
                else:
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
                    leg_type=leg.get("leg_type", "DELIVERY"),
                    is_dispatch=leg.get("is_dispatch", False),
                    payload_kg=leg.get("payload_kg"),
                    note=leg.get("note"),
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
            is_selectable=feasible,
            unselectable_reason=infeasible_reason,
            relay_battery_status=hub_statuses,
            is_cross_hub=is_cross_hub,
            drone_home_hub_id=drone_home_hub.id if is_cross_hub and drone_home_hub else None,
            drone_home_hub_name=drone_home_hub.name if is_cross_hub and drone_home_hub else None,
            drone_home_hub_code=drone_home_hub.code or drone_home_hub.name if is_cross_hub and drone_home_hub else None,
            pickup_hub_id=origin_hub.id if is_cross_hub else None,
            pickup_hub_name=origin_hub.name if is_cross_hub else None,
            pickup_hub_code=origin_hub.code or origin_hub.name if is_cross_hub else None,
            flight_speed_mps=d.get("flight_speed_mps", 10.0),
            speed_profile_label=d.get("speed_profile_label"),
            shap_values=d.get("shap_values"),
            ai_explanation=d.get("ai_explanation"),
        )
        routes_schemas.append(schema)
        raw_data_list.append(d)

    _set_zone("ZONE 09: Đóng gói RouteOptionSchema & Xếp hạng AI Recommendation")
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
            dispatch_prefix = (
                f"[Điều phối liên trạm]: Drone từ {drone_home_hub.name} bay rỗng {d_disp:.1f} km sang {origin_hub.name} nhận hàng. "
                if is_cross_hub and drone_home_hub else ""
            )

            if not s.feasible:
                s.recommended = False
                s.is_selectable = False
                s.unselectable_reason = s.infeasible_reason
                s.reason = f"Tuyến không khả thi (Không thể chọn): {s.infeasible_reason}"
            elif idx == best_idx:
                s.recommended = True
                s.is_selectable = True
                s.unselectable_reason = None
                if "DETOUR" in s.routeId:
                    relays_str = " -> ".join([leg.to_hub_code for leg in s.legs_detail[:-1]])
                    s.reason = (
                        f"{dispatch_prefix}Được AI đề xuất: Tuyến bay thẳng không đủ pin an toàn (vượt 80%). "
                        f"Tuyến đường vòng qua trạm {relays_str} thay pin (đủ pin tương thích gán trực tiếp cho Drone) "
                        f"đảm bảo an toàn năng lượng hoàn thành nhiệm vụ (tổng {s.predictedEnergyWh:.1f} Wh, dự phòng {s.remainingBatteryPct}%). "
                        f"{origin_bat_note}{swap_summary}"
                    ).strip()
                else:
                    s.reason = (
                        f"{dispatch_prefix}Được AI đề xuất: Tuyến bay thẳng tối ưu năng lượng (Hành lang Eco 10 m/s ~ 36 km/h • Độ cao 45m). "
                        f"Phân tích SHAP chứng minh vận tốc 10 m/s giảm thiểu lực cản khí động học (Drag), tiêu thụ thấp nhất "
                        f"(tổng {s.predictedEnergyWh:.1f} Wh, ~{s.batteryConsumptionPct}% pin/chặng, "
                        f"dự phòng {s.remainingBatteryPct}%). {origin_bat_note}{swap_summary}"
                    ).strip()
            else:
                s.recommended = False
                s.is_selectable = True
                s.unselectable_reason = None
                if "DETOUR" in s.routeId:
                    relays_str = " -> ".join([leg.to_hub_code for leg in s.legs_detail[:-1]])
                    diff_d = round(s.distanceKm - routes_schemas[0].distanceKm, 2) if routes_schemas else 0.0
                    diff_e = round(s.predictedEnergyWh - routes_schemas[0].predictedEnergyWh, 1) if routes_schemas else 0.0
                    s.reason = (
                        f"{dispatch_prefix}Tuyến trung chuyển qua trạm đổi pin (Relay Hub): Ghé trạm {relays_str} thay pin. "
                        f"Phân tích SHAP chỉ rõ việc đi vòng làm cự ly tăng thêm +{diff_d:.2f} km, tiêu hao thêm +{diff_e:.1f} Wh so với bay thẳng. "
                        f"Tuyến này đóng vai trò hành lang an toàn (Safe Corridor) hoặc phương án dự phòng khi cần trung chuyển thiết bị giữa các trạm "
                        f"(Tổng cự ly {s.distanceKm} km, {s.predictedEnergyWh:.1f} Wh). {origin_bat_note}{swap_summary}"
                    ).strip()
                elif "SHORT" in s.routeId:
                    s.reason = f"{dispatch_prefix}Quãng đường ngắn nhất: Cự ly {s.distanceKm} km, tiêu thụ {s.predictedEnergyWh:.1f} Wh. {origin_bat_note}{swap_summary}".strip()
                elif "DIR" in s.routeId:
                    s.reason = f"{dispatch_prefix}Tuyến bay thẳng trực tiếp: Cự ly {s.distanceKm} km, không qua trạm sạc trung gian. {origin_bat_note}".strip()
                elif "FAST" in s.routeId:
                    diff_wh = round(s.predictedEnergyWh - routes_schemas[0].predictedEnergyWh, 1) if routes_schemas else 2.8
                    pct_inc = int(round((diff_wh / routes_schemas[0].predictedEnergyWh) * 100)) if (routes_schemas and routes_schemas[0].predictedEnergyWh > 0) else 15
                    s.reason = (
                        f"{dispatch_prefix}Tuyến bay nhanh nhất / Trực tiếp: Bay thẳng với tốc độ hành trình cao (Hành lang Express 15 m/s ~ 54 km/h • Độ cao 75m). "
                        f"Rút ngắn thời gian còn {s.predictedDurationMin} phút, nhưng phân tích SHAP chỉ rõ lực cản không khí tăng bậc hai (v²) "
                        f"khiến năng lượng tiêu thụ tăng thêm +{diff_wh:.1f} Wh (+{pct_inc}%, tổng {s.predictedEnergyWh:.1f} Wh). {origin_bat_note}"
                    ).strip()
                else:
                    s.reason = f"{dispatch_prefix}Tuyến đường thay thế: Cự ly {s.distanceKm} km, thời gian {s.predictedDurationMin} phút. {origin_bat_note}{swap_summary}".strip()

    # Luôn trả về đầy đủ cả 3 tuyến đường để Operator quan sát toàn diện và so sánh các phương án
    # (các tuyến không đủ pin vẫn được hiển thị trên bảng điều khiển kèm cờ feasible=False, is_selectable=False và lý do chi tiết)
    return routes_schemas[:3], raw_data_list[:3]


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

    rec_by_mission = {}

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
                leg_pct = int(round((e / leg_cap) * 100)) if leg_cap > 0 else 50
                is_leg_done = (
                    leg.status == MissionLegStatus.COMPLETED
                    or str(m.status.value if hasattr(m.status, "value") else (m.status or "")).strip().lower() in ("completed", "mission completed")
                )
                if is_leg_done:
                    leg_rem_pct = int(round(leg_charge))
                else:
                    leg_rem_pct = max(0, leg_charge - leg_pct)
                leg_consumption_pcts.append(leg_pct)

                is_dispatch_leg = False
                if m.origin_hub_id and leg.to_hub_id == m.origin_hub_id and leg.from_hub_id != m.origin_hub_id and idx == 0 and len(m_legs) > 1:
                    is_dispatch_leg = True

                leg_type_val = "DISPATCH" if is_dispatch_leg else "DELIVERY"
                leg_payload_val = 0.0 if is_dispatch_leg else (payload if payload > 0 else None)

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
                        leg_type=leg_type_val,
                        is_dispatch=is_dispatch_leg,
                        payload_kg=leg_payload_val,
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
                leg_pct = int(round((e / origin_bat_cap) * 100)) if origin_bat_cap > 0 else 50
                is_mission_done = str(m.status.value if hasattr(m.status, "value") else (m.status or "")).strip().lower() in ("completed", "mission completed")
                if is_mission_done:
                    leg_rem_pct = int(round(origin_bat_charge))
                else:
                    leg_rem_pct = max(0, origin_bat_charge - leg_pct)
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
                        status=(
                            "COMPLETED" if str(m.status.value if hasattr(m.status, "value") else (m.status or "")).strip().lower() in ("completed", "mission completed")
                            else (
                                "IN_PROGRESS" if str(m.status.value if hasattr(m.status, "value") else (m.status or "")).strip().lower() in ("flying", "in progress", "active mission")
                                else (
                                    "CANCELLED" if str(m.status.value if hasattr(m.status, "value") else (m.status or "")).strip().lower() in ("cancelled", "rejected", "failed", "incident return")
                                    else "PENDING"
                                )
                            )
                        ),
                        energy_wh=round(e, 2),
                        battery_id=origin_bat_id,
                        battery_serial=origin_bat_obj.serial_number if origin_bat_obj else None,
                        battery_model=origin_bat_obj.battery_model if origin_bat_obj else None,
                        battery_capacity_wh=round(origin_bat_cap, 2),
                        battery_charge_pct=origin_bat_charge if origin_bat_obj else None,
                        battery_energy_wh=round(origin_bat_energy, 2),
                        battery_consumption_pct=leg_pct,
                        remaining_battery_pct=leg_rem_pct,
                        leg_type="DELIVERY",
                        is_dispatch=False,
                        payload_kg=payload if payload > 0 else None,
                    )
                )

        dist_km = round(m.distance_m / 1000.0, 2) if m.distance_m else round(sum(l.distance_km for l in legs_detail), 2)
        ai_pred = pred_by_mission.get(m.id)
        ai_rec = None

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
        rec_content = ai_pred.recommendation_content if ai_pred else None
        reason = (
            rec_content
            if rec_content
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

    reserved_info_map = await _get_reserved_battery_info_map(
        db, exclude_order_id=order_id.strip() if order_id else None
    )
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
        if target_hub_id is not None and b.current_hub_id != target_hub_id and b.drone_id != drone.id:
            continue
        if compatible_models and b.battery_model and b.battery_model not in compatible_models:
            continue
        cap_wh, charge_pct, avail_energy_wh = _compute_battery_energy_wh(b, default_cap)
        is_installed = (b.drone_id == drone.id)

        is_selectable = True
        status_str = "READY"
        assigned_mission_id = None
        assigned_mission_code = None
        assigned_mission_status = None
        unavailable_reason = None

        res_info = reserved_info_map.get(b.id)
        if res_info:
            is_selectable = False
            status_str = "ASSIGNED_TO_MISSION"
            assigned_mission_id = res_info.get("mission_id")
            assigned_mission_code = res_info.get("mission_code")
            assigned_mission_status = res_info.get("mission_status")
            unavailable_reason = res_info.get("reason")
        elif b.drone_id is not None and b.drone_id != drone.id:
            is_selectable = False
            status_str = "INSTALLED_ON_OTHER_DRONE"
            unavailable_reason = f"Đang được gắn trên Drone #{b.drone_id}"

        eligible_list.append({
            "id": b.id,
            "serial_number": b.serial_number,
            "battery_model": b.battery_model,
            "capacity_wh": round(cap_wh, 2),
            "charge_level_pct": charge_pct,
            "available_energy_wh": round(avail_energy_wh, 2),
            "current_hub_id": b.current_hub_id,
            "drone_id": b.drone_id,
            "is_installed_on_drone": is_installed,
            "available_status": status_str,
            "is_selectable": is_selectable,
            "assigned_mission_id": assigned_mission_id,
            "assigned_mission_code": assigned_mission_code,
            "assigned_mission_status": assigned_mission_status,
            "unavailable_reason": unavailable_reason,
        })

    eligible_list.sort(
        key=lambda item: (
            1 if item["is_selectable"] else 0,
            1 if item["is_installed_on_drone"] else 0,
            item["available_energy_wh"],
            item["charge_level_pct"],
        ),
        reverse=True,
    )

    default_battery_id = next((item["id"] for item in eligible_list if item["is_selectable"]), None)

    return {
        "order_id": order_id.strip() if order_id else None,
        "origin_hub_id": target_hub_id,
        "drone_id": drone.id,
        "drone_name": drone.name,
        "drone_model": drone.model,
        "compatible_battery_models": compatible_models,
        "default_battery_id": default_battery_id,
        "batteries": eligible_list,
    }



# ══════════════════════════════════════════════════════════════════════════════
# ORDER CONSOLIDATION / PIGGYBACKING (GOM ĐƠN / GHÉP ĐƠN)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/bundle/candidates-pre-analyze", summary="[Luồng 1 - Pre-Analyze] Lấy danh sách đơn có thể gộp trước khi analyze")
async def get_bundle_candidates_pre_analyze(
    request: PreAnalyzeBundleCandidatesRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Luồng 1 - Pre-Analyze Consolidation]**
    Trả về danh sách các đơn hàng khả dụng tại cùng Hub xuất phát & Hub đích để Operator gom đơn trước.
    Kèm gợi ý dòng Drone (Swift, Falcon, Eagle) phù hợp với tổng tải trọng.
    """
    return await get_pre_analyze_bundle_candidates(db, request.orderId)


@router.post("/bundle/validate-pre-analyze", summary="[Luồng 1 & 2] Xác thực gói đơn hàng gộp trước khi lập lộ trình hoặc tạo mission")
async def validate_bundle_orders_endpoint(
    request: ValidateBundleRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Xác thực tập hợp đơn hàng định gộp chung một chuyến bay:
    - Kiểm tra cùng Hub gửi và nhận.
    - Kiểm tra trạng thái AT_HUB & PAID.
    - Kiểm tra tổng tải trọng <= tải trọng tối đa của Drone (nếu có chọn Drone).
    """
    return await validate_bundle_orders(db, request.orderIds, request.droneId)


@router.post("/bundle/candidates-post-analyze", summary="[Luồng 2 - Post-Analyze] Hiển thị đơn có thể ghép thêm vào Drone và tuyến đã chọn")
async def get_bundle_candidates_post_analyze(
    request: PostAnalyzeBundleCandidatesRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Luồng 2 - Post-Analyze Consolidation / Piggybacking]**
    Sau khi Operator đã chọn đơn chính & Drone và chạy Analyze:
    - Tính tải trọng còn dư của Drone = payload_capacity - primary_order.payload.
    - Tìm và hiển thị các đơn hàng cùng tuyến có thể gộp thêm mà không vượt quá tải trọng còn lại.
    - Cho phép Operator chọn thêm đơn và tạo Mission trực tiếp mà không cần chạy lại vòng lặp analyze.
    """
    return await get_post_analyze_bundle_candidates(
        db=db,
        primary_order_id=request.orderId,
        drone_id=request.droneId,
        route_id=request.routeId,
    )

@router.post("/mission-planning/analyze", response_model=MissionPlanningAnalyzeResponse)
async def analyze_mission_planning(
    request: MissionPlanningAnalyzeRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db)
):
    zone_tracker = {"current": "ZONE 01: Kiểm tra Order & Thông tin Hub xuất phát - đích"}
    try:
        clean_order_id = str(request.orderId).strip() if request.orderId else request.orderId
        order = await db.get(Order, clean_order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        clean_drone_id = str(request.droneId).strip() if request.droneId is not None else None
        requested_battery_id = (request.batteryId or request.battery_id or "").strip() or None
        total_bundle_payload = None
        if request.bundledOrderIds:
            all_bundle_ids = [clean_order_id] + [str(x).strip() for x in request.bundledOrderIds if str(x).strip()]
            val_res = await validate_bundle_orders(db, all_bundle_ids, clean_drone_id)
            if not val_res["is_valid"]:
                raise HTTPException(status_code=400, detail=val_res["message"])
            total_bundle_payload = val_res["total_payload_kg"]

        routes_schemas, _ = await compute_mission_routes_internal(
            db=db,
            order=order,
            drone_id=clean_drone_id,
            battery_id=requested_battery_id,
            strict_battery=bool(requested_battery_id),
            override_payload_kg=total_bundle_payload,
            zone_tracker=zone_tracker,
        )

        zone_tracker["current"] = "ZONE 10: Hoàn tất đóng gói phản hồi MissionPlanningAnalyzeResponse"
        first_route = routes_schemas[0] if routes_schemas else None
        is_cross = first_route.is_cross_hub if first_route else False
        return MissionPlanningAnalyzeResponse(
            orderId=order.id,
            bundledOrderIds=request.bundledOrderIds if request.bundledOrderIds else None,
            totalPayloadKg=total_bundle_payload if total_bundle_payload is not None else float(order.payload_kg or 0),
            droneId=first_route.suggestedDroneId if first_route else clean_drone_id,
            assignedBatteryId=first_route.assignedBatteryId if first_route else requested_battery_id,
            assignedBatterySerial=first_route.assignedBatterySerial if first_route else None,
            assignedBatteryModel=first_route.assignedBatteryModel if first_route else None,
            assignedBatteryCapacityWh=first_route.assignedBatteryCapacityWh if first_route else None,
            assignedBatteryChargePct=first_route.assignedBatteryChargePct if first_route else None,
            assignedBatteryEnergyWh=first_route.assignedBatteryEnergyWh if first_route else None,
            droneCurrentHubId=first_route.drone_home_hub_id if (first_route and is_cross) else order.origin_hub_id,
            droneCurrentHubName=first_route.drone_home_hub_name if (first_route and is_cross) else None,
            droneCurrentHubCode=first_route.drone_home_hub_code if (first_route and is_cross) else None,
            isCrossHubDispatch=is_cross,
            pickupHubId=first_route.pickup_hub_id if (first_route and is_cross) else None,
            pickupHubName=first_route.pickup_hub_name if (first_route and is_cross) else None,
            pickupHubCode=first_route.pickup_hub_code if (first_route and is_cross) else None,
            routes=routes_schemas,
        )
    except HTTPException:
        raise
    except Exception as e:
        import logging, traceback
        failed_zone = zone_tracker.get("current", "UNKNOWN_ZONE")
        error_msg = f"[ERROR][{failed_zone}] Lỗi khi phân tích lộ trình Order {request.orderId}: {e}"
        logging.error(f"{error_msg}\n{traceback.format_exc()}")
        try:
            print(f"\n{error_msg}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=f"[{failed_zone}] Lỗi hệ thống khi phân tích nhiệm vụ: {str(e)}"
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
        6. Tạo bản ghi `Mission`, `MissionLeg`, `AIPrediction`.
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

    # Kiểm tra và xử lý ghép đơn (Order Consolidation / Piggybacking) nếu có truyền bundledOrderIds
    bundled_ids = [str(x).strip() for x in (request.bundledOrderIds or []) if str(x).strip() and str(x).strip() != clean_order_id]
    all_mission_order_ids = [clean_order_id] + bundled_ids
    total_bundle_payload = float(order.payload_kg or 0.0)
    total_delivery_fee = float(order.delivery_fee or 0.0)
    bundled_order_objs = [order]

    if bundled_ids:
        val_res = await validate_bundle_orders(db, all_mission_order_ids, request.droneId)
        if not val_res["is_valid"]:
            raise HTTPException(status_code=400, detail=val_res["message"])
        total_bundle_payload = val_res["total_payload_kg"]
        total_delivery_fee = val_res["total_delivery_fee"]
        for bid in bundled_ids:
            b_obj = await db.get(Order, bid)
            if b_obj:
                bundled_order_objs.append(b_obj)

    # Tái hiện các tùy chọn tuyến đường dựa trên năng lượng của pin được gán + chỉ định pin tại các Relay Hub
    routes_schemas, raw_data_list = await compute_mission_routes_internal(
        db=db,
        order=order,
        drone_id=request.droneId,
        battery_id=requested_battery_id,
        strict_battery=True,
        override_payload_kg=total_bundle_payload,
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

    if assigned_battery.charge_level_pct is not None:
        drone.battery_level_pct = assigned_battery.charge_level_pct

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
        payload_weight=total_bundle_payload,
        distance_m=round(total_dist_km * 1000, 2),
        delivery_fee=total_delivery_fee,
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
    rec_content = (
        selected_schema.reason
        if (selected_schema and selected_schema.reason)
        else (
            f"Lộ trình {route_str} được chọn theo phân tích AI ({total_energy_wh:.1f} Wh). "
            f"Pin xuất phát: {assigned_battery.id} ({assigned_battery.serial_number})."
            + (f" Pin thay thế trung gian: {', '.join(relay_swap_summary_parts)}." if relay_swap_summary_parts else "")
        )
    )
    ai_pred = AIPrediction(
        mission_id=mission_id,
        estimated_energy_wh=round(total_energy_wh, 2),
        estimated_duration_m=float(duration_min),
        risk_level=selected_schema.risk.capitalize() if (selected_schema and selected_schema.risk) else "Low",
        recommendation_content=rec_content,
        is_recommendation_applied=True,
        predicted_at=get_vn_datetime()
    )
    db.add(ai_pred)

    # 4. Ghi nhận thời gian planning trên tất cả các đơn hàng thuộc nhiệm vụ (xóa cờ replan_required_at nếu có)
    now_naive = get_vn_naive_datetime()
    for o_item in bundled_order_objs:
        o_item.planning_at = now_naive
        o_item.replan_required_at = None

    await db.commit()
    await db.refresh(mission)

    # Gửi email thông báo cho tất cả người nhận của các đơn hàng trong chuyến bay
    try:
        from app.core.email import send_mission_scheduled_email
        from app.core.config import settings
        for o_item in bundled_order_objs:
            if o_item.receiver_email:
                await send_mission_scheduled_email(
                    to_email=o_item.receiver_email,
                    order_id=o_item.id,
                    mission_id=mission.id,
                    frontend_url=settings.FRONTEND_URL,
                )
    except Exception as e:
        print(f"[EMAIL] Lỗi gửi mission scheduled email: {e}")

    resp_mission = await build_mission_detail_response(db, mission)
    resp_mission.bundled_order_ids = all_mission_order_ids
    resp_mission.total_orders_count = len(all_mission_order_ids)
    return resp_mission


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
    if mission.drone_id:
        drone_to_sync = await db.get(Drone, mission.drone_id)
        if drone_to_sync and new_battery.charge_level_pct is not None:
            drone_to_sync.battery_level_pct = new_battery.charge_level_pct

    # ── 5. Hoàn tất swap pin ──────────────────────────────────────────────────

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



# ── 3-Tier Failsafe Engine & Dynamic In-flight Divert ────────────────────────────

CRUISE_ALTITUDE_M = 50.0            # Độ cao tĩnh không đô thị chuẩn (50m - 80m theo QCVN)
CRUISE_SPEED_MS = 12.0              # Vận tốc bay hành trình (12 m/s ~ 43.2 km/h)
SAFE_BATTERY_RESERVE_PCT = 20.0     # Ngưỡng dự phòng an toàn bắt buộc khi tới đích (20%)
EMERGENCY_LAND_PCT = 10.0           # Ngưỡng cạn kiệt bắt buộc hạ cánh thẳng đứng (< 10%)
HIGH_WIND_THRESHOLD_MS = 8.0        # Ngưỡng gió bất lợi cấp 5 Beaufort (8 m/s)
BASE_CONSUMPTION_PCT_PER_KM = 2.0   # Mức tiêu thụ pin chuẩn: 2.0% mỗi km bay


@router.post("/{mission_id}/failsafe-eval", response_model=FailsafeEvaluateResponse)
async def evaluate_mission_failsafe(
    mission_id: str,
    body: FailsafeEvaluateRequest = FailsafeEvaluateRequest(),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator & Simulator] Đánh giá an toàn chuyến bay theo cơ chế 3-Tier Failsafe Engine.**
    Phát hiện sụt pin bất thường, gió lớn, tính toán điểm không thể quay đầu (PNR),
    và tự động đề xuất chuyển hướng hạ cánh khẩn cấp (Dynamic In-flight Divert).
    """
    mission = await db.get(Mission, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    # 1. Lấy thông tin Drone & Battery
    drone = await db.get(Drone, str(mission.drone_id)) if mission.drone_id else None
    battery = await db.get(Battery, str(mission.battery_id)) if mission.battery_id else None

    # 2. Lấy Telemetry log mới nhất
    latest_log_res = await db.execute(
        select(TelemetryLog)
        .where(TelemetryLog.mission_id == mission_id)
        .order_by(TelemetryLog.timestamp.desc())
        .limit(1)
    )
    latest_log = latest_log_res.scalars().first()

    # 3. Lấy chặng bay hiện tại (Leg đang IN_PROGRESS hoặc chặng đầu tiên PENDING)
    leg_res = await db.execute(
        select(MissionLeg)
        .where(MissionLeg.mission_id == mission_id)
        .order_by(MissionLeg.sequence_no.asc())
    )
    all_legs = leg_res.scalars().all()
    curr_leg = None
    for l in all_legs:
        if l.status == MissionLegStatus.IN_PROGRESS:
            curr_leg = l
            break
    if not curr_leg and all_legs:
        curr_leg = all_legs[0]

    # Lấy tọa độ trạm gốc và trạm đích của chặng
    origin_hub_id = curr_leg.from_hub_id if curr_leg and curr_leg.from_hub_id else mission.origin_hub_id
    target_hub_id = curr_leg.to_hub_id if curr_leg and curr_leg.to_hub_id else mission.destination_hub_id

    origin_hub = await db.get(Hub, origin_hub_id) if origin_hub_id else None
    target_hub = await db.get(Hub, target_hub_id) if target_hub_id else None

    # Tọa độ hiện tại của Drone
    curr_lat = body.current_lat
    curr_lng = body.current_lng
    if curr_lat is None or curr_lng is None:
        if latest_log:
            curr_lat = latest_log.latitude
            curr_lng = latest_log.longitude
        elif origin_hub and origin_hub.latitude and origin_hub.longitude:
            curr_lat = origin_hub.latitude
            curr_lng = origin_hub.longitude
        else:
            curr_lat = 10.7769
            curr_lng = 106.7009

    # Mức pin hiện tại
    if body.override_battery_pct is not None:
        curr_bat = max(0.0, min(100.0, float(body.override_battery_pct)))
    elif drone and drone.battery_level_pct is not None:
        curr_bat = float(drone.battery_level_pct)
    elif battery and battery.charge_level_pct is not None:
        curr_bat = float(battery.charge_level_pct)
    else:
        curr_bat = 100.0

    # Vận tốc gió
    if body.override_wind_speed is not None:
        wind_spd = float(body.override_wind_speed)
    elif body.simulate_wind_anomaly:
        wind_spd = 11.5  # Gió cấp 6 Beaufort (> 10.8 m/s)
    elif latest_log and latest_log.weather_wind_speed is not None:
        wind_spd = float(latest_log.weather_wind_speed)
    else:
        wind_spd = 3.2

    # Đánh giá gió & mức tiêu thụ pin hiệu dụng
    wind_warning = wind_spd > HIGH_WIND_THRESHOLD_MS
    wind_factor = (1.0 + (wind_spd - HIGH_WIND_THRESHOLD_MS) * 0.12) if wind_warning else 1.0
    effective_consumption_per_km = BASE_CONSUMPTION_PCT_PER_KM * wind_factor

    def _calc_dist_km(lat1, lon1, lat2, lon2):
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return 999.0
        R = 6371.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 3)

    # 4. Tính toán chặng đến Đích và quay về Gốc
    dist_to_dest_km = _calc_dist_km(curr_lat, curr_lng, target_hub.latitude if target_hub else None, target_hub.longitude if target_hub else None)
    pct_to_dest = round(dist_to_dest_km * effective_consumption_per_km, 2)
    rem_at_dest = round(curr_bat - pct_to_dest, 2)

    dist_to_origin_km = _calc_dist_km(curr_lat, curr_lng, origin_hub.latitude if origin_hub else None, origin_hub.longitude if origin_hub else None)
    pct_to_origin = round(dist_to_origin_km * effective_consumption_per_km, 2)
    rem_at_origin = round(curr_bat - pct_to_origin, 2)

    pnr_reached = (dist_to_origin_km >= dist_to_dest_km) or (rem_at_origin < SAFE_BATTERY_RESERVE_PCT)

    target_info = FailsafeHubInfo(
        hub_id=target_hub.id if target_hub else 0,
        hub_code=target_hub.code if target_hub else "",
        hub_name=target_hub.name if target_hub else "Unknown Target Hub",
        latitude=target_hub.latitude if target_hub else 0.0,
        longitude=target_hub.longitude if target_hub else 0.0,
        distance_km=dist_to_dest_km,
        estimated_battery_needed_pct=pct_to_dest,
        estimated_remaining_pct=rem_at_dest,
        is_safe=(rem_at_dest >= SAFE_BATTERY_RESERVE_PCT),
    ) if target_hub else None

    origin_info = FailsafeHubInfo(
        hub_id=origin_hub.id if origin_hub else 0,
        hub_code=origin_hub.code if origin_hub else "",
        hub_name=origin_hub.name if origin_hub else "Unknown Origin Hub",
        latitude=origin_hub.latitude if origin_hub else 0.0,
        longitude=origin_hub.longitude if origin_hub else 0.0,
        distance_km=dist_to_origin_km,
        estimated_battery_needed_pct=pct_to_origin,
        estimated_remaining_pct=rem_at_origin,
        is_safe=(rem_at_origin >= SAFE_BATTERY_RESERVE_PCT),
    ) if origin_hub else None

    # 5. Quét tất cả Hubs để tìm trạm dự phòng khẩn cấp an toàn nhất
    hubs_res = await db.execute(select(Hub).where(and_(Hub.latitude.isnot(None), Hub.longitude.isnot(None))))
    all_hubs = hubs_res.scalars().all()

    emergency_candidates: list[FailsafeHubInfo] = []
    for h in all_hubs:
        d_km = _calc_dist_km(curr_lat, curr_lng, h.latitude, h.longitude)
        needed_pct = round(d_km * effective_consumption_per_km, 2)
        rem_pct = round(curr_bat - needed_pct, 2)
        is_safe = rem_pct >= EMERGENCY_LAND_PCT
        info = FailsafeHubInfo(
            hub_id=h.id,
            hub_code=h.code,
            hub_name=h.name,
            latitude=h.latitude,
            longitude=h.longitude,
            distance_km=d_km,
            estimated_battery_needed_pct=needed_pct,
            estimated_remaining_pct=rem_pct,
            is_safe=is_safe,
        )
        if is_safe:
            emergency_candidates.append(info)

    # Lọc trạm khác đích hiện tại để làm trạm chuyển hướng dự phòng
    alt_candidates = [c for c in emergency_candidates if (target_hub is None or c.hub_id != target_hub.id)]
    tier_a_hubs = sorted([c for c in alt_candidates if c.estimated_remaining_pct >= SAFE_BATTERY_RESERVE_PCT], key=lambda x: x.distance_km)
    tier_b_hubs = sorted([c for c in alt_candidates if c.estimated_remaining_pct >= EMERGENCY_LAND_PCT], key=lambda x: x.distance_km)

    recommended_hub = tier_a_hubs[0] if tier_a_hubs else (tier_b_hubs[0] if tier_b_hubs else None)

    # 6. Ra quyết định 3-Tier Failsafe Engine
    divert_route = []
    if curr_bat < EMERGENCY_LAND_PCT or (not emergency_candidates):
        # TIER 3: Mức pin nguy cấp cạn kiệt (< 10%)
        status = "EMERGENCY_LAND_TRIGGERED"
        tier = 3
        action = "EMERGENCY_LAND"
        reason = (
            f"Mức pin nguy cấp ({curr_bat:.1f}%) không đủ duy trì bay tới bất kỳ trạm nào an toàn. "
            f"Kích hoạt hạ cánh khẩn cấp thẳng đứng có kiểm soát tại vị trí an toàn định trước."
        )
        can_execute = True

    elif rem_at_dest < SAFE_BATTERY_RESERVE_PCT or (wind_warning and rem_at_dest < 25.0):
        # Vi phạm ngưỡng an toàn 20% khi tới đích!
        if (not pnr_reached) and rem_at_origin >= SAFE_BATTERY_RESERVE_PCT and origin_info:
            # TIER 1: Chưa qua PNR -> Return-to-Home
            status = "RTH_TRIGGERED"
            tier = 1
            action = "RETURN_TO_HOME"
            recommended_hub = origin_info
            reason = (
                f"Trước điểm PNR: Gặp bất lợi thời tiết/sụt pin khiến dự phóng tới đích chỉ còn {rem_at_dest:.1f}% (< 20%). "
                f"Tự động kích hoạt Return-to-Home (RTH) quay đầu về trạm gốc {origin_hub.name if origin_hub else ''} "
                f"(dự phóng tiếp đất an toàn với {rem_at_origin:.1f}% pin)."
            )
            can_execute = True
        elif recommended_hub:
            # TIER 2: Đã qua PNR -> Dynamic In-flight Divert to Nearest Relay Hub
            status = "DIVERT_TRIGGERED"
            tier = 2
            action = "DIVERT_TO_HUB"
            reason = (
                f"Phát hiện suy hao pin bất thường ({curr_bat:.1f}%). "
                f"Dự phóng tới đích chỉ còn {rem_at_dest:.1f}% (vi phạm ngưỡng an toàn 20%). "
                f"Tự động kích hoạt chuyển hướng khẩn cấp về {recommended_hub.hub_name} cách {recommended_hub.distance_km:.2f}km "
                f"(dự kiến tiếp đất an toàn với {recommended_hub.estimated_remaining_pct:.1f}% pin để sạc/đổi pin)."
            )
            can_execute = True
        else:
            status = "EMERGENCY_LAND_TRIGGERED"
            tier = 3
            action = "EMERGENCY_LAND"
            reason = f"Không tìm thấy trạm dự phòng khả dụng trong bán kính an toàn. Kích hoạt hạ cánh khẩn cấp."
            can_execute = True
    else:
        # TIER 0: An toàn hoặc Cảnh báo nhẹ
        if rem_at_dest < 25.0 or wind_warning:
            status = "WARNING"
            tier = 0
            action = "CONTINUE"
            reason = (
                f"Cảnh báo: Pin dự phóng tại đích còn {rem_at_dest:.1f}% (tiệm cận ngưỡng an toàn 20%). "
                f"{'Gió mạnh ' + str(wind_spd) + 'm/s (> 8m/s). ' if wind_warning else ''}"
                f"Hệ thống duy trì giám sát telemetry liên tục."
            )
            can_execute = False
        else:
            status = "SAFE"
            tier = 0
            action = "CONTINUE"
            reason = f"Chuyến bay ổn định. Pin dự kiến khi tới trạm đích còn {rem_at_dest:.1f}% (đạt chuẩn an toàn >= 20%)."
            can_execute = False

    # 7. Sinh waypoints cho đường bay chuyển hướng khẩn cấp (vẽ line màu cam/đỏ trên bản đồ)
    if action in ("DIVERT_TO_HUB", "RETURN_TO_HOME") and recommended_hub:
        target_lat = recommended_hub.latitude
        target_lng = recommended_hub.longitude
        STEPS = 6
        for i in range(STEPS + 1):
            t = i / float(STEPS)
            wp_lat = round(curr_lat + (target_lat - curr_lat) * t, 6)
            wp_lng = round(curr_lng + (target_lng - curr_lng) * t, 6)
            divert_route.append({
                "step": i,
                "latitude": wp_lat,
                "longitude": wp_lng,
                "altitude": CRUISE_ALTITUDE_M,
            })

    return FailsafeEvaluateResponse(
        mission_id=mission.id,
        current_status=mission.status.value if hasattr(mission.status, "value") else str(mission.status),
        drone_id=mission.drone_id,
        current_lat=round(curr_lat, 6),
        current_lng=round(curr_lng, 6),
        current_battery_pct=round(curr_bat, 1),
        wind_speed=round(wind_spd, 1),
        wind_warning=wind_warning,
        status=status,
        tier=tier,
        action=action,
        reason=reason,
        pnr_reached=pnr_reached,
        target_hub=target_info,
        origin_hub=origin_info,
        recommended_hub=recommended_hub,
        available_emergency_hubs=tier_a_hubs + tier_b_hubs,
        divert_route=divert_route,
        can_execute=can_execute,
    )


@router.post("/{mission_id}/failsafe-execute", response_model=FailsafeExecuteResponse)
async def execute_mission_failsafe(
    mission_id: str,
    body: FailsafeExecuteRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator & System] Thực thi hành động an toàn theo quyết định của Failsafe Engine.**
    - DIVERT_TO_HUB: Chuyển hướng chặng bay sang Hub an toàn lân cận, tạo Incident HIGH, tạo MaintenanceAlert.
    - RETURN_TO_HOME: Quay đầu về Hub xuất phát, tạo Incident MEDIUM.
    - EMERGENCY_LAND: Hạ cánh khẩn cấp thẳng đứng, chuyển Drone sang MAINTENANCE.
    """
    from app.modules.system.models import Notification, NotificationType
    from app.modules.auth.models import User, ROLE_NAME_TO_ID
    from app.shared.roles import UserRole as _Role

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
            detail=f"Không thể thực thi failsafe cho mission đang ở trạng thái '{mission.status.value}'. "
                   f"Chỉ áp dụng cho mission đang hoạt động (Flying/In Progress/Approved)."
        )

    now = get_vn_naive_datetime()
    previous_status = mission.status.value if hasattr(mission.status, "value") else str(mission.status)

    # 1. Xử lý hạ cánh khẩn cấp (EMERGENCY_LAND)
    if body.action == "EMERGENCY_LAND":
        mission.status = MissionStatus.INCIDENT_RETURN
        mission.end_time = now

        incident = Incident(
            mission_id=mission.id,
            drone_id=str(mission.drone_id) if mission.drone_id else None,
            reporter_id=user.id,
            severity=IncidentSeverity.HIGH,
            description=f"[EMERGENCY LAND - FAILSAFE] Mission #{mission_id} — {body.reason}",
            status=IncidentStatus.OPEN,
            reported_at=now,
            requires_technical_inspection=True,
        )
        db.add(incident)
        await db.flush()

        alert = MaintenanceAlert(
            drone_id=str(mission.drone_id) if mission.drone_id else None,
            source="FailsafeEmergencyLand",
            incident_id=incident.id,
            mission_id=str(mission.id),
            title=f"Hạ cánh khẩn cấp Failsafe — Drone #{mission.drone_id} cần kiểm tra ngay",
            status="Pending",
            created_at=now,
        )
        db.add(alert)
        await db.flush()

        if mission.drone_id:
            drone = await db.get(Drone, str(mission.drone_id))
            if drone:
                drone.status = DroneStatus.MAINTENANCE

        await db.commit()

        return FailsafeExecuteResponse(
            mission_id=mission.id,
            previous_status=previous_status,
            current_status=MissionStatus.INCIDENT_RETURN.value,
            action_taken="EMERGENCY_LAND",
            incident_id=incident.id,
            alert_id=alert.id,
            message="Kích hoạt hạ cánh khẩn cấp thành công. Drone chuyển sang MAINTENANCE.",
        )

    # 2. Xử lý Chuyển hướng sang Hub lân cận (DIVERT_TO_HUB) hoặc Quay về gốc (RETURN_TO_HOME)
    target_hub_id = body.target_hub_id
    if body.action == "RETURN_TO_HOME":
        target_hub_id = mission.origin_hub_id

    if not target_hub_id:
        raise HTTPException(status_code=400, detail="Thiếu target_hub_id cho hành động chuyển hướng khẩn cấp.")

    target_hub = await db.get(Hub, target_hub_id)
    if not target_hub:
        raise HTTPException(status_code=404, detail=f"Target Hub #{target_hub_id} not found")

    # Lấy chặng bay đang IN_PROGRESS hoặc chặng đầu tiên PENDING để chuyển hướng đích
    leg_res = await db.execute(
        select(MissionLeg)
        .where(MissionLeg.mission_id == mission_id)
        .order_by(MissionLeg.sequence_no.asc())
    )
    all_legs = leg_res.scalars().all()
    curr_leg = None
    for l in all_legs:
        if l.status == MissionLegStatus.IN_PROGRESS:
            curr_leg = l
            break
    if not curr_leg and all_legs:
        curr_leg = all_legs[0]
        curr_leg.status = MissionLegStatus.IN_PROGRESS

    if curr_leg:
        curr_leg.to_hub_id = target_hub.id

    # Đóng/xóa các chặng phía sau vì hành trình đã được chuyển hướng sang trạm dự phòng
    if curr_leg:
        for l in all_legs:
            if (l.sequence_no or 0) > (curr_leg.sequence_no or 0):
                await db.delete(l)

    mission.destination_hub_id = target_hub.id

    # Tạo Incident ghi nhận sự cố chuyển hướng
    incident_tag = "[FAILSAFE DIVERT]" if body.action == "DIVERT_TO_HUB" else "[FAILSAFE RTH]"
    incident = Incident(
        mission_id=mission.id,
        drone_id=str(mission.drone_id) if mission.drone_id else None,
        reporter_id=user.id,
        severity=IncidentSeverity.HIGH if body.action == "DIVERT_TO_HUB" else IncidentSeverity.MEDIUM,
        description=f"{incident_tag} Mission #{mission_id} tự động chuyển hướng sang {target_hub.name} (Hub #{target_hub.id}). Lý do: {body.reason}",
        status=IncidentStatus.INVESTIGATING,
        reported_at=now,
        requires_technical_inspection=True,
    )
    db.add(incident)
    await db.flush()

    # Tạo MaintenanceAlert
    alert = MaintenanceAlert(
        drone_id=str(mission.drone_id) if mission.drone_id else None,
        source="FailsafeDivert",
        incident_id=incident.id,
        mission_id=str(mission.id),
        title=f"Failsafe chuyển hướng — Drone #{mission.drone_id} đáp tại {target_hub.name} cần kiểm tra pin",
        status="Pending",
        created_at=now,
    )
    db.add(alert)
    await db.flush()

    # Gửi Thông báo cho Operator & Customer
    op_role_id = ROLE_NAME_TO_ID.get(_Role.OPERATOR)
    op_result = await db.execute(select(User.id).where(User.role_id == op_role_id))
    operator_ids = op_result.scalars().all()

    notif_title = f"🚨 CHUYỂN HƯỚNG KHẨN CẤP — Mission #{mission_id}"
    notif_msg = (
        f"Hệ thống Failsafe đã kích hoạt chuyển hướng chuyến bay #{mission_id} "
        f"(Drone #{mission.drone_id}) sang {target_hub.name} (Hub #{target_hub.id}). "
        f"Lý do: {body.reason}."
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

    if mission.customer_id:
        db.add(Notification(
            user_id=mission.customer_id,
            title="Thông báo hành trình chuyến bay",
            message=f"Chuyến bay #{mission_id} đang tạm thời chuyển hướng sang trạm kỹ thuật {target_hub.name} để kiểm tra an toàn tối ưu.",
            notifications_type=NotificationType.MISSION_STATUS,
            mission_id=str(mission.id),
            created_at=now,
        ))

    await db.commit()

    return FailsafeExecuteResponse(
        mission_id=mission.id,
        previous_status=previous_status,
        current_status=mission.status.value if hasattr(mission.status, "value") else str(mission.status),
        action_taken=body.action,
        target_hub_id=target_hub.id,
        target_hub_name=target_hub.name,
        incident_id=incident.id,
        alert_id=alert.id,
        message=(
            f"Failsafe {body.action} thực thi thành công. "
            f"Tuyến bay đã được chuyển hướng về {target_hub.name}. "
            f"Incident #{incident.id} và MaintenanceAlert #{alert.id} đã được tạo."
        ),
    )



# ── Simulator Config ──────────────────────────────────────────────────────────

class SimulatorConfigRequest(BaseModel):
    incident_enabled:     Optional[bool]  = None
    incident_probability: Optional[float] = None  # 0.0 – 1.0
    incident_min_step:    Optional[int]   = None
    incident_severity:    Optional[str]   = None  # Low / Medium / High
    interval_sec:         Optional[int]   = None  # Giây giữa mỗi điểm telemetry
    steps_per_leg:        Optional[int]   = None  # Số điểm nội suy mỗi chặng


@router.get("/simulator/config", summary="Lấy cấu hình simulator hiện tại")
async def get_simulator_config(
    user: CurrentUser = Depends(allow_operator),
):
    from app.shared.drone_simulator import simulator_config
    return simulator_config.to_dict()


@router.patch("/simulator/config", summary="Cập nhật cấu hình simulator")
async def update_simulator_config(
    body: SimulatorConfigRequest,
    user: CurrentUser = Depends(allow_operator),
):
    from app.shared.drone_simulator import simulator_config

    if body.incident_enabled is not None:
        simulator_config.incident_enabled = body.incident_enabled
    if body.incident_probability is not None:
        if not 0.0 <= body.incident_probability <= 1.0:
            raise HTTPException(status_code=400, detail="incident_probability phải từ 0.0 đến 1.0")
        simulator_config.incident_probability = body.incident_probability
    if body.incident_min_step is not None:
        simulator_config.incident_min_step = body.incident_min_step
    if body.incident_severity is not None:
        if body.incident_severity not in ("Low", "Medium", "High"):
            raise HTTPException(status_code=400, detail="incident_severity phải là Low / Medium / High")
        simulator_config.incident_severity = body.incident_severity
    if body.interval_sec is not None:
        if body.interval_sec < 1:
            raise HTTPException(status_code=400, detail="interval_sec phải >= 1")
        simulator_config.interval_sec = body.interval_sec
    if body.steps_per_leg is not None:
        if body.steps_per_leg < 5:
            raise HTTPException(status_code=400, detail="steps_per_leg phải >= 5")
        simulator_config.steps_per_leg = body.steps_per_leg

    return {
        "message": "Cấu hình simulator đã cập nhật.",
        "config": simulator_config.to_dict(),
    }
