from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def run_maintenance_alerts_logic(db: AsyncSession):
    query = text("""
        INSERT INTO maintenance_alerts (drone_id, source, maintenance_schedule_id, title, status)
        SELECT drone_id, 'System', id, 'Scheduled maintenance due: ' || maintenance_type, 'Pending'
        FROM maintenance_schedules
        WHERE next_inspection_at <= NOW()
          AND status = 'Active'
          AND NOT EXISTS (
              SELECT 1 FROM maintenance_alerts 
              WHERE maintenance_alerts.maintenance_schedule_id = maintenance_schedules.id 
                AND maintenance_alerts.status = 'Pending'
          )
        RETURNING id;
    """)
    result = await db.execute(query)
    inserted_ids = [row[0] for row in result.fetchall()]
    await db.commit()
    return inserted_ids

async def run_battery_overdue_alerts_logic(db: AsyncSession):
    query_battery = text("""
        INSERT INTO maintenance_alerts (drone_id, source, title, status)
        SELECT id, 'System', 'Low battery alert (' || battery_level_pct || '%)', 'Pending'
        FROM drones
        WHERE battery_level_pct <= 20
          AND NOT EXISTS (
              SELECT 1 FROM maintenance_alerts 
              WHERE maintenance_alerts.drone_id = drones.id 
                AND maintenance_alerts.title LIKE 'Low battery alert%'
                AND maintenance_alerts.status = 'Pending'
          )
        RETURNING id;
    """)
    res_batt = await db.execute(query_battery)
    batt_ids = [row[0] for row in res_batt.fetchall()]
    
    query_overdue = text("""
        INSERT INTO maintenance_alerts (drone_id, source, title, status)
        SELECT drone_id, 'System', 'Overdue maintenance alert: ' || maintenance_type, 'Pending'
        FROM maintenance_schedules
        WHERE next_inspection_at <= NOW() - INTERVAL '7 days'
          AND status = 'Active'
          AND NOT EXISTS (
              SELECT 1 FROM maintenance_alerts 
              WHERE maintenance_alerts.maintenance_schedule_id = maintenance_schedules.id 
                AND maintenance_alerts.title LIKE 'Overdue maintenance alert%'
                AND maintenance_alerts.status = 'Pending'
          )
        RETURNING id;
    """)
    res_overdue = await db.execute(query_overdue)
    overdue_ids = [row[0] for row in res_overdue.fetchall()]
    
    await db.commit()
    return batt_ids, overdue_ids


# ── Preventive Maintenance Scheduler ─────────────────────────────────────────

# Ngưỡng giờ bay kích hoạt bảo dưỡng định kỳ
MAINTENANCE_THRESHOLD_HOURS = 50.0    # Tạo MaintenanceAlert khi đủ 50h
WARNING_THRESHOLD_HOURS     = 40.0    # Gửi cảnh báo sắp đến hạn khi đủ 40h


async def run_preventive_maintenance_scheduler(db: AsyncSession):
    """
    Worker: Tự động tạo MaintenanceAlert theo vòng đời giờ bay drone.

    - drone.total_flight_hours >= 50h (kể từ last_maintenance_at) → tạo alert MAINTENANCE_DUE
    - drone.total_flight_hours >= 40h → tạo alert WARNING (sắp đến hạn)
    - Dedup: chỉ tạo 1 alert per drone khi chưa có alert FLIGHT_HOURS đang Pending
    """
    from datetime import datetime, timezone
    from sqlalchemy import select, and_
    from app.modules.fleet.models import Drone, DroneStatus, MaintenanceAlert
    from app.modules.system.models import Notification, NotificationType
    from app.modules.auth.models import User as _User, ROLE_NAME_TO_ID as _ROLE_MAP
    from app.shared.roles import UserRole as _Role

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Lấy drone đang Available hoặc Maintenance, có giờ bay >= ngưỡng warning
    drones = (await db.execute(
        select(Drone).where(
            Drone.total_flight_hours >= WARNING_THRESHOLD_HOURS,
            Drone.status.in_([DroneStatus.AVAILABLE, DroneStatus.MAINTENANCE]),
        )
    )).scalars().all()

    _tech_role_id = _ROLE_MAP.get(_Role.TECHNICIAN)
    created = 0

    for drone in drones:
        hours = float(drone.total_flight_hours or 0.0)
        is_overdue = hours >= MAINTENANCE_THRESHOLD_HOURS

        # Kiểm tra đã có alert FLIGHT_HOURS Pending chưa
        existing = (await db.execute(
            select(MaintenanceAlert).where(
                MaintenanceAlert.drone_id == drone.id,
                MaintenanceAlert.source == "FLIGHT_HOURS",
                MaintenanceAlert.status.in_(["Pending", "PENDING"]),
            ).limit(1)
        )).scalars().first()
        if existing:
            continue

        # Tạo MaintenanceAlert
        title = (
            f"🔧 Preventive Maintenance Due — {drone.name} ({hours:.1f}h)"
            if is_overdue
            else f"⚠️ Maintenance Due Soon — {drone.name} ({hours:.1f}h / {MAINTENANCE_THRESHOLD_HOURS:.0f}h)"
        )
        alert = MaintenanceAlert(
            drone_id=drone.id,
            source="FLIGHT_HOURS",
            title=title,
            description=(
                f"Drone {drone.name} has accumulated {hours:.1f} flight hours. "
                f"{'Maintenance is now OVERDUE.' if is_overdue else f'Maintenance threshold is {MAINTENANCE_THRESHOLD_HOURS:.0f}h.'} "
                f"Please schedule a full inspection and service."
            ),
            status="Pending",
            created_at=now,
        )
        db.add(alert)
        await db.flush()

        # Notify Technicians tại hub của drone
        if drone.current_hub_id and _tech_role_id:
            tech_ids = (await db.execute(
                select(_User.id).where(
                    _User.role_id == _tech_role_id,
                    _User.hub_id == str(drone.current_hub_id),
                )
            )).scalars().all()
            for tid in tech_ids:
                db.add(Notification(
                    user_id=tid,
                    title=title,
                    message=(
                        f"Drone {drone.name} at your hub has {hours:.1f} flight hours. "
                        f"{'Preventive maintenance is overdue.' if is_overdue else 'Approaching maintenance threshold.'} "
                        f"Please check the work order queue."
                    ),
                    notifications_type=NotificationType.MAINTENANCE,
                    drone_id=str(drone.id),
                    reference_id=f"FLIGHT_HOURS_{drone.id}",
                    created_at=now,
                ))
        created += 1

    if created > 0:
        await db.commit()

    return created


# ── Drone Daily Stats Worker ──────────────────────────────────────────────────

async def run_drone_daily_stats(db: AsyncSession):
    """
    Worker: Tổng kết thống kê hoạt động drone theo ngày.

    Chạy 1 lần/ngày (cron hourly nhưng chỉ ghi khi chưa có record cho ngày hôm qua).
    Tổng hợp: số chuyến bay, completed, incidents, tổng km, tổng thời gian bay, tổng energy Wh.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, func, case, and_
    from app.modules.fleet.models import Drone, DroneDailyStat
    from app.modules.missions.models import Mission, MissionStatus, Incident

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Chỉ chạy 1 lần/ngày: kiểm tra đã có stat cho hôm qua chưa
    existing_count = (await db.execute(
        select(func.count(DroneDailyStat.id)).where(
            DroneDailyStat.stat_date == yesterday
        )
    )).scalar() or 0

    if existing_count > 0:
        return 0  # Đã tính rồi

    # Lấy missions của hôm qua theo drone
    _COMPLETED = [MissionStatus.ARRIVAL_COMPLETED, MissionStatus.MISSION_COMPLETED]

    missions = (await db.execute(
        select(Mission).where(
            Mission.departed_at >= yesterday,
            Mission.departed_at < today_start,
            Mission.drone_id.isnot(None),
        )
    )).scalars().all()

    if not missions:
        return 0

    # Lấy incidents liên quan
    m_ids = [m.id for m in missions]
    incident_counts: dict[str, int] = {}
    if m_ids:
        inc_rows = (await db.execute(
            select(Mission.drone_id, func.count(Incident.id).label("cnt"))
            .join(Incident, Incident.mission_id == Mission.id)
            .where(Mission.id.in_(m_ids), Mission.drone_id.isnot(None))
            .group_by(Mission.drone_id)
        )).all()
        incident_counts = {r.drone_id: r.cnt for r in inc_rows}

    # AI energy
    from app.modules.ai_predictions.models import AIPrediction
    energy_map: dict[str, float] = {}
    pred_rows = (await db.execute(
        select(AIPrediction.mission_id, AIPrediction.estimated_energy_wh)
        .where(AIPrediction.mission_id.in_(m_ids))
    )).all()
    for p in pred_rows:
        energy_map[str(p.mission_id)] = float(p.estimated_energy_wh or 0)

    # Group by drone_id
    from collections import defaultdict
    drone_data: dict[str, dict] = defaultdict(lambda: {
        "missions": 0, "completed": 0, "distance_m": 0.0,
        "flight_min": 0.0, "energy_wh": 0.0,
    })

    for m in missions:
        d = drone_data[m.drone_id]
        d["missions"] += 1
        if m.status in _COMPLETED:
            d["completed"] += 1
        d["distance_m"] += float(m.distance_m or 0)
        dep = m.departed_at
        arr = m.arrived_at or m.end_time
        if dep and arr:
            secs = max(0.0, (arr - dep).total_seconds())
            d["flight_min"] += secs / 60.0
        d["energy_wh"] += energy_map.get(str(m.id), 0.0)

    created = 0
    for drone_id, stats in drone_data.items():
        db.add(DroneDailyStat(
            drone_id=drone_id,
            stat_date=yesterday,
            mission_count=stats["missions"],
            completed_count=stats["completed"],
            incident_count=incident_counts.get(drone_id, 0),
            total_distance_km=round(stats["distance_m"] / 1000.0, 2),
            total_flight_min=round(stats["flight_min"], 1),
            total_energy_wh=round(stats["energy_wh"], 1),
            created_at=now,
        ))
        created += 1

    if created > 0:
        await db.commit()

    return created
