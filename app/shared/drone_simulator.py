"""
app/shared/drone_simulator.py
──────────────────────────────
Auto-simulator chạy server-side dưới dạng asyncio background task.

Khi Technician bấm Dispatch → dispatch_drone() gọi asyncio.create_task(simulate_mission_flight(...))
Simulator sẽ:
  1. Lấy tất cả MissionLeg của mission (sorted by sequence_no)
  2. Với mỗi leg: lấy lat/lng của from_hub và to_hub từ bảng hubs
  3. Nội suy STEPS_PER_LEG điểm trung gian giữa 2 hub
  4. Mỗi INTERVAL_SEC giây insert 1 TelemetryLog trực tiếp vào DB
  5. Khi hết điểm của leg → set mission.handling_status = AT_HUB, dừng chờ Technician
"""

import asyncio
import math
import random
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update

from app.core.timezone import get_vn_datetime, get_vn_naive_datetime
from app.database.db import AsyncSessionLocal
from app.modules.missions.models import (
    Mission, MissionStatus, MissionLeg, MissionLegStatus,
    TelemetryLog, HandlingStatus, Incident, IncidentStatus, IncidentSeverity,
)
from app.modules.ai_predictions.models import AIPrediction
from app.modules.system.models import Hub
from app.modules.fleet.models import Drone, Battery


# ── Cấu hình ─────────────────────────────────────────────────────────────────

INTERVAL_SEC = 1        # Giây giữa mỗi điểm telemetry (giảm từ 3s để mượt hơn, không khựng)
STEPS_PER_LEG = 60      # Số điểm nội suy mỗi chặng (tăng để có đủ điểm giữa updates)

# Thông số tĩnh không đô thị & bay an toàn (Theo chuẩn QCVN & Luật Phòng không)
CRUISE_ALTITUDE = 50.0  # Độ cao bay hành trình tĩnh không đô thị chuẩn (50m - 80m)
CRUISE_SPEED = 12.0     # Vận tốc bay hành trình (12 m/s ~ 43.2 km/h)
MAX_WIND_LIMIT = 8.0    # Ngưỡng gió an toàn tối đa (8.0 m/s ~ Cấp 5 Beaufort)
SAFE_BATTERY_RESERVE_PCT = 20.0 # Ngưỡng pin an toàn tối thiểu (20%)

# ── Cấu hình Incident Simulator ──────────────────────────────────────────────
# Mặc định 100% chuyến bay hoàn thành bình thường (SUCCESS).
# Chỉ kích hoạt sự cố 1 lần cho chuyến bay kế tiếp khi gọi API trigger-next-incident.
INCIDENT_ENABLED     = False  # Tắt random incident mặc định (luôn SUCCESS trừ khi bật thủ công)
INCIDENT_PROBABILITY = 0.0    # Xác suất random incident mỗi điểm (0.0 → 0%)
INCIDENT_MIN_STEP    = 3      # Không xảy ra trước điểm thứ N (tránh incident ngay khi cất cánh)
INCIDENT_SEVERITY    = "Medium"  # Low / Medium / High
VALID_INCIDENT_TYPES = ("FAILSAFE_RTH", "SIGNAL_LOSS", "DISCONNECT_CRASH")


class _SimulatorConfig:
    """Object lưu config simulator — có thể mutate từ endpoint API."""
    def __init__(self):
        self.incident_enabled     = INCIDENT_ENABLED
        self.incident_probability = INCIDENT_PROBABILITY
        self.incident_min_step    = INCIDENT_MIN_STEP
        self.incident_severity    = INCIDENT_SEVERITY
        self.interval_sec         = INTERVAL_SEC
        self.steps_per_leg        = STEPS_PER_LEG
        self.speed_multiplier     = 1.0  # ← Tốc độ mô phỏng: 1.0 = realtime, 2.0 = 2x tốc độ, 5.0 = 5x tốc độ
        # One-shot trigger: nếu True, duy nhất chuyến bay kế tiếp gặp incident rồi tự reset về False
        self.next_flight_incident          = False
        self.next_flight_incident_step     = None
        self.next_flight_incident_severity = None
        self.next_flight_incident_type     = "FAILSAFE_RTH"
        self.next_flight_incident_armed_at = None

    def arm_next_flight_incident(
        self,
        enabled: bool = True,
        severity: Optional[str] = None,
        trigger_step: Optional[int] = None,
        incident_type: Optional[str] = None,
    ) -> dict:
        """Kích hoạt (hoặc hủy) chế độ sự cố 1 lần cho chuyến bay kế tiếp."""
        if not enabled:
            self.next_flight_incident = False
            self.next_flight_incident_step = None
            self.next_flight_incident_severity = None
            self.next_flight_incident_type = "FAILSAFE_RTH"
            self.next_flight_incident_armed_at = None
            return self.to_dict()

        clean_type = (incident_type or "FAILSAFE_RTH").strip().upper()
        if clean_type not in VALID_INCIDENT_TYPES:
            raise ValueError(f"incident_type must be one of {VALID_INCIDENT_TYPES}")
        if severity is not None and severity not in ("Low", "Medium", "High"):
            raise ValueError("severity must be Low / Medium / High")
        if trigger_step is not None and trigger_step < 1:
            raise ValueError("trigger_step must be >= 1")

        self.next_flight_incident = True
        self.next_flight_incident_step = trigger_step
        self.next_flight_incident_severity = severity or self.incident_severity
        self.next_flight_incident_type = clean_type
        self.next_flight_incident_armed_at = get_vn_datetime().isoformat()
        return self.to_dict()

    def consume_next_flight_incident(self, total_points: int) -> Optional[dict]:
        """
        Kiểm tra và tiêu thụ một lần kích hoạt sự cố cho chuyến bay vừa cất cánh.
        Không dùng await bên trong để đảm bảo atomic tuyệt đối — ngay sau khi chuyến bay kế tiếp
        nhận kịch bản incident, cờ tự động reset về False để các chuyến bay sau trở lại SUCCESS.
        """
        if not self.next_flight_incident:
            return None

        armed_step = self.next_flight_incident_step
        armed_severity = self.next_flight_incident_severity or self.incident_severity
        armed_type = self.next_flight_incident_type or "FAILSAFE_RTH"

        # Reset ngay lập tức để mọi chuyến bay tiếp theo quay về SUCCESS bình thường
        self.next_flight_incident = False
        self.next_flight_incident_step = None
        self.next_flight_incident_severity = None
        self.next_flight_incident_type = "FAILSAFE_RTH"
        self.next_flight_incident_armed_at = None

        if total_points <= 1:
            trigger_idx = 0
        elif armed_step is not None:
            trigger_idx = max(0, min(total_points - 1, armed_step - 1))
        else:
            default_idx = max(self.incident_min_step, int(round(total_points * 0.6)) - 1)
            trigger_idx = min(max(1, total_points - 2), default_idx)

        return {
            "armed": True,
            "trigger_idx": trigger_idx,
            "severity": armed_severity,
            "incident_type": armed_type,
        }

    def to_dict(self):
        return {
            "incident_enabled":              self.incident_enabled,
            "incident_probability":          self.incident_probability,
            "incident_min_step":             self.incident_min_step,
            "incident_severity":             self.incident_severity,
            "interval_sec":                  self.interval_sec,
            "steps_per_leg":                 self.steps_per_leg,
            "speed_multiplier":              self.speed_multiplier,
            "next_flight_incident":          self.next_flight_incident,
            "next_flight_incident_step":     self.next_flight_incident_step,
            "next_flight_incident_severity": self.next_flight_incident_severity,
            "next_flight_incident_type":     self.next_flight_incident_type,
            "next_flight_incident_armed_at": self.next_flight_incident_armed_at,
        }

# Singleton config — import cái này từ các module khác để đọc/ghi
simulator_config = _SimulatorConfig()


def apply_simulator_config_update(
    *,
    incident_enabled: Optional[bool] = None,
    incident_probability: Optional[float] = None,
    incident_min_step: Optional[int] = None,
    incident_severity: Optional[str] = None,
    interval_sec: Optional[int] = None,
    steps_per_leg: Optional[int] = None,
    speed_multiplier: Optional[float] = None,
    next_flight_incident: Optional[bool] = None,
) -> dict:
    """Cập nhật tập trung cấu hình Simulator kèm kiểm tra giá trị hợp lệ."""
    if incident_enabled is not None:
        simulator_config.incident_enabled = incident_enabled
    if incident_probability is not None:
        prob = float(incident_probability)
        if not 0.0 <= prob <= 1.0:
            raise ValueError("incident_probability must be between 0.0 and 1.0")
        simulator_config.incident_probability = prob
    if incident_min_step is not None:
        if incident_min_step < 1:
            raise ValueError("incident_min_step must be >= 1")
        simulator_config.incident_min_step = incident_min_step
    if incident_severity is not None:
        if incident_severity not in ("Low", "Medium", "High"):
            raise ValueError("incident_severity must be Low / Medium / High")
        simulator_config.incident_severity = incident_severity
    if interval_sec is not None:
        if interval_sec < 1:
            raise ValueError("interval_sec must be >= 1")
        simulator_config.interval_sec = interval_sec
    if steps_per_leg is not None:
        if steps_per_leg < 5:
            raise ValueError("steps_per_leg must be >= 5")
        simulator_config.steps_per_leg = steps_per_leg
    if speed_multiplier is not None:
        multiplier = float(speed_multiplier)
        if multiplier < 0.1 or multiplier > 100.0:
            raise ValueError("speed_multiplier must be between 0.1 and 100.0")
        simulator_config.speed_multiplier = multiplier
    if next_flight_incident is not None:
        simulator_config.arm_next_flight_incident(enabled=next_flight_incident)
    return simulator_config.to_dict()


# ── Haversine helper ──────────────────────────────────────────────────────────

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Khoảng cách Haversine (mét) giữa 2 tọa độ WGS-84."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _interpolate(lat1: float, lon1: float, lat2: float, lon2: float, steps: int) -> list[tuple[float, float]]:
    """Nội suy tuyến tính `steps` điểm từ (lat1,lon1) → (lat2,lon2), bao gồm điểm đích."""
    points = []
    for i in range(1, steps + 1):
        t = i / steps
        lat = lat1 + (lat2 - lat1) * t
        lon = lon1 + (lon2 - lon1) * t
        points.append((round(lat, 6), round(lon, 6)))
    return points


# ── Core simulator ────────────────────────────────────────────────────────────

async def _notify_hub_technicians_drone_arrived(
    db: AsyncSession,
    mission: Mission,
    hub: Optional[Hub],
    is_final_hub: bool = True,
    is_incident_return: bool = False,
) -> int:
    """
    Gửi thông báo tới các Technician đang active tại Hub nơi Drone hạ cánh
    (AT_HUB bình thường hoặc quay đầu sự cố CANNOT_CONTINUE). Trả về số Notification đã tạo.
    """
    if not hub:
        return 0
    try:
        from app.modules.auth.models import Account, ROLE_NAME_TO_ID
        from app.modules.system.models import Notification, NotificationType
        from app.shared.roles import UserRole

        tech_role_id = ROLE_NAME_TO_ID.get(UserRole.TECHNICIAN, "TECHNICIAN")
        tech_res = await db.execute(
            select(Account.id).where(
                Account.role_id == tech_role_id,
                Account.is_active == True,
                Account.hub_id == hub.id,
            )
        )
        tech_ids = list(tech_res.scalars().all())
        if not tech_ids:
            return 0

        now_tz = get_vn_datetime()
        hub_code_part = f" ({hub.code})" if hub.code else ""
        if is_incident_return:
            title = f"Drone #{mission.drone_id} returned due to incident to {hub.name}"
            message = (
                f"Mission #{mission.id} made an emergency landing back at {hub.name}{hub_code_part} "
                f"(Cannot continue). Please receive it for technical inspection and cargo unloading."
            )
            notif_type = NotificationType.SYSTEM_ALERT
        elif is_final_hub:
            title = f"Drone #{mission.drone_id} arrived at {hub.name} — Awaiting confirmation"
            message = (
                f"Mission #{mission.id} has landed at destination hub {hub.name}{hub_code_part}. "
                f"Please inspect the cargo and click Confirm Arrival."
            )
            notif_type = NotificationType.MISSION_STATUS
        else:
            title = f"Drone #{mission.drone_id} arrived at relay hub {hub.name}"
            message = (
                f"Mission #{mission.id} has landed at intermediate hub {hub.name}{hub_code_part}. "
                f"Please swap the battery and dispatch the next leg."
            )
            notif_type = NotificationType.MISSION_STATUS

        for tech_id in tech_ids:
            db.add(Notification(
                user_id=tech_id,
                title=title,
                message=message,
                notifications_type=notif_type,
                mission_id=str(mission.id),
                drone_id=str(mission.drone_id) if mission.drone_id else None,
                reference_id=f"DRONE_ARRIVED_HUB_{hub.id}_{mission.id}",
                created_at=now_tz,
            ))
        return len(tech_ids)
    except Exception as e:
        print(f"[SIMULATOR] Lỗi gửi notification hạ cánh cho Technician tại Hub #{hub.id}: {e}")
        return 0


async def _find_nearest_active_hub(
    db: AsyncSession,
    lat: float,
    lng: float,
    fallback_hub: Hub,
) -> Hub:
    """
    Tìm Hub đang hoạt động (active) gần tọa độ gặp sự cố nhất (tính theo Haversine).
    Bao gồm Hub xuất phát, Hub đích và toàn bộ Hub lân cận đang active.
    """
    res = await db.execute(select(Hub).where(func.lower(Hub.status) == "active"))
    hubs = list(res.scalars().all())
    if not hubs:
        res = await db.execute(select(Hub))
        hubs = list(res.scalars().all())

    best_hub = fallback_hub
    best_dist = float("inf")
    for h in hubs:
        if h.latitude is None or h.longitude is None:
            continue
        d_m = _haversine_m(lat, lng, h.latitude, h.longitude)
        if d_m < best_dist:
            best_dist = d_m
            best_hub = h
    return best_hub


async def _trigger_disconnect_crash(
    mission_id: str,
    incident_lat: float,
    incident_lng: float,
    from_hub: Hub,
    severity: str = "High",
) -> None:
    """
    Xử lý kịch bản DISCONNECT_CRASH: Drone mất tín hiệu / rớt giữa chặng.
    Ghi điểm rơi cuối (alt=0, speed=0), gắn recovery hub gần nhất để Technician thấy trên Incoming
    và xác nhận thu hồi bằng chính nút Confirm Arrival khi mang hàng/drone về Hub.
    """
    from app.modules.auth.models import Account, ROLE_NAME_TO_ID
    from app.modules.fleet.models import DroneStatus, MaintenanceAlert
    from app.modules.system.models import Notification, NotificationType
    from app.shared.roles import UserRole
    from app.shared.workers import get_operator_and_admin_ids

    async with AsyncSessionLocal() as db:
        try:
            mission = await db.get(Mission, mission_id)
            if not mission:
                return

            now_naive = get_vn_naive_datetime()
            now_tz = get_vn_datetime()
            recovery_hub = await _find_nearest_active_hub(db, incident_lat, incident_lng, fallback_hub=from_hub)

            # 1. Ghi 1 TelemetryLog cuối cùng tại tọa độ rơi (altitude=0, speed=0)
            db.add(TelemetryLog(
                mission_id=mission_id,
                timestamp=now_naive,
                latitude=incident_lat,
                longitude=incident_lng,
                altitude=0.0,
                speed=0.0,
                battery_voltage=0.0,
                battery_pct=0.0,
                energy_consumed_wh=None,
                wind_speed=None,
            ))

            # 2. Chuyển Mission → FAILED + CANNOT_CONTINUE (chưa đặt arrived_at để chờ Technician xác nhận thu hồi qua confirm-arrival)
            mission.status = MissionStatus.FAILED
            mission.handling_status = HandlingStatus.CANNOT_CONTINUE
            mission.end_time = now_naive
            mission.destination_hub_id = recovery_hub.id

            # Cập nhật chặng bay đang IN_PROGRESS trỏ đích dự kiến thu hồi về recovery_hub
            leg_res = await db.execute(
                select(MissionLeg)
                .where(
                    MissionLeg.mission_id == str(mission_id),
                    MissionLeg.status == MissionLegStatus.IN_PROGRESS,
                )
                .order_by(MissionLeg.sequence_no)
                .limit(1)
            )
            curr_leg = leg_res.scalars().first()
            if curr_leg:
                curr_leg.to_hub_id = recovery_hub.id
                curr_leg.updated_at = now_naive

            # 3. Khóa Drone → MAINTENANCE (không giải phóng Pin hay đưa Order về AT_HUB khi hàng chưa về Hub)
            if mission.drone_id:
                drone = await db.get(Drone, mission.drone_id)
                if drone:
                    drone.status = DroneStatus.MAINTENANCE

            # 4. Tạo Incident mức HIGH & MaintenanceAlert Pending
            inc_res = await db.execute(
                select(Incident).where(Incident.mission_id == str(mission_id)).limit(1)
            )
            inc = inc_res.scalars().first()
            if not inc:
                inc = Incident(
                    mission_id=str(mission_id),
                    drone_id=str(mission.drone_id) if mission.drone_id else None,
                    reporter_id=mission.operator_id,
                    severity=IncidentSeverity.HIGH,
                    description=(
                        f"[DISCONNECT / CRASH] Complete telemetry signal loss / suspected drone crash at GPS "
                        f"({incident_lat:.5f}, {incident_lng:.5f}). Field recovery assigned to {recovery_hub.name}."
                    ),
                    status=IncidentStatus.OPEN,
                    reported_at=now_tz,
                    requires_technical_inspection=True,
                )
                db.add(inc)
                await db.flush()

            alert = MaintenanceAlert(
                drone_id=str(mission.drone_id) if mission.drone_id else None,
                source="Incident",
                incident_id=inc.id,
                mission_id=str(mission_id),
                title=f"Signal lost — Potential drone crash at ({incident_lat:.5f}, {incident_lng:.5f})",
                description=(
                    f"Mission: {mission_id}\n"
                    f"Order: {mission.order_id}\n"
                    f"Drone: {mission.drone_id}\n"
                    f"Incident Type: DISCONNECT_CRASH\n"
                    f"Location: GPS ({incident_lat:.5f}, {incident_lng:.5f})\n"
                    f"Recovery Hub: {recovery_hub.name}\n"
                    f"Details: Complete telemetry signal loss / suspected drone crash. Field recovery assigned."
                ),
                status="Pending",
                created_at=now_naive,
            )
            db.add(alert)
            await db.flush()

            # 5. Bắn Notification khẩn tới Operator, Admin, Technician tại Hub thu hồi và Customer
            op_admin_ids = await get_operator_and_admin_ids(db)
            for uid in op_admin_ids:
                db.add(Notification(
                    user_id=uid,
                    title=f"CRITICAL: Drone Disconnected — Mission #{mission_id}",
                    message=(
                        f"Drone #{mission.drone_id} lost all telemetry signal / potential crash at GPS "
                        f"({incident_lat:.5f}, {incident_lng:.5f}). Recovery team at {recovery_hub.name} notified."
                    ),
                    notifications_type=NotificationType.SYSTEM_ALERT,
                    mission_id=str(mission_id),
                    drone_id=str(mission.drone_id) if mission.drone_id else None,
                    reference_id=str(inc.id),
                    created_at=now_tz,
                ))

            tech_role_id = ROLE_NAME_TO_ID.get(UserRole.TECHNICIAN, "TECHNICIAN")
            tech_ids = list((await db.execute(
                select(Account.id).where(
                    Account.role_id == tech_role_id,
                    Account.is_active == True,
                    Account.hub_id == recovery_hub.id,
                )
            )).scalars().all())
            for tid in tech_ids:
                db.add(Notification(
                    user_id=tid,
                    title=f"Field Recovery Needed: Drone #{mission.drone_id}",
                    message=(
                        f"Mission #{mission_id} lost connection at GPS ({incident_lat:.5f}, {incident_lng:.5f}). "
                        f"Please recover the drone and package back to {recovery_hub.name} and click Confirm Arrival."
                    ),
                    notifications_type=NotificationType.SYSTEM_ALERT,
                    mission_id=str(mission_id),
                    drone_id=str(mission.drone_id) if mission.drone_id else None,
                    reference_id=str(inc.id),
                    created_at=now_tz,
                ))

            if mission.customer_id:
                db.add(Notification(
                    user_id=mission.customer_id,
                    title="Flight Signal Interrupted — Order Under Investigation",
                    message=(
                        f"Flight #{mission_id} carrying your order #{mission.order_id} experienced an unexpected "
                        f"signal interruption mid-route. Our recovery team is actively securing your parcel and will update you shortly."
                    ),
                    notifications_type=NotificationType.SYSTEM_ALERT,
                    mission_id=str(mission_id),
                    reference_id=str(inc.id),
                    created_at=now_tz,
                ))

            await db.commit()
            print(
                f"[SIMULATOR] DISCONNECT_CRASH đã ghi nhận cho mission {mission_id} tại ({incident_lat:.5f}, {incident_lng:.5f}) "
                f"→ recovery_hub={recovery_hub.name}, notified ops/admins={len(op_admin_ids)}, techs={len(tech_ids)}."
            )
        except Exception as e:
            print(f"[SIMULATOR] Lỗi xử lý DISCONNECT_CRASH: {e}")


async def _trigger_incident(
    mission_id: str,
    incident_lat: float,
    incident_lng: float,
    return_hub: Hub,
    from_hub: Optional[Hub] = None,
    severity: str = "Medium",
) -> None:
    """Xử lý Failsafe chuyển hướng về Hub gần nhất (hoặc quay đầu) cho mission khi gặp sự cố, thông báo cho Operators."""
    from app.modules.system.models import Notification, NotificationType
    from app.shared.workers import get_operator_ids

    async with AsyncSessionLocal() as db:
        try:
            mission = await db.get(Mission, mission_id)
            if not mission:
                return

            now_tz = get_vn_datetime()
            now_naive = get_vn_naive_datetime()
            is_divert = bool(from_hub and return_hub.id != from_hub.id)

            # 1. Chuyển mission → INCIDENT_RETURN và cập nhật Hub hạ cánh khẩn cấp (nearest hub)
            mission.status = MissionStatus.INCIDENT_RETURN
            mission.handling_status = HandlingStatus.INCOMING
            mission.destination_hub_id = return_hub.id

            # Cập nhật chặng bay đang IN_PROGRESS hướng tới return_hub để bản đồ & Confirm Arrival đồng bộ
            leg_res = await db.execute(
                select(MissionLeg)
                .where(
                    MissionLeg.mission_id == str(mission_id),
                    MissionLeg.status == MissionLegStatus.IN_PROGRESS,
                )
                .order_by(MissionLeg.sequence_no)
                .limit(1)
            )
            curr_leg = leg_res.scalars().first()
            if curr_leg:
                curr_leg.to_hub_id = return_hub.id
                curr_leg.updated_at = now_naive

            # Tạo bản ghi Incident nếu chưa có
            inc_res = await db.execute(
                select(Incident).where(Incident.mission_id == str(mission_id)).limit(1)
            )
            existing_inc = inc_res.scalars().first()
            tag = "[FAILSAFE DIVERT]" if is_divert else "[FAILSAFE RTH]"
            action_phrase = "diverting to nearest hub" if is_divert else "returning to"
            if not existing_inc:
                sev_enum = IncidentSeverity.HIGH if str(severity).lower() == "high" else (
                    IncidentSeverity.LOW if str(severity).lower() == "low" else IncidentSeverity.MEDIUM
                )
                db.add(Incident(
                    mission_id=str(mission_id),
                    drone_id=str(mission.drone_id) if mission.drone_id else None,
                    reporter_id=mission.operator_id,
                    severity=sev_enum,
                    description=(
                        f"{tag} Incident automatically detected at ({incident_lat:.5f}, {incident_lng:.5f}) — "
                        f"Drone {action_phrase} {return_hub.name}."
                    ),
                    status=IncidentStatus.OPEN,
                    reported_at=now_tz,
                    requires_technical_inspection=True,
                ))

            # 2. Gửi Notification cho tất cả Operators & Admins (theo dõi chú ý trên Live-tracking)
            try:
                from app.shared.workers import get_operator_and_admin_ids
                recipient_ids = await get_operator_and_admin_ids(db)
                notif_title = f"Failsafe {'Divert' if is_divert else 'RTH'}: Mission #{mission_id}"
                for uid in recipient_ids:
                    db.add(Notification(
                        user_id=uid,
                        title=notif_title,
                        message=(
                            f"Drone #{mission.drone_id} triggered incident at ({incident_lat:.5f}, {incident_lng:.5f}). "
                            f"Safely {action_phrase} {return_hub.name}."
                        ),
                        notifications_type=NotificationType.SYSTEM_ALERT,
                        mission_id=str(mission_id),
                        drone_id=str(mission.drone_id) if mission.drone_id else None,
                        created_at=now_tz,
                    ))
            except Exception as e:
                print(f"[SIMULATOR] Lỗi gửi notification incident: {e}")

            await db.commit()
            print(f"[SIMULATOR] {tag} kích hoạt: mission {mission_id} → INCIDENT_RETURN, đáp về {return_hub.name}.")

        except Exception as e:
            print(f"[SIMULATOR] Lỗi kích hoạt Failsafe RTH: {e}")


async def _fly_incident_return(
    mission_id: str,
    waypoints: list[tuple[float, float]],
    return_hub: Hub,
    active_battery_id: str | None,
    active_drone_id: str | None,
    battery_capacity_wh: float,
    current_battery_pct: float,
) -> None:
    """Bay ngược về from_hub sau khi incident xảy ra."""
    total = len(waypoints)
    battery_volt = 22.0
    energy_wh = 0.0

    for idx, (lat, lng) in enumerate(waypoints):
        # ── Áp dụng speed_multiplier vào interval_sec ──
        effective_interval = simulator_config.interval_sec / simulator_config.speed_multiplier
        await asyncio.sleep(effective_interval)

        altitude    = round(40.0 + math.sin(idx * 0.3) * 3, 2)
        speed       = round(8.0 + math.cos(idx * 0.2) * 2, 2)
        battery_volt = round(max(18.0, battery_volt - 0.05), 2)
        energy_wh   = round(energy_wh + 0.9, 3)
        step_wh = 0.9
        pct_consumed = (step_wh / battery_capacity_wh) * 100.0
        current_battery_pct = max(1.0, current_battery_pct - pct_consumed)
        new_charge_pct = int(round(current_battery_pct))

        from app.shared.weather import fetch_weather
        weather = await fetch_weather(lat, lng)

        async with AsyncSessionLocal() as db:
            try:
                m_exists = (await db.execute(select(Mission.id).where(Mission.id == mission_id))).scalar_one_or_none()
                if not m_exists:
                    return

                log = TelemetryLog(
                    mission_id=mission_id,
                    timestamp=get_vn_naive_datetime(),
                    latitude=lat, longitude=lng,
                    altitude=altitude, speed=speed,
                    battery_voltage=battery_volt,
                    battery_pct=float(new_charge_pct),
                    energy_consumed_wh=energy_wh,
                    wind_speed=round(3.0 + random.uniform(-0.5, 0.5), 2),
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
                db.add(log)

                if active_battery_id:
                    await db.execute(
                        update(Battery).where(Battery.id == active_battery_id).values(charge_level_pct=new_charge_pct)
                    )
                if active_drone_id:
                    await db.execute(
                        update(Drone).where(Drone.id == active_drone_id).values(battery_level_pct=new_charge_pct)
                    )

                await db.commit()
                print(f"[SIMULATOR] [RETURN {idx+1:>3}/{total}] ({lat:.6f}, {lng:.6f}) pin={new_charge_pct}%")

            except Exception as e:
                print(f"[SIMULATOR] Lỗi telemetry lúc quay về: {e}")

    # Đáp về from_hub
    async with AsyncSessionLocal() as db:
        try:
            mission = await db.get(Mission, mission_id)
            if mission:
                now = get_vn_naive_datetime()
                mission.arrived_at = now
                mission.destination_hub_id = return_hub.id
                mission.handling_status = HandlingStatus.CANNOT_CONTINUE

                # Cập nhật chặng bay (leg) kết thúc khi tiếp đất Hub
                leg_res = await db.execute(
                    select(MissionLeg)
                    .where(
                        MissionLeg.mission_id == mission_id,
                        MissionLeg.status == MissionLegStatus.IN_PROGRESS,
                    )
                    .order_by(MissionLeg.sequence_no)
                    .limit(1)
                )
                leg = leg_res.scalars().first()
                if leg:
                    # Leg vẫn IN_PROGRESS, chỉ set handling_status để Technician thấy
                    leg.updated_at = now

                drone = await db.get(Drone, mission.drone_id) if mission.drone_id else None
                if drone:
                    drone.current_hub_id = return_hub.id
                notified_cnt = await _notify_hub_technicians_drone_arrived(
                    db=db,
                    mission=mission,
                    hub=return_hub,
                    is_final_hub=False,
                    is_incident_return=True,
                )
                await db.commit()
                print(
                    f"[SIMULATOR] Drone đáp {return_hub.name} an toàn — arrived_at={now}, "
                    f"handling_status=Cannot continue, notified_techs={notified_cnt}."
                )
        except Exception as e:
            print(f"[SIMULATOR] Lỗi cập nhật sau khi quay về hub: {e}")


async def simulate_mission_flight(mission_id: str) -> None:
    """
    Background coroutine — giả lập 1 chặng bay của mission.
    Chỉ chạy chặng đang IN_PROGRESS hiện tại rồi dừng (set AT_HUB).
    Technician sẽ dispatch lại → trigger coroutine mới cho chặng tiếp theo.
    """
    # Mỗi task dùng session riêng để tránh conflict với request session
    async with AsyncSessionLocal() as db:
        try:
            mission = await db.get(Mission, mission_id)
            if not mission:
                print(f"[SIMULATOR] Mission {mission_id} not found, aborting.")
                return

            # Mission phải đang FLYING (hoặc tự động chuẩn hóa về FLYING) mới chạy simulator
            if mission.status != MissionStatus.FLYING:
                mission.status = MissionStatus.FLYING
                await db.commit()
            elif mission.status != MissionStatus.FLYING:
                print(f"[SIMULATOR] Mission {mission_id} không ở trạng thái FLYING ({mission.status}), aborting.")
                return

            # Lấy leg đang IN_PROGRESS (leg vừa được dispatch)
            leg_result = await db.execute(
                select(MissionLeg)
                .where(
                    MissionLeg.mission_id == mission_id,
                    MissionLeg.status == MissionLegStatus.IN_PROGRESS,
                )
                .order_by(MissionLeg.sequence_no)
                .limit(1)
            )
            leg = leg_result.scalars().first()

            if not leg:
                print(f"[SIMULATOR] No IN_PROGRESS leg for mission {mission_id}, aborting.")
                return

            # Lấy tọa độ from_hub và to_hub
            from_hub = await db.get(Hub, leg.from_hub_id) if leg.from_hub_id else None
            to_hub   = await db.get(Hub, leg.to_hub_id)   if leg.to_hub_id   else None

            if not from_hub or not from_hub.latitude or not from_hub.longitude:
                print(f"[SIMULATOR] from_hub #{leg.from_hub_id} thiếu tọa độ, aborting.")
                return
            if not to_hub or not to_hub.latitude or not to_hub.longitude:
                print(f"[SIMULATOR] to_hub #{leg.to_hub_id} thiếu tọa độ, aborting.")
                return

            # Nội suy điểm trung gian
            waypoints = _interpolate(
                from_hub.latitude, from_hub.longitude,
                to_hub.latitude,   to_hub.longitude,
                simulator_config.steps_per_leg,
            )

            dist_m = _haversine_m(
                from_hub.latitude, from_hub.longitude,
                to_hub.latitude,   to_hub.longitude,
            )

            # Lấy thông tin pin & drone ban đầu để tính toán khấu trừ pin
            active_battery_id = leg.battery_id or mission.battery_id
            active_drone_id = mission.drone_id
            battery_capacity_wh = 150.0
            initial_battery_pct = 100.0

            if active_battery_id:
                bat_init = await db.get(Battery, active_battery_id)
                if bat_init:
                    if bat_init.capacity_wh and bat_init.capacity_wh > 0:
                        battery_capacity_wh = float(bat_init.capacity_wh)
                    if bat_init.charge_level_pct is not None:
                        initial_battery_pct = float(bat_init.charge_level_pct)
            elif active_drone_id:
                dr_init = await db.get(Drone, active_drone_id)
                if dr_init and dr_init.battery_level_pct is not None:
                    initial_battery_pct = float(dr_init.battery_level_pct)

            current_battery_pct = initial_battery_pct

            # Lấy planned_leg_energy_wh từ AIPrediction hoặc predict_flight_energy
            planned_leg_energy_wh = None
            ai_pred_res = await db.execute(
                select(AIPrediction)
                .where(AIPrediction.mission_id == str(mission_id))
                .order_by(AIPrediction.id.desc())
                .limit(1)
            )
            ai_pred = ai_pred_res.scalars().first()
            if ai_pred and ai_pred.estimated_energy_wh:
                total_legs_cnt = await db.scalar(
                    select(func.count(MissionLeg.id)).where(MissionLeg.mission_id == mission_id)
                ) or 1
                planned_leg_energy_wh = float(ai_pred.estimated_energy_wh) / float(total_legs_cnt)

            if not planned_leg_energy_wh or planned_leg_energy_wh <= 0:
                from app.modules.ai_predictions.service import predict_flight_energy
                payload_val = float(mission.payload_weight) if mission.payload_weight is not None else 0.5
                if mission.payload_weight is None and mission.drone_id:
                    dr = await db.get(Drone, mission.drone_id)
                    if dr and dr.payload_capacity_kg:
                        payload_val = float(dr.payload_capacity_kg) * 0.5
                pred_res = predict_flight_energy(
                    distance_km=dist_m / 1000.0,
                    payload_weight=payload_val,
                    speed=CRUISE_SPEED,
                    altitude=CRUISE_ALTITUDE
                )
                planned_leg_energy_wh = float(pred_res.get("energy_wh", 50.0))

            print(
                f"[SIMULATOR] Mission {mission_id} | Leg {leg.sequence_no}: "
                f"{from_hub.name} → {to_hub.name} | "
                f"{dist_m/1000:.2f} km | {len(waypoints)} điểm | interval={INTERVAL_SEC}s (speed_multiplier={simulator_config.speed_multiplier}) | "
                f"Battery start: {initial_battery_pct:.1f}% (cap: {battery_capacity_wh}Wh, planned_leg_energy: {planned_leg_energy_wh:.1f}Wh)"
            )

        except Exception as e:
            print(f"[SIMULATOR] Lỗi khởi tạo mission {mission_id}: {e}")
            return

    # ── Bay từng điểm ─────────────────────────────────────────────────────────
    # Dùng session mới cho từng điểm để tránh session timeout
    battery_volt  = 24.0
    energy_wh     = 0.0
    total_points  = len(waypoints)
    armed_incident = simulator_config.consume_next_flight_incident(total_points)
    if armed_incident:
        print(
            f"[SIMULATOR] Chuyến bay {mission_id} nhận kịch bản incident 1 lần: "
            f"type={armed_incident['incident_type']}, severity={armed_incident['severity']}, step={armed_incident['trigger_idx'] + 1}/{total_points}"
        )

    signal_loss_triggered = False  # ← Track nếu SIGNAL_LOSS đã trigger
    signal_loss_duration = 0  # ← Đếm bao nhiêu điểm mất kết nối (tạm thời)

    for idx, (lat, lng) in enumerate(waypoints):
        # ── Áp dụng speed_multiplier vào interval_sec ──
        effective_interval = simulator_config.interval_sec / simulator_config.speed_multiplier
        await asyncio.sleep(effective_interval)

        # ── Hạ cánh mượt mà: Bắt đầu giảm từ 50% quãng đường ──
        progress = (idx + 1) / total_points  # 0.0 → 1.0
        is_descending = progress >= 0.5  # Bắt đầu giảm từ 50% (mượt mà hơn)
        
        if is_descending:
            # descent_factor: từ 0.0 (50%) → 1.0 (100%)
            descent_factor = (progress - 0.5) / 0.5  
            speed = round(CRUISE_SPEED * (1 - descent_factor), 2)  # Giảm từ cruise → 0 m/s
            altitude = round(CRUISE_ALTITUDE * (1 - descent_factor), 2)  # Giảm → 0m
        else:
            # Giả lập sensor values chuẩn hóa lúc bay bình thường
            altitude     = round(CRUISE_ALTITUDE + math.sin(idx * 0.3) * 3, 2)
            speed        = round(CRUISE_SPEED + math.cos(idx * 0.2) * 1.5, 2)
        
        battery_volt = round(max(18.0, battery_volt - 0.04), 2)
        wind_speed   = round(2.0 + math.sin(idx * 0.5) * 1.5 + random.uniform(-0.2, 0.2), 2)

        # Khấu trừ mức pin theo năng lượng tiêu hao kế hoạch
        consumed_pct = ((idx + 1) / total_points) * ((planned_leg_energy_wh / battery_capacity_wh) * 100.0)
        current_battery_pct = max(1.0, initial_battery_pct - consumed_pct)
        new_charge_pct = int(round(current_battery_pct))
        energy_wh = round(((idx + 1) / total_points) * planned_leg_energy_wh, 3)

        # ── Kiểm tra incident trước khi ghi telemetry ───
        triggered_now = False
        trigger_sev = simulator_config.incident_severity
        trigger_type = "FAILSAFE_RTH"

        if not signal_loss_triggered:  # Chỉ kiểm tra incident 1 lần
            if armed_incident and idx >= armed_incident["trigger_idx"]:
                triggered_now = True
                trigger_sev = armed_incident["severity"]
                trigger_type = armed_incident["incident_type"]
            elif simulator_config.incident_enabled and idx >= simulator_config.incident_min_step:
                if random.random() < simulator_config.incident_probability:
                    triggered_now = True

            if triggered_now:
                print(f"[SIMULATOR] INCIDENT ({trigger_type}) xảy ra tại điểm {idx+1}/{total_points} ({lat:.6f}, {lng:.6f})")
                if trigger_type == "DISCONNECT_CRASH":
                    await _trigger_disconnect_crash(
                        mission_id=mission_id,
                        incident_lat=lat,
                        incident_lng=lng,
                        from_hub=from_hub,
                        severity=trigger_sev,
                    )
                    return
                if trigger_type == "SIGNAL_LOSS":
                    # Mất kết nối tạm thời: drone vẫn bay tiếp nhưng telemetry dừng khoảng 20 điểm (~60s)
                    print(f"[SIMULATOR] Mất tín hiệu cho Mission {mission_id} — drone vẫn bay tiếp, telemetry dừng (duration: ~20 points).")
                    signal_loss_triggered = True
                    signal_loss_duration = 20  # ← Mất 20 điểm rồi có lại
                    continue  # ← Bỏ qua ghi telemetry lần này, bay tiếp

                # FAILSAFE_RTH hoặc incident khác
                async with AsyncSessionLocal() as db_hub:
                    target_hub = await _find_nearest_active_hub(db_hub, lat, lng, fallback_hub=from_hub)

                await _trigger_incident(
                    mission_id=mission_id,
                    incident_lat=lat,
                    incident_lng=lng,
                    return_hub=target_hub,
                    from_hub=from_hub,
                    severity=trigger_sev,
                )
                # Bay tới Hub gần nhất (target_hub)
                target_lat = target_hub.latitude if target_hub.latitude is not None else from_hub.latitude
                target_lng = target_hub.longitude if target_hub.longitude is not None else from_hub.longitude
                return_waypoints = _interpolate(lat, lng, target_lat, target_lng, max(5, (total_points - idx)))
                await _fly_incident_return(mission_id, return_waypoints, target_hub, active_battery_id, active_drone_id, battery_capacity_wh, current_battery_pct)
                return

        # Fetch weather TRƯỚC khi mở DB session để không giữ DB connection trong lúc chờ Open-Meteo
        weather = None
        if not signal_loss_triggered or signal_loss_duration <= 1:
            from app.shared.weather import fetch_weather
            weather = await fetch_weather(lat, lng)

        async with AsyncSessionLocal() as db:
            try:
                # Kiểm tra mission còn FLYING không (chỉ lấy 2 cột cần thiết)
                m_row = (await db.execute(
                    select(Mission.status, Mission.arrived_at).where(Mission.id == mission_id)
                )).first()
                if not m_row:
                    return
                m_status, m_arrived_at = m_row
                if m_status == MissionStatus.INCIDENT_RETURN and m_arrived_at is None:
                    print(f"[SIMULATOR] Mission {mission_id} đã kích hoạt INCIDENT_RETURN, bắt đầu bay quay về trạm.")
                    return_waypoints = _interpolate(lat, lng, from_hub.latitude, from_hub.longitude, max(5, (total_points - idx)))
                    await _fly_incident_return(mission_id, return_waypoints, from_hub, active_battery_id, active_drone_id, battery_capacity_wh, current_battery_pct)
                    return
                elif m_status != MissionStatus.FLYING:
                    print(f"[SIMULATOR] Mission {mission_id} không còn FLYING ({m_status}), dừng simulator.")
                    return

                # ── Nếu SIGNAL_LOSS đã trigger, chỉ update pin, không ghi telemetry ──
                if signal_loss_triggered:
                    signal_loss_duration -= 1  # ← Đếm ngược
                    
                    # Direct UPDATE không cần db.get() trước
                    if active_battery_id:
                        await db.execute(
                            update(Battery).where(Battery.id == active_battery_id).values(charge_level_pct=new_charge_pct)
                        )
                    if active_drone_id:
                        await db.execute(
                            update(Drone).where(Drone.id == active_drone_id).values(battery_level_pct=new_charge_pct)
                        )
                    await db.commit()
                    
                    if signal_loss_duration > 0:
                        # Vẫn mất kết nối
                        print(
                            f"[SIMULATOR] [{idx+1:>3}/{total_points}] "
                            f"({lat:.6f}, {lng:.6f}) "
                            f"[NO TELEMETRY] pin={new_charge_pct}% (signal lost, {signal_loss_duration} points remaining)"
                        )
                        continue
                    else:
                        # Kết nối phục hồi
                        print(
                            f"[SIMULATOR] [{idx+1:>3}/{total_points}] "
                            f"({lat:.6f}, {lng:.6f}) "
                            f"SIGNAL RECOVERED — telemetry restored, pin={new_charge_pct}%"
                        )
                        signal_loss_triggered = False  # ← Kết nối bình thường lại
                        # Tiếp tục ghi telemetry bình thường ở dưới

                # Ghi nhận Telemetry Log (chỉ khi không mất kết nối)
                log = TelemetryLog(
                    mission_id=mission_id,
                    timestamp=get_vn_naive_datetime(),
                    latitude=lat,
                    longitude=lng,
                    altitude=altitude,
                    speed=speed,
                    battery_voltage=battery_volt,
                    battery_pct=float(new_charge_pct),
                    energy_consumed_wh=energy_wh,
                    wind_speed=wind_speed,
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
                db.add(log)

                # Cập nhật mức pin trực tiếp vào Database (direct UPDATE bỏ 2 SELECT thừa)
                if active_battery_id:
                    await db.execute(
                        update(Battery).where(Battery.id == active_battery_id).values(charge_level_pct=new_charge_pct)
                    )
                if active_drone_id:
                    await db.execute(
                        update(Drone).where(Drone.id == active_drone_id).values(battery_level_pct=new_charge_pct)
                    )

                await db.commit()

                print(
                    f"[SIMULATOR] [{idx+1:>3}/{total_points}] "
                    f"({lat:.6f}, {lng:.6f}) "
                    f"alt={altitude}m spd={speed}m/s bat={battery_volt}V pin={new_charge_pct}%"
                    + (f" [FAILSAFE WARNING: PIN THẤP < 20%]" if new_charge_pct < SAFE_BATTERY_RESERVE_PCT else "")
                )

            except Exception as e:
                print(f"[SIMULATOR] Lỗi insert TelemetryLog & cập nhật pin điểm {idx+1}: {e}")

    # ── Hết điểm → drone đã đến hub đích của leg này ─────────────────────────
    async with AsyncSessionLocal() as db:
        try:
            mission = await db.get(Mission, mission_id)
            leg_res = await db.execute(
                select(MissionLeg)
                .where(
                    MissionLeg.mission_id == mission_id,
                    MissionLeg.status == MissionLegStatus.IN_PROGRESS,
                )
                .order_by(MissionLeg.sequence_no)
                .limit(1)
            )
            leg = leg_res.scalars().first()

            if mission and leg:
                to_hub = await db.get(Hub, leg.to_hub_id) if leg.to_hub_id else None

                # Kiểm tra còn leg PENDING tiếp theo không
                next_leg_res = await db.execute(
                    select(MissionLeg)
                    .where(
                        MissionLeg.mission_id == mission_id,
                        MissionLeg.status == MissionLegStatus.PENDING,
                    )
                    .order_by(MissionLeg.sequence_no)
                    .limit(1)
                )
                next_leg = next_leg_res.scalars().first()

                # Drone đáp xuống hub → set AT_HUB để Technician thấy nút confirm
                # Leg vẫn IN_PROGRESS cho tới khi Technician bấm confirm-relay-arrival hoặc confirm-arrival
                now = get_vn_naive_datetime()
                mission.handling_status = HandlingStatus.AT_HUB
                mission.arrived_at = now
                if to_hub:
                    drone = await db.get(Drone, mission.drone_id) if mission.drone_id else None
                    if drone:
                        drone.current_hub_id = to_hub.id

                notified_cnt = await _notify_hub_technicians_drone_arrived(
                    db=db,
                    mission=mission,
                    hub=to_hub,
                    is_final_hub=(next_leg is None),
                    is_incident_return=False,
                )

                await db.commit()

                hub_label = to_hub.name if to_hub else f"Hub #{leg.to_hub_id}"
                if next_leg:
                    print(f"[SIMULATOR] Mission {mission_id} đáp {hub_label} — chờ Technician swap pin & dispatch tiếp.")
                else:
                    print(f"[SIMULATOR] Mission {mission_id} đáp {hub_label} (hub đích) — chờ Technician confirm arrival.")

        except Exception as e:
            print(f"[SIMULATOR] Lỗi cập nhật trạng thái sau khi bay xong: {e}")
