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
from sqlalchemy import select

from app.database.db import AsyncSessionLocal
from app.modules.missions.models import (
    Mission, MissionStatus, MissionLeg, MissionLegStatus,
    TelemetryLog, HandlingStatus,
)
from app.modules.system.models import Hub
from app.modules.fleet.models import Drone, Battery


# ── Cấu hình ─────────────────────────────────────────────────────────────────

INTERVAL_SEC = 3        # Giây giữa mỗi điểm telemetry
STEPS_PER_LEG = 20      # Số điểm nội suy mỗi chặng (tăng lên để mượt hơn)


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

            # Mission phải đang FLYING mới chạy simulator
            if mission.status != MissionStatus.FLYING:
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
                STEPS_PER_LEG,
            )

            dist_m = _haversine_m(
                from_hub.latitude, from_hub.longitude,
                to_hub.latitude,   to_hub.longitude,
            )

            # Lấy thông tin pin & drone ban đầu để tính toán khấu trừ pin
            active_battery_id = leg.battery_id or mission.battery_id
            active_drone_id = mission.drone_id
            battery_capacity_wh = 150.0
            current_battery_pct = 100.0

            if active_battery_id:
                bat_init = await db.get(Battery, active_battery_id)
                if bat_init:
                    if bat_init.capacity_wh and bat_init.capacity_wh > 0:
                        battery_capacity_wh = float(bat_init.capacity_wh)
                    if bat_init.charge_level_pct is not None:
                        current_battery_pct = float(bat_init.charge_level_pct)
            elif active_drone_id:
                dr_init = await db.get(Drone, active_drone_id)
                if dr_init and dr_init.battery_level_pct is not None:
                    current_battery_pct = float(dr_init.battery_level_pct)

            print(
                f"[SIMULATOR] Mission {mission_id} | Leg {leg.sequence_no}: "
                f"{from_hub.name} → {to_hub.name} | "
                f"{dist_m/1000:.2f} km | {len(waypoints)} điểm | interval={INTERVAL_SEC}s | "
                f"Battery start: {current_battery_pct:.1f}% (cap: {battery_capacity_wh}Wh)"
            )

        except Exception as e:
            print(f"[SIMULATOR] Lỗi khởi tạo mission {mission_id}: {e}")
            return

    # ── Bay từng điểm ─────────────────────────────────────────────────────────
    # Dùng session mới cho từng điểm để tránh session timeout
    battery_volt  = 24.0
    energy_wh     = 0.0
    total_points  = len(waypoints)

    for idx, (lat, lng) in enumerate(waypoints):
        await asyncio.sleep(INTERVAL_SEC)

        # Giả lập sensor values
        altitude     = round(50.0 + math.sin(idx * 0.3) * 5, 2)
        speed        = round(12.0 + math.cos(idx * 0.2) * 3, 2)
        battery_volt = round(max(18.0, battery_volt - 0.04), 2)
        energy_wh    = round(energy_wh + 0.8, 3)
        wind_speed   = round(2.0 + math.sin(idx * 0.5) * 1.5 + random.uniform(-0.2, 0.2), 2)

        # Khấu trừ mức pin theo năng lượng tiêu hao mỗi bước
        step_wh = 0.8
        pct_consumed = (step_wh / battery_capacity_wh) * 100.0
        current_battery_pct = max(1.0, current_battery_pct - pct_consumed)
        new_charge_pct = int(round(current_battery_pct))

        async with AsyncSessionLocal() as db:
            try:
                # Kiểm tra mission còn FLYING không (có thể bị cancel)
                mission = await db.get(Mission, mission_id)
                if not mission or mission.status != MissionStatus.FLYING:
                    print(f"[SIMULATOR] Mission {mission_id} không còn FLYING, dừng simulator.")
                    return

                # Ghi nhận Telemetry Log
                log = TelemetryLog(
                    mission_id=mission_id,
                    timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                    latitude=lat,
                    longitude=lng,
                    altitude=altitude,
                    speed=speed,
                    battery_voltage=battery_volt,
                    energy_consumed_wh=energy_wh,
                    wind_speed=wind_speed,
                )
                db.add(log)

                # Cập nhật mức pin trực tiếp vào Database (batteries và drones)
                if active_battery_id:
                    bat = await db.get(Battery, active_battery_id)
                    if bat:
                        bat.charge_level_pct = new_charge_pct
                if active_drone_id:
                    dr = await db.get(Drone, active_drone_id)
                    if dr:
                        dr.battery_level_pct = new_charge_pct

                await db.commit()

                print(
                    f"[SIMULATOR] [{idx+1:>3}/{total_points}] "
                    f"({lat:.6f}, {lng:.6f}) "
                    f"alt={altitude}m spd={speed}m/s bat={battery_volt}V pin={new_charge_pct}%"
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

                # Drone đáp xuống hub → cập nhật vị trí, giữ Incoming chờ Technician confirm
                mission.handling_status = HandlingStatus.INCOMING
                if to_hub:
                    
                    drone = await db.get(Drone, mission.drone_id) if mission.drone_id else None
                    if drone:
                        drone.current_hub_id = to_hub.id

                await db.commit()

                hub_label = to_hub.name if to_hub else f"Hub #{leg.to_hub_id}"
                if next_leg:
                    print(f"[SIMULATOR] Mission {mission_id} đáp {hub_label} — chờ Technician swap pin & dispatch tiếp.")
                else:
                    print(f"[SIMULATOR] Mission {mission_id} đáp {hub_label} (hub đích) — chờ Technician confirm arrival.")

        except Exception as e:
            print(f"[SIMULATOR] Lỗi cập nhật trạng thái sau khi bay xong: {e}")
