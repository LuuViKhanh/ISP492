from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from datetime import timedelta, datetime, timezone
from typing import Any, Optional, List, Union
import math
import copy
import time
import asyncio

from app.core.timezone import get_vn_naive_datetime, get_vn_datetime
from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.core.config import settings
from app.modules.missions.models import Mission, MissionStatus, HandlingStatus, Incident, IncidentStatus, IncidentSeverity, TelemetryLog, MissionLeg, MissionLegStatus
from app.modules.system.models import Hub, ActivityLog
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
    AutoDispatchRequest, AutoDispatchResponse,
    RecommendResourcesRequest, RecommendResourcesResponse,
)
from app.modules.missions.auto_dispatch_service import (
    run_auto_dispatch_selection,
    recommend_resources_for_order,
    build_route_nl_explanation,
    build_route_business_rule_check,
    build_analyze_drone_explanation,
)
from app.modules.missions.compensation_service import (
    validate_and_build_route_override_entry,
    extract_mission_override_and_compensation,
)
from app.modules.missions.bundle_service import (
    get_pre_analyze_bundle_candidates,
    validate_bundle_orders,
    get_post_analyze_bundle_candidates,
    resolve_effective_origin_hub_id,
    resolve_effective_origin_from_missions,
    predict_flight_energy_cached,
    get_compatible_battery_models_map,
    resolve_compatible_models_from_map,
    get_all_hubs_cached,
)
from app.modules.fleet.models import Drone, Battery, DroneStatus, batteries_status
from app.modules.fleet.schemas import CheckAvailabilityRequest, AvailabilityResponse, AvailableBatterySchema

router = APIRouter(prefix="/operator/missions", tags=["Missions"])

allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])

# [PERF NOTE] Bộ nhớ đệm ngắn hạn 120s giữ kết quả Route Analyze để nút Assign/Create Mission kế tiếp
# tái sử dụng trực tiếp (hoặc đi Snapshot Fast-Path từ legs_detail) mà không chạy lại Dijkstra + thời tiết.
_ROUTE_ANALYSIS_CACHE: dict[tuple, tuple[float, list, list]] = {}
_ROUTE_ANALYSIS_CACHE_TTL_SEC = 120.0
_ROUTE_ANALYSIS_CACHE_MAX = 128


def _build_route_cache_key(
    order_id: str,
    drone_id: Optional[Union[str, int]],
    battery_id: Optional[str],
    payload_kg: float,
    recommend_mode: Optional[str],
    strict_battery: bool,
) -> tuple:
    return (
        str(order_id or "").strip(),
        str(drone_id).strip() if drone_id is not None else "",
        str(battery_id).strip() if battery_id else "",
        round(float(payload_kg or 0.0), 3),
        str(recommend_mode or "AUTO").strip().upper(),
        bool(strict_battery),
    )


def _invalidate_route_analysis_cache() -> None:
    _ROUTE_ANALYSIS_CACHE.clear()


from app.modules.missions.bundle_service import (
    register_route_cache_invalidator as _register_route_cache_invalidator,
    invalidate_route_analysis_cache as _invalidate_route_cache_global,
)
_register_route_cache_invalidator(_invalidate_route_analysis_cache)


def _send_mission_scheduled_email_sync(
    to_email: str,
    order_id: str,
    mission_id: str,
    frontend_url: Optional[str],
) -> None:
    """Run synchronous email HTTP call inside a background thread so API response never blocks."""
    from app.core.email import send_mission_scheduled_email

    try:
        asyncio.run(
            send_mission_scheduled_email(
                to_email=to_email,
                order_id=order_id,
                mission_id=mission_id,
                frontend_url=frontend_url,
            )
        )
    except Exception as exc:
        print(f"[EMAIL/NOTIF] Background send_mission_scheduled_email error: {exc}")


def _schedule_mission_email_background(
    to_email: str,
    order_id: str,
    mission_id: str,
    frontend_url: Optional[str],
) -> None:
    async def _runner() -> None:
        await asyncio.to_thread(
            _send_mission_scheduled_email_sync,
            to_email,
            order_id,
            mission_id,
            frontend_url,
        )

    try:
        asyncio.create_task(_runner())
    except RuntimeError:
        _send_mission_scheduled_email_sync(to_email, order_id, mission_id, frontend_url)


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
    hubs = sorted(await get_all_hubs_cached(db), key=lambda h: h.id)
    return [{"id": h.id, "code": h.code, "name": h.name, "address": h.address, "latitude": h.latitude, "longitude": h.longitude, "status": h.status} for h in hubs]


@router.get("/mini-hubs")
async def list_mini_hubs(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """Lấy danh sách các Hub (Mini-hubs và Hubs đã hợp nhất)."""
    hubs = sorted(await get_all_hubs_cached(db), key=lambda h: h.id)
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
    now_vn_naive = get_vn_naive_datetime()
    ten_mins_ago = now_vn_naive - timedelta(minutes=10)
    flying_result = await db.execute(
        select(Mission).where(
            or_(
                Mission.status == MissionStatus.FLYING,
                and_(
                    Mission.status == MissionStatus.INCIDENT_RETURN,
                    Mission.arrived_at.is_(None),
                    Mission.end_time.is_(None),
                ),
                # Giữ hiển thị các chuyến bay vừa rớt / mất kết nối trong 10 phút gần nhất khi chưa thu hồi xong
                and_(
                    Mission.status == MissionStatus.FAILED,
                    Mission.arrival_confirmed_at.is_(None),
                    or_(
                        Mission.end_time >= ten_mins_ago,
                        Mission.departed_at >= ten_mins_ago,
                        Mission.scheduled_time >= ten_mins_ago,
                    ),
                ),
            )
        )
    )
    flying_missions = flying_result.scalars().all()

    if not flying_missions:
        return LiveTrackingResponse(active_count=0, drones=[])

    mission_ids = [m.id for m in flying_missions]
    cp_counts = {
        m.id: sum(
            1 for cp in (m.checkpoints_json or [])
            if isinstance(cp, dict) and cp.get("drone_latitude") is not None and cp.get("passed_at") is not None
        )
        for m in flying_missions
    }

    # Lấy thông tin Legs cho các mission đang bay
    legs_result = await db.execute(
        select(MissionLeg)
        .where(MissionLeg.mission_id.in_(mission_ids))
        .order_by(MissionLeg.sequence_no.asc())
    )
    all_legs = legs_result.scalars().all()
    legs_by_mid = {}
    for l in all_legs:
        legs_by_mid.setdefault(l.mission_id, []).append(l)

    hubs_map = {h.id: h for h in await get_all_hubs_cached(db)}

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

    # [PERF NOTE] Gộp latest_logs và trail (50 điểm gần nhất) thành 1 window query row_number() duy nhất.
    latest_logs: dict[str, Any] = {}
    trails_by_mid: dict[str, list[dict]] = {mid: [] for mid in mission_ids}
    trail_ranked_subq = (
        select(
            TelemetryLog.mission_id.label("mission_id"),
            TelemetryLog.latitude.label("latitude"),
            TelemetryLog.longitude.label("longitude"),
            TelemetryLog.altitude.label("altitude"),
            TelemetryLog.speed.label("speed"),
            TelemetryLog.battery_voltage.label("battery_voltage"),
            TelemetryLog.energy_consumed_wh.label("energy_consumed_wh"),
            TelemetryLog.wind_speed.label("wind_speed"),
            TelemetryLog.weather_temperature.label("weather_temperature"),
            TelemetryLog.weather_apparent_temp.label("weather_apparent_temp"),
            TelemetryLog.weather_dew_point.label("weather_dew_point"),
            TelemetryLog.weather_humidity.label("weather_humidity"),
            TelemetryLog.weather_wind_speed.label("weather_wind_speed"),
            TelemetryLog.weather_wind_gust.label("weather_wind_gust"),
            TelemetryLog.weather_wind_direction.label("weather_wind_direction"),
            TelemetryLog.weather_precipitation.label("weather_precipitation"),
            TelemetryLog.weather_pressure.label("weather_pressure"),
            TelemetryLog.weather_cloud_cover.label("weather_cloud_cover"),
            TelemetryLog.timestamp.label("timestamp"),
            func.row_number()
            .over(
                partition_by=TelemetryLog.mission_id,
                order_by=(TelemetryLog.timestamp.desc(), TelemetryLog.id.desc()),
            )
            .label("rn"),
        )
        .where(TelemetryLog.mission_id.in_(mission_ids))
        .subquery()
    )
    trail_rows = await db.execute(
        select(trail_ranked_subq)
        .where(trail_ranked_subq.c.rn <= 50)
        .order_by(trail_ranked_subq.c.mission_id, trail_ranked_subq.c.timestamp.asc(), trail_ranked_subq.c.rn.desc())
    )
    for r in trail_rows.all():
        if r.rn == 1:
            latest_logs[r.mission_id] = r
        trails_by_mid.setdefault(r.mission_id, []).append(
            {
                "latitude": r.latitude,
                "longitude": r.longitude,
                "altitude": r.altitude,
                "speed": r.speed,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
        )

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

    # Lấy MaintenanceAlert mất tín hiệu đang mở của các mission
    sig_alerts_res = await db.execute(
        select(MaintenanceAlert.mission_id).where(
            MaintenanceAlert.mission_id.in_(mission_ids),
            MaintenanceAlert.source == "Incident",
            MaintenanceAlert.status.in_(["Pending", "PENDING"]),
            MaintenanceAlert.title.in_([
                "Telemetry signal lost (>10s)",
                "Signal lost for over 3s",
                "Mất tín hiệu quá 3s",
            ]),
        )
    )
    signal_loss_alert_mids = {mid for mid in sig_alerts_res.scalars().all() if mid}

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
            if str(leg_stat).upper() in ("IN_PROGRESS", "IN PROGRESS", "IN-PROGRESS"):
                leg_stat = "FLYING"
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
            if leg.status == MissionLegStatus.IN_PROGRESS or str(leg.status).upper() in ("IN_PROGRESS", "FLYING"):
                curr_leg = {
                    "id": item["id"],
                    "sequence_no": item["sequence_no"],
                    "status": "FLYING",
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

        inc_desc = (inc.description or "") if inc else ""
        inc_desc_upper = inc_desc.upper()
        is_airborne_stale = bool(
            mission.status == MissionStatus.FLYING
            and mission.handling_status in (None, HandlingStatus.INCOMING)
            and log is not None
            and log.timestamp is not None
            and (now_vn_naive - log.timestamp).total_seconds() > 10.0
        )
        is_disconnected = bool(
            mission.status == MissionStatus.FAILED
            or "[DISCONNECT" in inc_desc_upper
            or "[SIGNAL LOSS]" in inc_desc_upper
            or mission.id in signal_loss_alert_mids
            or is_airborne_stale
        )

        if is_disconnected:
            failsafe_status = "DISCONNECTED"
            failsafe_alert = (
                inc_desc
                if inc_desc and ("[DISCONNECT" in inc_desc_upper or "[SIGNAL LOSS]" in inc_desc_upper)
                else "CONNECTION LOST: Complete telemetry signal loss / potential drone crash!"
            )
            divert_hub_id = mission.destination_hub_id
            if divert_hub_id and divert_hub_id in hubs_map:
                divert_hub_name = hubs_map[divert_hub_id].name
        elif inc and "[FAILSAFE DIVERT]" in inc_desc:
            failsafe_status = "DIVERTED"
            failsafe_alert = inc.description
            is_diverted = True
            divert_hub_id = mission.destination_hub_id
            if divert_hub_id and divert_hub_id in hubs_map:
                divert_hub_name = hubs_map[divert_hub_id].name
        elif inc and "[FAILSAFE RTH]" in inc_desc:
            failsafe_status = "RTH"
            failsafe_alert = inc.description
            is_diverted = True
            divert_hub_id = mission.destination_hub_id or mission.origin_hub_id
            if divert_hub_id and divert_hub_id in hubs_map:
                divert_hub_name = hubs_map[divert_hub_id].name
        elif mission.status == MissionStatus.INCIDENT_RETURN:
            failsafe_status = "RTH"
            failsafe_alert = inc.description if inc else "In-flight incident — Drone is returning safely to Hub."
            is_diverted = True
            divert_hub_id = mission.destination_hub_id or mission.origin_hub_id
            if divert_hub_id and divert_hub_id in hubs_map:
                divert_hub_name = hubs_map[divert_hub_id].name
        elif bat_pct is not None and bat_pct < 20:
            failsafe_status = "WARNING"
            failsafe_alert = f"Low battery warning: {bat_pct}% (approaching 20% safety threshold)"

        drones.append(
            LiveTrackingDrone(
                mission_id=mission.id,
                drone_id=mission.drone_id,
                status=mission.status,
                handling_status=mission.handling_status.value if mission.handling_status and hasattr(mission.handling_status, "value") else (str(mission.handling_status) if mission.handling_status else None),
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
            mission_id=str(body.mission_id) if body.mission_id else None,
            source="INCIDENT",
            incident_id=incident.id,
            title=f"Incident #{incident.id}: {body.description[:50]}",
            description=(
                f"INCIDENT REPORT #{incident.id}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Drone: {body.drone_id}\n"
                f"Mission: {body.mission_id}\n"
                f"Severity: {body.severity}\n"
                f"Reported at: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Reporter: {user.email if 'user' in locals() else 'System'}\n"
                f"\nINCIDENT DETAILS:\n"
                f"{body.description}\n"
                f"\nACTION REQUIRED:\n"
                f"- Inspect drone #{body.drone_id} for damage\n"
                f"- Review flight logs and sensor data\n"
                f"- Document findings and repair if needed\n"
                f"- Test drone before next flight"
            ),
            status="PENDING",
            created_at=now.replace(tzinfo=None),
        )
        db.add(alert)

    # Gửi thông báo sự cố tới toàn bộ Operators & Admins
    try:
        from app.modules.system.models import Notification, NotificationType
        from app.shared.workers import get_operator_and_admin_ids

        sev_label = body.severity.value if hasattr(body.severity, "value") else str(body.severity or "Medium")
        for uid in await get_operator_and_admin_ids(db):
            db.add(Notification(
                user_id=uid,
                title=f"Incident #{incident.id} ({sev_label}) — Mission #{body.mission_id or 'N/A'}",
                message=(
                    f"Incident reported for Drone #{body.drone_id or 'N/A'}"
                    f"{f' on Mission #{body.mission_id}' if body.mission_id else ''}: {body.description}"
                ),
                notifications_type=NotificationType.SYSTEM_ALERT,
                mission_id=str(body.mission_id) if body.mission_id else None,
                drone_id=str(body.drone_id) if body.drone_id else None,
                reference_id=str(incident.id),
                created_at=now,
            ))

        # Notify Technicians at the drone's current hub
        if body.drone_id:
            from app.modules.fleet.models import Drone as _Drone
            from app.modules.auth.models import User as _IUser, ROLE_NAME_TO_ID as _IROLE_MAP
            from app.shared.roles import UserRole as _IRole
            _inc_drone = await db.get(_Drone, body.drone_id)
            _inc_hub_id = _inc_drone.current_hub_id if _inc_drone else None
            _itech_role_id = _IROLE_MAP.get(_IRole.TECHNICIAN)
            if _inc_hub_id and _itech_role_id:
                _itech_res = await db.execute(
                    select(_IUser.id).where(
                        _IUser.role_id == _itech_role_id,
                        _IUser.hub_id == str(_inc_hub_id),
                    )
                )
                for _itid in _itech_res.scalars().all():
                    db.add(Notification(
                        user_id=_itid,
                        title=f"Incident — Drone #{body.drone_id} Requires Inspection",
                        message=(
                            f"Incident #{incident.id} ({sev_label}) reported for Drone #{body.drone_id}"
                            f"{f' on Mission #{body.mission_id}' if body.mission_id else ''}: {body.description}"
                        ),
                        notifications_type=NotificationType.MAINTENANCE,
                        mission_id=str(body.mission_id) if body.mission_id else None,
                        drone_id=str(body.drone_id) if body.drone_id else None,
                        reference_id=str(incident.id),
                        created_at=now,
                    ))
    except Exception as e:
        print(f"[INCIDENT] Lỗi gửi notification khi tạo incident: {e}")

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

    if mission.status not in (MissionStatus.SCHEDULED, MissionStatus.FLYING):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot ingest telemetry for mission in '{mission.status.value}' status. "
                   f"Only SCHEDULED or FLYING missions are accepted."
        )

    # ── 1. Xử lý chuyển trạng thái SCHEDULED → FLYING ───────────────────────────
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if mission.status == MissionStatus.SCHEDULED:
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

    # ── 2. Fetch weather song song (dedup theo tọa độ) + Lưu telemetry logs ───
    from app.shared.weather import fetch_weather

    unique_coords = {
        (round(float(d.latitude), 2), round(float(d.longitude), 2))
        for d in telemetry_data
    }
    weather_results = await asyncio.gather(
        *(fetch_weather(lat, lng) for lat, lng in unique_coords),
        return_exceptions=True,
    )
    weather_by_coord = {
        coord: (None if isinstance(res, Exception) else res)
        for coord, res in zip(unique_coords, weather_results)
    }

    logs_to_insert: list[TelemetryLog] = []
    for data in telemetry_data:
        naive_timestamp = data.timestamp.replace(tzinfo=None) if data.timestamp.tzinfo else data.timestamp
        weather = weather_by_coord.get((round(float(data.latitude), 2), round(float(data.longitude), 2)))

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

    # ── 3. Tải hubs từ cache để detect proximity ─────────────────────────────
    all_hubs = [
        h for h in await get_all_hubs_cached(db)
        if h.latitude is not None and h.longitude is not None
    ]

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
                if isinstance(cp, dict) and cp.get("drone_latitude") is not None and cp.get("hub_id") == h.id:
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

    # ── 6. Flush lấy ID batch rồi Commit tất cả (bỏ N+1 db.refresh) ────────────
    await db.flush()
    mission_status_val = mission.status
    await db.commit()

    return TelemetryIngestResponse(
        saved_count=len(logs_to_insert),
        logs=logs_to_insert,
        new_checkpoints=new_checkpoints,
        mission_status=mission_status_val,
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

    return [
        HubCheckpointResponse(**cp)
        for cp in (mission.checkpoints_json or [])
        if isinstance(cp, dict) and cp.get("drone_latitude") is not None and cp.get("passed_at") is not None
    ]


# ── Hubs & Locations ──────────────────────────────────────────────────────────────


from app.modules.missions.schemas import (
    MissionPlanningAnalyzeRequest, MissionPlanningAnalyzeResponse,
    RouteOptionSchema, RouteLegSchema, MissionCreateRequest, HubBatteryStatus,
    RelayHubBatteryOptionSchema, RouteWeatherSchema,
)
from app.modules.missions.models import Order, OrderStatus, PaymentStatus, MissionLeg, MissionLegStatus, DeliveryMode
from app.modules.ai_predictions.models import AIPrediction
from app.modules.ai_predictions.service import predict_flight_energy, haversine_distance, calculate_bearing_deg
from app.modules.missions.technician.router import DRONE_BATTERY_COMPATIBILITY, get_compatible_battery_models
from app.shared.weather import fetch_weather, weather_to_override_dict, evaluate_weather_safety
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


def _apply_leg_battery_step(
    leg_energy_wh: float,
    bat_obj: Optional[Battery],
    fallback_cap_wh: float,
    running_bat_state: dict[str, dict[str, Any]],
    override_initial_energy_wh: Optional[float] = None,
) -> tuple[float, Optional[int], float, int, int, float]:
    """
    Tính thông số Pin nối tiếp cho từng chặng (Leg) trong cùng 1 Mission/Route:
    - Nếu dùng viên pin mới (chưa xuất hiện ở chặng trước): khởi tạo từ trạng thái DB thực tế.
    - Nếu tiếp tục dùng CÙNG `battery_id` với chặng trước (bay nối tiếp không đổi pin):
      `start_charge_pct` và `start_energy_wh` phải lấy phần CÒN LẠI sau chặng trước thay vì giữ nguyên như Leg 1.
    Returns:
        (cap_wh, start_charge_pct, start_energy_wh, cons_pct, rem_pct, rem_energy_wh)
    """
    if not bat_obj:
        cap = float(fallback_cap_wh or 130.0)
        cons_pct = int(round((leg_energy_wh / cap) * 100)) if cap > 0 else 100
        rem_pct = max(0, 100 - cons_pct)
        return cap, None, 0.0, cons_pct, rem_pct, 0.0

    bat_key = str(bat_obj.id)
    if bat_key in running_bat_state:
        st = running_bat_state[bat_key]
        cap = float(st["cap_wh"])
        start_energy_wh = max(0.0, float(st["energy_wh"]))
        start_charge_pct = max(0, min(100, int(st["charge_pct"])))
        initial_energy_wh = float(st["initial_energy_wh"])
    else:
        cap, start_charge_pct, start_energy_wh = _compute_battery_energy_wh(bat_obj, fallback_cap_wh)
        if override_initial_energy_wh is not None and override_initial_energy_wh > 0:
            start_energy_wh = float(override_initial_energy_wh)
            if cap > 0:
                start_charge_pct = max(0, min(100, int(round((start_energy_wh / cap) * 100))))
        initial_energy_wh = start_energy_wh

    cons_pct = int(round((leg_energy_wh / cap) * 100)) if cap > 0 else 100
    rem_energy_wh = max(0.0, round(start_energy_wh - leg_energy_wh, 2))
    rem_pct = max(0, start_charge_pct - cons_pct)
    running_bat_state[bat_key] = {
        "cap_wh": cap,
        "energy_wh": rem_energy_wh,
        "charge_pct": rem_pct,
        "initial_energy_wh": initial_energy_wh,
    }
    return cap, start_charge_pct, round(start_energy_wh, 2), cons_pct, rem_pct, rem_energy_wh


def _summarize_mission_battery_pcts(
    legs_detail: list[RouteLegSchema],
    origin_charge_pct: Optional[int],
    pred_energy_wh: float,
    origin_energy_wh: float,
) -> tuple[int, int]:
    """
    Tổng hợp `batteryConsumptionPct` và `remainingBatteryPct` toàn mission:
    - Nếu bay 1 chặng hoặc nối tiếp không đổi pin (tất cả leg dùng chung 1 `battery_id`):
      tính tổng `%` tiêu hao trên viên pin đó và phần `%` còn lại sau chặng cuối.
    - Nếu có đổi pin ở Relay Hub (nhiều `battery_id` khác nhau):
      `batteryConsumptionPct` là mức tiêu hao lớn nhất trong các chặng (không cộng dồn nhiều pin khác nhau),
      và `remainingBatteryPct` là phần `%` còn lại của chặng cuối khi về đích.
    """
    leg_pcts = [
        int(l.battery_consumption_pct)
        for l in legs_detail
        if l.battery_consumption_pct is not None
    ]
    unique_bat_ids = {str(l.battery_id) for l in legs_detail if l.battery_id}
    if leg_pcts:
        if len(legs_detail) > 1 and len(unique_bat_ids) <= 1:
            batt_pct = sum(leg_pcts)
        else:
            batt_pct = max(leg_pcts)
    else:
        batt_pct = int(round((pred_energy_wh / origin_energy_wh) * 100)) if origin_energy_wh else 50

    last_rem = legs_detail[-1].remaining_battery_pct if legs_detail else None
    if last_rem is not None:
        rem_batt_pct = max(0, int(last_rem))
    else:
        base_pct = int(origin_charge_pct) if origin_charge_pct is not None else 100
        rem_batt_pct = max(0, base_pct - batt_pct)
    return batt_pct, rem_batt_pct


def _build_relay_summaries_from_legs(
    legs_detail: list[RouteLegSchema],
    hub_by_id: dict[int, Any],
    battery_by_id: dict[str, Battery],
    fallback_cap_wh: float,
) -> tuple[list[str], list[HubBatteryStatus]]:
    """Build `relayHubs` descriptions and `relay_battery_status` consistently from sequenced `legs_detail`."""
    relay_hubs: list[str] = []
    relay_battery_status_list: list[HubBatteryStatus] = []
    for idx in range(len(legs_detail) - 1):
        cur_leg = legs_detail[idx]
        next_leg = legs_detail[idx + 1]
        to_hub_id = cur_leg.to_hub_id
        h2 = hub_by_id.get(to_hub_id) if to_hub_id is not None else None
        to_code = (h2.code or h2.name) if h2 else (cur_leg.to_hub_code or str(to_hub_id or "?"))
        hub_label = h2.name if h2 else (cur_leg.to_hub_name or to_code)
        e_cur = float(cur_leg.energy_wh or 0.0)
        swap_bat_id = next_leg.battery_id
        swap_bat_obj = battery_by_id.get(swap_bat_id) if swap_bat_id else None
        if swap_bat_obj:
            s_cap = float(next_leg.battery_capacity_wh or fallback_cap_wh)
            s_charge = int(next_leg.battery_charge_pct or 0)
            s_energy = float(next_leg.battery_energy_wh or 0.0)
            is_same_bat = (swap_bat_id == cur_leg.battery_id)
            action_word = "Continue with battery" if is_same_bat else "Swap battery"
            relay_hubs.append(
                f"{hub_label} (Uses {e_cur:.1f}Wh) - {action_word} {swap_bat_obj.id} "
                f"({swap_bat_obj.serial_number or swap_bat_obj.battery_model or 'Ready'}, "
                f"{s_energy:.0f}Wh, {s_charge}%)"
            )
            if to_hub_id is not None:
                relay_battery_status_list.append(
                    HubBatteryStatus(
                        hub_id=to_hub_id,
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
                        swap_battery_id=None if is_same_bat else swap_bat_obj.id,
                        swap_battery_serial=None if is_same_bat else swap_bat_obj.serial_number,
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
            relay_hubs.append(f"{hub_label} (Uses {e_cur:.1f}Wh) - Quick battery swap (0m)")
    return relay_hubs, relay_battery_status_list



async def _get_reserved_battery_info_map(
    db: AsyncSession,
    exclude_order_id: Optional[str] = None,
    exclude_mission_id: Optional[str] = None,
) -> dict[str, dict]:
    """
    Trả về mapping thông tin chi tiết nhiệm vụ giữ chỗ cho từng viên pin (1 query duy nhất).
    """
    active_statuses = [
        MissionStatus.SCHEDULED,
        MissionStatus.FLYING,
    ]
    info_map: dict[str, dict] = {}

    stmt = (
        select(
            Mission.id,
            Mission.status,
            Mission.drone_id,
            Mission.order_id,
            Mission.battery_id,
            MissionLeg.battery_id,
            MissionLeg.sequence_no,
        )
        .outerjoin(
            MissionLeg,
            and_(
                MissionLeg.mission_id == Mission.id,
                MissionLeg.status.in_([MissionLegStatus.PENDING, MissionLegStatus.IN_PROGRESS]),
                MissionLeg.battery_id.is_not(None),
            ),
        )
        .where(Mission.status.in_(active_statuses))
    )
    if exclude_order_id:
        stmt = stmt.where(Mission.order_id != exclude_order_id)
    if exclude_mission_id:
        stmt = stmt.where(Mission.id != exclude_mission_id)

    res = await db.execute(stmt)
    for m_id, m_stat, m_drone_id, m_order_id, m_bat_id, leg_bat_id, leg_seq in res.all():
        m_status = m_stat.value if hasattr(m_stat, "value") else str(m_stat)
        if m_bat_id and m_bat_id not in info_map:
            info_map[m_bat_id] = {
                "mission_id": m_id,
                "mission_code": m_id,
                "mission_status": m_status,
                "drone_id": m_drone_id,
                "order_id": m_order_id,
                "reason": f"In use by Mission #{m_id} ({m_status})",
            }
        if leg_bat_id:
            seq_info = f"Leg {leg_seq}" if leg_seq else "Intermediate Leg"
            info_map[leg_bat_id] = {
                "mission_id": m_id,
                "mission_code": m_id,
                "mission_status": m_status,
                "drone_id": m_drone_id,
                "order_id": m_order_id,
                "reason": f"In use by Mission #{m_id} ({seq_info} - {m_status})",
            }

    return info_map


_ACTIVE_HOLD_MISSION_STATUSES = [
    MissionStatus.SCHEDULED,
    MissionStatus.FLYING,
]


def _parse_reserved_and_holds_rows(
    rows,
    relay_hub_set: set[int],
    compatible_models: Optional[list[str]] = None,
    busy_drone_ids_out: Optional[set[str]] = None,
) -> tuple[set[str], dict[int, int]]:
    """Extract reserved battery IDs, relay virtual-hold counts, and optional busy drone IDs from Mission/MissionLeg rows."""
    reserved_battery_ids: set[str] = set()
    held_count_by_hub: dict[int, int] = {}
    seen_hold_leg_ids: set[int] = set()

    for _, m_drone_id, m_bat_id, leg_id, leg_bat_id, leg_seq, leg_stat, from_hub_id, other_drone_model in rows:
        if busy_drone_ids_out is not None and m_drone_id is not None:
            busy_drone_ids_out.add(m_drone_id)
        if m_bat_id:
            reserved_battery_ids.add(m_bat_id)
        if leg_id is None:
            continue
        if leg_bat_id and leg_stat in (MissionLegStatus.PENDING, MissionLegStatus.IN_PROGRESS):
            reserved_battery_ids.add(leg_bat_id)
        elif (
            relay_hub_set
            and leg_id not in seen_hold_leg_ids
            and (leg_seq or 0) > 1
            and leg_stat == MissionLegStatus.PENDING
            and leg_bat_id is None
            and from_hub_id in relay_hub_set
        ):
            seen_hold_leg_ids.add(leg_id)
            other_compat = DRONE_BATTERY_COMPATIBILITY.get(
                other_drone_model or "",
                [other_drone_model] if other_drone_model else [],
            )
            if not compatible_models or not other_compat or bool(set(compatible_models) & set(other_compat)):
                held_count_by_hub[from_hub_id] = held_count_by_hub.get(from_hub_id, 0) + 1

    return reserved_battery_ids, held_count_by_hub


async def _get_reserved_batteries_and_virtual_holds(
    db: AsyncSession,
    relay_hub_ids: Optional[list[int]] = None,
    compatible_models: Optional[list[str]] = None,
    exclude_order_id: Optional[str] = None,
    exclude_mission_id: Optional[str] = None,
    busy_drone_ids_out: Optional[set[str]] = None,
) -> tuple[set[str], dict[int, int]]:
    """
    [PERF NOTE] Trả về reserved_battery_ids và held_count_by_hub (kèm busy_drone_ids_out nếu cần)
    trong 1 query SQL duy nhất; không tách thành nhiều query Mission/MissionLeg riêng lẻ.
    """
    relay_hub_set = set(relay_hub_ids) if relay_hub_ids else set()

    stmt = (
        select(
            Mission.id,
            Mission.drone_id,
            Mission.battery_id,
            MissionLeg.id,
            MissionLeg.battery_id,
            MissionLeg.sequence_no,
            MissionLeg.status,
            MissionLeg.from_hub_id,
            Drone.model,
        )
        .outerjoin(MissionLeg, MissionLeg.mission_id == Mission.id)
        .outerjoin(Drone, Mission.drone_id == Drone.id)
        .where(Mission.status.in_(_ACTIVE_HOLD_MISSION_STATUSES))
    )
    if exclude_order_id:
        stmt = stmt.where(Mission.order_id != exclude_order_id)
    if exclude_mission_id:
        stmt = stmt.where(Mission.id != exclude_mission_id)

    res = await db.execute(stmt)
    return _parse_reserved_and_holds_rows(
        rows=res.all(),
        relay_hub_set=relay_hub_set,
        compatible_models=compatible_models,
        busy_drone_ids_out=busy_drone_ids_out,
    )


async def _resolve_drone_battery_capacity_wh(
    db: AsyncSession,
    drone_obj: Optional[Drone],
    all_active_batteries: list[Battery],
    compat_models_map: Optional[dict[str, list[str]]] = None,
) -> tuple[list[str], float]:
    """
    Xác định danh sách model pin tương thích và dung lượng chuẩn 1 viên pin thực tế (Wh) của Drone.
    Tuyệt đối KHÔNG lấy nhầm dung lượng pin của dòng Drone khác tại cùng Hub.
    """
    compatible_models: list[str] = []
    if drone_obj and drone_obj.model:
        if compat_models_map is not None:
            compatible_models = resolve_compatible_models_from_map(compat_models_map, drone_obj.model)
        else:
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
    compat_models_map: Optional[dict[str, list[str]]] = None,
) -> tuple[list[str], Optional[Battery], float, int, float, float]:
    """
    Xác định viên pin được chọn/assign cho Drone tại Hub xuất phát và năng lượng thực tế (Wh) của viên pin đó.
    Returns:
        (compatible_models, assigned_battery, bat_capacity_wh, bat_charge_pct, bat_energy_wh, model_default_capacity_wh)
    """
    compatible_models, model_default_cap_wh = await _resolve_drone_battery_capacity_wh(
        db, drone_obj, all_active_batteries, compat_models_map=compat_models_map
    )

    clean_req_bat_id = (requested_battery_id or "").strip() or None
    if clean_req_bat_id:
        bat = next((b for b in all_active_batteries if b.id == clean_req_bat_id), None)
        if not bat:
            bat = await db.get(Battery, clean_req_bat_id)
        if not bat:
            raise HTTPException(
                status_code=404,
                detail=f"Battery '{clean_req_bat_id}' not found in the system."
            )
        if bat.status != batteries_status.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail=f"Battery {bat.id} is not in Active status (Status: {bat.status})."
            )
        if bat.drone_id is not None and (not drone_obj or bat.drone_id != drone_obj.id):
            raise HTTPException(
                status_code=400,
                detail=f"Battery {bat.id} is currently installed on Drone #{bat.drone_id} and cannot be assigned to Drone #{drone_obj.id if drone_obj else '?'}."
            )
        if bat.current_hub_id != origin_hub_id and (not drone_obj or bat.drone_id != drone_obj.id):
            raise HTTPException(
                status_code=400,
                detail=f"Battery {bat.id} is currently at Hub #{bat.current_hub_id}, not at origin Hub #{origin_hub_id}."
            )
        if compatible_models and bat.battery_model and bat.battery_model not in compatible_models:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Battery {bat.id} (Model: {bat.battery_model}) is incompatible with "
                    f"Drone {drone_obj.name if drone_obj else ''} ({drone_obj.model if drone_obj else ''}). "
                    f"Required battery models: {', '.join(compatible_models)}."
                )
            )
        if bat.id in reserved_battery_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Battery {bat.id} is currently reserved for another active mission."
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
            detail=f"No compatible battery{model_label} available at origin Hub #{origin_hub_id} to assign to Drone #{drone_obj.id if drone_obj else '?'}."
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
    running_bat_state: dict[str, dict[str, Any]] = {}
    leg0_energy = 0.0

    if legs_detail:
        leg0 = legs_detail[0]
        leg0_energy = float(leg0.get("energy_wh") or 0.0)
        if leg0_energy <= 0 and leg0.get("distance_km") is not None:
            leg0_energy = float(
                predict_flight_energy_cached(float(leg0["distance_km"]), float(leg0.get("payload_kg") or 0.0))["energy_wh"]
            )

        if assigned_battery is not None:
            l0_cap, l0_charge, l0_wh, leg0_cons_pct, l0_rem_pct, _ = _apply_leg_battery_step(
                leg0_energy,
                assigned_battery,
                drone_capacity_wh,
                running_bat_state,
                override_initial_energy_wh=assigned_battery_energy_wh,
            )
            leg0["battery_id"] = assigned_battery.id
            leg0["battery_serial"] = assigned_battery.serial_number
            leg0["battery_model"] = assigned_battery.battery_model
            leg0["battery_capacity_wh"] = round(l0_cap, 2)
            leg0["battery_charge_pct"] = l0_charge
            leg0["battery_energy_wh"] = l0_wh
            leg0["battery_consumption_pct"] = leg0_cons_pct
            leg0["remaining_battery_pct"] = l0_rem_pct

            if leg0_energy > max_safe_origin_wh:
                infeasible_reasons.append(
                    f"Leg 1 ({leg0.get('from_hub_code', '?')} -> {leg0.get('to_hub_code', '?')}) "
                    f"consumes {leg0_energy:.1f} Wh (~{leg0_cons_pct}% of battery {assigned_battery.id}), "
                    f"exceeding the 80% safe threshold ({max_safe_origin_wh:.1f} Wh / {origin_energy_wh:.1f} Wh, "
                    f"charge {origin_charge_pct}%, minimum 20% reserve required)"
                )
        else:
            _, _, _, leg0_cons_pct, l0_rem_pct, _ = _apply_leg_battery_step(
                leg0_energy, None, origin_cap_wh, running_bat_state
            )
            leg0["battery_consumption_pct"] = leg0_cons_pct
            leg0["remaining_battery_pct"] = l0_rem_pct
            infeasible_reasons.append(
                f"Origin hub has no compatible battery{model_str} available to assign to Drone"
            )
            if origin_energy_wh > 0 and leg0_energy > max_safe_origin_wh:
                infeasible_reasons.append(
                    f"Leg 1 ({leg0.get('from_hub_code', '?')} -> {leg0.get('to_hub_code', '?')}) "
                    f"consumes {leg0_energy:.1f} Wh (~{leg0_cons_pct}% battery), exceeding the 80% safe threshold "
                    f"({max_safe_origin_wh:.1f} Wh / {origin_energy_wh:.1f} Wh{model_str}, minimum 20% reserve required)"
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
                    predict_flight_energy_cached(float(next_leg["distance_km"]), float(next_leg.get("payload_kg") or 0.0))["energy_wh"]
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
            sel_cap_wh, sel_charge_pct, sel_energy_wh, leg_cons_pct, leg_rem_pct, _ = _apply_leg_battery_step(
                next_leg_energy, selected_bat, drone_capacity_wh, running_bat_state
            )
            sel_max_safe_wh = round(sel_energy_wh * 0.8, 2)

            if next_leg is not None:
                next_leg["battery_id"] = selected_bat.id
                next_leg["battery_serial"] = selected_bat.serial_number
                next_leg["battery_model"] = selected_bat.battery_model
                next_leg["battery_capacity_wh"] = round(sel_cap_wh, 2)
                next_leg["battery_charge_pct"] = sel_charge_pct
                next_leg["battery_energy_wh"] = sel_energy_wh
                next_leg["battery_consumption_pct"] = leg_cons_pct
                next_leg["remaining_battery_pct"] = leg_rem_pct

            if next_leg is not None and next_leg_energy > sel_max_safe_wh:
                leg_pct = int(round((next_leg_energy / sel_energy_wh) * 100)) if sel_energy_wh > 0 else 100
                infeasible_reasons.append(
                    f"Leg {relay_idx + 2} ({next_leg.get('from_hub_code', '?')} -> {next_leg.get('to_hub_code', '?')}) "
                    f"consumes {next_leg_energy:.1f} Wh (~{leg_pct}% of swap battery {selected_bat.id}), "
                    f"exceeding the 80% safe threshold ({sel_max_safe_wh:.1f} Wh / {sel_energy_wh:.1f} Wh{model_str}, "
                    f"minimum 20% reserve required)"
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
                selected_battery_energy_wh=sel_energy_wh,
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
            prev_st = running_bat_state.get(str(assigned_battery.id)) if assigned_battery else None
            cur_rem_wh = float(prev_st["energy_wh"]) if prev_st else origin_energy_wh
            init_bat_wh = float(prev_st["initial_energy_wh"]) if prev_st else origin_energy_wh
            can_continue_same_bat = (
                is_cross_hub
                and hub_id == origin_hub_id
                and assigned_battery is not None
                and init_bat_wh > 0
                and (cur_rem_wh - next_leg_energy) >= (init_bat_wh * 0.2)
            )
            if can_continue_same_bat:
                hub_feasible = True
                # Tiếp tục dùng chung pin từ chặng Dispatch trước -> % và Wh đầu chặng phải giảm đi đúng phần đã tiêu hao ở Leg trước
                cont_cap_wh, cont_charge_pct, cont_energy_wh, leg_cons_pct, leg_rem_pct, _ = _apply_leg_battery_step(
                    next_leg_energy, assigned_battery, drone_capacity_wh, running_bat_state
                )
                if next_leg is not None:
                    next_leg["battery_id"] = assigned_battery.id
                    next_leg["battery_serial"] = assigned_battery.serial_number
                    next_leg["battery_model"] = assigned_battery.battery_model
                    next_leg["battery_capacity_wh"] = round(cont_cap_wh, 2)
                    next_leg["battery_charge_pct"] = cont_charge_pct
                    next_leg["battery_energy_wh"] = cont_energy_wh
                    next_leg["battery_consumption_pct"] = leg_cons_pct
                    next_leg["remaining_battery_pct"] = leg_rem_pct

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
                    selected_battery_capacity_wh=round(cont_cap_wh, 2),
                    selected_battery_charge_pct=cont_charge_pct,
                    selected_battery_energy_wh=cont_energy_wh,
                    swap_battery_id=None,
                    swap_battery_serial=None,
                    available_batteries=[],
                ))
            else:
                hub_feasible = False
                fallback_max_safe_wh = round(drone_capacity_wh * 0.8, 2)
                if next_leg is not None:
                    _, _, _, leg_cons_pct, _, _ = _apply_leg_battery_step(
                        next_leg_energy, None, drone_capacity_wh, running_bat_state
                    )
                    prev_leg = legs_detail[relay_idx] if relay_idx < len(legs_detail) else None
                    prev_rem_val = prev_leg.get("remaining_battery_pct") if prev_leg else None
                    base_start_pct = int(prev_rem_val) if prev_rem_val is not None else 100
                    next_leg["battery_consumption_pct"] = leg_cons_pct
                    next_leg["remaining_battery_pct"] = max(0, base_start_pct - leg_cons_pct)
                    if drone_capacity_wh > 0 and next_leg_energy > fallback_max_safe_wh:
                        infeasible_reasons.append(
                            f"Leg {relay_idx + 2} ({next_leg.get('from_hub_code', '?')} -> {next_leg.get('to_hub_code', '?')}) "
                            f"consumes {next_leg_energy:.1f} Wh (~{leg_cons_pct}% battery), exceeding the 80% safe threshold "
                            f"({fallback_max_safe_wh:.1f} Wh / {drone_capacity_wh:.1f} Wh{model_str}, minimum 20% reserve required)"
                        )

                if raw_ready_count > 0 and held_by_others > 0:
                    infeasible_reasons.append(
                        f"Hub {hub_name} has {raw_ready_count} compatible batteries{model_str}, but all are reserved ({held_by_others} batteries) for other missions"
                    )
                else:
                    infeasible_reasons.append(
                        f"Hub {hub_name} has no compatible battery{model_str} available in stock (sufficient energy, uninstalled, and unreserved)"
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


def _aggregate_route_weather(legs_raw: list[dict], altitude_m: float = 45.0) -> dict:
    """
    Tổng hợp dữ liệu thời tiết toàn tuyến từ từng chặng bay.
    Ưu tiên giữ nguyên cảnh báo nếu bất kỳ chặng nào gặp gió mạnh (>= 8 m/s) hoặc mưa lớn/giông.
    """
    leg_weathers = [leg.get("weather") for leg in legs_raw if isinstance(leg.get("weather"), dict)]
    if not leg_weathers:
        return evaluate_weather_safety(None, altitude_m=altitude_m)

    bad_legs = [w for w in leg_weathers if w.get("is_bad_weather")]
    rep = (
        max(bad_legs, key=lambda w: float(w.get("wind_speed_ms") or 0.0))
        if bad_legs
        else max(leg_weathers, key=lambda w: float(w.get("wind_speed_ms") or 0.0))
    )

    warnings = []
    for idx, leg in enumerate(legs_raw):
        w = leg.get("weather")
        if isinstance(w, dict) and w.get("warning_message"):
            msg = f"Leg {idx + 1}: {w['warning_message']}" if len(legs_raw) > 1 else w["warning_message"]
            if msg not in warnings:
                warnings.append(msg)

    temps = [float(w["temperature_c"]) for w in leg_weathers if w.get("temperature_c") is not None]
    hums = [float(w["humidity_pct"]) for w in leg_weathers if w.get("humidity_pct") is not None]
    max_wind = max((float(w.get("wind_speed_ms") or 0.0) for w in leg_weathers), default=0.0)
    max_gust = max((float(w.get("wind_gust_ms") or 0.0) for w in leg_weathers), default=0.0)
    max_precip = max((float(w.get("precipitation_mm") or 0.0) for w in leg_weathers), default=0.0)

    return {
        "temperature_c": round(sum(temps) / len(temps), 1) if temps else rep.get("temperature_c"),
        "wind_speed_ms": round(max_wind, 1),
        "wind_gust_ms": round(max_gust, 1),
        "precipitation_mm": round(max_precip, 2),
        "humidity_pct": round(sum(hums) / len(hums), 1) if hums else rep.get("humidity_pct"),
        "condition_label": rep.get("condition_label"),
        "is_bad_weather": bool(bad_legs),
        "warning_message": "; ".join(warnings) if warnings else None,
        "weather_summary": rep.get("weather_summary"),
    }


def _seg_bearing(h1: Hub, h2: Hub) -> float:
    """Compute flight heading bearing (0..360 deg) between two Hubs for directional wind estimation."""
    return calculate_bearing_deg(h1.latitude or 0, h1.longitude or 0, h2.latitude or 0, h2.longitude or 0)


def _populate_route_presentation_fields(
    routes_schemas: list[RouteOptionSchema],
    raw_data_list: list[dict],
    rec_idx: int,
) -> None:
    """Populate FE-friendly strategy metadata and comparison diffs vs the AI-recommended route."""
    if not routes_schemas:
        return
    safe_rec_idx = rec_idx if 0 <= rec_idx < len(routes_schemas) else 0
    rec_schema = routes_schemas[safe_rec_idx]
    rec_raw = raw_data_list[safe_rec_idx] if safe_rec_idx < len(raw_data_list) else {}
    rec_path = tuple(rec_raw.get("path") or [])
    rec_spd_kmh = int(round(float(rec_schema.flight_speed_mps or 10.0) * 3.6))
    rec_alt_m = int(round(float(rec_schema.flight_altitude_m or 45.0)))

    strategy_map = {
        "ENG": "ECO",
        "FAST": "FAST",
        "DETOUR": "DETOUR",
        "SAFE": "SAFE",
        "DISPATCH": "DISPATCH",
    }

    for idx, s in enumerate(routes_schemas):
        d = raw_data_list[idx] if idx < len(raw_data_list) else {}
        k = d.get("_route_key") or s.routeId.rsplit("-", 1)[-1]
        delivery_legs = [lg for lg in s.legs_detail if not lg.is_dispatch]
        is_multi_leg = len(delivery_legs) > 1
        spd_kmh = int(round(float(s.flight_speed_mps or 10.0) * 3.6))
        alt_m = int(round(float(s.flight_altitude_m or 45.0)))

        strategy_code = strategy_map.get(k, k)
        title_map = {
            "ENG": "Energy Saver Relay" if is_multi_leg else "Energy Saver Direct",
            "FAST": "Express Fast Relay" if is_multi_leg else "Express Fast Direct",
            "DETOUR": "Alternate Corridor Relay" if is_multi_leg else "Alternate Direct Route",
            "SAFE": "Safe Low-Speed Relay" if is_multi_leg else "Safe Low-Speed Fallback",
            "DISPATCH": "Cross-Hub Drone Dispatch",
        }
        display_title = title_map.get(k, s.speed_profile_label or s.routeId)

        if k == "DISPATCH" or d.get("override_cross_hub"):
            corridor_type = "CROSS_HUB_DISPATCH"
        elif not is_multi_leg:
            corridor_type = "DIRECT"
        elif k == "DETOUR":
            corridor_type = "RELAY_ALTERNATE"
        else:
            corridor_type = "RELAY_PRIMARY"

        cur_path = tuple(d.get("path") or [])
        same_hub_path = bool(rec_path and cur_path and cur_path == rec_path)
        delta_dist = round(float(s.distanceKm or 0.0) - float(rec_schema.distanceKm or 0.0), 2)
        delta_dur = int(s.predictedDurationMin or 0) - int(rec_schema.predictedDurationMin or 0)
        delta_eng = round(float(s.predictedEnergyWh or 0.0) - float(rec_schema.predictedEnergyWh or 0.0), 2)
        delta_spd_kmh = spd_kmh - rec_spd_kmh
        delta_alt_m = alt_m - rec_alt_m

        time_diff_str = f"{delta_dur:+d} min" if delta_dur != 0 else "same duration"
        energy_diff_str = f"{delta_eng:+.1f} Wh" if abs(delta_eng) >= 0.1 else "same energy"

        if s.routeId == rec_schema.routeId:
            difference_summary = "AI Recommended baseline route"
            if strategy_code == "ECO":
                differentiator_tag = f"{spd_kmh} km/h • {alt_m}m • Lowest balanced energy ({s.remainingBatteryPct}% reserve)"
            elif strategy_code == "FAST":
                differentiator_tag = f"{spd_kmh} km/h • {alt_m}m • Fastest delivery ({s.remainingBatteryPct}% reserve)"
            elif strategy_code == "SAFE":
                differentiator_tag = f"{spd_kmh} km/h • {alt_m}m • Safe contingency ({s.remainingBatteryPct}% reserve)"
            elif strategy_code == "DETOUR":
                differentiator_tag = f"{spd_kmh} km/h • {alt_m}m • Safe alternate corridor ({s.remainingBatteryPct}% reserve)"
            else:
                differentiator_tag = f"{spd_kmh} km/h • {alt_m}m • {s.remainingBatteryPct}% reserve"
        else:
            if same_hub_path:
                if strategy_code == "SAFE":
                    difference_summary = f"Same corridor • Conservative safety profile ({delta_spd_kmh} km/h, {delta_alt_m:+d}m altitude)"
                elif delta_spd_kmh > 0:
                    difference_summary = f"Same corridor • Faster flight profile (+{delta_spd_kmh} km/h, {delta_alt_m:+d}m altitude)"
                elif delta_spd_kmh < 0:
                    difference_summary = f"Same corridor • Energy-saver profile ({delta_spd_kmh} km/h, {delta_alt_m:+d}m altitude)"
                else:
                    difference_summary = "Same corridor • Alternate speed/altitude profile"
            elif strategy_code == "DISPATCH":
                difference_summary = f"Cross-hub dispatch from {s.drone_home_hub_name or 'nearby hub'} ({delta_dist:+.2f} km)"
            else:
                difference_summary = f"Different hub corridor ({delta_dist:+.2f} km, {energy_diff_str})"

            if not s.feasible:
                differentiator_tag = f"{spd_kmh} km/h • {alt_m}m • Infeasible ({s.risk} risk)"
            elif strategy_code == "FAST":
                differentiator_tag = f"{time_diff_str} • {energy_diff_str} • {spd_kmh} km/h • {alt_m}m"
            elif strategy_code == "SAFE":
                differentiator_tag = f"{spd_kmh} km/h • {alt_m}m • {s.remainingBatteryPct}% reserve ({time_diff_str})"
            elif strategy_code == "DETOUR":
                differentiator_tag = f"Alternate corridor ({delta_dist:+.2f} km) • {spd_kmh} km/h • {alt_m}m"
            elif strategy_code == "DISPATCH":
                differentiator_tag = f"Cross-hub dispatch ({delta_dist:+.2f} km) • {spd_kmh} km/h • {alt_m}m"
            else:
                differentiator_tag = f"{energy_diff_str} • {time_diff_str} • {spd_kmh} km/h • {alt_m}m"

        s.strategy_code = strategy_code
        s.display_title = display_title
        s.corridor_type = corridor_type
        s.differentiator_tag = differentiator_tag
        s.comparison_vs_recommended = {
            "delta_distance_km": delta_dist,
            "delta_duration_min": delta_dur,
            "delta_energy_wh": delta_eng,
            "same_hub_path": same_hub_path,
            "difference_summary": difference_summary,
        }


async def compute_mission_routes_internal(
    db: AsyncSession,
    order: Order,
    drone_id: Optional[Union[str, int]] = None,
    battery_id: Optional[str] = None,
    strict_battery: bool = False,
    override_payload_kg: Optional[float] = None,
    recommend_mode: Optional[str] = "AUTO",
    zone_tracker: Optional[dict] = None,
    preloaded_context: Optional[dict] = None,
) -> tuple[list[RouteOptionSchema], list[dict]]:
    effective_payload = float(override_payload_kg) if override_payload_kg is not None else float(order.payload_kg or 0)
    ctx = preloaded_context or {}
    def _set_zone(zone_name: str):
        if zone_tracker is not None:
            zone_tracker["current"] = zone_name
        try:
            print(f"[MISSION_PLANNING] {zone_name}")
        except Exception:
            pass

    _set_zone("ZONE 01: Kiểm tra Order & Thông tin Hub xuất phát - đích")
    if "hubs" in ctx and ctx["hubs"] is not None:
        hubs = list(ctx["hubs"])
    else:
        all_cached_hubs = await get_all_hubs_cached(db)
        hubs = [h for h in all_cached_hubs if (h.status or "").lower() == "active"] or list(all_cached_hubs)
    hub_dict = {h.id: h for h in hubs}
    all_hub_ids = [h.id for h in hubs]

    _need_origin = "eff_origin_hub_id" not in ctx
    _need_holds = not ("reserved_battery_ids" in ctx and "held_count_by_hub" in ctx)

    if _need_origin and _need_holds:
        # [PERF] Gộp kiểm tra replan của Order + kiểm tra giữ chỗ Pin active thành 1 SQL duy nhất
        combined_m_stmt = (
            select(
                Mission,
                MissionLeg.id,
                MissionLeg.battery_id,
                MissionLeg.sequence_no,
                MissionLeg.status,
                MissionLeg.from_hub_id,
                Drone.model,
            )
            .outerjoin(MissionLeg, MissionLeg.mission_id == Mission.id)
            .outerjoin(Drone, Mission.drone_id == Drone.id)
            .where(or_(Mission.order_id == order.id, Mission.status.in_(_ACTIVE_HOLD_MISSION_STATUSES)))
            .order_by(Mission.id.desc())
        )
        combined_rows = (await db.execute(combined_m_stmt)).all()
        order_missions_map: dict[str, Mission] = {}
        active_hold_rows = []
        active_hold_set = set(_ACTIVE_HOLD_MISSION_STATUSES)
        for m_obj, leg_id, leg_bat_id, leg_seq, leg_stat, from_hub_id, d_model in combined_rows:
            if m_obj.order_id == order.id:
                order_missions_map.setdefault(m_obj.id, m_obj)
            elif m_obj.status in active_hold_set:
                active_hold_rows.append(
                    (m_obj.id, m_obj.drone_id, m_obj.battery_id, leg_id, leg_bat_id, leg_seq, leg_stat, from_hub_id, d_model)
                )
        eff_origin_hub_id, _ = resolve_effective_origin_from_missions(order, list(order_missions_map.values()))
        reserved_battery_ids, held_count_by_hub = _parse_reserved_and_holds_rows(
            rows=active_hold_rows,
            relay_hub_set=set(all_hub_ids),
            compatible_models=[],
        )
    else:
        if "eff_origin_hub_id" in ctx:
            eff_origin_hub_id = ctx["eff_origin_hub_id"]
        else:
            eff_origin_hub_id, _ = await resolve_effective_origin_hub_id(db, order)

    if not eff_origin_hub_id or not order.destination_hub_id:
        raise HTTPException(status_code=400, detail="Order is missing Origin Hub or Destination Hub information")

    _set_zone("ZONE 02: Nạp đồ thị mạng lưới Hubs & Kho Pin ACTIVE")
    origin_hub = hub_dict.get(eff_origin_hub_id) or await db.get(Hub, eff_origin_hub_id)
    dest_hub = hub_dict.get(order.destination_hub_id) or await db.get(Hub, order.destination_hub_id)
    if not origin_hub or not dest_hub:
        raise HTTPException(status_code=400, detail="Origin Hub or Destination Hub does not exist in the system")

    # Đảm bảo origin_hub và dest_hub luôn có mặt trong hub_dict và hubs
    if origin_hub.id not in hub_dict:
        hub_dict[origin_hub.id] = origin_hub
        hubs.append(origin_hub)
    if dest_hub.id not in hub_dict:
        hub_dict[dest_hub.id] = dest_hub
        hubs.append(dest_hub)

    # 2. Lấy toàn bộ pin ACTIVE (cả pin rời và pin đang gắn trên drone)
    if "all_active_batteries" in ctx and ctx["all_active_batteries"] is not None:
        all_active_batteries = ctx["all_active_batteries"]
    else:
        result = await db.execute(
            select(Battery).where(Battery.status == batteries_status.ACTIVE).order_by(Battery.id)
        )
        all_active_batteries = result.scalars().all()
    all_batteries = [b for b in all_active_batteries if b.drone_id is None]

    _set_zone("ZONE 03: Kiểm tra giữ chỗ Pin (Virtual Holds)")
    if not (_need_origin and _need_holds):
        if "reserved_battery_ids" in ctx and "held_count_by_hub" in ctx:
            reserved_battery_ids = ctx["reserved_battery_ids"]
            held_count_by_hub = ctx["held_count_by_hub"]
        else:
            reserved_battery_ids, held_count_by_hub = await _get_reserved_batteries_and_virtual_holds(
                db=db,
                relay_hub_ids=[h.id for h in hubs],
                compatible_models=[],
                exclude_order_id=order.id,
            )

    if "compat_models_map" in ctx and ctx["compat_models_map"] is not None:
        _compat_models_map = ctx["compat_models_map"]
    else:
        _compat_models_map = await get_compatible_battery_models_map(db)

    async def _drone_has_ready_battery(d_cand):
        """Kiểm tra xem Drone tại trạm d_cand.current_hub_id có ít nhất 1 viên pin đạt 100% khả dụng không."""
        c_models = resolve_compatible_models_from_map(_compat_models_map, d_cand.model)
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
    drones_by_id = ctx.get("drones_by_id") or {}
    if clean_drone_id:
        drone_obj = drones_by_id.get(clean_drone_id) or await db.get(Drone, clean_drone_id)
        if not drone_obj and clean_drone_id.isdigit():
            num_suffix = f"{int(clean_drone_id):04d}"
            alt_id = f"DRN-{num_suffix}"
            drone_obj = drones_by_id.get(alt_id) or await db.get(Drone, alt_id)
            if not drone_obj:
                drone_obj = next(
                    (d for did, d in drones_by_id.items() if did.endswith(f"-DRN{num_suffix}")),
                    None,
                )
                if not drone_obj:
                    d_res = await db.execute(
                        select(Drone).where(Drone.id.like(f"%-DRN{num_suffix}")).limit(1)
                    )
                    drone_obj = d_res.scalar_one_or_none()
            if drone_obj:
                suggested_drone_id = drone_obj.id

        if not drone_obj:
            raise HTTPException(
                status_code=404,
                detail=f"Drone '{drone_id}' not found in the system."
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
        compat_models_map=_compat_models_map,
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
            and b.drone_id is None
            and b.id not in reserved_battery_ids
            and (not assigned_battery or b.id != assigned_battery.id)
            and (not compatible_models or (b.battery_model and b.battery_model in compatible_models))
        ]
        if not h_bats:
            hub_wait_times[h.id] = float('inf')
            hub_best_energy_wh[h.id] = 0.0
        else:
            ready_h_bats = [b for b in h_bats if (b.charge_level_pct or 0) > 0]
            ready_h_bats.sort(
                key=lambda b: _compute_battery_energy_wh(b, drone_capacity_wh)[2],
                reverse=True,
            )
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

    def plan_single_path(
        mode="energy",
        exclude_nodes=None,
        allow_direct=True,
        blocked_edges=None,
        speed: float = 10.0,
        altitude: float = 45.0,
        soft_avoid_nodes=None,
    ):
        if exclude_nodes is None:
            exclude_nodes = set()
        if blocked_edges is None:
            blocked_edges = set()
        if soft_avoid_nodes is None:
            soft_avoid_nodes = set()

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
            # Chặng 1 (xuất phát từ Origin Hub) dùng năng lượng thực tế của viên pin được assign cho Drone
            # (hoặc pin thay tại Origin Hub nếu là Cross-Hub Dispatch); các chặng sau dùng pin tại Relay Hub.
            if current_node == origin_hub.id:
                if is_cross_hub and hub_best_energy_wh.get(origin_hub.id, 0.0) > 0:
                    leg_available_energy_wh = hub_best_energy_wh.get(origin_hub.id, 0.0)
                else:
                    leg_available_energy_wh = (
                        origin_bat_energy_wh
                        if origin_bat_energy_wh > 0
                        else (drone_capacity_wh if not assigned_battery else 0.0)
                    )
            else:
                leg_available_energy_wh = hub_best_energy_wh.get(current_node, 0.0)

            for n_hub in hubs:
                if n_hub.id == current_node or n_hub.id in exclude_nodes:
                    continue
                if not allow_direct and current_node == origin_hub.id and n_hub.id == dest_hub.id:
                    continue
                if (current_node, n_hub.id) in blocked_edges:
                    continue

                dist_km = haversine_distance(
                    curr_h.latitude or 0, curr_h.longitude or 0,
                    n_hub.latitude or 0, n_hub.longitude or 0
                )
                pred = predict_flight_energy_cached(
                    dist_km, payload, speed=speed, altitude=altitude, bearing_deg=_seg_bearing(curr_h, n_hub)
                )
                energy_wh = pred["energy_wh"]

                # Kiểm tra giới hạn năng lượng của viên pin ở chặng hiện tại (tối đa 80% -> dự phòng >= 20%)
                if energy_wh > leg_available_energy_wh * 0.8:
                    continue

                flight_time = (dist_km * 1000.0) / max(float(speed), 1.0) / 60.0
                swap_overhead = 3.0 if n_hub.id != dest_hub.id else 0.0
                wait_time = 0
                exp = f"{n_hub.name} (Uses {energy_wh:.1f}Wh)"

                if n_hub.id != dest_hub.id:
                    wait_time = hub_wait_times.get(n_hub.id, float('inf'))
                    if mode == "zero_wait" and wait_time > 0:
                        continue
                    if wait_time == float('inf') or hub_best_energy_wh.get(n_hub.id, 0.0) <= 0:
                        continue
                    if wait_time == 0:
                        exp += " - Quick battery swap (0m)"
                    else:
                        exp += f" - Charge wait {wait_time}m"

                avoid_penalty = (
                    5000.0
                    if n_hub.id in soft_avoid_nodes and n_hub.id not in (origin_hub.id, dest_hub.id)
                    else 0.0
                )
                if mode == "energy":
                    edge_cost = energy_wh + 3.0 + avoid_penalty
                elif mode == "shortest":
                    edge_cost = dist_km + avoid_penalty
                elif mode == "zero_wait":
                    edge_cost = flight_time + swap_overhead + avoid_penalty
                elif mode in ("time", "fastest"):
                    edge_cost = flight_time + swap_overhead + wait_time + avoid_penalty
                else:  # balanced
                    edge_cost = flight_time + wait_time + avoid_penalty

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
            e = predict_flight_energy_cached(
                d, payload, speed=speed, altitude=altitude, bearing_deg=_seg_bearing(h1, h2)
            )["energy_wh"]
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
        num_swaps = max(0, len(path) - 2)
        total_time = int(math.ceil(total_dist * 2.2 * (10.0 / max(float(speed), 1.0)))) + len(path) - 1 + (num_swaps * 3) + total_wait

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

    _set_zone("ZONE 07: Lập 3 phương án lộ trình (Tiết kiệm năng lượng, Tốc độ nhanh, Trung chuyển / Điều phối)")
    raw_routes = []
    seen_route_strings = set()

    # Tính cự ly bay thẳng & lọc sơ bộ các trạm trung gian thỏa mãn hình học để prefetch thời tiết song song
    dist_direct = haversine_distance(
        origin_hub.latitude or 0, origin_hub.longitude or 0,
        dest_hub.latitude or 0, dest_hub.longitude or 0
    )
    direct_bearing = _seg_bearing(origin_hub, dest_hub)
    other_hubs = [h for h in hubs if h.id not in (origin_hub.id, dest_hub.id)]
    corridor_geo_candidates: list[tuple[Hub, float, float]] = []
    detour_geo_candidates: list[tuple[Hub, float, float]] = []
    for mid_h in other_hubs:
        d1 = haversine_distance(origin_hub.latitude or 0, origin_hub.longitude or 0, mid_h.latitude or 0, mid_h.longitude or 0)
        d2 = haversine_distance(mid_h.latitude or 0, mid_h.longitude or 0, dest_hub.latitude or 0, dest_hub.longitude or 0)
        # Điều kiện hình học bắt buộc: Hub trung gian phải nằm giữa đường bay,
        # không được xa hơn Hub đích (d1 < dist_direct và d2 < dist_direct) và không đi vòng quá xa.
        if dist_direct <= 0 or d1 >= dist_direct or d2 >= dist_direct:
            continue
        detour_ratio = (d1 + d2) / dist_direct
        if detour_ratio <= 1.45:
            corridor_geo_candidates.append((mid_h, d1, d2))
        if detour_ratio <= 1.65:
            detour_geo_candidates.append((mid_h, d1, d2))

    _seg_weather_raw_cache: dict[tuple[float, float], object] = ctx.setdefault("seg_weather_cache", {})

    async def _fetch_seg_weather(h1: Hub, h2: Hub):
        mlat = round(((h1.latitude or 0.0) + (h2.latitude or 0.0)) / 2.0, 4)
        mlng = round(((h1.longitude or 0.0) + (h2.longitude or 0.0)) / 2.0, 4)
        key = (mlat, mlng)
        if key not in _seg_weather_raw_cache:
            _seg_weather_raw_cache[key] = await fetch_weather(mlat, mlng)
        return _seg_weather_raw_cache[key]

    async def _prefetch_seg_weathers(pairs: list[tuple[Hub, Hub]]):
        unique_coords: list[tuple[float, float]] = []
        seen_coords = set(_seg_weather_raw_cache.keys())
        for h1, h2 in pairs:
            mlat = round(((h1.latitude or 0.0) + (h2.latitude or 0.0)) / 2.0, 4)
            mlng = round(((h1.longitude or 0.0) + (h2.longitude or 0.0)) / 2.0, 4)
            key = (mlat, mlng)
            if key not in seen_coords:
                seen_coords.add(key)
                unique_coords.append(key)
        if unique_coords:
            results = await asyncio.gather(
                *(fetch_weather(lat, lng) for lat, lng in unique_coords),
                return_exceptions=True,
            )
            for key, res in zip(unique_coords, results):
                _seg_weather_raw_cache[key] = None if isinstance(res, Exception) else res

    prefetch_pairs: list[tuple[Hub, Hub]] = [(origin_hub, dest_hub)]
    for mid_h, _, _ in detour_geo_candidates:
        prefetch_pairs.append((origin_hub, mid_h))
        prefetch_pairs.append((mid_h, dest_hub))
    if is_cross_hub and drone_home_hub:
        prefetch_pairs.append((drone_home_hub, origin_hub))
    await _prefetch_seg_weathers(prefetch_pairs)

    w_direct_raw = await _fetch_seg_weather(origin_hub, dest_hub)
    w_direct_override = weather_to_override_dict(w_direct_raw)
    w_direct_eco = evaluate_weather_safety(w_direct_raw, altitude_m=45.0)
    w_direct_fast = evaluate_weather_safety(w_direct_raw, altitude_m=60.0)

    def _build_direct_route_data(
        speed_mps: float,
        altitude_m: float,
        profile_name: str,
        is_fast: bool = False,
        baseline_eco_dur: Optional[int] = None,
    ) -> dict:
        if altitude_m >= 60.0:
            w_eval = w_direct_fast
        elif altitude_m == 45.0:
            w_eval = w_direct_eco
        else:
            w_eval = evaluate_weather_safety(w_direct_raw, altitude_m=altitude_m)

        pred = predict_flight_energy_cached(
            dist_direct,
            payload,
            speed=speed_mps,
            altitude=altitude_m,
            weather_override=w_direct_override,
            bearing_deg=direct_bearing,
        )
        energy_wh = round(float(pred["energy_wh"]), 2)
        speed_kmh = round(speed_mps * 3.6, 1)
        speed_kmh_int = int(round(speed_kmh))
        alt_int = int(round(altitude_m))
        if is_fast:
            raw_dur = max(1, int(math.ceil(dist_direct * 2.2 * (10.0 / max(float(speed_mps), 1.0)))) + 1)
            if baseline_eco_dur is not None and baseline_eco_dur > 1:
                dur_min = min(baseline_eco_dur - 1, raw_dur)
            else:
                dur_min = raw_dur
        else:
            dur_min = max(3, int(math.ceil(dist_direct * 2.2 * (10.0 / max(float(speed_mps), 1.0)))) + 2)

        o_code = origin_hub.code or origin_hub.name
        d_code = dest_hub.code or dest_hub.name
        route_str = f"{o_code} -> {d_code} ({profile_name} • {speed_kmh_int} km/h • Altitude {alt_int}m)"
        ai_exp = dict(pred.get("ai_explanation") or {})
        ai_exp["legs_breakdown"] = [{
            "leg": 1,
            "from_hub_code": o_code,
            "to_hub_code": d_code,
            "distance_km": round(dist_direct, 2),
            "energy_wh": energy_wh,
            "speed_mps": speed_mps,
            "speed_kmh": speed_kmh,
            "action": f"Direct {profile_name.lower()} flight from {origin_hub.name} to {dest_hub.name} ({speed_kmh_int} km/h • Altitude {alt_int}m • {w_eval['weather_summary']})",
        }]
        return {
            "path": [origin_hub.id, dest_hub.id],
            "relayHubs": [],
            "distanceKm": round(dist_direct, 2),
            "predictedEnergyWh": energy_wh,
            "predictedDurationMin": int(dur_min),
            "wait_time": 0,
            "flight_speed_mps": speed_mps,
            "flight_altitude_m": altitude_m,
            "speed_profile_label": f"{profile_name} ({speed_kmh_int} km/h • Altitude {alt_int}m)",
            "shap_values": pred.get("shap_values"),
            "ai_explanation": ai_exp,
            "legs_detail": [{
                "from_hub_id": origin_hub.id,
                "to_hub_id": dest_hub.id,
                "from_hub_code": o_code,
                "to_hub_code": d_code,
                "distance_km": round(dist_direct, 2),
                "energy_wh": energy_wh,
                "payload_kg": payload,
                "weather": w_eval,
                "weather_summary": w_eval["weather_summary"],
            }],
            "route_string": route_str,
            "has_bad_weather": bool(w_eval.get("is_bad_weather")),
        }

    pred_direct_eco = predict_flight_energy_cached(
        dist_direct, payload, speed=10.0, altitude=45.0, weather_override=w_direct_override, bearing_deg=direct_bearing
    )
    e_direct = pred_direct_eco["energy_wh"]
    t_eco = max(3, int(math.ceil(dist_direct * 2.2)) + 2)

    async def _build_multi_hop_relay_route_data(
        path: list[int],
        wait_min: int = 0,
        is_fallback: bool = False,
        speed_mps: float = 10.0,
        altitude_m: float = 45.0,
        profile_name: str = "Battery Swap Relay",
        summary_override: Optional[str] = None,
    ) -> dict:
        seg_pairs = [(hub_dict[path[i]], hub_dict[path[i + 1]]) for i in range(len(path) - 1)]
        await _prefetch_seg_weathers(seg_pairs)

        speed_kmh = round(speed_mps * 3.6, 1)
        speed_kmh_int = int(round(speed_kmh))
        alt_int = int(round(altitude_m))

        legs_detail = []
        legs_breakdown = []
        relay_labels = []
        leg_energies = []
        tot_d = 0.0
        tot_e = 0.0
        shap_speed_tot = 0.0
        shap_dist_tot = 0.0
        shap_payload_tot = 0.0
        shap_wind_tot = 0.0
        shap_alt_tot = 0.0
        max_leg_wind = 0.0
        cond_labels: list[str] = []
        route_has_bad_weather = False

        for i, (h1, h2) in enumerate(seg_pairs):
            d_leg = haversine_distance(
                h1.latitude or 0, h1.longitude or 0,
                h2.latitude or 0, h2.longitude or 0,
            )
            w_raw = await _fetch_seg_weather(h1, h2)
            w_eval = evaluate_weather_safety(w_raw, altitude_m=altitude_m)
            pred_leg = predict_flight_energy_cached(
                d_leg,
                payload,
                speed=speed_mps,
                altitude=altitude_m,
                weather_override=weather_to_override_dict(w_raw),
                bearing_deg=_seg_bearing(h1, h2),
            )
            e_leg = pred_leg["energy_wh"]
            leg_energies.append(e_leg)
            tot_d += d_leg
            tot_e += e_leg

            s_leg = pred_leg.get("shap_values") or {}
            shap_speed_tot += float(s_leg.get("speed", 0.0))
            shap_dist_tot += float(s_leg.get("distance", 0.0))
            shap_payload_tot += float(s_leg.get("payload", 0.0))
            shap_wind_tot += float(s_leg.get("relative_wind_angle", 0.0))
            shap_alt_tot += float(s_leg.get("altitude", 0.0))

            max_leg_wind = max(max_leg_wind, float(w_eval.get("wind_speed_ms") or 0.0))
            if w_eval.get("condition_label") and w_eval["condition_label"] not in cond_labels:
                cond_labels.append(w_eval["condition_label"])
            if w_eval.get("is_bad_weather"):
                route_has_bad_weather = True

            h1_code = h1.code or h1.name
            h2_code = h2.code or h2.name
            legs_detail.append({
                "from_hub_id": h1.id,
                "to_hub_id": h2.id,
                "from_hub_code": h1_code,
                "to_hub_code": h2_code,
                "distance_km": round(d_leg, 2),
                "energy_wh": round(e_leg, 2),
                "payload_kg": payload,
                "weather": w_eval,
                "weather_summary": w_eval["weather_summary"],
            })

            if i == 0:
                action_txt = f"Fly from {h1.name} to {h2.name} ({speed_kmh_int} km/h • Altitude {alt_int}m • Uses {e_leg:.1f} Wh • {w_eval['weather_summary']})"
            else:
                action_txt = f"Swap battery at {h1.name} and continue to {h2.name} ({speed_kmh_int} km/h • Altitude {alt_int}m • Uses {e_leg:.1f} Wh • {w_eval['weather_summary']})"
            legs_breakdown.append({
                "leg": i + 1,
                "from_hub_code": h1_code,
                "to_hub_code": h2_code,
                "distance_km": round(d_leg, 2),
                "energy_wh": round(e_leg, 2),
                "speed_mps": speed_mps,
                "speed_kmh": speed_kmh,
                "action": action_txt,
            })

            if i < len(seg_pairs) - 1:
                relay_labels.append(
                    f"{h2.name} (Uses {e_leg:.1f}Wh) - Insufficient safe battery range / stock for relay"
                    if is_fallback
                    else f"{h2.name} (Uses {e_leg:.1f}Wh)"
                )

        shap_speed_tot = round(shap_speed_tot, 2)
        shap_dist_tot = round(shap_dist_tot, 2)
        shap_payload_tot = round(shap_payload_tot, 2)
        shap_wind_tot = round(shap_wind_tot, 2)
        shap_alt_tot = round(shap_alt_tot, 2)
        aggregated_shap = {
            "speed": shap_speed_tot,
            "distance": shap_dist_tot,
            "payload": shap_payload_tot,
            "relative_wind_angle": shap_wind_tot,
            "altitude": shap_alt_tot,
        }

        diff_dist = round(tot_d - dist_direct, 2)
        diff_energy = round(tot_e - e_direct, 1)
        wind_impact = (
            "Tailwind assists battery efficiency along the entire route"
            if shap_wind_tot <= 0
            else "Headwind increases aerodynamic drag across flight legs"
        )
        if shap_speed_tot > 0:
            speed_impact = (
                f"High-speed cruise ({speed_mps:.1f} m/s ~ {speed_kmh_int} km/h) adds +{shap_speed_tot:.1f} Wh due to aerodynamic drag while cutting transit time"
            )
        elif shap_speed_tot < 0:
            speed_impact = (
                f"Low-speed safety cruise ({speed_kmh_int} km/h) across all {len(seg_pairs)} legs reduces aerodynamic drag ({shap_speed_tot:.1f} Wh) for higher safety reserve"
            )
        else:
            speed_impact = f"Cruising at energy-saving speed ({speed_kmh_int} km/h) across all {len(seg_pairs)} legs minimizes aerodynamic drag"
        mid_names = [hub_dict[nid].name for nid in path[1:-1]]
        mid_names_str = " -> ".join(mid_names)
        o_code = hub_dict[path[0]].code or hub_dict[path[0]].name
        d_code = hub_dict[path[-1]].code or hub_dict[path[-1]].name
        mid_route_parts = [f"{hub_dict[nid].code or hub_dict[nid].name} (Battery Swap)" for nid in path[1:-1]]
        route_str = f"{' -> '.join([o_code] + mid_route_parts + [d_code])} ({profile_name} • {speed_kmh_int} km/h • Altitude {alt_int}m)"
        cond_summary = " / ".join(cond_labels[:3]) if cond_labels else "Clear"

        relay_ai_explanation = {
            "flight_speed_mps": speed_mps,
            "flight_speed_kmh": speed_kmh,
            "flight_altitude_m": altitude_m,
            "total_energy_wh": round(tot_e, 2),
            "feature_contributions": [] if is_fallback else [
                {
                    "feature": "distance",
                    "label": "Route Distance",
                    "value": f"{tot_d:.2f} km ({len(seg_pairs)} legs via {mid_names_str})",
                    "shap_contribution_wh": shap_dist_tot,
                    "impact": f"Multi-hub corridor spans {tot_d:.2f} km (+{diff_dist:.2f} km vs direct line, {'+' if diff_energy >= 0 else ''}{diff_energy:.1f} Wh vs direct Eco baseline)",
                },
                {
                    "feature": "payload",
                    "label": "Cargo Payload",
                    "value": f"{payload:.2f} kg",
                    "shap_contribution_wh": shap_payload_tot,
                    "impact": f"Carrying cargo payload over {tot_d:.2f} km adds +{shap_payload_tot:.1f} Wh consumption",
                },
                {
                    "feature": "speed",
                    "label": "Flight Speed",
                    "value": f"{speed_mps:.1f} m/s ({speed_kmh:.1f} km/h)",
                    "shap_contribution_wh": shap_speed_tot,
                    "impact": speed_impact,
                },
                {
                    "feature": "altitude",
                    "label": "Flight Altitude",
                    "value": f"{alt_int} m",
                    "shap_contribution_wh": shap_alt_tot,
                    "impact": (
                        f"Higher cruise altitude ({alt_int}m) adds +{shap_alt_tot:.1f} Wh climb/wind-shear load"
                        if shap_alt_tot > 0
                        else f"Optimized cruise altitude ({alt_int}m) reduces climb energy and wind exposure ({shap_alt_tot:.1f} Wh)"
                    ),
                },
                {
                    "feature": "wind",
                    "label": "Wind Effect",
                    "value": f"Max {max_leg_wind:.1f} m/s ({cond_summary})",
                    "shap_contribution_wh": shap_wind_tot,
                    "impact": wind_impact,
                },
            ],
            "legs_breakdown": legs_breakdown,
            "summary": summary_override or (
                f"Fallback relay route via {mid_names_str}. Total distance {tot_d:.2f} km, "
                f"energy consumption {tot_e:.1f} Wh. Relay leg energy exceeds safe battery reserve (80%) or compatible stock is unavailable."
                if is_fallback
                else (
                    f"{profile_name} multi-hub route via {len(path) - 2} battery swap stop(s) ({mid_names_str}). "
                    f"Total distance {tot_d:.2f} km, total energy {tot_e:.1f} Wh at {speed_kmh_int} km/h across {len(seg_pairs)} feasible legs."
                )
            ),
        }

        num_swaps = max(1, len(path) - 2)
        fly_min = max(1, int(math.ceil(tot_d * 2.2 * (10.0 / max(float(speed_mps), 1.0)))) + len(seg_pairs))
        return {
            "path": path,
            "relayHubs": relay_labels,
            "distanceKm": round(tot_d, 2),
            "predictedEnergyWh": round(tot_e, 2),
            "predictedDurationMin": fly_min + (num_swaps * 3) + int(wait_min),
            "wait_time": int(wait_min),
            "flight_speed_mps": speed_mps,
            "flight_altitude_m": altitude_m,
            "speed_profile_label": f"{profile_name} ({speed_kmh_int} km/h • Altitude {alt_int}m)",
            "shap_values": aggregated_shap,
            "ai_explanation": relay_ai_explanation,
            "legs_detail": legs_detail,
            "route_string": route_str,
            "cost_metric": tot_e + tot_d * 2,
            "has_bad_weather": route_has_bad_weather,
            "_e1": leg_energies[0] if leg_energies else 0.0,
            "_e2": leg_energies[1] if len(leg_energies) > 1 else 0.0,
            "_leg_energies": leg_energies,
        }

    leg1_max_safe_wh = (
        hub_best_energy_wh.get(origin_hub.id, 0.0) * 0.8
        if is_cross_hub and hub_best_energy_wh.get(origin_hub.id, 0.0) > 0
        else max_safe_origin_energy_wh
    )
    effective_leg1_safe_wh = (
        leg1_max_safe_wh
        if leg1_max_safe_wh > 0
        else (drone_capacity_wh * 0.8 if (not assigned_battery and drone_capacity_wh > 0) else 0.0)
    )

    async def _find_feasible_multi_hop_route(
        mode: str,
        speed_mps: float,
        altitude_m: float,
        profile_name: str,
        allow_direct: bool = False,
        initial_blocked_edges: Optional[set[tuple[int, int]]] = None,
        soft_avoid_nodes: Optional[set[int]] = None,
        hard_exclude_nodes: Optional[set[int]] = None,
        avoid_bad_weather: bool = False,
    ) -> Optional[dict]:
        blocked_edges: set[tuple[int, int]] = set(initial_blocked_edges or set())
        exclude_nodes_mh: set[int] = set(hard_exclude_nodes or set())
        weather_tolerant_fallback: Optional[dict] = None

        for _attempt in range(6):
            dijk_res = plan_single_path(
                mode=mode,
                exclude_nodes=exclude_nodes_mh,
                allow_direct=allow_direct,
                blocked_edges=blocked_edges,
                speed=speed_mps,
                altitude=altitude_m,
                soft_avoid_nodes=soft_avoid_nodes,
            )
            if not dijk_res or (not allow_direct and len(dijk_res.get("path") or []) < 3):
                break
            mh_path = dijk_res["path"]
            mh_cand = await _build_multi_hop_relay_route_data(
                path=mh_path,
                wait_min=int(dijk_res.get("wait_time") or 0),
                is_fallback=False,
                speed_mps=speed_mps,
                altitude_m=altitude_m,
                profile_name=profile_name,
            )
            legs_copy = [dict(l) for l in mh_cand["legs_detail"]]
            mh_feasible, _, mh_statuses = await check_relay_hub_battery_availability(
                db=db,
                relay_hub_ids=mh_path[1:-1],
                hub_dict=hub_dict,
                all_batteries=all_batteries,
                compatible_models=compatible_models,
                drone_id=suggested_drone_id,
                drone_capacity_wh=drone_capacity_wh,
                legs_detail=legs_copy,
                assigned_battery=assigned_battery,
                assigned_battery_energy_wh=(
                    hub_best_energy_wh.get(origin_hub.id, 0.0)
                    if is_cross_hub and hub_best_energy_wh.get(origin_hub.id, 0.0) > 0
                    else origin_bat_energy_wh
                ),
                reserved_battery_ids=reserved_battery_ids,
                held_count_by_hub=held_count_by_hub,
                is_cross_hub=False,
                origin_hub_id=origin_hub.id,
            )
            if mh_feasible:
                if avoid_bad_weather and mh_cand.get("has_bad_weather"):
                    if weather_tolerant_fallback is None:
                        weather_tolerant_fallback = mh_cand
                    blocked_w = False
                    for leg_d in mh_cand["legs_detail"]:
                        w_d = leg_d.get("weather") or {}
                        if w_d.get("is_bad_weather") and leg_d.get("from_hub_id") and leg_d.get("to_hub_id"):
                            blocked_edges.add((leg_d["from_hub_id"], leg_d["to_hub_id"]))
                            blocked_w = True
                    if blocked_w:
                        continue
                return mh_cand

            added_block = False
            # Kiểm tra từng chặng (kể cả Leg 1 khi gió ngược thực tế đẩy mức tiêu thụ > 80%)
            for leg_i, leg_d in enumerate(legs_copy):
                bat_e = float(leg_d.get("battery_energy_wh") or 0.0)
                leg_e = float(leg_d.get("energy_wh") or 0.0)
                if bat_e > 0 and leg_e > bat_e * 0.8 and leg_i + 1 < len(mh_path):
                    blocked_edges.add((mh_path[leg_i], mh_path[leg_i + 1]))
                    added_block = True
            for idx_s, st in enumerate(mh_statuses):
                if not st.feasible:
                    if not st.selected_battery_id:
                        exclude_nodes_mh.add(st.hub_id)
                    elif idx_s + 1 < len(mh_path) - 1:
                        blocked_edges.add((mh_path[idx_s + 1], mh_path[idx_s + 2]))
                    added_block = True
            if not added_block and len(mh_path) >= 2:
                blocked_edges.add((mh_path[0], mh_path[1]))

        return weather_tolerant_fallback

    # ── 1. Tuyến 1: Tiết kiệm năng lượng (Adaptive: ưu tiên 10 m/s • 45m, tự hạ 9 m/s • 40m nếu giảm trạm/tiết kiệm rõ rệt) ──
    r_direct_eco = _build_direct_route_data(10.0, 45.0, "Energy Saver", is_fast=False)
    eng_chosen = None
    if effective_leg1_safe_wh <= 0 or e_direct <= effective_leg1_safe_wh:
        eng_chosen = r_direct_eco
    else:
        eng_10 = await _find_feasible_multi_hop_route(
            mode="energy",
            speed_mps=10.0,
            altitude_m=45.0,
            profile_name="Energy Saver Relay",
            allow_direct=False,
        )
        r_direct_eco_9 = _build_direct_route_data(9.0, 40.0, "Energy Saver", is_fast=False)
        if r_direct_eco_9["predictedEnergyWh"] <= effective_leg1_safe_wh:
            eng_chosen = r_direct_eco_9
        elif eng_10 is None or len(eng_10.get("path") or []) >= 4:
            eng_9 = await _find_feasible_multi_hop_route(
                mode="energy",
                speed_mps=9.0,
                altitude_m=40.0,
                profile_name="Energy Saver Relay",
                allow_direct=False,
            )
            if eng_9 and (
                eng_10 is None
                or len(eng_9.get("path") or []) < len(eng_10.get("path") or [])
                or float(eng_9.get("predictedEnergyWh") or 0.0) <= float(eng_10.get("predictedEnergyWh") or 0.0) * 0.97
            ):
                eng_chosen = eng_9
            else:
                eng_chosen = eng_10
        else:
            eng_chosen = eng_10
    raw_routes.append({"key": "ENG", "type": "energy", "data": eng_chosen or r_direct_eco})

    # ── 2. Tuyến 2: Ưu tiên thời gian nhanh nhất (Adaptive Joint Speed–Relay Optimization) ──
    r_direct_fast = _build_direct_route_data(
        15.0, 60.0, "Express Fast", is_fast=True, baseline_eco_dur=t_eco
    )

    def _fast_rank_key(cand: dict) -> tuple:
        return (
            int(cand.get("predictedDurationMin") or 999999),
            max(0, len(cand.get("path") or []) - 2),
            1 if cand.get("has_bad_weather") else 0,
            float(cand.get("distanceKm") or 999999.0),
            float(cand.get("predictedEnergyWh") or 999999.0),
        )

    fast_chosen = None
    if effective_leg1_safe_wh > 0:
        fast_candidates: list[dict] = []
        best_fast: Optional[dict] = None
        for fast_spd, fast_alt in [(15.0, 60.0), (13.5, 55.0), (12.5, 50.0), (11.5, 50.0)]:
            lower_bound_min = max(1, int(math.ceil(dist_direct * 2.2 * (10.0 / fast_spd))) + 1)
            if best_fast is not None and not best_fast.get("has_bad_weather") and lower_bound_min > int(best_fast.get("predictedDurationMin") or 999999):
                break

            dir_cand = (
                r_direct_fast
                if (fast_spd == 15.0 and fast_alt == 60.0)
                else _build_direct_route_data(fast_spd, fast_alt, "Express Fast", is_fast=True, baseline_eco_dur=t_eco)
            )
            if float(dir_cand["predictedEnergyWh"]) <= effective_leg1_safe_wh:
                fast_candidates.append(dir_cand)
                if best_fast is None or _fast_rank_key(dir_cand) < _fast_rank_key(best_fast):
                    best_fast = dir_cand
                if not dir_cand.get("has_bad_weather"):
                    break
                continue

            mh_cand = await _find_feasible_multi_hop_route(
                mode="time",
                speed_mps=fast_spd,
                altitude_m=fast_alt,
                profile_name="Express Fast Relay",
                allow_direct=False,
            )
            if mh_cand:
                fast_candidates.append(mh_cand)
                if best_fast is None or _fast_rank_key(mh_cand) < _fast_rank_key(best_fast):
                    best_fast = mh_cand

        if fast_candidates:
            fast_chosen = min(fast_candidates, key=_fast_rank_key)

    raw_routes.append({"key": "FAST", "type": "fastest", "data": fast_chosen or r_direct_fast})

    # ── 3. Tuyến 3: Hành lang thay thế an toàn khác Hub (DETOUR), Điều phối (DISPATCH) hoặc An toàn tốc độ thấp (SAFE) ──
    detour_candidates = []
    in_corridor_hubs = list(corridor_geo_candidates)
    eng_data_chosen = raw_routes[0]["data"]
    fast_data_chosen = raw_routes[1]["data"]
    eng_path = eng_data_chosen.get("path") or []
    fast_path = fast_data_chosen.get("path") or []
    eng_mid_nodes = set(eng_path[1:-1])
    fast_mid_nodes = set(fast_path[1:-1])
    eng_is_multi_hop = len(eng_path) >= 3

    primary_bad_edges_45m: set[tuple[int, int]] = set()
    for r_entry in raw_routes[:2]:
        for leg_d in r_entry["data"].get("legs_detail", []):
            h_from = hub_dict.get(leg_d.get("from_hub_id"))
            h_to = hub_dict.get(leg_d.get("to_hub_id"))
            if h_from and h_to:
                w45_raw = await _fetch_seg_weather(h_from, h_to)
                if evaluate_weather_safety(w45_raw, altitude_m=45.0).get("is_bad_weather"):
                    primary_bad_edges_45m.add((h_from.id, h_to.id))

    if primary_bad_edges_45m or eng_data_chosen.get("has_bad_weather") or fast_data_chosen.get("has_bad_weather"):
        detour_spd, detour_alt = 8.0, 40.0
    else:
        detour_spd, detour_alt = 10.0, 45.0

    for mid_h, d1, d2 in detour_geo_candidates:
        # Không lặp lại đúng hub trung gian mà tuyến ENG đã chọn
        if eng_is_multi_hop and mid_h.id in eng_mid_nodes:
            continue
        mid_energy = hub_best_energy_wh.get(mid_h.id, 0.0)
        mid_wait = hub_wait_times.get(mid_h.id, float('inf'))
        if mid_energy <= 0 or mid_wait == float('inf'):
            continue

        cand = await _build_multi_hop_relay_route_data(
            path=[origin_hub.id, mid_h.id, dest_hub.id],
            wait_min=int(mid_wait) if mid_wait != float('inf') else 0,
            is_fallback=False,
            speed_mps=detour_spd,
            altitude_m=detour_alt,
            profile_name="Alternate Corridor Relay",
        )
        # Chặng 1 & Chặng 2 phải <= 80% pin
        if leg1_max_safe_wh > 0 and cand["_e1"] > leg1_max_safe_wh:
            continue
        if cand["_e2"] > mid_energy * 0.8:
            continue
        detour_candidates.append(cand)

    # Ưu tiên trạm trung chuyển thời tiết an toàn và khác hành lang tuyến chính trước
    detour_candidates = [c for c in detour_candidates if c.get("path") != eng_path]
    detour_candidates.sort(
        key=lambda x: (
            1 if x.get("has_bad_weather") else 0,
            1 if x.get("path") == fast_path else 0,
            x.get("cost_metric", 0),
        )
    )

    has_distinct_safe_one_hop = any(
        not c.get("has_bad_weather")
        and c.get("path") != eng_path
        for c in detour_candidates
    )
    if not has_distinct_safe_one_hop:
        hard_excl = eng_mid_nodes if eng_is_multi_hop else None
        soft_excl = (fast_mid_nodes - eng_mid_nodes) if eng_is_multi_hop else fast_mid_nodes
        mh_detour = await _find_feasible_multi_hop_route(
            mode="energy",
            speed_mps=detour_spd,
            altitude_m=detour_alt,
            profile_name="Alternate Corridor Relay",
            allow_direct=False,
            initial_blocked_edges=primary_bad_edges_45m,
            soft_avoid_nodes=soft_excl,
            hard_exclude_nodes=hard_excl,
            avoid_bad_weather=True,
        )
        if not mh_detour and primary_bad_edges_45m:
            mh_detour = await _find_feasible_multi_hop_route(
                mode="energy",
                speed_mps=detour_spd,
                altitude_m=detour_alt,
                profile_name="Alternate Corridor Relay",
                allow_direct=False,
                soft_avoid_nodes=soft_excl,
                hard_exclude_nodes=hard_excl,
                avoid_bad_weather=True,
            )
        if (
            mh_detour
            and mh_detour.get("path") != eng_path
            and (dist_direct <= 0 or float(mh_detour.get("distanceKm") or 0.0) <= dist_direct * 1.75)
        ):
            detour_candidates.append(mh_detour)
            detour_candidates.sort(
                key=lambda x: (
                    1 if x.get("has_bad_weather") else 0,
                    1 if x.get("path") == fast_path else 0,
                    x.get("cost_metric", 0),
                )
            )

    if detour_candidates:
        # Trường hợp 1: Tồn tại hành lang trung chuyển khác Hub đủ pin -> Tuyến 3 DETOUR
        best_detour = detour_candidates[0]
        raw_routes.append({"key": "DETOUR", "type": "detour", "data": best_detour})
    elif not eng_is_multi_hop and in_corridor_hubs:
        # ENG đi thẳng (hoặc hub trung gian thiếu pin) -> hiển thị đường relay để cảnh báo/so sánh
        nearest_mid, _, _ = min(in_corridor_hubs, key=lambda item: item[1] + item[2])
        fallback_detour = await _build_multi_hop_relay_route_data(
            path=[origin_hub.id, nearest_mid.id, dest_hub.id],
            wait_min=0,
            is_fallback=True,
            speed_mps=detour_spd,
            altitude_m=detour_alt,
            profile_name="Alternate Corridor Relay",
        )
        raw_routes.append({"key": "DETOUR", "type": "detour", "data": fallback_detour})
    elif eng_is_multi_hop:
        # Đồ thị Hub chỉ có 1 hành lang khả thi duy nhất (không có trạm vòng hợp lý khác):
        # Cấm nhân bản ENG -> chuyển Tuyến 3 sang cấu hình an toàn tốc độ thấp (8.0 m/s ~ 29 km/h • 40m)
        safe_relay_data = await _build_multi_hop_relay_route_data(
            path=list(eng_path),
            wait_min=int(eng_data_chosen.get("wait_time") or 0),
            is_fallback=False,
            speed_mps=8.0,
            altitude_m=40.0,
            profile_name="Safe Low-Speed Relay",
        )
        raw_routes.append({"key": "SAFE", "type": "safe", "data": safe_relay_data})
    else:
        # Không có trạm trung gian nào nằm giữa đường bay (các trạm khác đều xa hơn Hub đích)
        # -> Tuyệt đối không vẽ đường vòng vô lý! Thay bằng Trường hợp 2: Điều phối Drone từ trạm lân cận gần nhất
        standby_route_added = False
        if not is_cross_hub:
            standby_res = await db.execute(
                select(Drone).where(
                    and_(
                        Drone.status == DroneStatus.AVAILABLE,
                        Drone.current_hub_id != origin_hub.id,
                        Drone.current_hub_id.is_not(None),
                        Drone.payload_capacity_kg >= effective_payload,
                    )
                ).order_by(Drone.id)
            )
            standby_raw = []
            for sd in standby_res.scalars().all():
                s_hub = hub_dict.get(sd.current_hub_id)
                if s_hub:
                    d_s = haversine_distance(s_hub.latitude or 0, s_hub.longitude or 0, origin_hub.latitude or 0, origin_hub.longitude or 0)
                    standby_raw.append((d_s, sd, s_hub))
            standby_raw.sort(key=lambda x: x[0])

            standby_candidates = []
            for d_s, sd, s_hub in standby_raw:
                if await _drone_has_ready_battery(sd):
                    standby_candidates.append((d_s, sd, s_hub))
                    break

            if standby_candidates:
                d_disp_opt, sb_drone, sb_hub = standby_candidates[0]
                sb_compat, sb_bat, sb_cap, sb_charge, sb_energy, sb_default_cap = await _resolve_assigned_drone_battery(
                    db=db,
                    origin_hub_id=sb_hub.id,
                    drone_obj=sb_drone,
                    requested_battery_id=None,
                    all_active_batteries=all_active_batteries,
                    reserved_battery_ids=reserved_battery_ids,
                    strict=False,
                    compat_models_map=_compat_models_map,
                )
                w_sb_raw = await _fetch_seg_weather(sb_hub, origin_hub)
                w_sb_eval = evaluate_weather_safety(w_sb_raw, altitude_m=45.0)
                pred_disp = predict_flight_energy_cached(
                    d_disp_opt,
                    0.0,
                    speed=10.0,
                    altitude=45.0,
                    weather_override=weather_to_override_dict(w_sb_raw),
                    bearing_deg=_seg_bearing(sb_hub, origin_hub),
                )
                e_disp_opt = pred_disp["energy_wh"]
                t_disp_opt = max(1, int(round(d_disp_opt * 2.0)))
                sb_code = sb_hub.code or sb_hub.name
                o_code = origin_hub.code or origin_hub.name
                d_code = dest_hub.code or dest_hub.name
                disp_route_str = f"[Dispatch from {sb_code}] -> {o_code} (Pickup) -> {d_code} (Delivery) (36 km/h • Altitude 45m)"
                disp_ai_exp = {
                    "flight_speed_mps": 10.0,
                    "flight_speed_kmh": 36.0,
                    "flight_altitude_m": 45.0,
                    "total_energy_wh": round(e_disp_opt + e_direct, 2),
                    "feature_contributions": [],
                    "legs_breakdown": [
                        {
                            "leg": 1,
                            "from_hub_code": sb_code,
                            "to_hub_code": o_code,
                            "distance_km": round(d_disp_opt, 2),
                            "energy_wh": round(e_disp_opt, 2),
                            "speed_mps": 10.0,
                            "speed_kmh": 36.0,
                            "action": f"Dispatch Drone {sb_drone.name} empty from {sb_hub.name} to {origin_hub.name} for pickup (Uses {e_disp_opt:.1f} Wh • {w_sb_eval['weather_summary']})",
                        },
                        {
                            "leg": 2,
                            "from_hub_code": o_code,
                            "to_hub_code": d_code,
                            "distance_km": round(dist_direct, 2),
                            "energy_wh": round(e_direct, 2),
                            "speed_mps": 10.0,
                            "speed_kmh": 36.0,
                            "action": f"Pick up package at {origin_hub.name} (swap/charge battery if needed) and fly to {dest_hub.name} (Uses {e_direct:.1f} Wh • {w_direct_eco['weather_summary']})",
                        }
                    ],
                    "summary": (
                        f"Case 2 (Cross-Hub Dispatch): No intermediate hub lies along the route {o_code} -> {d_code}. "
                        f"Standby option ready to dispatch Drone {sb_drone.name} from {sb_hub.name} to {origin_hub.name} for pickup."
                    )
                }
                disp_opt_data = {
                    "path": [sb_hub.id, origin_hub.id, dest_hub.id],
                    "relayHubs": [f"{origin_hub.name} (Pickup)"],
                    "distanceKm": round(d_disp_opt + dist_direct, 2),
                    "predictedEnergyWh": round(e_disp_opt + e_direct, 2),
                    "predictedDurationMin": t_eco + t_disp_opt,
                    "wait_time": 0,
                    "flight_speed_mps": 10.0,
                    "flight_altitude_m": 45.0,
                    "speed_profile_label": "Cross-Hub Drone Dispatch (36 km/h • Altitude 45m)",
                    "shap_values": pred_direct_eco.get("shap_values"),
                    "ai_explanation": disp_ai_exp,
                    "legs_detail": [
                        {
                            "from_hub_id": sb_hub.id,
                            "to_hub_id": origin_hub.id,
                            "from_hub_code": sb_code,
                            "to_hub_code": o_code,
                            "distance_km": round(d_disp_opt, 2),
                            "energy_wh": round(e_disp_opt, 2),
                            "payload_kg": 0.0,
                            "leg_type": "DISPATCH",
                            "is_dispatch": True,
                            "note": f"Dispatch Drone from {sb_hub.name} to {origin_hub.name} for pickup",
                            "weather": w_sb_eval,
                            "weather_summary": w_sb_eval["weather_summary"],
                        },
                        {
                            "from_hub_id": origin_hub.id,
                            "to_hub_id": dest_hub.id,
                            "from_hub_code": o_code,
                            "to_hub_code": d_code,
                            "distance_km": round(dist_direct, 2),
                            "energy_wh": round(e_direct, 2),
                            "payload_kg": payload,
                            "leg_type": "DELIVERY",
                            "is_dispatch": False,
                            "weather": w_direct_eco,
                            "weather_summary": w_direct_eco["weather_summary"],
                        }
                    ],
                    "route_string": disp_route_str,
                    "override_cross_hub": True,
                    "override_drone_id": sb_drone.id,
                    "override_assigned_bat": sb_bat,
                    "override_bat_cap_wh": sb_cap,
                    "override_bat_charge_pct": sb_charge,
                    "override_bat_energy_wh": sb_energy,
                    "override_drone_cap_wh": sb_default_cap,
                    "override_compat_models": sb_compat,
                    "override_home_hub": sb_hub,
                }
                raw_routes.append({"key": "DISPATCH", "type": "dispatch", "data": disp_opt_data})
                standby_route_added = True

        if not standby_route_added:
            # Phương án dự phòng an toàn trực tiếp (khi đang điều phối từ Hub khác hoặc không có Hub trung gian / Drone lân cận)
            w_direct_safe = evaluate_weather_safety(w_direct_raw, altitude_m=40.0)
            pred_safe = predict_flight_energy_cached(
                dist_direct,
                payload,
                speed=8.0,
                altitude=40.0,
                weather_override=w_direct_override,
                bearing_deg=direct_bearing,
            )
            e_safe = round(pred_safe["energy_wh"], 2)
            t_safe = max(t_eco + 2, int(math.ceil(dist_direct * 2.5)) + 2)
            o_code = origin_hub.code or origin_hub.name
            d_code = dest_hub.code or dest_hub.name
            safe_str = f"{o_code} -> {d_code} (Safe Fallback • 29 km/h • Altitude 40m)"
            safe_exp = dict(pred_safe.get("ai_explanation") or {})
            safe_exp["legs_breakdown"] = [{
                "leg": 1,
                "from_hub_code": o_code,
                "to_hub_code": d_code,
                "distance_km": round(dist_direct, 2),
                "energy_wh": e_safe,
                "speed_mps": 8.0,
                "speed_kmh": 28.8,
                "action": f"Stable low-speed fallback flight from {origin_hub.name} to {dest_hub.name} (29 km/h • Altitude 40m • {w_direct_safe['weather_summary']})",
            }]
            r_safe = {
                "path": [origin_hub.id, dest_hub.id],
                "relayHubs": [],
                "distanceKm": round(dist_direct, 2),
                "predictedEnergyWh": e_safe,
                "predictedDurationMin": t_safe,
                "wait_time": 0,
                "flight_speed_mps": 8.0,
                "flight_altitude_m": 40.0,
                "speed_profile_label": "Safe Fallback (29 km/h • Altitude 40m)",
                "shap_values": pred_safe.get("shap_values"),
                "ai_explanation": safe_exp,
                "legs_detail": [{
                    "from_hub_id": origin_hub.id,
                    "to_hub_id": dest_hub.id,
                    "from_hub_code": o_code,
                    "to_hub_code": d_code,
                    "distance_km": round(dist_direct, 2),
                    "energy_wh": e_safe,
                    "payload_kg": payload,
                    "weather": w_direct_safe,
                    "weather_summary": w_direct_safe["weather_summary"],
                }],
                "route_string": safe_str,
                "no_relay_note": "No intermediate hub lies along the flight path (avoids detouring farther than the destination hub)",
            }
            raw_routes.append({"key": "SAFE", "type": "safe", "data": r_safe})

    # Bộ kiểm tra chữ ký tuyến (Route Signature Guard): bảo đảm không bao giờ trả về 2 tuyến giống hệt nhau
    def _route_sig(entry: dict) -> tuple:
        d_obj = entry["data"]
        return (
            tuple(d_obj.get("path") or []),
            round(float(d_obj.get("flight_speed_mps") or 10.0), 1),
            round(float(d_obj.get("flight_altitude_m") or 45.0), 0),
            bool(d_obj.get("override_cross_hub")),
        )

    deduped_routes: list[dict] = []
    seen_sigs: set[tuple] = set()
    for entry in raw_routes:
        sig = _route_sig(entry)
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped_routes.append(entry)
    raw_routes = deduped_routes

    # ── Điều phối Drone từ Hub khác (Cross-Hub Drone Dispatch khi Hub gốc hết Drone / hết Pin) ──
    d_disp = 0.0
    e_disp = 0.0
    t_disp = 0
    if is_cross_hub and drone_home_hub:
        d_disp = haversine_distance(
            drone_home_hub.latitude or 0, drone_home_hub.longitude or 0,
            origin_hub.latitude or 0, origin_hub.longitude or 0
        )
        w_disp_raw = await _fetch_seg_weather(drone_home_hub, origin_hub)
        w_disp_eval = evaluate_weather_safety(w_disp_raw, altitude_m=45.0)
        e_disp = predict_flight_energy_cached(
            d_disp,
            0.0,
            speed=10.0,
            altitude=45.0,
            weather_override=weather_to_override_dict(w_disp_raw),
            bearing_deg=_seg_bearing(drone_home_hub, origin_hub),
        )["energy_wh"]
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
            "note": f"Dispatch Drone from {drone_home_hub.name} to {origin_hub.name} for pickup (empty flight 0kg)",
            "weather": w_disp_eval,
            "weather_summary": w_disp_eval["weather_summary"],
        }
        home_code = drone_home_hub.code or drone_home_hub.name
        origin_code = origin_hub.code or origin_hub.name
        dest_code = dest_hub.code or dest_hub.name

        for entry in raw_routes:
            d = entry["data"]
            if d.get("override_cross_hub"):
                continue
            for l_item in d.get("legs_detail", []):
                l_item.setdefault("leg_type", "DELIVERY")
                l_item.setdefault("is_dispatch", False)
                l_item.setdefault("payload_kg", payload)

            d["legs_detail"] = [disp_leg_template.copy()] + d.get("legs_detail", [])
            d["path"] = [drone_home_hub.id] + d.get("path", [])
            d["distanceKm"] = round(d["distanceKm"] + d_disp, 2)
            d["predictedEnergyWh"] = round(d["predictedEnergyWh"] + e_disp, 2)
            d["predictedDurationMin"] = d["predictedDurationMin"] + t_disp

            mid_hubs = [hub_dict[nid].code or hub_dict[nid].name for nid in d["path"] if nid not in (drone_home_hub.id, origin_hub.id, dest_hub.id)]
            mid_str = " -> ".join([f"{m} (Battery Swap)" for m in mid_hubs])
            spd_kmh = int(round(float(d.get("flight_speed_mps", 10.0)) * 3.6))
            alt_m = int(round(float(d.get("flight_altitude_m", 45.0))))
            if entry["key"] in ("ENG", "DIR"):
                body = f"{mid_str} -> {dest_code} (Delivery)" if mid_str else f"{dest_code} (Delivery)"
                d["route_string"] = f"[Dispatch from {home_code}] -> {origin_code} (Pickup) -> {body} (Energy Saver • {spd_kmh} km/h • Altitude {alt_m}m)"
            elif entry["key"] == "FAST":
                body = f"{mid_str} -> {dest_code} (Delivery)" if mid_str else f"{dest_code} (Delivery)"
                d["route_string"] = f"[Dispatch from {home_code}] -> {origin_code} (Pickup) -> {body} (Express Fast • {spd_kmh} km/h • Altitude {alt_m}m)"
            elif entry["key"] == "DETOUR":
                if mid_str:
                    d["route_string"] = f"[Dispatch from {home_code}] -> {origin_code} (Pickup) -> {mid_str} -> {dest_code} (Delivery) (Alternate Corridor • {spd_kmh} km/h • Altitude {alt_m}m)"
                else:
                    d["route_string"] = f"[Dispatch from {home_code}] -> {origin_code} (Pickup) -> {dest_code} (Delivery) (Battery Swap)"
            elif entry["key"] == "SAFE":
                body = f"{mid_str} -> {dest_code} (Delivery)" if mid_str else f"{dest_code} (Delivery)"
                safe_label = "Safe Low-Speed Relay" if mid_str else "Safe Fallback"
                d["route_string"] = f"[Dispatch from {home_code}] -> {origin_code} (Pickup) -> {body} ({safe_label} • {spd_kmh} km/h • Altitude {alt_m}m)"
            else:
                d["route_string"] = f"[Dispatch from {home_code}] -> {origin_code} (Pickup) -> {d['route_string']}"

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

        # Hỗ trợ per-route override (ví dụ Option 3 DISPATCH điều phối dự phòng từ trạm khác khi Hub gốc còn Drone)
        opt_is_cross_hub = bool(d.get("override_cross_hub")) or is_cross_hub
        opt_drone_id = d.get("override_drone_id") or suggested_drone_id
        opt_home_hub = d.get("override_home_hub") if d.get("override_cross_hub") else drone_home_hub
        opt_assigned_bat = d.get("override_assigned_bat") if d.get("override_cross_hub") else assigned_battery
        opt_bat_cap = float(d.get("override_bat_cap_wh", origin_bat_capacity_wh)) if d.get("override_cross_hub") else origin_bat_capacity_wh
        opt_bat_charge = int(d.get("override_bat_charge_pct", origin_bat_charge_pct)) if d.get("override_cross_hub") else origin_bat_charge_pct
        opt_bat_energy = float(d.get("override_bat_energy_wh", origin_bat_energy_wh)) if d.get("override_cross_hub") else origin_bat_energy_wh
        opt_drone_cap = float(d.get("override_drone_cap_wh", drone_capacity_wh)) if d.get("override_cross_hub") else drone_capacity_wh
        opt_compat_models = d.get("override_compat_models", compatible_models) if d.get("override_cross_hub") else compatible_models
        opt_max_safe_wh = round(opt_bat_energy * 0.8, 2)
        opt_compat_label = f" ({', '.join(opt_compat_models)})" if opt_compat_models else ""

        departure_id = opt_home_hub.id if opt_is_cross_hub and opt_home_hub else origin_hub.id
        relay_hub_ids = [nid for nid in path if nid not in (departure_id, dest_hub.id)]

        if not relay_hub_ids:
            # Direct route — kiểm tra năng lượng chặng duy nhất dựa trên năng lượng của pin được assign cho Drone
            leg0_energy = float(legs_raw[0].get("energy_wh") or d["predictedEnergyWh"]) if legs_raw else float(d["predictedEnergyWh"])
            batt_pct = int(round((leg0_energy / opt_bat_cap) * 100)) if opt_bat_cap > 0 else 100
            rem_batt_pct = max(0, opt_bat_charge - batt_pct)

            if legs_raw:
                legs_raw[0]["battery_id"] = opt_assigned_bat.id if opt_assigned_bat else None
                legs_raw[0]["battery_serial"] = opt_assigned_bat.serial_number if opt_assigned_bat else None
                legs_raw[0]["battery_model"] = opt_assigned_bat.battery_model if opt_assigned_bat else None
                legs_raw[0]["battery_capacity_wh"] = round(opt_bat_cap, 2) if opt_assigned_bat else round(opt_drone_cap, 2)
                legs_raw[0]["battery_charge_pct"] = opt_bat_charge if opt_assigned_bat else None
                legs_raw[0]["battery_energy_wh"] = round(opt_bat_energy, 2)
                legs_raw[0]["battery_consumption_pct"] = batt_pct
                legs_raw[0]["remaining_battery_pct"] = rem_batt_pct

            if opt_assigned_bat is None:
                feasible = False
                infeasible_reason = (
                    f"Origin hub has no compatible battery{opt_compat_label} available to assign to Drone."
                )
            elif leg0_energy > opt_bat_energy:
                feasible = False
                infeasible_reason = (
                    f"Required energy ({leg0_energy:.1f} Wh, ~{batt_pct}% of battery {opt_assigned_bat.id}) "
                    f"exceeds available energy of assigned battery{opt_compat_label} "
                    f"({opt_bat_energy:.1f} Wh / {opt_bat_cap:.1f} Wh, charge {opt_bat_charge}%). "
                    f"Drone lacks sufficient battery for direct flight (max {opt_max_safe_wh:.1f} Wh to maintain 20% reserve)."
                )
            elif leg0_energy > opt_max_safe_wh:
                feasible = False
                infeasible_reason = (
                    f"Required energy ({leg0_energy:.1f} Wh, ~{batt_pct}% of battery {opt_assigned_bat.id}) "
                    f"exceeds the 80% safe threshold of assigned battery{opt_compat_label} "
                    f"({opt_max_safe_wh:.1f} Wh / {opt_bat_energy:.1f} Wh, charge {opt_bat_charge}%). "
                    f"Minimum 20% safety reserve cannot be guaranteed."
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
                compatible_models=opt_compat_models,
                drone_id=opt_drone_id,
                drone_capacity_wh=opt_drone_cap,
                legs_detail=legs_raw,
                assigned_battery=opt_assigned_bat,
                assigned_battery_energy_wh=opt_bat_energy,
                reserved_battery_ids=reserved_battery_ids,
                held_count_by_hub=held_count_by_hub,
                is_cross_hub=opt_is_cross_hub,
                origin_hub_id=origin_hub.id,
            )
            # Cập nhật lại chuỗi hiển thị relayHubs kèm mã pin thay thế cụ thể tại từng Hub trung gian
            updated_relay_labels: list[str] = []
            for r_idx, st in enumerate(hub_statuses):
                prev_leg_e = float(legs_raw[r_idx].get("energy_wh") or 0.0) if r_idx < len(legs_raw) else 0.0
                if opt_is_cross_hub and st.hub_id == origin_hub.id:
                    if st.selected_battery_id and st.selected_battery_id != (opt_assigned_bat.id if opt_assigned_bat else None):
                        updated_relay_labels.append(
                            f"{st.hub_name} (Pickup) - Swap battery {st.selected_battery_id} "
                            f"({st.selected_battery_serial or st.selected_battery_model or 'Ready'}, "
                            f"{st.selected_battery_energy_wh or st.selected_battery_capacity_wh or 0:.0f}Wh, "
                            f"{st.selected_battery_charge_pct or 0}%)"
                        )
                    else:
                        updated_relay_labels.append(
                            f"{st.hub_name} (Pickup) - Continue with dispatched battery {opt_assigned_bat.id if opt_assigned_bat else ''}"
                        )
                else:
                    if st.selected_battery_id:
                        updated_relay_labels.append(
                            f"{st.hub_name} (Uses {prev_leg_e:.1f}Wh) - Swap battery {st.selected_battery_id} "
                            f"({st.selected_battery_serial or st.selected_battery_model or 'Ready'}, "
                            f"{st.selected_battery_energy_wh or st.selected_battery_capacity_wh or 0:.0f}Wh, "
                            f"{st.selected_battery_charge_pct or 0}%)"
                        )
                    else:
                        updated_relay_labels.append(
                            f"{st.hub_name} (Uses {prev_leg_e:.1f}Wh) - No compatible battery available"
                        )
            d["relayHubs"] = updated_relay_labels

            leg_pcts = [int(leg.get("battery_consumption_pct") or 0) for leg in legs_raw if leg.get("battery_consumption_pct") is not None]
            unique_bat_ids = {str(leg.get("battery_id")) for leg in legs_raw if leg.get("battery_id")}
            if leg_pcts:
                batt_pct = sum(leg_pcts) if (len(legs_raw) > 1 and len(unique_bat_ids) <= 1) else max(leg_pcts)
            else:
                batt_pct = int(round((float(d["predictedEnergyWh"]) / opt_bat_energy) * 100)) if opt_bat_energy > 0 else 50
            last_leg_rem = legs_raw[-1].get("remaining_battery_pct") if legs_raw else None
            if last_leg_rem is not None:
                rem_batt_pct = max(0, int(last_leg_rem))
            else:
                base_start = int(opt_bat_charge) if opt_assigned_bat else 100
                rem_batt_pct = max(0, base_start - batt_pct)

        risk = "LOW" if batt_pct < 60 else ("MEDIUM" if batt_pct <= 80 else "HIGH")
        route_altitude = float(d.get("flight_altitude_m") or 45.0)
        route_weather_dict = _aggregate_route_weather(legs_raw, altitude_m=route_altitude)
        route_weather_schema = RouteWeatherSchema(**route_weather_dict)
        if route_weather_schema.is_bad_weather:
            risk = "HIGH"
        confidence_pct = 65 if route_weather_schema.is_bad_weather else (90 if risk == "LOW" else (85 if risk == "MEDIUM" else 75))

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
                    weather=RouteWeatherSchema(**leg["weather"]) if isinstance(leg.get("weather"), dict) else None,
                    weather_summary=(
                        leg.get("weather_summary")
                        or (leg["weather"].get("weather_summary") if isinstance(leg.get("weather"), dict) else None)
                    ),
                ) for idx, leg in enumerate(legs_raw)
            ],
            distanceKm=d["distanceKm"],
            relayHubs=d["relayHubs"],
            predictedDurationMin=d["predictedDurationMin"],
            predictedEnergyWh=d["predictedEnergyWh"],
            batteryConsumptionPct=batt_pct,
            remainingBatteryPct=rem_batt_pct,
            confidencePct=confidence_pct,
            risk=risk,
            recommended=False,
            reason="",
            suggestedDroneId=opt_drone_id,
            assignedBatteryId=opt_assigned_bat.id if opt_assigned_bat else None,
            assignedBatterySerial=opt_assigned_bat.serial_number if opt_assigned_bat else None,
            assignedBatteryModel=opt_assigned_bat.battery_model if opt_assigned_bat else None,
            assignedBatteryCapacityWh=round(opt_bat_cap, 2) if opt_assigned_bat else None,
            assignedBatteryChargePct=opt_bat_charge if opt_assigned_bat else None,
            assignedBatteryEnergyWh=round(opt_bat_energy, 2) if opt_assigned_bat else None,
            feasible=feasible,
            infeasible_reason=infeasible_reason,
            is_selectable=feasible,
            unselectable_reason=infeasible_reason,
            relay_battery_status=hub_statuses,
            is_cross_hub=opt_is_cross_hub,
            drone_home_hub_id=opt_home_hub.id if opt_is_cross_hub and opt_home_hub else None,
            drone_home_hub_name=opt_home_hub.name if opt_is_cross_hub and opt_home_hub else None,
            drone_home_hub_code=opt_home_hub.code or opt_home_hub.name if opt_is_cross_hub and opt_home_hub else None,
            pickup_hub_id=origin_hub.id if opt_is_cross_hub else None,
            pickup_hub_name=origin_hub.name if opt_is_cross_hub else None,
            pickup_hub_code=origin_hub.code or origin_hub.name if opt_is_cross_hub else None,
            flight_speed_mps=d.get("flight_speed_mps", 10.0),
            flight_altitude_m=route_altitude,
            speed_profile_label=d.get("speed_profile_label"),
            weather=route_weather_schema,
            weather_summary=route_weather_schema.weather_summary,
            shap_values=d.get("shap_values"),
            ai_explanation=d.get("ai_explanation"),
        )
        d["_route_key"] = k
        routes_schemas.append(schema)
        raw_data_list.append(d)

    _set_zone("ZONE 09: Đóng gói RouteOptionSchema & Xếp hạng AI Recommendation")
    # ĐÁNH DẤU RECOMMEND THEO CHẾ ĐỘ ĐƠN HÀNG (NORMAL/SCHEDULED -> ECO, EXPRESS -> FAST) & NÉ THỜI TIẾT XẤU
    if routes_schemas:
        req_mode = (recommend_mode or "AUTO").strip().upper()
        order_mode_raw = order.delivery_mode.value if hasattr(order.delivery_mode, "value") else str(order.delivery_mode or "NORMAL")
        order_mode_val = order_mode_raw.strip().upper()
        if req_mode in ("ECO", "FAST"):
            pref_mode = req_mode
        elif order_mode_val == DeliveryMode.EXPRESS.value:
            pref_mode = "FAST"
        else:
            pref_mode = "ECO"

        feasible_indices = [i for i, s in enumerate(routes_schemas) if s.feasible]
        best_idx = None
        unfiltered_best_idx = None
        if feasible_indices:
            safe_indices = [
                i for i in feasible_indices
                if not (routes_schemas[i].weather and routes_schemas[i].weather.is_bad_weather)
            ]
            candidate_indices = safe_indices if safe_indices else feasible_indices

            def _score_route(i: int):
                s_item = routes_schemas[i]
                # Tuyến 3 (DETOUR/SAFE/DISPATCH) chỉ được recommend khi các tuyến chính (ENG/FAST)
                # vi phạm 1 trong 2 điều kiện an toàn: (1) thiếu pin/dự phòng <20%, hoặc (2) thời tiết xấu.
                is_contingency = (
                    1
                    if ("DETOUR" in s_item.routeId or "SAFE" in s_item.routeId or "DISPATCH" in s_item.routeId)
                    else 0
                )
                if pref_mode == "FAST":
                    return (is_contingency, s_item.predictedDurationMin, s_item.predictedEnergyWh)
                return (is_contingency, s_item.predictedEnergyWh, s_item.predictedDurationMin)

            unfiltered_best_idx = min(feasible_indices, key=_score_route)
            best_idx = min(candidate_indices, key=_score_route)

        weather_avoided_note = ""
        if best_idx is not None and unfiltered_best_idx is not None and best_idx != unfiltered_best_idx:
            skipped_r = routes_schemas[unfiltered_best_idx]
            skipped_warn = (
                skipped_r.weather.warning_message
                if skipped_r.weather and skipped_r.weather.warning_message
                else "bad weather exceeding safety thresholds"
            )
            weather_avoided_note = (
                f"AI proactively avoided {skipped_r.routeId} due to weather alert ({skipped_warn}), switching to a safer route. "
            )

        eng_ref_route = next((r for r in routes_schemas if "ENG" in r.routeId), routes_schemas[0])

        for idx, (s, d) in enumerate(zip(routes_schemas, raw_data_list)):
            swap_notes = [
                f"{st.hub_name}: swap battery {st.selected_battery_id} ({st.selected_battery_serial})"
                for st in s.relay_battery_status
                if st.selected_battery_id
            ]
            swap_summary = f" Intermediate battery swap plan: {'; '.join(swap_notes)}." if swap_notes else ""
            s_bat_note = (
                f"Origin battery: {s.assignedBatteryId} ({s.assignedBatterySerial}, {s.assignedBatteryEnergyWh:.1f} Wh)."
                if s.assignedBatteryId and s.assignedBatteryEnergyWh is not None else ""
            )
            opt_disp_prefix = (
                f"[Cross-Hub Dispatch]: Drone from {s.drone_home_hub_name} flies empty to {origin_hub.name} for pickup. "
                if s.is_cross_hub and s.drone_home_hub_name else ""
            )
            weather_note = f" Weather: {s.weather_summary}." if s.weather_summary else ""
            bad_weather_prefix = ""
            if s.weather and s.weather.is_bad_weather and s.weather.warning_message:
                if idx == best_idx:
                    bad_weather_prefix = (
                        f"Weather advisory ({s.weather.warning_message}) — best available feasible route; proceed with caution. "
                    )
                else:
                    bad_weather_prefix = (
                        f"Severe weather warning ({s.weather.warning_message}) — AI does not recommend this route. "
                    )

            spd_mps = float(s.flight_speed_mps or 10.0)
            spd_kmh = int(round(spd_mps * 3.6))
            alt_m = int(round(float(s.flight_altitude_m or 45.0)))
            delivery_legs = [lg for lg in s.legs_detail if not lg.is_dispatch]
            is_multi_leg = len(delivery_legs) > 1
            flight_mode_phrase = f"Multi-hub relay ({len(delivery_legs)} legs)" if is_multi_leg else "Direct flight"

            if not s.feasible:
                s.recommended = False
                s.is_selectable = False
                s.unselectable_reason = s.infeasible_reason
                s.reason = f"Infeasible route (Unselectable): {s.infeasible_reason}{weather_note}"
            elif idx == best_idx:
                s.recommended = True
                s.is_selectable = True
                s.unselectable_reason = None
                if "DETOUR" in s.routeId:
                    relays_str = " -> ".join([leg.to_hub_code for leg in s.legs_detail[:-1]])
                    intro = (
                        weather_avoided_note
                        if weather_avoided_note
                        else "Primary routes (Energy Saver / Express Fast) cannot meet the 80% battery safety threshold or stock constraints. "
                    )
                    s.reason = (
                        f"{opt_disp_prefix}{bad_weather_prefix}AI Recommended: {intro}"
                        f"Contingency relay route via battery swap hub(s) {relays_str} ensures safe energy reserves to complete the mission "
                        f"(total {s.predictedEnergyWh:.1f} Wh, max leg {s.batteryConsumptionPct}% / reserve {s.remainingBatteryPct}%). "
                        f"{s_bat_note}{swap_summary}{weather_note}"
                    ).strip()
                elif "DISPATCH" in s.routeId:
                    s.reason = (
                        f"{opt_disp_prefix}{bad_weather_prefix}AI Recommended: {weather_avoided_note}"
                        f"Dispatch Drone from nearby hub {s.drone_home_hub_name} to {origin_hub.name} for pickup and deliver to {dest_hub.name} "
                        f"(total {s.predictedEnergyWh:.1f} Wh, reserve {s.remainingBatteryPct}%). {s_bat_note}{weather_note}"
                    ).strip()
                elif "SAFE" in s.routeId:
                    intro = (
                        weather_avoided_note
                        if weather_avoided_note
                        else "Primary routes (Energy Saver / Express Fast) cannot meet safety thresholds. "
                    )
                    s.reason = (
                        f"{opt_disp_prefix}{bad_weather_prefix}AI Recommended: {intro}"
                        f"Safe low-speed flight ({spd_mps:.1f} m/s ~ {spd_kmh} km/h • Altitude {alt_m}m) maximizes battery reserve "
                        f"and flight stability (total {s.predictedEnergyWh:.1f} Wh, max leg {s.batteryConsumptionPct}% / reserve {s.remainingBatteryPct}%). "
                        f"{s_bat_note}{swap_summary}{weather_note}"
                    ).strip()
                elif "FAST" in s.routeId:
                    diff_wh = round(s.predictedEnergyWh - eng_ref_route.predictedEnergyWh, 1)
                    diff_sign = "+" if diff_wh >= 0 else ""
                    s.reason = (
                        f"{opt_disp_prefix}{bad_weather_prefix}AI Recommended ({order_mode_val} Order • Express Fast Priority): "
                        f"{flight_mode_phrase} at high cruise speed ({spd_mps:.1f} m/s ~ {spd_kmh} km/h • Altitude {alt_m}m) in safe weather conditions, "
                        f"reducing transit time to {s.predictedDurationMin} min (total {s.predictedEnergyWh:.1f} Wh, {diff_sign}{diff_wh:.1f} Wh vs Energy Saver, "
                        f"reserve {s.remainingBatteryPct}%). {s_bat_note}{swap_summary}{weather_note}"
                    ).strip()
                else:
                    mode_reason = (
                        weather_avoided_note
                        if weather_avoided_note
                        else f"{flight_mode_phrase} Energy Saver route ({order_mode_val} Order • {spd_mps:.1f} m/s ~ {spd_kmh} km/h • Altitude {alt_m}m). "
                    )
                    s.reason = (
                        f"{opt_disp_prefix}{bad_weather_prefix}AI Recommended: {mode_reason}"
                        f"SHAP analysis confirms {spd_mps:.1f} m/s cruise speed minimizes aerodynamic drag for the lowest battery consumption "
                        f"(total {s.predictedEnergyWh:.1f} Wh, ~{s.batteryConsumptionPct}% max battery/leg, "
                        f"reserve {s.remainingBatteryPct}%). {s_bat_note}{swap_summary}{weather_note}"
                    ).strip()
            else:
                s.recommended = False
                s.is_selectable = True
                s.unselectable_reason = None
                if "DETOUR" in s.routeId:
                    relays_str = " -> ".join([leg.to_hub_code for leg in s.legs_detail[:-1]])
                    diff_d = round(s.distanceKm - eng_ref_route.distanceKm, 2)
                    diff_e = round(s.predictedEnergyWh - eng_ref_route.predictedEnergyWh, 1)
                    diff_e_sign = "+" if diff_e >= 0 else ""
                    s.reason = (
                        f"{opt_disp_prefix}{bad_weather_prefix}Alternate Corridor Relay route: Stop at {relays_str} for battery swap. "
                        f"Reserved as an alternate corridor to bypass primary battery deficits or bad weather "
                        f"({'+' if diff_d >= 0 else ''}{diff_d:.2f} km, {diff_e_sign}{diff_e:.1f} Wh vs Energy Saver; "
                        f"Total distance {s.distanceKm} km, {s.predictedEnergyWh:.1f} Wh). {s_bat_note}{swap_summary}{weather_note}"
                    ).strip()
                elif "DISPATCH" in s.routeId:
                    s.reason = (
                        f"{opt_disp_prefix}{bad_weather_prefix}Case 2 — Cross-Hub Drone Dispatch: No intermediate hub lies along the route (avoids longer detours past destination). "
                        f"This option dispatches Drone {s.suggestedDroneId} from {s.drone_home_hub_name} to {origin_hub.name} for pickup and delivers to {dest_hub.name} "
                        f"(Total distance {s.distanceKm} km, {s.predictedEnergyWh:.1f} Wh). {s_bat_note}{weather_note}"
                    ).strip()
                elif "SAFE" in s.routeId:
                    if is_multi_leg:
                        s.reason = (
                            f"{opt_disp_prefix}{bad_weather_prefix}Safe Low-Speed Relay route ({spd_mps:.1f} m/s ~ {spd_kmh} km/h • Altitude {alt_m}m): "
                            f"Conservative contingency profile prioritizing maximum battery safety reserve ({s.remainingBatteryPct}% reserve, "
                            f"total {s.predictedEnergyWh:.1f} Wh, duration {s.predictedDurationMin} min) over transit speed. "
                            f"{s_bat_note}{swap_summary}{weather_note}"
                        ).strip()
                    else:
                        no_relay = d.get("no_relay_note") or f"Stable low-speed flight ({spd_kmh} km/h • Altitude {alt_m}m)"
                        s.reason = (
                            f"{opt_disp_prefix}{bad_weather_prefix}Safe Fallback option: {no_relay}. "
                            f"Direct flight at {spd_mps:.0f} m/s ({spd_kmh} km/h • Altitude {alt_m}m) to maximize safety reserve margin "
                            f"(Distance {s.distanceKm} km, {s.predictedEnergyWh:.1f} Wh, duration {s.predictedDurationMin} min). {s_bat_note}{weather_note}"
                        ).strip()
                elif "ENG" in s.routeId:
                    s.reason = (
                        f"{opt_disp_prefix}{bad_weather_prefix}{flight_mode_phrase} Energy Saver route ({spd_mps:.1f} m/s ~ {spd_kmh} km/h • Altitude {alt_m}m): "
                        f"Lowest energy consumption ({s.predictedEnergyWh:.1f} Wh, duration {s.predictedDurationMin} min, max leg {s.batteryConsumptionPct}%, reserve {s.remainingBatteryPct}%). "
                        f"{s_bat_note}{swap_summary}{weather_note}"
                    ).strip()
                elif "SHORT" in s.routeId:
                    s.reason = f"{opt_disp_prefix}{bad_weather_prefix}Shortest path: Distance {s.distanceKm} km, consumes {s.predictedEnergyWh:.1f} Wh. {s_bat_note}{swap_summary}{weather_note}".strip()
                elif "DIR" in s.routeId:
                    s.reason = f"{opt_disp_prefix}{bad_weather_prefix}Direct point-to-point route: Distance {s.distanceKm} km, no intermediate charging stop. {s_bat_note}{weather_note}".strip()
                elif "FAST" in s.routeId:
                    diff_wh = round(s.predictedEnergyWh - eng_ref_route.predictedEnergyWh, 1)
                    pct_inc = int(round((diff_wh / eng_ref_route.predictedEnergyWh) * 100)) if eng_ref_route.predictedEnergyWh > 0 else 15
                    diff_sign = "+" if diff_wh >= 0 else ""
                    pct_sign = "+" if pct_inc >= 0 else ""
                    s.reason = (
                        f"{opt_disp_prefix}{bad_weather_prefix}Express Fast route ({flight_mode_phrase} at {spd_mps:.1f} m/s ~ {spd_kmh} km/h • Altitude {alt_m}m): "
                        f"Shortens transit time to {s.predictedDurationMin} min, while SHAP analysis shows aerodynamic drag at higher speed "
                        f"adjusts energy consumption by {diff_sign}{diff_wh:.1f} Wh ({pct_sign}{pct_inc}%, total {s.predictedEnergyWh:.1f} Wh). {s_bat_note}{swap_summary}{weather_note}"
                    ).strip()
                else:
                    s.reason = f"{opt_disp_prefix}{bad_weather_prefix}Alternative route: Distance {s.distanceKm} km, duration {s.predictedDurationMin} min. {s_bat_note}{swap_summary}{weather_note}".strip()

        _populate_route_presentation_fields(
            routes_schemas=routes_schemas,
            raw_data_list=raw_data_list,
            rec_idx=best_idx if best_idx is not None else 0,
        )

    # Kiểm tra BR đầy đủ trên cả 3 tuyến bay (trạng thái Available, tải trọng, pin + 20% safety buffer)
    d_status_val = (
        drone_obj.status.value if hasattr(drone_obj.status, "value") else str(drone_obj.status or "")
    ) if drone_obj else "Available"
    d_is_avail = (d_status_val == DroneStatus.AVAILABLE.value) if drone_obj else True
    d_max_payload = float(drone_obj.payload_capacity_kg or 0.0) if drone_obj else effective_payload
    d_payload_ok = (d_max_payload >= effective_payload) if drone_obj else True

    for s in routes_schemas:
        same_selected_drone = (
            drone_obj is None
            or not s.suggestedDroneId
            or str(s.suggestedDroneId) == str(drone_obj.id)
        )
        route_avail = d_is_avail if same_selected_drone else True
        route_payload_ok = d_payload_ok if same_selected_drone else True
        if not route_avail or not route_payload_ok:
            br_fail_msg = (
                f"Drone {drone_obj.id} status is '{d_status_val}' (must be 'Available')."
                if not route_avail
                else f"Drone {drone_obj.id} max payload ({d_max_payload:.2f} kg) is below order payload ({effective_payload:.2f} kg)."
            )
            s.feasible = False
            s.is_selectable = False
            s.recommended = False
            s.infeasible_reason = br_fail_msg
            s.unselectable_reason = br_fail_msg
            if not s.reason or not s.reason.startswith("Infeasible"):
                s.reason = f"Infeasible route (Unselectable): {br_fail_msg}"

        s.business_rule_check = build_route_business_rule_check(
            route=s,
            payload_ok=route_payload_ok,
            is_available=route_avail,
            drone_status_label=d_status_val,
            order_payload_kg=effective_payload,
            max_payload_kg=d_max_payload,
        )

    # Luôn trả về đầy đủ cả 3 tuyến đường để Operator quan sát toàn diện và so sánh các phương án
    # (các tuyến không đủ pin vẫn được hiển thị trên bảng điều khiển kèm cờ feasible=False, is_selectable=False và lý do chi tiết)
    if raw_data_list:
        raw_data_list[0]["_eff_origin_hub_id"] = eff_origin_hub_id
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

    # 1. Lấy tất cả Hubs (dùng cache 30s)
    hub_list = await get_all_hubs_cached(db)
    hub_dict = {h.id: h for h in hub_list}

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
    compat_models_map = await get_compatible_battery_models_map(db)
    drone_ids = {m.drone_id for m in missions if m.drone_id is not None}
    drone_by_id: dict[str, Drone] = {}
    if drone_ids:
        drones_res = await db.execute(select(Drone).where(Drone.id.in_(drone_ids)))
        drone_by_id = {d.id: d for d in drones_res.scalars().all()}

    if len(missions) <= 5:
        needed_bat_ids: set[str] = {str(m.battery_id) for m in missions if m.battery_id}
        for legs in legs_by_mission.values():
            for lg in legs:
                if lg.battery_id:
                    needed_bat_ids.add(str(lg.battery_id))
        bat_filters = []
        if needed_bat_ids:
            bat_filters.append(Battery.id.in_(list(needed_bat_ids)))
        if drone_ids:
            bat_filters.append(Battery.drone_id.in_(list(drone_ids)))
        if bat_filters:
            bats_res = await db.execute(select(Battery).where(or_(*bat_filters)))
            all_batteries = bats_res.scalars().all()
        else:
            all_batteries = []
    else:
        bats_res = await db.execute(select(Battery))
        all_batteries = bats_res.scalars().all()
    battery_by_id = {b.id: b for b in all_batteries}

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
            _, drone_capacity_wh = await _resolve_drone_battery_capacity_wh(
                db, drone_obj, all_batteries, compat_models_map=compat_models_map
            )

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
        running_bat_state: dict[str, dict[str, Any]] = {}

        if m_legs:
            for idx, leg in enumerate(m_legs):
                h1 = hub_dict.get(leg.from_hub_id)
                h2 = hub_dict.get(leg.to_hub_id)
                from_code = (h1.code or h1.name) if h1 else str(leg.from_hub_id)
                to_code = (h2.code or h2.name) if h2 else str(leg.to_hub_id)

                is_dispatch_leg = bool(
                    m.origin_hub_id
                    and leg.to_hub_id == m.origin_hub_id
                    and leg.from_hub_id != m.origin_hub_id
                    and idx == 0
                    and len(m_legs) > 1
                )
                leg_type_val = "DISPATCH" if is_dispatch_leg else "DELIVERY"
                leg_payload_val = 0.0 if is_dispatch_leg else (payload if payload > 0 else None)

                d = haversine_distance(
                    h1.latitude or 0, h1.longitude or 0,
                    h2.latitude or 0, h2.longitude or 0
                ) if (h1 and h2) else 0.0
                e = float(predict_flight_energy_cached(d, 0.0 if is_dispatch_leg else payload)["energy_wh"])
                leg_energies.append(e)

                leg_bat_id = str(leg.battery_id) if leg.battery_id else (origin_bat_id if idx == 0 else None)
                leg_bat_obj = battery_by_id.get(leg_bat_id) if leg_bat_id else None
                leg_cap, leg_charge, leg_energy_avail, leg_pct, leg_rem_pct, _ = _apply_leg_battery_step(
                    e, leg_bat_obj, drone_capacity_wh, running_bat_state
                )

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
                        status="FLYING" if str(leg.status.value if hasattr(leg.status, "value") else (leg.status or "")).strip().upper() in ("IN_PROGRESS", "IN PROGRESS", "IN-PROGRESS") else (leg.status.value if hasattr(leg.status, "value") else (str(leg.status) if leg.status else None)),
                        energy_wh=round(e, 2),
                        battery_id=leg_bat_id,
                        battery_serial=leg_bat_obj.serial_number if leg_bat_obj else None,
                        battery_model=leg_bat_obj.battery_model if leg_bat_obj else None,
                        battery_capacity_wh=round(leg_cap, 2),
                        battery_charge_pct=leg_charge,
                        battery_energy_wh=leg_energy_avail,
                        battery_consumption_pct=leg_pct,
                        remaining_battery_pct=leg_rem_pct,
                        leg_type=leg_type_val,
                        is_dispatch=is_dispatch_leg,
                        payload_kg=leg_payload_val,
                    )
                )

            relay_hubs, relay_battery_status_list = _build_relay_summaries_from_legs(
                legs_detail=legs_detail,
                hub_by_id=hub_dict,
                battery_by_id=battery_by_id,
                fallback_cap_wh=drone_capacity_wh,
            )

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
                e = float(predict_flight_energy_cached(d, payload)["energy_wh"])
                leg_energies.append(e)
                s_cap, s_charge, s_energy, leg_pct, leg_rem_pct, _ = _apply_leg_battery_step(
                    e, origin_bat_obj, drone_capacity_wh, running_bat_state
                )
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
                                "FLYING" if str(m.status.value if hasattr(m.status, "value") else (m.status or "")).strip().lower() in ("flying", "in progress", "active mission")
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

        batt_pct, rem_batt_pct = _summarize_mission_battery_pcts(
            legs_detail=legs_detail,
            origin_charge_pct=origin_bat_charge if origin_bat_obj else None,
            pred_energy_wh=pred_energy,
            origin_energy_wh=origin_bat_energy,
        )

        if ai_pred and ai_pred.risk_level:
            risk = str(ai_pred.risk_level).upper()
        else:
            risk = "LOW" if batt_pct < 60 else ("MEDIUM" if batt_pct <= 80 else "HIGH")

        conf_pct = 90 if risk == "LOW" else (85 if risk == "MEDIUM" else 75)
        rec_content = ai_pred.recommendation_content if ai_pred else None
        reason = (
            rec_content
            if rec_content
            else f"Route {route_string} selected by AI analysis ({pred_energy:.1f} Wh)."
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
        meta_info = extract_mission_override_and_compensation(m)
        m_dict["route_override_info"] = meta_info.get("route_override")
        m_dict["late_delivery_compensation"] = meta_info.get("late_delivery_compensation")

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
    if not target_hub_id:
        eff_hub_id = None
        if order_id:
            order = await db.get(Order, order_id.strip())
            if not order:
                raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")
            eff_hub_id, _ = await resolve_effective_origin_hub_id(db, order)
        target_hub_id = drone.current_hub_id or eff_hub_id

    reserved_info_map = await _get_reserved_battery_info_map(
        db, exclude_order_id=order_id.strip() if order_id else None
    )
    compat_models_map = await get_compatible_battery_models_map(db)
    compatible_models = resolve_compatible_models_from_map(compat_models_map, drone.model)
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
            unavailable_reason = f"Installed on Drone #{b.drone_id}"

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
    zone_tracker = {"current": "ZONE 01: Validate Order & Origin/Destination Hubs"}
    try:
        clean_order_id = str(request.orderId).strip() if request.orderId else request.orderId
        clean_drone_id = str(request.droneId).strip() if request.droneId is not None else None
        requested_battery_id = (request.batteryId or request.battery_id or "").strip() or None

        preloaded_drone = None
        if clean_drone_id:
            pair_row = (
                await db.execute(
                    select(Order, Drone)
                    .outerjoin(Drone, Drone.id == clean_drone_id)
                    .where(Order.id == clean_order_id)
                )
            ).first()
            order = pair_row[0] if pair_row else None
            preloaded_drone = pair_row[1] if pair_row else None
        else:
            order = await db.get(Order, clean_order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        total_bundle_payload = None
        if request.bundledOrderIds:
            all_bundle_ids = [clean_order_id] + [str(x).strip() for x in request.bundledOrderIds if str(x).strip()]
            val_res = await validate_bundle_orders(db, all_bundle_ids, clean_drone_id)
            if not val_res["is_valid"]:
                raise HTTPException(status_code=400, detail=val_res["message"])
            total_bundle_payload = val_res["total_payload_kg"]

        analyze_ctx = {"drones_by_id": {preloaded_drone.id: preloaded_drone}} if preloaded_drone is not None else None
        routes_schemas, raw_data_list = await compute_mission_routes_internal(
            db=db,
            order=order,
            drone_id=clean_drone_id,
            battery_id=requested_battery_id,
            strict_battery=bool(requested_battery_id),
            override_payload_kg=total_bundle_payload,
            recommend_mode=getattr(request, "recommend_mode", "AUTO"),
            zone_tracker=zone_tracker,
            preloaded_context=analyze_ctx,
        )

        # Lưu cache kết quả phân tích để bước Create/Assign Mission tái sử dụng ngay mà không chạy lại từ đầu
        cache_payload = total_bundle_payload if total_bundle_payload is not None else float(order.payload_kg or 0)
        cache_key = _build_route_cache_key(
            order_id=order.id,
            drone_id=clean_drone_id,
            battery_id=requested_battery_id,
            payload_kg=cache_payload,
            recommend_mode=getattr(request, "recommend_mode", "AUTO"),
            strict_battery=bool(requested_battery_id),
        )
        if len(_ROUTE_ANALYSIS_CACHE) >= _ROUTE_ANALYSIS_CACHE_MAX:
            _ROUTE_ANALYSIS_CACHE.clear()
        _ROUTE_ANALYSIS_CACHE[cache_key] = (
            time.monotonic(),
            copy.deepcopy(routes_schemas),
            copy.deepcopy(raw_data_list),
        )

        zone_tracker["current"] = "ZONE 10: Finalize MissionPlanningAnalyzeResponse"
        eff_origin_hub_id = (
            raw_data_list[0].get("_eff_origin_hub_id")
            if raw_data_list and isinstance(raw_data_list[0], dict)
            else None
        )
        if eff_origin_hub_id is None:
            eff_origin_hub_id, _ = await resolve_effective_origin_hub_id(db, order)
        first_route = routes_schemas[0] if routes_schemas else None
        is_cross = first_route.is_cross_hub if first_route else False
        route_expl = None
        if routes_schemas:
            chosen_route = next((r for r in routes_schemas if r.recommended), routes_schemas[0])
            req_mode = (getattr(request, "recommend_mode", None) or "AUTO").strip().upper()
            order_mode_raw = (
                order.delivery_mode.value
                if hasattr(order.delivery_mode, "value")
                else str(order.delivery_mode or "NORMAL")
            ).strip().upper()
            opt_obj = (
                req_mode if req_mode in ("ECO", "FAST")
                else ("FAST" if order_mode_raw == DeliveryMode.EXPRESS.value else "ECO")
            )
            route_expl = build_route_nl_explanation(
                routes_schemas=routes_schemas,
                selected_route=chosen_route,
                optimization_objective=opt_obj,
            )

        resolved_drone_id = clean_drone_id or (
            str(first_route.suggestedDroneId) if (first_route and first_route.suggestedDroneId) else None
        )
        resolved_drone_obj = None
        if resolved_drone_id:
            if preloaded_drone is not None and str(preloaded_drone.id) == resolved_drone_id:
                resolved_drone_obj = preloaded_drone
            else:
                resolved_drone_obj = await db.get(Drone, resolved_drone_id)

        drone_expl, br_summary = await build_analyze_drone_explanation(
            db=db,
            order=order,
            drone_obj=resolved_drone_obj,
            routes_schemas=routes_schemas,
            total_payload_kg=cache_payload,
            eff_origin_hub_id=int(eff_origin_hub_id),
        )

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
            droneCurrentHubId=first_route.drone_home_hub_id if (first_route and is_cross) else eff_origin_hub_id,
            droneCurrentHubName=first_route.drone_home_hub_name if (first_route and is_cross) else None,
            droneCurrentHubCode=first_route.drone_home_hub_code if (first_route and is_cross) else None,
            isCrossHubDispatch=is_cross,
            pickupHubId=first_route.pickup_hub_id if (first_route and is_cross) else None,
            pickupHubName=first_route.pickup_hub_name if (first_route and is_cross) else None,
            pickupHubCode=first_route.pickup_hub_code if (first_route and is_cross) else None,
            routes=routes_schemas,
            route_explanation=route_expl,
            drone_explanation=drone_expl,
            business_rules_summary=br_summary,
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
            detail=f"[{failed_zone}] System error while analyzing mission: {str(e)}"
        )


def _get_cached_routes_for_create(
    order_id: str,
    drone_id: Union[str, int],
    requested_battery_id: Optional[str],
    total_payload_kg: float,
    recommend_mode: str,
    route_id: Optional[str],
) -> Optional[tuple[list[RouteOptionSchema], list[dict]]]:
    """Retrieve fresh route analysis snapshot cached during the Review/Analyze step."""
    now_mono = time.monotonic()
    candidate_keys = [
        _build_route_cache_key(order_id, drone_id, requested_battery_id, total_payload_kg, recommend_mode, bool(requested_battery_id)),
        _build_route_cache_key(order_id, drone_id, None, total_payload_kg, recommend_mode, False),
        _build_route_cache_key(order_id, None, None, total_payload_kg, recommend_mode, False),
    ]
    for key in candidate_keys:
        entry = _ROUTE_ANALYSIS_CACHE.get(key)
        if not entry:
            continue
        cached_at, cached_schemas, cached_raw = entry
        if (now_mono - cached_at) > _ROUTE_ANALYSIS_CACHE_TTL_SEC:
            _ROUTE_ANALYSIS_CACHE.pop(key, None)
            continue
        if route_id and not any(s.routeId == route_id for s in cached_schemas):
            continue
        return copy.deepcopy(cached_schemas), copy.deepcopy(cached_raw)
    return None


async def _build_fast_routes_from_request(
    db: AsyncSession,
    order: Order,
    drone: Drone,
    request: MissionCreateRequest,
    requested_battery_id: Optional[str],
    total_payload_kg: float,
    eff_origin_hub_id: Optional[int],
    compat_models_map: dict[str, list[str]],
    reserved_bat_ids: set[str],
    extra_battery_ids: Optional[set[str]] = None,
    preloaded_batteries_out: Optional[dict[str, Battery]] = None,
) -> Optional[tuple[list[RouteOptionSchema], list[dict]]]:
    """
    Fast-path when FE submits the reviewed route snapshot (`legs_detail`) during Assign/Create Mission.
    Skips re-running Dijkstra & external weather fetches while validating live DB Drone/Battery safety.
    """
    if not request.legs_detail:
        return None

    all_hubs = await get_all_hubs_cached(db)
    hub_by_id = {h.id: h for h in all_hubs}
    hub_by_code: dict[str, Any] = {}
    for h in all_hubs:
        if h.code:
            hub_by_code[h.code.strip().upper()] = h
        if h.name:
            hub_by_code[h.name.strip().upper()] = h

    legs_raw: list[dict] = []
    legs_schemas: list[RouteLegSchema] = []
    tot_dist_km = 0.0
    tot_energy_wh = 0.0

    for idx, leg_in in enumerate(request.legs_detail):
        h_from = hub_by_id.get(leg_in.from_hub_id) if leg_in.from_hub_id else None
        if not h_from and leg_in.from_hub_code:
            h_from = hub_by_code.get(leg_in.from_hub_code.strip().upper())
        h_to = hub_by_id.get(leg_in.to_hub_id) if leg_in.to_hub_id else None
        if not h_to and leg_in.to_hub_code:
            h_to = hub_by_code.get(leg_in.to_hub_code.strip().upper())

        if not h_from or not h_to:
            return None

        d_km = float(leg_in.distance_km or 0.0)
        if d_km <= 0 and h_from.latitude is not None and h_from.longitude is not None and h_to.latitude is not None and h_to.longitude is not None:
            d_km = round(haversine_distance(h_from.latitude, h_from.longitude, h_to.latitude, h_to.longitude), 2)

        is_disp = bool(leg_in.is_dispatch or (str(leg_in.leg_type or "").upper() == "DISPATCH"))
        leg_payload = 0.0 if is_disp else (float(leg_in.payload_kg) if leg_in.payload_kg is not None else total_payload_kg)
        e_wh = float(leg_in.energy_wh or 0.0)
        if e_wh <= 0:
            e_wh = float(predict_flight_energy_cached(d_km, leg_payload)["energy_wh"])

        tot_dist_km += d_km
        tot_energy_wh += e_wh
        from_code = h_from.code or h_from.name
        to_code = h_to.code or h_to.name

        req_bat_for_leg = leg_in.battery_id
        if idx > 0 and request.relay_battery_ids:
            req_bat_for_leg = (
                request.relay_battery_ids.get(str(h_from.id))
                or request.relay_battery_ids.get(str(from_code))
                or req_bat_for_leg
            )

        leg_dict = {
            "from_hub_id": h_from.id,
            "to_hub_id": h_to.id,
            "from_hub_code": from_code,
            "to_hub_code": to_code,
            "distance_km": round(d_km, 2),
            "energy_wh": round(e_wh, 2),
            "battery_id": str(req_bat_for_leg).strip() if req_bat_for_leg else None,
            "leg_type": "DISPATCH" if is_disp else (leg_in.leg_type or "DELIVERY"),
            "is_dispatch": is_disp,
            "payload_kg": leg_payload,
            "h_from": h_from,
            "h_to": h_to,
        }
        legs_raw.append(leg_dict)

    is_cross_hub = bool(legs_raw[0]["is_dispatch"] or (request.routeId and str(request.routeId).endswith("-DISPATCH")))
    departure_hub_id = legs_raw[0]["from_hub_id"] or eff_origin_hub_id or order.origin_hub_id

    # Validate Origin/Leg-1 battery live against DB (and preload requested/relay batteries in 1 query)
    extra_ids = {str(bid).strip() for bid in (extra_battery_ids or set()) if str(bid).strip()}
    if requested_battery_id:
        extra_ids.add(requested_battery_id)
    for lr in legs_raw:
        if lr.get("battery_id"):
            extra_ids.add(str(lr["battery_id"]).strip())

    bat_filters = [
        Battery.drone_id == drone.id,
        and_(Battery.status == batteries_status.ACTIVE, Battery.current_hub_id == departure_hub_id),
    ]
    if extra_ids:
        bat_filters.append(Battery.id.in_(list(extra_ids)))
    fetched_bats = list((await db.execute(select(Battery).where(or_(*bat_filters)))).scalars().all())
    if preloaded_batteries_out is not None:
        for b in fetched_bats:
            preloaded_batteries_out[b.id] = b
    fetched_bats_by_id = {b.id: b for b in fetched_bats}

    active_dep_bats = [
        b for b in fetched_bats
        if b.status == batteries_status.ACTIVE or (requested_battery_id and b.id == requested_battery_id)
    ]
    compat_models, assigned_bat, bat_cap, bat_charge, bat_energy, model_default_cap = await _resolve_assigned_drone_battery(
        db=db,
        origin_hub_id=departure_hub_id,
        drone_obj=drone,
        requested_battery_id=requested_battery_id,
        all_active_batteries=active_dep_bats,
        reserved_battery_ids=reserved_bat_ids,
        strict=True,
        compat_models_map=compat_models_map,
    )

    feasible = True
    infeasible_reasons: list[str] = []
    if assigned_bat is None:
        feasible = False
        infeasible_reasons.append(f"Origin hub has no compatible battery available to assign to Drone #{drone.id}.")

    running_bat_state: dict[str, dict[str, Any]] = {}
    for idx, leg_dict in enumerate(legs_raw):
        e_cur = float(leg_dict["energy_wh"] or 0.0)
        if idx == 0:
            leg_bat_obj = assigned_bat
            leg_bat_id = assigned_bat.id if assigned_bat else requested_battery_id
        else:
            leg_bat_id = leg_dict.get("battery_id")
            leg_bat_obj = fetched_bats_by_id.get(leg_bat_id) if leg_bat_id else None

        reused_prior_bat = (leg_bat_obj is not None and str(leg_bat_obj.id) in running_bat_state)
        l_cap, l_start_pct, l_start_wh, l_cons_pct, l_rem_pct, l_rem_wh = _apply_leg_battery_step(
            e_cur, leg_bat_obj, model_default_cap, running_bat_state
        )

        if leg_bat_obj is not None:
            init_wh = float(running_bat_state[str(leg_bat_obj.id)]["initial_energy_wh"])
            max_safe_wh = round((init_wh if reused_prior_bat else l_start_wh) * 0.8, 2)
            if e_cur > l_start_wh:
                feasible = False
                infeasible_reasons.append(
                    f"Leg {idx + 1} required energy ({e_cur:.1f} Wh) exceeds available energy of battery {leg_bat_obj.id} "
                    f"({l_start_wh:.1f} Wh / {l_cap:.1f} Wh, charge {l_start_pct}%)."
                )
            elif reused_prior_bat and l_rem_wh < round(init_wh * 0.2, 2):
                feasible = False
                infeasible_reasons.append(
                    f"Leg {idx + 1} leaves only {l_rem_wh:.1f} Wh on continued battery {leg_bat_obj.id}, below the 20% safety reserve ({init_wh * 0.2:.1f} Wh)."
                )
            elif not reused_prior_bat and e_cur > max_safe_wh:
                feasible = False
                infeasible_reasons.append(
                    f"Leg {idx + 1} consumes {e_cur:.1f} Wh (~{l_cons_pct}% of battery {leg_bat_obj.id}), "
                    f"exceeding the 80% safe threshold ({max_safe_wh:.1f} Wh / {l_start_wh:.1f} Wh, minimum 20% reserve required)."
                )

        h_from = leg_dict.pop("h_from")
        h_to = leg_dict.pop("h_to")
        leg_dict["battery_id"] = leg_bat_id
        leg_dict["battery_serial"] = leg_bat_obj.serial_number if leg_bat_obj else None
        leg_dict["battery_model"] = leg_bat_obj.battery_model if leg_bat_obj else None
        leg_dict["battery_capacity_wh"] = round(l_cap, 2)
        leg_dict["battery_charge_pct"] = l_start_pct
        leg_dict["battery_energy_wh"] = l_start_wh
        leg_dict["battery_consumption_pct"] = l_cons_pct
        leg_dict["remaining_battery_pct"] = l_rem_pct

        legs_schemas.append(
            RouteLegSchema(
                from_hub_code=leg_dict["from_hub_code"],
                to_hub_code=leg_dict["to_hub_code"],
                distance_km=leg_dict["distance_km"],
                sequence_no=idx + 1,
                from_hub_id=leg_dict["from_hub_id"],
                to_hub_id=leg_dict["to_hub_id"],
                from_hub_name=h_from.name,
                to_hub_name=h_to.name,
                from_latitude=h_from.latitude,
                from_longitude=h_from.longitude,
                to_latitude=h_to.latitude,
                to_longitude=h_to.longitude,
                energy_wh=leg_dict["energy_wh"],
                battery_id=leg_bat_id,
                battery_serial=leg_dict["battery_serial"],
                battery_model=leg_dict["battery_model"],
                battery_capacity_wh=leg_dict["battery_capacity_wh"],
                battery_charge_pct=l_start_pct,
                battery_energy_wh=l_start_wh,
                battery_consumption_pct=l_cons_pct,
                remaining_battery_pct=l_rem_pct,
                leg_type=leg_dict["leg_type"],
                is_dispatch=leg_dict["is_dispatch"],
                payload_kg=leg_dict["payload_kg"],
            )
        )

    infeasible_reason = " | ".join(infeasible_reasons) if infeasible_reasons else None
    final_dist_km = round(float(request.distance_km), 2) if request.distance_km and request.distance_km > 0 else round(tot_dist_km, 2)
    final_energy_wh = round(float(request.predicted_energy_wh), 2) if request.predicted_energy_wh and request.predicted_energy_wh > 0 else round(tot_energy_wh, 2)
    final_duration_min = int(request.predicted_duration_min) if request.predicted_duration_min and request.predicted_duration_min > 0 else max(3, int(math.ceil(final_dist_km * 2.2)) + len(legs_raw))
    route_str = request.route_string or " -> ".join([legs_raw[0]["from_hub_code"]] + [l["to_hub_code"] for l in legs_raw])
    batt_pct, rem_pct = _summarize_mission_battery_pcts(
        legs_detail=legs_schemas,
        origin_charge_pct=bat_charge if assigned_bat else None,
        pred_energy_wh=final_energy_wh,
        origin_energy_wh=bat_energy,
    )
    risk = "LOW" if batt_pct < 60 else ("MEDIUM" if batt_pct <= 80 else "HIGH")

    fast_relay_hubs, fast_relay_statuses = _build_relay_summaries_from_legs(
        legs_detail=legs_schemas,
        hub_by_id=hub_by_id,
        battery_by_id=fetched_bats_by_id,
        fallback_cap_wh=model_default_cap,
    )

    schema = RouteOptionSchema(
        routeId=request.routeId or f"R-{order.id}-FAST-ASSIGN",
        route_string=route_str,
        legs_detail=legs_schemas,
        distanceKm=final_dist_km,
        relayHubs=fast_relay_hubs,
        predictedDurationMin=final_duration_min,
        predictedEnergyWh=final_energy_wh,
        batteryConsumptionPct=batt_pct,
        remainingBatteryPct=rem_pct,
        confidencePct=90 if risk == "LOW" else (85 if risk == "MEDIUM" else 75),
        risk=risk,
        recommended=True,
        suggestedDroneId=drone.id,
        assignedBatteryId=assigned_bat.id if assigned_bat else requested_battery_id,
        assignedBatterySerial=assigned_bat.serial_number if assigned_bat else None,
        assignedBatteryModel=assigned_bat.battery_model if assigned_bat else None,
        assignedBatteryCapacityWh=round(bat_cap, 2) if assigned_bat else None,
        assignedBatteryChargePct=bat_charge if assigned_bat else None,
        assignedBatteryEnergyWh=round(bat_energy, 2) if assigned_bat else None,
        relay_battery_status=fast_relay_statuses,
        feasible=feasible,
        infeasible_reason=infeasible_reason,
        is_selectable=feasible,
        unselectable_reason=infeasible_reason,
        is_cross_hub=is_cross_hub,
    )
    return [schema], [{"legs_detail": legs_raw, "route_string": route_str}]


def _build_created_mission_response(
    mission: Mission,
    drone: Drone,
    assigned_battery: Battery,
    created_legs: list[MissionLeg],
    hub_by_id: dict[int, Any],
    battery_by_id: dict[str, Battery],
    selected_schema: Optional[RouteOptionSchema],
    ai_pred: AIPrediction,
    all_mission_order_ids: list[str],
) -> MissionResponse:
    """Build MissionResponse directly from in-memory objects created during _create_mission_internal."""
    m_dict = {c.name: getattr(mission, c.name) for c in mission.__table__.columns}
    payload = float(mission.payload_weight or 0.0)
    drone_cap_wh = float(
        assigned_battery.capacity_wh
        or DEFAULT_DRONE_BATTERY_CAPACITY_WH.get(drone.model or "", 130.0)
    )
    origin_cap, origin_charge, origin_energy = _compute_battery_energy_wh(assigned_battery, drone_cap_wh)

    legs_detail: list[RouteLegSchema] = []
    relay_hubs: list[str] = []
    relay_battery_status_list: list[HubBatteryStatus] = []
    leg_energies: list[float] = []
    running_bat_state: dict[str, dict[str, Any]] = {}

    schema_legs = selected_schema.legs_detail if (selected_schema and selected_schema.legs_detail) else []
    for idx, leg in enumerate(created_legs):
        h1 = hub_by_id.get(leg.from_hub_id)
        h2 = hub_by_id.get(leg.to_hub_id)
        from_code = (h1.code or h1.name) if h1 else str(leg.from_hub_id)
        to_code = (h2.code or h2.name) if h2 else str(leg.to_hub_id)
        s_leg = schema_legs[idx] if idx < len(schema_legs) else None

        d = (
            float(s_leg.distance_km)
            if (s_leg and s_leg.distance_km is not None)
            else (
                haversine_distance(
                    h1.latitude or 0, h1.longitude or 0,
                    h2.latitude or 0, h2.longitude or 0,
                )
                if (h1 and h2)
                else 0.0
            )
        )
        is_dispatch_leg = bool(
            (s_leg and s_leg.is_dispatch)
            or (
                mission.origin_hub_id
                and leg.to_hub_id == mission.origin_hub_id
                and leg.from_hub_id != mission.origin_hub_id
                and idx == 0
                and len(created_legs) > 1
            )
        )
        leg_payload_val = 0.0 if is_dispatch_leg else (payload if payload > 0 else None)
        e = (
            float(s_leg.energy_wh)
            if (s_leg and s_leg.energy_wh is not None)
            else float(predict_flight_energy_cached(d, 0.0 if is_dispatch_leg else payload)["energy_wh"])
        )
        leg_energies.append(e)

        leg_bat_id = str(leg.battery_id) if leg.battery_id else (assigned_battery.id if idx == 0 else None)
        leg_bat_obj = battery_by_id.get(leg_bat_id) if leg_bat_id else None
        leg_cap, leg_charge, leg_energy_avail, leg_pct, leg_rem_pct, _ = _apply_leg_battery_step(
            e, leg_bat_obj, drone_cap_wh, running_bat_state
        )

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
                status="PENDING",
                energy_wh=round(e, 2),
                battery_id=leg_bat_id,
                battery_serial=leg_bat_obj.serial_number if leg_bat_obj else None,
                battery_model=leg_bat_obj.battery_model if leg_bat_obj else None,
                battery_capacity_wh=round(leg_cap, 2),
                battery_charge_pct=leg_charge,
                battery_energy_wh=leg_energy_avail,
                battery_consumption_pct=leg_pct,
                remaining_battery_pct=leg_rem_pct,
                leg_type="DISPATCH" if is_dispatch_leg else "DELIVERY",
                is_dispatch=is_dispatch_leg,
                payload_kg=leg_payload_val,
            )
        )

    relay_hubs, relay_battery_status_list = _build_relay_summaries_from_legs(
        legs_detail=legs_detail,
        hub_by_id=hub_by_id,
        battery_by_id=battery_by_id,
        fallback_cap_wh=drone_cap_wh,
    )

    route_codes = [legs_detail[0].from_hub_code] + [l.to_hub_code for l in legs_detail] if legs_detail else []
    route_string = (
        selected_schema.route_string
        if (selected_schema and selected_schema.route_string)
        else (" -> ".join(route_codes) if route_codes else "")
    )
    dist_km = round(mission.distance_m / 1000.0, 2) if mission.distance_m else round(sum(l.distance_km for l in legs_detail), 2)
    pred_energy = round(float(ai_pred.estimated_energy_wh or sum(leg_energies)), 2)
    pred_duration = int(ai_pred.estimated_duration_m or int(dist_km * 2))
    batt_pct, rem_batt_pct = _summarize_mission_battery_pcts(
        legs_detail=legs_detail,
        origin_charge_pct=origin_charge,
        pred_energy_wh=pred_energy,
        origin_energy_wh=origin_energy,
    )
    risk = str(ai_pred.risk_level or "Low").upper()
    conf_pct = 90 if risk == "LOW" else (85 if risk == "MEDIUM" else 75)
    reason = ai_pred.recommendation_content or f"Route {route_string} selected by AI analysis ({pred_energy:.1f} Wh)."
    route_id = (selected_schema.routeId if selected_schema else None) or f"R-{mission.order_id or mission.id}-SCH"

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
        suggestedDroneId=mission.drone_id,
        assignedBatteryId=assigned_battery.id,
        assignedBatterySerial=assigned_battery.serial_number,
        assignedBatteryModel=assigned_battery.battery_model,
        assignedBatteryCapacityWh=round(origin_cap, 2),
        assignedBatteryChargePct=origin_charge,
        assignedBatteryEnergyWh=round(origin_energy, 2),
        relay_battery_status=relay_battery_status_list,
    )

    m_dict.update({
        "battery_id": assigned_battery.id,
        "battery_serial": assigned_battery.serial_number,
        "battery_model": assigned_battery.battery_model,
        "battery_capacity_wh": round(origin_cap, 2),
        "battery_charge_pct": origin_charge,
        "battery_energy_wh": round(origin_energy, 2),
        "assignedBatteryId": assigned_battery.id,
        "assignedBatterySerial": assigned_battery.serial_number,
        "assignedBatteryModel": assigned_battery.battery_model,
        "assignedBatteryCapacityWh": round(origin_cap, 2),
        "assignedBatteryChargePct": origin_charge,
        "assignedBatteryEnergyWh": round(origin_energy, 2),
        "relay_battery_status": relay_battery_status_list,
        "route_path_string": route_string,
        "routeId": route_id,
        "route_string": route_string,
        "legs_detail": legs_detail,
        "distanceKm": dist_km,
        "relayHubs": relay_hubs,
        "predictedDurationMin": pred_duration,
        "predictedEnergyWh": pred_energy,
        "batteryConsumptionPct": batt_pct,
        "remainingBatteryPct": rem_batt_pct,
        "confidencePct": conf_pct,
        "risk": risk,
        "recommended": True,
        "reason": reason,
        "suggestedDroneId": mission.drone_id,
        "scheduled_route": scheduled_route,
        "routes": [scheduled_route],
        "bundled_order_ids": all_mission_order_ids,
        "total_orders_count": len(all_mission_order_ids),
        "route_override_info": extract_mission_override_and_compensation(mission).get("route_override"),
        "late_delivery_compensation": extract_mission_override_and_compensation(mission).get("late_delivery_compensation"),
    })
    return MissionResponse(**m_dict)


async def _create_mission_internal(
    db: AsyncSession,
    user: CurrentUser,
    request: MissionCreateRequest,
    recommend_mode: str = "AUTO",
    precomputed_routes: Optional[tuple] = None,
    custom_recommendation_content: Optional[str] = None,
) -> MissionResponse:
    """
    Hàm lõi dùng chung cho cả tạo Mission thủ công (`POST /operator/missions`)
    và Tự động điều phối (`POST /operator/missions/auto-dispatch`).
    [PERF NOTE] Để giữ tốc độ Assign nhanh qua Cloudflare Tunnel + Supabase:
    1. Ưu tiên đọc từ _ROUTE_ANALYSIS_CACHE hoặc _build_fast_routes_from_request (từ legs_detail) trước khi tính lại full.
    2. Build MissionResponse ngay trong RAM (_build_created_mission_response) sau khi commit, không gọi lại build_mission_detail_response.
    3. Gửi email ở nền (_schedule_mission_email_background), không block response API.
    """
    clean_order_id = request.orderId.strip() if request.orderId else request.orderId
    od_res = await db.execute(
        select(Order, Drone)
        .outerjoin(Drone, Drone.id == request.droneId)
        .where(Order.id == clean_order_id)
    )
    od_row = od_res.first()
    if not od_row:
        raise HTTPException(status_code=404, detail="Order not found")
    order, drone = od_row[0], od_row[1]

    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Order has not been paid")

    if order.status != OrderStatus.AT_HUB:
        raise HTTPException(
            status_code=400,
            detail=f"Order is currently in '{order.status.value}' status. A mission can only be created once a Technician confirms package receipt at the hub (AT_HUB)."
        )

    active_statuses = {
        MissionStatus.SCHEDULED,
        MissionStatus.FLYING,
    }
    active_status_list = list(active_statuses)

    # Gộp check Order active mission, Order incident chưa thu hồi, Drone bận, eff_origin_hub_id, và reserved batteries trong 1 query
    rel_missions_res = await db.execute(
        select(
            Mission,
            MissionLeg.id,
            MissionLeg.battery_id,
            MissionLeg.sequence_no,
            MissionLeg.status,
            MissionLeg.from_hub_id,
        )
        .outerjoin(MissionLeg, MissionLeg.mission_id == Mission.id)
        .where(
            or_(
                Mission.order_id == order.id,
                Mission.drone_id == request.droneId,
                Mission.status.in_(_ACTIVE_HOLD_MISSION_STATUSES),
            )
        )
        .order_by(Mission.id.desc())
    )
    rel_rows = rel_missions_res.all()
    seen_missions: dict[str, Mission] = {}
    hold_rows = []
    for m_obj, leg_id, leg_bat_id, leg_seq, leg_stat, from_hub_id in rel_rows:
        if m_obj.id not in seen_missions:
            seen_missions[m_obj.id] = m_obj
        if m_obj.status in active_statuses:
            hold_rows.append(
                (m_obj.id, m_obj.drone_id, m_obj.battery_id, leg_id, leg_bat_id, leg_seq, leg_stat, from_hub_id, None)
            )

    rel_missions = [
        m_obj for m_obj in seen_missions.values()
        if m_obj.order_id == order.id or m_obj.drone_id == request.droneId
    ]
    order_missions = [m for m in rel_missions if m.order_id == order.id]

    if any(m.status in active_statuses for m in order_missions):
        raise HTTPException(
            status_code=400,
            detail="This order already has a scheduled or active mission"
        )

    if any(
        m.status in (MissionStatus.INCIDENT_RETURN, MissionStatus.FAILED)
        and (m.arrival_confirmed_at is None or m.handling_status is not None)
        for m in order_missions
    ):
        raise HTTPException(
            status_code=400,
            detail="A previous flight for this order encountered an incident or disconnection, and a Technician has not yet confirmed drone/package recovery at a hub. Please wait for the Technician to confirm recovery via confirm-arrival before creating a replacement mission."
        )

    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")

    busy_mission = next((m for m in rel_missions if m.drone_id == drone.id and m.status in active_statuses), None)
    if busy_mission:
        raise HTTPException(
            status_code=400,
            detail=f"Drone #{drone.id} ({drone.name}) is currently assigned to mission {busy_mission.id}."
        )

    # Xác định eff_origin_hub_id trực tiếp từ danh sách order_missions đã tải (tránh query lại DB)
    eff_origin_hub_id = order.origin_hub_id
    last_inc_mission = next(
        (m for m in order_missions if m.status in (MissionStatus.INCIDENT_RETURN, MissionStatus.FAILED)),
        None,
    )
    if (
        last_inc_mission
        and last_inc_mission.destination_hub_id
        and (order.replan_required_at is not None or last_inc_mission.arrival_confirmed_at is not None)
    ):
        newer_completed = any(
            m.id > last_inc_mission.id
            and (m.status in (MissionStatus.ARRIVAL_COMPLETED, MissionStatus.MISSION_COMPLETED) or m.status in active_statuses)
            for m in order_missions
        )
        if not newer_completed and last_inc_mission.destination_hub_id != order.destination_hub_id:
            eff_origin_hub_id = last_inc_mission.destination_hub_id

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
        if bundled_ids:
            b_res = await db.execute(select(Order).where(Order.id.in_(bundled_ids)))
            b_by_id = {b.id: b for b in b_res.scalars().all()}
            for bid in bundled_ids:
                if bid in b_by_id:
                    bundled_order_objs.append(b_by_id[bid])

    compat_models_map = await get_compatible_battery_models_map(db)
    reserved_bat_ids, _ = _parse_reserved_and_holds_rows(
        rows=hold_rows,
        relay_hub_set=set(),
    )

    preloaded_fast_bats: dict[str, Battery] = {}
    preloaded_drone_id: Optional[str] = None

    # Tái hiện lộ trình nhanh từ cache Analyze hoặc snapshot legs_detail; chỉ tính lại toàn bộ khi fallback
    if precomputed_routes is not None:
        routes_schemas, raw_data_list = precomputed_routes
    else:
        cached_routes = _get_cached_routes_for_create(
            order_id=order.id,
            drone_id=request.droneId,
            requested_battery_id=requested_battery_id,
            total_payload_kg=total_bundle_payload,
            recommend_mode=recommend_mode,
            route_id=request.routeId,
        )
        if cached_routes is not None:
            routes_schemas, raw_data_list = cached_routes
        else:
            req_bat_ids: set[str] = set()
            if requested_battery_id:
                req_bat_ids.add(requested_battery_id)
            if request.relay_battery_ids:
                for v in request.relay_battery_ids.values():
                    if v:
                        req_bat_ids.add(str(v).strip())
            if request.legs_detail:
                for req_leg in request.legs_detail:
                    rl_bat = getattr(req_leg, "battery_id", None)
                    if rl_bat:
                        req_bat_ids.add(str(rl_bat).strip())

            fast_routes = await _build_fast_routes_from_request(
                db=db,
                order=order,
                drone=drone,
                request=request,
                requested_battery_id=requested_battery_id,
                total_payload_kg=total_bundle_payload,
                eff_origin_hub_id=eff_origin_hub_id,
                compat_models_map=compat_models_map,
                reserved_bat_ids=reserved_bat_ids,
                extra_battery_ids=req_bat_ids,
                preloaded_batteries_out=preloaded_fast_bats,
            )
            if fast_routes is not None:
                preloaded_drone_id = drone.id
                routes_schemas, raw_data_list = fast_routes
            else:
                routes_schemas, raw_data_list = await compute_mission_routes_internal(
                    db=db,
                    order=order,
                    drone_id=request.droneId,
                    battery_id=requested_battery_id,
                    strict_battery=True,
                    override_payload_kg=total_bundle_payload,
                    recommend_mode=recommend_mode,
                    preloaded_context={
                        "eff_origin_hub_id": eff_origin_hub_id,
                        "compat_models_map": compat_models_map,
                    },
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
                f"Cannot create mission: Route '{selected_schema.route_string}' is infeasible. "
                f"{selected_schema.infeasible_reason or 'No compatible battery at relay hub or exceeds the 20% safety reserve.'} "
                f"Please select another feasible route or a battery with sufficient energy before assigning."
            )
        )

    # Nếu Operator chọn Option 3 Điều phối liên trạm (-DISPATCH), đồng bộ đúng Drone hỗ trợ tại trạm lân cận
    assigned_drone_id = drone.id
    if (
        selected_schema
        and selected_schema.is_cross_hub
        and selected_schema.suggestedDroneId
        and request.routeId
        and str(request.routeId).endswith("-DISPATCH")
        and str(selected_schema.suggestedDroneId) != str(drone.id)
    ):
        disp_drone = await db.get(Drone, str(selected_schema.suggestedDroneId))
        if disp_drone:
            drone = disp_drone
            assigned_drone_id = disp_drone.id

    battery_id = requested_battery_id or (selected_schema.assignedBatteryId if selected_schema else None)
    legs = selected_data.get("legs_detail", []) if selected_data else []

    # Nạp 1 lượt tất cả Battery liên quan (pin xuất phát, pin đang gắn trên drone, và các pin relay override)
    bat_ids_to_load: set[str] = set()
    if battery_id:
        bat_ids_to_load.add(battery_id)
    if request.relay_battery_ids:
        for v in request.relay_battery_ids.values():
            if v:
                bat_ids_to_load.add(str(v).strip())
    for idx, leg_item in enumerate(legs):
        if idx > 0 and leg_item.get("battery_id"):
            bat_ids_to_load.add(str(leg_item["battery_id"]).strip())
        if request.legs_detail and idx < len(request.legs_detail):
            req_leg_bat = getattr(request.legs_detail[idx], "battery_id", None)
            if req_leg_bat:
                bat_ids_to_load.add(str(req_leg_bat).strip())

    # Tái sử dụng Battery đã preload từ fast-path (nếu cùng drone), chỉ query bổ sung phần còn thiếu
    if preloaded_drone_id == drone.id and preloaded_fast_bats:
        battery_by_id = dict(preloaded_fast_bats)
        missing_bat_ids = [bid for bid in bat_ids_to_load if bid not in battery_by_id]
        if missing_bat_ids:
            extra_loaded = list((await db.execute(select(Battery).where(Battery.id.in_(missing_bat_ids)))).scalars().all())
            for b in extra_loaded:
                battery_by_id[b.id] = b
        loaded_bats = list(battery_by_id.values())
    else:
        bat_load_filters = [Battery.drone_id == drone.id]
        if bat_ids_to_load:
            bat_load_filters.append(Battery.id.in_(list(bat_ids_to_load)))
        loaded_bats = list((await db.execute(select(Battery).where(or_(*bat_load_filters)))).scalars().all())
        battery_by_id: dict[str, Battery] = {b.id: b for b in loaded_bats}

    assigned_battery = battery_by_id.get(battery_id) if battery_id else None
    if not assigned_battery:
        raise HTTPException(
            status_code=400,
            detail=f"No compatible battery available at Hub #{eff_origin_hub_id or order.origin_hub_id} to assign to Drone #{drone.id} ({drone.model})."
        )

    compatible_models = resolve_compatible_models_from_map(compat_models_map, drone.model)

    # Kiểm tra an toàn pin xuất phát trực tiếp với dữ liệu DB thực tế (kể cả khi đi qua fast-path/cache)
    if assigned_battery.status != batteries_status.ACTIVE or (assigned_battery.charge_level_pct or 0) <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"Battery {assigned_battery.id} is not ready (status: {assigned_battery.status}, charge: {assigned_battery.charge_level_pct or 0}%)."
        )
    if compatible_models and assigned_battery.battery_model and assigned_battery.battery_model not in compatible_models:
        raise HTTPException(
            status_code=400,
            detail=f"Battery {assigned_battery.id} ({assigned_battery.battery_model}) is incompatible with {drone.model}."
        )
    if assigned_battery.id in reserved_bat_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Battery {assigned_battery.id} is already reserved for another active mission."
        )
    if legs:
        leg0_req_energy = float(legs[0].get("energy_wh") or 0.0)
        _, _, origin_bat_energy_live = _compute_battery_energy_wh(assigned_battery)
        if leg0_req_energy > origin_bat_energy_live * 0.8:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Battery {assigned_battery.id} ({origin_bat_energy_live:.1f} Wh, charge {assigned_battery.charge_level_pct}%) "
                    f"lacks sufficient safe energy (max {origin_bat_energy_live * 0.8:.1f} Wh) for Leg 1 ({leg0_req_energy:.1f} Wh, minimum 20% reserve required)."
                )
            )

    # Gắn liên kết viên pin được chọn với Drone thực hiện nhiệm vụ
    if assigned_battery.drone_id != drone.id:
        for ob in loaded_bats:
            if ob.drone_id == drone.id and ob.id != assigned_battery.id:
                ob.drone_id = None
        assigned_battery.drone_id = drone.id

    if assigned_battery.charge_level_pct is not None:
        drone.battery_level_pct = assigned_battery.charge_level_pct

    mission_id = await generate_sequential_id(db, Mission, "MSN")

    hub_list = await get_all_hubs_cached(db)
    hub_by_id = {h.id: h for h in hub_list}
    orig_hub_obj = hub_by_id.get(eff_origin_hub_id or order.origin_hub_id)
    dest_hub_obj = hub_by_id.get(order.destination_hub_id)

    # Kiểm tra ràng buộc lý do khi chọn Route khác Delivery Mode (VD: EXPRESS nhưng chọn ECO/DETOUR)
    route_override_entry = await validate_and_build_route_override_entry(
        orders_list=bundled_order_objs,
        selected_schema=selected_schema,
        route_id=request.routeId,
        reason_type=getattr(request, "route_override_reason_type", None),
        note=getattr(request, "route_override_note", None),
        operator_id=user.id,
        origin_hub=orig_hub_obj,
        dest_hub=dest_hub_obj,
    )

    total_dist_km = selected_schema.distanceKm if selected_schema else 0.0
    total_energy_wh = selected_schema.predictedEnergyWh if selected_schema else 0.0
    duration_min = selected_schema.predictedDurationMin if selected_schema else 0
    route_str = selected_schema.route_string if selected_schema else ""

    # 1. Tạo bản ghi Mission (dùng eff_origin_hub_id để đồng bộ Hub xuất phát khi Replan, vẫn giữ nguyên order.origin_hub_id)
    mission = Mission(
        id=mission_id,
        order_id=order.id,
        customer_id=order.customer_id,
        drone_id=assigned_drone_id,
        battery_id=assigned_battery.id,
        origin_hub_id=eff_origin_hub_id or order.origin_hub_id,
        destination_hub_id=order.destination_hub_id,
        payload_weight=total_bundle_payload,
        distance_m=round(total_dist_km * 1000, 2),
        delivery_fee=total_delivery_fee,
        scheduled_time=order.requested_delivery_at or get_vn_naive_datetime(),
        status=MissionStatus.SCHEDULED,
        handling_status=HandlingStatus.AT_HUB,
        operator_id=user.id,
        checkpoints_json=[route_override_entry] if route_override_entry else [],
    )
    db.add(mission)

    if route_override_entry:
        db.add(ActivityLog(
            user_id=user.id,
            user_email=getattr(user, "email", None),
            role="OPERATOR",
            action="ROUTE_OVERRIDE",
            resource_type="mission",
            resource_id=mission_id,
            description=(
                f"Operator overrode EXPRESS route to {route_override_entry.get('selected_strategy_code')} "
                f"(reason: {route_override_entry.get('reason_type')})."
            ),
            status="success",
            details_json=route_override_entry,
            created_at=get_vn_datetime(),
        ))

    # 2. Tạo các chặng bay (MissionLeg) và chỉ định chính xác viên pin cho từng chặng (kể cả Relay Hub)
    used_mission_bat_ids: set[str] = {assigned_battery.id}
    relay_swap_summary_parts: list[str] = []
    created_legs: list[MissionLeg] = []
    now_naive = get_vn_naive_datetime()

    for idx, leg_item in enumerate(legs):
        if idx == 0:
            leg_battery_id = assigned_battery.id
        else:
            relay_hub_id = leg_item.get("from_hub_id")
            relay_hub_code = leg_item.get("from_hub_code") or str(relay_hub_id)
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
            is_dispatch_same_bat = bool(
                candidate_bat_id
                and candidate_bat_id == assigned_battery.id
                and all(bool(legs[k].get("is_dispatch")) for k in range(idx))
            )
            if is_dispatch_same_bat:
                _, _, origin_bat_energy_live = _compute_battery_energy_wh(assigned_battery)
                cum_req_wh = sum(float(legs[k].get("energy_wh") or 0.0) for k in range(idx + 1))
                if cum_req_wh > origin_bat_energy_live * 0.8:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Battery {assigned_battery.id} ({origin_bat_energy_live:.1f} Wh, charge {assigned_battery.charge_level_pct}%) "
                            f"cannot continue through Leg {idx + 1} without swap (cumulative {cum_req_wh:.1f} Wh exceeds 80% safe threshold)."
                        )
                    )
                leg_battery_id = assigned_battery.id
            elif override_bat_id:
                relay_bat = battery_by_id.get(candidate_bat_id) if candidate_bat_id else None
                if not relay_bat:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Swap battery '{candidate_bat_id}' not found for relay Hub {relay_hub_code}."
                    )
                if relay_bat.current_hub_id != relay_hub_id:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Swap battery {relay_bat.id} is not located at relay Hub {relay_hub_code} (Hub #{relay_hub_id})."
                    )
                if relay_bat.status != batteries_status.ACTIVE or (relay_bat.charge_level_pct or 0) <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Swap battery {relay_bat.id} at relay Hub {relay_hub_code} is not ready (charge level: {relay_bat.charge_level_pct or 0}%)."
                    )
                if compatible_models and relay_bat.battery_model and relay_bat.battery_model not in compatible_models:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Swap battery {relay_bat.id} ({relay_bat.battery_model}) at Hub {relay_hub_code} is incompatible with {drone.model}."
                    )
                if relay_bat.id in reserved_bat_ids or relay_bat.id in used_mission_bat_ids:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Swap battery {relay_bat.id} at relay Hub {relay_hub_code} is already reserved for another leg/mission."
                    )
                _, _, relay_bat_energy = _compute_battery_energy_wh(relay_bat)
                leg_req_energy = float(leg_item.get("energy_wh") or 0.0)
                if leg_req_energy > relay_bat_energy * 0.8:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Swap battery {relay_bat.id} ({relay_bat_energy:.1f} Wh, charge {relay_bat.charge_level_pct}%) at Hub {relay_hub_code} "
                            f"lacks sufficient safe energy (max {relay_bat_energy * 0.8:.1f} Wh) for Leg {idx + 1} ({leg_req_energy:.1f} Wh, minimum 20% reserve required)."
                        )
                    )
                leg_battery_id = relay_bat.id
            else:
                leg_battery_id = candidate_bat_id

            if leg_battery_id:
                used_mission_bat_ids.add(leg_battery_id)
                relay_swap_summary_parts.append(f"{relay_hub_code}: battery {leg_battery_id}")

        m_leg = MissionLeg(
            mission_id=mission_id,
            sequence_no=idx + 1,
            from_hub_id=leg_item["from_hub_id"],
            to_hub_id=leg_item["to_hub_id"],
            battery_id=leg_battery_id,
            status=MissionLegStatus.PENDING,
            created_at=now_naive,
            updated_at=now_naive,
        )
        db.add(m_leg)
        created_legs.append(m_leg)

    # 3. Lưu AI Snapshot
    rec_content = (
        custom_recommendation_content
        or (
            selected_schema.reason
            if (selected_schema and selected_schema.reason)
            else (
                f"Route {route_str} selected by AI analysis ({total_energy_wh:.1f} Wh). "
                f"Origin battery: {assigned_battery.id} ({assigned_battery.serial_number})."
                + (f" Relay swap batteries: {', '.join(relay_swap_summary_parts)}." if relay_swap_summary_parts else "")
            )
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
    for o_item in bundled_order_objs:
        o_item.planning_at = now_naive
        o_item.replan_required_at = None

    # 5. Tạo in-app notification cho Customer trong cùng 1 transaction commit
    from app.core.config import settings
    from app.modules.system.models import Notification, NotificationType
    emails_to_send: list[tuple[str, str]] = []
    _eta = (mission.scheduled_time or now_naive).strftime("%H:%M %d/%m") if (mission.scheduled_time or now_naive) else "N/A"
    for o_item in bundled_order_objs:
        if o_item.customer_id:
            db.add(Notification(
                user_id=o_item.customer_id,
                title=f"Order #{o_item.id} Flight Scheduled",
                message=(
                    f"Flight #{mission.id} has been scheduled to deliver your order #{o_item.id}. "
                    f"Estimated ETA: {_eta}. You will be notified upon arrival."
                ),
                notifications_type=NotificationType.MISSION_STATUS,
                mission_id=str(mission.id),
                reference_id=f"MISSION_SCHEDULED_{mission.id}",
                created_at=now_naive,
            ))
        if o_item.receiver_email:
            emails_to_send.append((o_item.receiver_email, o_item.id))

    # hub_list & hub_by_id đã được tải ở trên khi kiểm tra route_override

    # 6. Notify Technicians at origin hub: new mission assigned (strictly inside ACID tx)
    try:
        from app.modules.auth.models import User as _TUser, ROLE_NAME_TO_ID as _ROLE_MAP
        from app.shared.roles import UserRole as _TRole
        _tech_role_id = _ROLE_MAP.get(_TRole.TECHNICIAN)
        if mission.origin_hub_id and _tech_role_id:
            _origin_hub_id_int = int(mission.origin_hub_id)
            _origin_hub = hub_by_id.get(_origin_hub_id_int)
            _origin_hub_name = _origin_hub.name if _origin_hub else f"Hub #{mission.origin_hub_id}"
            _tech_res = await db.execute(
                select(_TUser.id).where(
                    _TUser.role_id == _tech_role_id,
                    _TUser.hub_id == _origin_hub_id_int,
                )
            )
            for _tid in _tech_res.scalars().all():
                db.add(Notification(
                    user_id=_tid,
                    title=f"New Mission Scheduled — {_origin_hub_name}",
                    message=(
                        f"Mission #{mission.id} has been scheduled. "
                        f"Please prepare drone and batteries at your hub."
                    ),
                    notifications_type=NotificationType.MISSION_STATUS,
                    mission_id=str(mission.id),
                    reference_id=f"MISSION_SCHEDULED_TECH_{mission.id}",
                    created_at=now_naive,
                ))
    except Exception as _e:
        print(f"[MISSION] Failed to notify hub technicians: {_e}")

    await db.commit()
    _invalidate_route_analysis_cache()

    # Gửi email thông báo dưới nền (non-blocking) để API Assign/Create Mission trả về tức thì
    for to_email, o_id in emails_to_send:
        _schedule_mission_email_background(
            to_email=to_email,
            order_id=o_id,
            mission_id=mission.id,
            frontend_url=settings.FRONTEND_URL,
        )
    return _build_created_mission_response(
        mission=mission,
        drone=drone,
        assigned_battery=assigned_battery,
        created_legs=created_legs,
        hub_by_id=hub_by_id,
        battery_by_id=battery_by_id,
        selected_schema=selected_schema,
        ai_pred=ai_pred,
        all_mission_order_ids=all_mission_order_ids,
    )


@router.post("", response_model=MissionResponse)
async def create_mission(
    request: MissionCreateRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API] Tạo nhiệm vụ bay (Create Mission)**

    - **Mục đích**: Chính thức xác nhận tạo chuyến bay để giao một Order cụ thể kèm lựa chọn Drone và Pin.
    """
    return await _create_mission_internal(db=db, user=user, request=request)


@router.post(
    "/auto-dispatch/recommend-resources",
    response_model=RecommendResourcesResponse,
    summary="Bước 1: AI tự động chọn Drone & Pin kèm giải thích bằng chữ (SHAP) và cho phép Operator đổi",
)
async def recommend_dispatch_resources(
    request: RecommendResourcesRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API - Bước 1] Gợi ý tự động Drone & Pin kèm giải thích bằng chữ**

    - Tự động chọn Drone & Pin tối ưu nhất dựa trên vị trí Hub, tải trọng, năng lượng dự phòng 20%, độ chai pin và cân bằng hao mòn.
    - Trả về `drone_explanation` và `battery_explanation` hoàn toàn bằng tiếng Anh tự nhiên (từ phân tích SHAP), giải thích rõ **vì sao chọn** và **vì sao không chọn các phương án khác**.
    - Kèm danh sách `available_drones` (chứa sẵn `eligible_batteries` của từng Drone) để Operator tự do thay đổi trước khi sang Bước 2 (`/mission-planning/analyze`).
    """
    return await recommend_resources_for_order(
        db=db,
        order_id=request.orderId,
        bundled_order_ids=request.bundledOrderIds,
        compute_routes_fn=compute_mission_routes_internal,
        get_reserved_batteries_fn=_get_reserved_batteries_and_virtual_holds,
    )


@router.post(
    "/auto-dispatch",
    response_model=AutoDispatchResponse,
    summary="Tự động chọn Drone, Pin, Lộ trình tối ưu và Tạo Mission kèm giải thích XAI SHAP",
)
async def auto_dispatch_mission(
    request: AutoDispatchRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API] Tự động điều phối toàn trình (Auto-Dispatch + XAI SHAP)**

    - **Thay thế hoàn toàn** luồng chọn Drone thủ công, chọn Pin thủ công, chọn Route thủ công và Tạo Mission.
    - **Chống black-box**: Tính và trả về đầy đủ `xai_shap_report` theo chuẩn Additive SHAP
      (`base_value + sum(shap_values) == total_score`) cho cả 3 quyết định (Drone, Pin, Route) kèm
      Physics Energy SHAP (tốc độ, độ cao, quãng đường, tải trọng, gió Open-Meteo) và đối chứng (counterfactuals).
    - Hỗ trợ `dry_run=True` để xem trước kết quả AI và biểu đồ SHAP trước khi tạo bản ghi Mission trong CSDL.
    """
    selection = await run_auto_dispatch_selection(
        db=db,
        order_id=request.orderId,
        bundled_order_ids=request.bundledOrderIds,
        recommend_mode=request.recommend_mode,
        compute_routes_fn=compute_mission_routes_internal,
        get_reserved_batteries_fn=_get_reserved_batteries_and_virtual_holds,
    )

    order = selection["order"]
    best_drone = selection["drone"]
    best_battery = selection["battery"]
    best_route = selection["selected_route"]
    available_routes = selection["available_routes"]
    raw_list = selection["raw_list"]
    xai_report = selection["xai_report"]
    bundled_ids = selection["bundled_order_ids"]

    created_mission: Optional[MissionResponse] = None
    if not request.dry_run:
        create_req = MissionCreateRequest(
            orderId=order.id,
            droneId=best_drone.id,
            batteryId=best_battery.id,
            routeId=best_route.routeId,
            bundledOrderIds=bundled_ids or None,
        )
        created_mission = await _create_mission_internal(
            db=db,
            user=user,
            request=create_req,
            recommend_mode=request.recommend_mode or "AUTO",
            precomputed_routes=(available_routes, raw_list),
            custom_recommendation_content=f"[Auto-Dispatch XAI] {xai_report.executive_summary}",
        )

    is_dry = bool(request.dry_run)
    status_label = "DRY_RUN_PREVIEW" if is_dry else "MISSION_CREATED"
    msg = (
        "Dry-run preview generated. Optimal drone, battery, and route selected with additive XAI SHAP attribution; no mission was created."
        if is_dry
        else f"Mission {created_mission.id} created automatically via AI auto-dispatch with full XAI SHAP explainability."
    )

    return AutoDispatchResponse(
        status=status_label,
        dry_run=is_dry,
        mission_created=not is_dry,
        message=msg,
        order_id=order.id,
        bundled_order_ids=bundled_ids,
        total_payload_kg=selection["total_payload_kg"],
        selected_drone_id=best_drone.id,
        selected_drone_name=best_drone.name,
        selected_drone_model=best_drone.model,
        selected_battery_id=best_battery.id,
        selected_battery_serial=best_battery.serial_number,
        selected_route_id=best_route.routeId,
        selected_route_string=best_route.route_string,
        selected_route=best_route,
        available_routes=available_routes,
        xai_shap_report=xai_report,
        mission=created_mission,
    )


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
                f"Mission must be stopped at a hub (AT_HUB / READY / INCOMING). "
                f"Current status: {mission.handling_status.value if mission.handling_status else 'None'}."
            ),
        )

    if not mission.drone_id:
        raise HTTPException(status_code=400, detail="Mission does not have an assigned drone.")

    drone = await db.get(Drone, str(mission.drone_id))
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found.")

    # Hub hiện tại của drone
    hub_id = drone.current_hub_id
    if not hub_id:
        raise HTTPException(
            status_code=400,
            detail="Drone is not currently at any hub (current_hub_id is None). Cannot swap battery.",
        )

    # ── 2. Validate pin mới ───────────────────────────────────────────────────
    new_battery = await db.get(Battery, body.new_battery_id)
    if not new_battery:
        raise HTTPException(status_code=404, detail=f"Battery '{body.new_battery_id}' not found.")

    if new_battery.status != batteries_status.ACTIVE:
        raise HTTPException(status_code=400, detail=f"Battery {new_battery.id} is not in Active status.")

    if new_battery.current_hub_id != hub_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Battery {new_battery.id} is not at the drone's current hub "
                f"(Hub #{hub_id}). Battery is at Hub #{new_battery.current_hub_id}."
            ),
        )

    if new_battery.drone_id is not None and str(new_battery.drone_id) != str(mission.drone_id):
        raise HTTPException(
            status_code=400,
            detail=f"Battery {new_battery.id} is currently installed on another drone ({new_battery.drone_id}).",
        )

    charge = new_battery.charge_level_pct or 0
    if charge < 80:
        raise HTTPException(
            status_code=400,
            detail=f"Battery {new_battery.id} is at {charge}% — insufficient for safe flight (requires ≥ 80%).",
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
        select(_User.id).where(
            _User.role_id == tech_role_id,
            _User.hub_id == str(hub_id),
        )
    )
    technician_ids = tech_result.scalars().all()

    for tech_id in technician_ids:
        db.add(Notification(
            user_id=tech_id,
            title=f"Operator Requested Battery Swap — Mission #{mission_id}",
            message=(
                f"Operator {user.id} swapped the battery for Mission #{mission_id} at {hub_name}. "
                f"Old battery: {old_battery_id or 'N/A'} → New battery: {new_battery.id} ({charge}%)."
            ),
            notifications_type=NotificationType.MISSION_STATUS,
            mission_id=str(mission.id),
            created_at=now,
        ))

    await db.commit()
    _invalidate_route_analysis_cache()

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
            f"Battery swap succeeded. Mission #{mission_id} at {hub_name}: "
            f"battery {old_battery_id or 'previous'} → {new_battery.id} ({charge}%)."
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
        MissionStatus.SCHEDULED,
    }
    if mission.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Mission is currently in '{mission.status.value}' status — cannot confirm transit. "
                f"Only applicable for active missions."
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
            detail="This is a direct flight (1 leg) — no relay hub requires transit confirmation. "
                   "Use confirm-arrival to complete the flight.",
        )

    # Tìm next_leg PENDING đầu tiên
    next_leg = next(
        (leg for leg in legs if leg.status == MissionLegStatus.PENDING), None
    )
    if next_leg is None:
        raise HTTPException(
            status_code=400,
            detail="No PENDING leg found. All legs have been completed "
                   "or the mission has already ended.",
        )

    # Leg vừa completed (chặng ngay trước next_leg)
    completed_leg = next(
        (leg for leg in reversed(legs) if leg.status == MissionLegStatus.COMPLETED),
        None,
    )
    if completed_leg is None:
        raise HTTPException(
            status_code=400,
            detail="No leg has been completed yet. Technician must run confirm-relay-arrival first.",
        )

    relay_hub_id = completed_leg.to_hub_id
    if not relay_hub_id:
        raise HTTPException(status_code=400, detail="Unable to determine relay hub.")

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
        f"Operator {user.id} confirmed transit at {relay_hub_name}. "
        f"Mission #{mission_id} is now flying leg {next_leg.sequence_no}/{len(legs)} "
        f"to Hub #{next_leg.to_hub_id}."
        + (f" Note: {body.note}" if body.note else "")
    )
    for op_id in operator_ids:
        db.add(Notification(
            user_id=op_id,
            title=f"Transit Confirmed — Mission #{mission_id}",
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
            f"Transit confirmed. Mission #{mission_id} is flying leg "
            f"{next_leg.sequence_no}/{len(legs)} from {relay_hub_name} → Hub #{next_leg.to_hub_id}."
        ),
    )


# ── Emergency Land ────────────────────────────────────────────────────────────

class EmergencyLandRequest(BaseModel):
    reason: str = "Operator requested emergency landing"


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
    }
    if mission.status not in active_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot trigger emergency landing for mission in '{mission.status.value}' status. "
                   f"Only applicable for active missions (Flying/In Progress/Approved)."
        )

    now = get_vn_naive_datetime()
    previous_status = mission.status.value

    # ── 2. Chuyển mission → INCIDENT_RETURN ─────────────────────────────────
    mission.status = MissionStatus.INCIDENT_RETURN
    mission.handling_status = HandlingStatus.CANNOT_CONTINUE
    mission.arrived_at = now
    mission.end_time = now
    mission.arrival_confirmed_at = None

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
        title=f"Emergency Landing — Drone #{mission.drone_id} requires immediate inspection",
        description=(
            f"EMERGENCY LANDING ALERT #{incident.id}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Drone: {mission.drone_id}\n"
            f"Mission: {mission.id}\n"
            f"Order: {mission.order_id}\n"
            f"Landing triggered at: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"\nREASON FOR LANDING:\n"
            f"{body.reason}\n"
            f"\nCRITICAL ACTIONS:\n"
            f"✓ Immediate visual inspection of drone for external damage\n"
            f"✓ Check flight logs, IMU, and sensor data for anomalies\n"
            f"✓ Verify battery health and electrical connections\n"
            f"✓ Run full self-test before next flight\n"
            f"✓ Update maintenance log with findings\n"
            f"\nDO NOT FLY until inspection completed and approved."
        ),
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

    notif_title = f"EMERGENCY LANDING — Mission #{mission_id}"
    notif_msg = (
        f"Operator {user.id} triggered emergency landing for Mission #{mission_id} "
        f"(Drone #{mission.drone_id}). Reason: {body.reason}. "
        f"Drone has been placed in MAINTENANCE mode."
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
            title="Your flight encountered an incident",
            message=(
                f"Flight #{mission_id} is undergoing emergency incident handling. "
                f"Our team is resolving the issue and will update you as soon as possible."
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
            f"Emergency landing triggered successfully. "
            f"Incident #{incident.id} and MaintenanceAlert #{alert.id} have been created. "
            f"Drone #{mission.drone_id} switched to MAINTENANCE."
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
            f"Critical battery level ({curr_bat:.1f}%) is insufficient to reach any hub safely. "
            f"Triggering controlled vertical emergency landing at a predefined safe zone."
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
                f"Before PNR: Adverse weather or battery drop reduces projected destination battery to {rem_at_dest:.1f}% (< 20%). "
                f"Automatically triggering Return-to-Home (RTH) back to origin hub {origin_hub.name if origin_hub else ''} "
                f"(projected safe landing with {rem_at_origin:.1f}% battery)."
            )
            can_execute = True
        elif recommended_hub:
            # TIER 2: Đã qua PNR -> Dynamic In-flight Divert to Nearest Relay Hub
            status = "DIVERT_TRIGGERED"
            tier = 2
            action = "DIVERT_TO_HUB"
            reason = (
                f"Abnormal battery drain detected ({curr_bat:.1f}%). "
                f"Projected battery at destination is only {rem_at_dest:.1f}% (below 20% safety threshold). "
                f"Automatically triggering emergency divert to {recommended_hub.hub_name} ({recommended_hub.distance_km:.2f}km away, "
                f"projected safe landing with {recommended_hub.estimated_remaining_pct:.1f}% battery for charging/swap)."
            )
            can_execute = True
        else:
            status = "EMERGENCY_LAND_TRIGGERED"
            tier = 3
            action = "EMERGENCY_LAND"
            reason = "No fallback hub available within safe range. Triggering emergency landing."
            can_execute = True
    else:
        # TIER 0: An toàn hoặc Cảnh báo nhẹ
        if rem_at_dest < 25.0 or wind_warning:
            status = "WARNING"
            tier = 0
            action = "CONTINUE"
            reason = (
                f"Warning: Projected battery at destination is {rem_at_dest:.1f}% (approaching 20% safety threshold). "
                f"{'Strong wind ' + str(wind_spd) + 'm/s (> 8m/s). ' if wind_warning else ''}"
                f"System is maintaining continuous telemetry monitoring."
            )
            can_execute = False
        else:
            status = "SAFE"
            tier = 0
            action = "CONTINUE"
            reason = f"Flight is stable. Projected battery upon arrival at destination hub is {rem_at_dest:.1f}% (meets >= 20% safety standard)."
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
    }
    if mission.status not in active_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot execute failsafe for mission in '{mission.status.value}' status. "
                   f"Only applicable for active missions (Flying/In Progress/Approved)."
        )

    now = get_vn_naive_datetime()
    previous_status = mission.status.value if hasattr(mission.status, "value") else str(mission.status)

    # 1. Xử lý hạ cánh khẩn cấp (EMERGENCY_LAND)
    if body.action == "EMERGENCY_LAND":
        mission.status = MissionStatus.INCIDENT_RETURN
        mission.handling_status = HandlingStatus.CANNOT_CONTINUE
        mission.arrived_at = now
        mission.end_time = now
        mission.arrival_confirmed_at = None

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
            title=f"Failsafe Emergency Landing — Drone #{mission.drone_id} requires immediate inspection",
            description=(
                f"FAILSAFE EMERGENCY LANDING ALERT #{incident.id}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Drone: {mission.drone_id}\n"
                f"Mission: {mission.id}\n"
                f"Order: {mission.order_id}\n"
                f"Failsafe triggered at: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"\nFAILSAFE TRIGGER REASON:\n"
                f"{body.reason}\n"
                f"\nSYSTEM DIAGNOSIS REQUIRED:\n"
                f"✓ Check failsafe system logs and telemetry data\n"
                f"✓ Inspect drone for physical damage\n"
                f"✓ Verify GPS, compass, and barometer calibration\n"
                f"✓ Test remote link and failsafe thresholds\n"
                f"✓ Update firmware if necessary\n"
                f"✓ Perform complete pre-flight check\n"
                f"\nDO NOT FLY until root cause identified and resolved."
            ),
            status="Pending",
            created_at=now,
        )
        db.add(alert)
        await db.flush()

        if mission.drone_id:
            drone = await db.get(Drone, str(mission.drone_id))
            if drone:
                drone.status = DroneStatus.MAINTENANCE

        # Bắn Notification khẩn cho Operator, Admin và Customer khi kích hoạt EMERGENCY_LAND
        from app.shared.workers import get_operator_and_admin_ids
        now_tz = get_vn_datetime()
        for uid in await get_operator_and_admin_ids(db):
            db.add(Notification(
                user_id=uid,
                title=f"EMERGENCY LANDING — Mission #{mission_id}",
                message=(
                    f"Failsafe EMERGENCY_LAND triggered for Mission #{mission_id} "
                    f"(Drone #{mission.drone_id}). Reason: {body.reason}. Drone switched to MAINTENANCE."
                ),
                notifications_type=NotificationType.SYSTEM_ALERT,
                mission_id=str(mission.id),
                drone_id=str(mission.drone_id) if mission.drone_id else None,
                reference_id=str(incident.id),
                created_at=now_tz,
            ))

        if mission.customer_id:
            db.add(Notification(
                user_id=mission.customer_id,
                title="Flight Emergency Landing — Safety Inspection",
                message=(
                    f"Flight #{mission_id} performed a precautionary emergency landing for safety inspection. "
                    f"Our team is securing your package and will update you shortly."
                ),
                notifications_type=NotificationType.SYSTEM_ALERT,
                mission_id=str(mission.id),
                reference_id=str(incident.id),
                created_at=now_tz,
            ))

        await db.commit()

        return FailsafeExecuteResponse(
            mission_id=mission.id,
            previous_status=previous_status,
            current_status=MissionStatus.INCIDENT_RETURN.value,
            action_taken="EMERGENCY_LAND",
            incident_id=incident.id,
            alert_id=alert.id,
            message="Emergency landing triggered successfully. Drone switched to MAINTENANCE.",
        )

    # 2. Xử lý Chuyển hướng sang Hub lân cận (DIVERT_TO_HUB) hoặc Quay về gốc (RETURN_TO_HOME)
    target_hub_id = body.target_hub_id
    if body.action == "RETURN_TO_HOME":
        target_hub_id = mission.origin_hub_id

    if not target_hub_id:
        raise HTTPException(status_code=400, detail="Missing target_hub_id for emergency divert action.")

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
        description=f"{incident_tag} Mission #{mission_id} automatically diverted to {target_hub.name} (Hub #{target_hub.id}). Reason: {body.reason}",
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
        title=f"Failsafe Divert — Drone #{mission.drone_id} landed at {target_hub.name} for battery inspection",
        description=(
            f"FAILSAFE DIVERT ALERT #{incident.id}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Drone: {mission.drone_id}\n"
            f"Mission: {mission.id}\n"
            f"Order: {mission.order_id}\n"
            f"Divert triggered at: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"\nFLIGHT ROUTE CHANGE:\n"
            f"Original destination: {mission.destination_hub_id if 'mission' in locals() else 'Unknown'}\n"
            f"Emergency divert hub: {target_hub.name} (#{target_hub.id})\n"
            f"\nREASON FOR DIVERT:\n"
            f"{body.reason}\n"
            f"\nMAINTENANCE CHECKLIST:\n"
            f"✓ Inspect battery pack for damage/swelling\n"
            f"✓ Check battery voltage and cell balance\n"
            f"✓ Review power draw during flight\n"
            f"✓ Examine propellers and motor for strain\n"
            f"✓ Clear any error logs from failsafe system\n"
            f"✓ Determine if battery replacement needed\n"
            f"\nDrone grounded until battery inspection completed."
        ),
        status="Pending",
        created_at=now,
    )
    db.add(alert)
    await db.flush()

    # Gửi Thông báo cho Operator, Admin & Customer
    from app.shared.workers import get_operator_and_admin_ids
    recipient_ids = await get_operator_and_admin_ids(db)

    notif_title = f"EMERGENCY DIVERT — Mission #{mission_id}"
    notif_msg = (
        f"Failsafe system triggered a flight divert for Mission #{mission_id} "
        f"(Drone #{mission.drone_id}) to {target_hub.name} (Hub #{target_hub.id}). "
        f"Reason: {body.reason}."
    )
    now_tz = get_vn_datetime()
    for op_id in recipient_ids:
        db.add(Notification(
            user_id=op_id,
            title=notif_title,
            message=notif_msg,
            notifications_type=NotificationType.SYSTEM_ALERT,
            mission_id=str(mission.id),
            drone_id=str(mission.drone_id) if mission.drone_id else None,
            reference_id=str(incident.id),
            created_at=now_tz,
        ))

    if mission.customer_id:
        db.add(Notification(
            user_id=mission.customer_id,
            title="Flight Route Update",
            message=f"Flight #{mission_id} is temporarily diverting to technical hub {target_hub.name} for an optimal safety check.",
            notifications_type=NotificationType.MISSION_STATUS,
            mission_id=str(mission.id),
            created_at=now,
        ))

    await db.commit()
    _invalidate_route_analysis_cache()

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
            f"Failsafe {body.action} executed successfully. "
            f"Flight route diverted to {target_hub.name}. "
            f"Incident #{incident.id} and MaintenanceAlert #{alert.id} have been created."
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
    speed_multiplier:     Optional[float] = None  # 1.0 = realtime, 2.0 = 2x tốc độ, 5.0 = 5x tốc độ
    next_flight_incident: Optional[bool]  = None


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
    from app.shared.drone_simulator import apply_simulator_config_update
    try:
        cfg = apply_simulator_config_update(
            incident_enabled=body.incident_enabled,
            incident_probability=body.incident_probability,
            incident_min_step=body.incident_min_step,
            incident_severity=body.incident_severity,
            interval_sec=body.interval_sec,
            steps_per_leg=body.steps_per_leg,
            speed_multiplier=body.speed_multiplier,
            next_flight_incident=body.next_flight_incident,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "message": "Simulator configuration updated.",
        "config": cfg,
    }
