import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.missions.models import Mission, MissionStatus, TelemetryLog
from app.modules.system.models import Notification, NotificationType
from app.modules.auth.models import User, ROLE_NAME_TO_ID
from app.shared.roles import UserRole
from app.modules.fleet.models import MaintenanceAlert

async def get_operator_ids(db: AsyncSession) -> list[str]:
    operator_role_id = ROLE_NAME_TO_ID.get(UserRole.OPERATOR, "595bec91-7f4e-47ec-bfec-6f0087b84620")
    result = await db.execute(select(User.id).where(User.role_id == operator_role_id))
    return result.scalars().all()

async def run_approval_deadline_watcher(db: AsyncSession):
    """
    Worker 1: Quản lý đếm ngược (Approval Deadline Watcher)
    Tự động hủy các chuyến bay chưa được duyệt khi đã quá giờ scheduled_time.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await db.execute(
        select(Mission).where(
            and_(
                Mission.status == MissionStatus.PENDING_APPROVAL,
                Mission.scheduled_time <= now
            )
        )
    )
    overdue_missions = result.scalars().all()
    
    if not overdue_missions:
        return

    operator_ids = await get_operator_ids(db)
    
    for mission in overdue_missions:
        mission.status = MissionStatus.CANCELLED
        
        # Notify Customer
        if mission.customer_id:
            db.add(Notification(
                user_id=mission.customer_id,
                title="Chuyến bay bị hủy",
                message=f"Chuyến bay #{mission.id} của bạn đã bị hủy do quá hạn duyệt.",
                notifications_type=NotificationType.MISSION_STATUS,
                mission_id=str(mission.id),
                created_at=now
            ))
            
        # Notify Operators
        for op_id in operator_ids:
            db.add(Notification(
                user_id=op_id,
                title="Hủy chuyến bay quá hạn",
                message=f"Chuyến bay #{mission.id} đã tự động hủy do quá hạn duyệt.",
                notifications_type=NotificationType.MISSION_STATUS,
                mission_id=str(mission.id),
                created_at=now
            ))
            
    await db.commit()


async def run_telemetry_signal_loss_watcher(db: AsyncSession):
    """
    Worker 2: Quét log, mất tín hiệu quá 3s tự tạo Incident và đẩy thông báo cho Operator
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    threshold_time = now - timedelta(seconds=3)
    
    # Tìm các chuyến bay đang bay
    flying_missions_result = await db.execute(
        select(Mission).where(Mission.status == MissionStatus.FLYING)
    )
    flying_missions = flying_missions_result.scalars().all()
    
    if not flying_missions:
        return
        
    operator_ids = await get_operator_ids(db)
    
    for mission in flying_missions:
        # Lấy log gần nhất
        log_result = await db.execute(
            select(TelemetryLog)
            .where(TelemetryLog.mission_id == mission.id)
            .order_by(TelemetryLog.timestamp.desc())
            .limit(1)
        )
        latest_log = log_result.scalar_one_or_none()
        
        # Nếu có log mà log gần nhất quá 3s so với hiện tại
        if latest_log and latest_log.timestamp < threshold_time:
            # Kiểm tra xem đã có cảnh báo Incident mất tín hiệu cho mission này mà chưa xử lý không
            existing_alert_result = await db.execute(
                select(MaintenanceAlert).where(
                    and_(
                        MaintenanceAlert.mission_id == str(mission.id),
                        MaintenanceAlert.source == "Incident",
                        MaintenanceAlert.title == "Mất tín hiệu quá 3s",
                        MaintenanceAlert.status == "Pending"
                    )
                )
            )
            existing_alert = existing_alert_result.scalar_one_or_none()
            
            if not existing_alert:
                alert = MaintenanceAlert(
                    drone_id=mission.drone_id,
                    source="Incident",
                    mission_id=str(mission.id),
                    title="Mất tín hiệu quá 3s",
                    status="Pending",
                    created_at=now
                )
                db.add(alert)
                await db.flush() # Để lấy alert.id
                
                # Notify operators
                for op_id in operator_ids:
                    db.add(Notification(
                        user_id=op_id,
                        title="⚠️ Cảnh báo mất tín hiệu Drone",
                        message=f"Chuyến bay #{mission.id} (Drone #{mission.drone_id}) đã mất tín hiệu quá 3 giây! Lần cập nhật cuối: {latest_log.timestamp}",
                        notifications_type=NotificationType.SYSTEM_ALERT,
                        mission_id=str(mission.id),
                        reference_id=str(alert.id),
                        created_at=now
                    ))
    
    await db.commit()


async def run_battery_charging_simulation(db: AsyncSession):
    """
    Worker 3: Giả lập trạm sạc tự động tại Hub.
    Mỗi khi worker chạy, các viên pin đang ở Hub (drone_id is NULL) 
    và chưa đầy (< 100%) sẽ được sạc thêm một lượng % nhất định (vd: +20%).
    """
    from app.modules.fleet.models import Battery
    
    # Tìm các pin đang ở Hub (drone_id IS NULL) và pin chưa đầy
    result = await db.execute(
        select(Battery).where(
            and_(
                Battery.current_hub_id.isnot(None),
                Battery.drone_id.is_(None),
                Battery.charge_level_pct < 100
            )
        )
    )
    charging_batteries = result.scalars().all()
    
    if not charging_batteries:
        return
        
    CHARGE_RATE = 20  # Mỗi lần chạy giả lập sạc được 20%
    
    for battery in charging_batteries:
        current_charge = battery.charge_level_pct if battery.charge_level_pct is not None else 0
        new_charge = current_charge + CHARGE_RATE
        battery.charge_level_pct = min(new_charge, 100) # Đảm bảo không vượt quá 100%
        
    await db.commit()
