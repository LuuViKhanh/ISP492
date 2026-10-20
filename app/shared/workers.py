import asyncio
from datetime import datetime, timedelta, timezone
from app.core.timezone import get_vn_naive_datetime, get_vn_datetime
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
    now = get_vn_naive_datetime()
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
    now = get_vn_naive_datetime()
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
    Worker 3: Giả lập trạm sạc tự động tại Hub theo thuật toán CC-CV.
    
    Lý do áp dụng thuật toán CC-CV thay vì sạc tuyến tính (Linear Charging):
    - Sạc tuyến tính (vd: cộng đều đặn 20% mỗi chu kỳ cho đến khi đầy) là phi thực tế với đặc tính vật lý của pin Li-Po/Li-ion.
    - Trong thực tế, để chống cháy nổ và bảo vệ cell pin, hệ thống quản lý pin (BMS) áp dụng CC-CV:
      1. Giai đoạn CC (Constant Current): Từ 0-80%, dòng điện được bơm tối đa, mức pin (SoC) tăng tuyến tính rất nhanh.
      2. Giai đoạn CV (Constant Voltage): Từ 80-100%, điện áp giữ cố định, dòng điện phải giảm dần theo hàm mũ, 
         làm cho tốc độ sạc chậm lại đáng kể khi tiến gần về 100% (Trickle charge).
    - Việc áp dụng mô hình toán học này vào phần mềm giúp Digital Twin của hệ thống Drone mô phỏng chính xác
      khoảng thời gian chết (downtime), giúp AI điều phối quyết định việc lấy pin 85% đi bay ngay thay vì đợi sạc đầy 100%.
    """
    from app.modules.fleet.models import Battery, batteries_status
    
    # Tìm các pin đang ở Hub (drone_id IS NULL), đang ACTIVE và pin chưa đầy
    result = await db.execute(
        select(Battery).where(
            and_(
                Battery.current_hub_id.isnot(None),
                Battery.drone_id.is_(None),
                Battery.status == batteries_status.ACTIVE,
                Battery.charge_level_pct < 100
            )
        )
    )
    charging_batteries = result.scalars().all()
    
    if not charging_batteries:
        return
        
    for battery in charging_batteries:
        current_charge = battery.charge_level_pct if battery.charge_level_pct is not None else 0
        
        if current_charge < 80:
            # Giai đoạn 1 (CC): Sạc nhanh tuyến tính (ví dụ bơm 40% mỗi chu kỳ)
            new_charge = current_charge + 40
        else:
            # Giai đoạn 2 (CV): Sạc chậm dần theo hàm mũ (Exponential decay)
            # Tốc độ sạc tỷ lệ thuận với lượng pin còn thiếu: Delta_SoC = k * (100 - SoC_current)
            # Ở đây chọn hệ số suy giảm k = 0.5 (mỗi chu kỳ sạc được 50% phần dung lượng còn trống)
            new_charge = current_charge + 0.5 * (100 - current_charge)
            
        # Làm tròn số và chặn trần tối đa ở 100%
        battery.charge_level_pct = min(round(new_charge), 100)
        battery.update_at = datetime.now(timezone.utc)
        
    await db.commit()


# ── Hằng số cấu hình cho nhắc nhận hàng ────────────────────────────
PICKUP_REMINDER_REF = "PICKUP_REMINDER"          # Marker để nhận diện thông báo nhắc (dedup)
PICKUP_REMINDER_INTERVAL = timedelta(hours=5)    # Cứ mỗi 5 tiếng nhắc 1 lần
PICKUP_CANCEL_AFTER = timedelta(days=3)          # Quá 3 ngày không nhận thì hủy


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    """
    Chuẩn hóa datetime về naive UTC để so sánh nhất quán.
    - Cột arrived_at là DateTime (không timezone) -> naive.
    - Cột notifications.created_at là DateTime(timezone=True) -> có thể trả về aware.
    So sánh naive với aware sẽ raise TypeError, nên phải quy về cùng dạng.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def run_pickup_reminder_watcher(db: AsyncSession):
    """
    Worker: Nhắc customer tới nhận hàng và tự hủy khi quá hạn.

    Quét các kiện hàng drone đã chở tới Hub đích nhưng khách CHƯA tới nhận
    (arrived_at đã set, arrival_confirmed_at còn trống, mission chưa bị hủy/hoàn tất).

    Với mỗi kiện, tính thời gian chờ kể từ arrived_at:
    - Nếu >= 5 ngày: tự hủy chuyến (status = CANCELLED) và thông báo cho Customer + Operators.
    - Ngược lại: cứ mỗi 5 tiếng gửi 1 thông báo nhắc cho Customer.
      Chống spam theo "Cách B": không thêm cột DB, mà truy vấn bảng notifications tìm
      thông báo nhắc gần nhất của kiện (reference_id == PICKUP_REMINDER_REF); chỉ gửi
      thông báo mới nếu đã đủ 5 tiếng kể từ lần nhắc gần nhất (hoặc kể từ arrived_at nếu chưa nhắc lần nào).
    """
    now = get_vn_naive_datetime()

    # Lấy các kiện đang chờ khách tới nhận (drone đã đáp hub đích: arrived_at != None, khách chưa nhận: arrival_confirmed_at == None)
    result = await db.execute(
        select(Mission).where(
            and_(
                Mission.arrived_at.is_not(None),
                Mission.arrival_confirmed_at.is_(None),
                Mission.status.notin_([
                    MissionStatus.CANCELLED,
                    MissionStatus.MISSION_COMPLETED,
                ]),
            )
        )
    )
    waiting_missions = result.scalars().all()

    if not waiting_missions:
        return

    operator_ids = await get_operator_ids(db)

    for mission in waiting_missions:
        arrived_at = _as_naive_utc(mission.arrived_at)
        if arrived_at is None:
            continue

        waited = now - arrived_at

        # ── Vế 1: Quá 3 ngày -> hủy hàng ──────────────────────────
        if waited >= PICKUP_CANCEL_AFTER:
            mission.status = MissionStatus.CANCELLED
            mission.handling_status = None

            if mission.order_id:
                from app.modules.missions.models import Order, OrderStatus
                order_obj = await db.get(Order, mission.order_id)
                if order_obj:
                    order_obj.status = OrderStatus.CANCELLED
                    order_obj.cancelled_at = now
                    order_obj.updated_at = now

            # Notify Customer
            if mission.customer_id:
                db.add(Notification(
                    user_id=mission.customer_id,
                    title="Kiện hàng đã bị hủy",
                    message=(
                        f"Kiện hàng của chuyến bay #{mission.id} đã bị hủy do bạn không "
                        f"tới nhận trong vòng 3 ngày kể từ khi hàng về Hub."
                    ),
                    notifications_type=NotificationType.MISSION_STATUS,
                    mission_id=str(mission.id),
                    created_at=now,
                ))

            # Notify Operators
            for op_id in operator_ids:
                db.add(Notification(
                    user_id=op_id,
                    title="Hủy kiện hàng quá hạn nhận",
                    message=(
                        f"Chuyến bay #{mission.id} đã tự động hủy do khách không tới "
                        f"nhận hàng sau 3 ngày."
                    ),
                    notifications_type=NotificationType.MISSION_STATUS,
                    mission_id=str(mission.id),
                    created_at=now,
                ))
            continue

        # ── Vế 2: Chưa tới 5 ngày -> nhắc mỗi 5 tiếng ─────────────
        # Không có customer thì không có ai để nhắc
        if not mission.customer_id:
            continue

        # Cách B: tìm thông báo nhắc gần nhất của kiện này
        last_reminder_result = await db.execute(
            select(Notification)
            .where(
                and_(
                    Notification.mission_id == str(mission.id),
                    Notification.reference_id == PICKUP_REMINDER_REF,
                )
            )
            .order_by(Notification.created_at.desc())
            .limit(1)
        )
        last_reminder = last_reminder_result.scalar_one_or_none()

        # Mốc tính 5 tiếng: lần nhắc gần nhất, hoặc arrived_at nếu chưa nhắc lần nào
        anchor = _as_naive_utc(last_reminder.created_at) if last_reminder else arrived_at

        if now - anchor >= PICKUP_REMINDER_INTERVAL:
            db.add(Notification(
                user_id=mission.customer_id,
                title="Kiện hàng đang chờ bạn tới nhận",
                message=(
                    f"Kiện hàng của chuyến bay #{mission.id} đã về Hub và đang chờ bạn "
                    f"tới nhận. Vui lòng tới nhận sớm; sau 3 ngày kể từ khi hàng về Hub, "
                    f"đơn sẽ tự động bị hủy."
                ),
                notifications_type=NotificationType.MISSION_STATUS,
                reference_id=PICKUP_REMINDER_REF,
                mission_id=str(mission.id),
                created_at=now,
            ))

    await db.commit()


# ── Hằng số cho Aging Priority ──────────────────────────────────────
from app.modules.missions.models import DeliveryMode as _DeliveryMode


async def run_aging_priority_watcher(db: AsyncSession):
    # T?m th?i v� hi?u h�a worker v� DB server chua migrate c?t is_aging_priority
    return
    """
    Worker: Tự động đánh dấu Aging Priority cho đơn NORMAL khi sắp hết deadline.

    Logic:
    - Quét các đơn NORMAL + PENDING + chưa có mission + is_aging_priority=False.
    - Nếu now >= aging_deadline → set is_aging_priority=True + notify tất cả Operators.
    - KHÔNG thay đổi bất cứ thứ gì của SCHEDULED orders (đúng với yêu cầu
      "không ảnh hưởng tới schedule").

    Mỗi đơn chỉ được nâng cấp 1 lần (dedup bằng is_aging_priority flag).
    """
    return  # Tạm thời vô hiệu hóa cho tới khi DB migration cột is_aging_priority
    from app.modules.missions.models import Order, OrderStatus, Mission

    now = get_vn_naive_datetime()

    # Lấy đơn NORMAL đủ điều kiện nâng cấp
    result = await db.execute(
        select(Order).where(
            and_(
                Order.delivery_mode == _DeliveryMode.NORMAL,
                Order.status == OrderStatus.PENDING,
                Order.is_aging_priority == False,     # noqa: E712
                Order.aging_deadline.isnot(None),
                Order.aging_deadline <= now,
            )
        )
    )
    normal_orders = result.scalars().all()

    if not normal_orders:
        return

    operator_ids = await get_operator_ids(db)

    for order in normal_orders:
        # Kiểm tra đơn này đã có mission chưa (nếu đã có thì không cần notify)
        mission_check = await db.execute(
            select(Mission).where(Mission.order_id == order.id).limit(1)
        )
        if mission_check.scalar_one_or_none():
            # Đã có mission → bỏ qua, không cần Aging Priority
            continue

        # Nâng cấp lên Aging Priority
        order.is_aging_priority = True
        order.updated_at = now

        # Notify Customer
        if order.customer_id:
            db.add(Notification(
                user_id=order.customer_id,
                title="Đơn hàng của bạn đang được ưu tiên xử lý",
                message=(
                    f"Đơn hàng #{order.id} đã được nâng lên Aging Priority để đảm bảo "
                    f"giao hàng đúng thời gian cam kết của bạn."
                ),
                notifications_type=NotificationType.MISSION_STATUS,
                reference_id=str(order.id),
                created_at=now,
            ))

        # Notify Operators — cần lên lịch bay gấp
        for op_id in operator_ids:
            db.add(Notification(
                user_id=op_id,
                title="⚡ Aging Priority: Cần lên lịch bay gấp",
                message=(
                    f"Đơn #{order.id} (NORMAL mode) đã đạt Aging Priority. "
                    f"Deadline giao hàng: {order.estimated_window_end.strftime('%H:%M %d/%m') if order.estimated_window_end else 'N/A'}. "
                    f"Vui lòng lên lịch mission ngay để không vi phạm cam kết."
                ),
                notifications_type=NotificationType.MISSION_STATUS,
                reference_id=str(order.id),
                created_at=now,
            ))

    await db.commit()



# ── Critical Order Notification ──────────────────────────────────────

CRITICAL_NOTIF_INTERVAL_HOURS = 1   # gửi lại mỗi 1h nếu vẫn Critical

async def run_critical_order_notifier(db: AsyncSession):
    """
    Worker: Quét các đơn CRITICAL (Slack <= 0, chưa có Mission) và thông báo cho Operator + Customer.

    Chạy mỗi 15 phút trong realtime cron.
    Dedup: chỉ gửi lại nếu đã qua CRITICAL_NOTIF_INTERVAL_HOURS kể từ lần thông báo gần nhất.
    Không ảnh hưởng đơn đã có Mission đang hoạt động.
    """
    from app.modules.missions.models import Order, OrderStatus, Mission
    from app.modules.auth.models import User, ROLE_NAME_TO_ID
    from app.shared.roles import UserRole as _Role

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Lấy đơn AT_HUB, PENDING hoặc trạng thái chờ, chưa bị hủy
    actionable_statuses = [OrderStatus.AT_HUB, OrderStatus.PENDING, OrderStatus.AWAITING_DROP_OFF]
    result = await db.execute(
        select(Order).where(
            and_(
                Order.status.in_(actionable_statuses),
                Order.estimated_window_end.isnot(None),
                Order.estimated_window_end <= now,  # đã quá customer_deadline
            )
        )
    )
    overdue_orders = result.scalars().all()

    if not overdue_orders:
        return

    # Lọc đơn đã có Mission active → bỏ qua
    overdue_ids = [o.id for o in overdue_orders]
    active_mission_result = await db.execute(
        select(Mission.order_id).where(
            and_(
                Mission.order_id.in_(overdue_ids),
                Mission.status.in_([
                    MissionStatus.SCHEDULED,
                    MissionStatus.APPROVED,
                    MissionStatus.FLYING,
                    MissionStatus.IN_PROGRESS,
                    MissionStatus.ACTIVE_MISSION,
                ])
            )
        )
    )
    has_mission_ids = {row[0] for row in active_mission_result.all() if row[0]}

    critical_orders = [o for o in overdue_orders if o.id not in has_mission_ids]
    if not critical_orders:
        return

    # Lấy Operator IDs
    operator_ids = await get_operator_ids(db)
    cutoff = now - timedelta(hours=CRITICAL_NOTIF_INTERVAL_HOURS)

    for order in critical_orders:
        # Dedup: kiểm tra đã gửi thông báo Critical trong khoảng time gần nhất chưa
        existing = await db.execute(
            select(Notification)
            .where(
                and_(
                    Notification.reference_id == f"CRITICAL_{order.id}",
                    Notification.created_at >= cutoff,
                )
            )
            .limit(1)
        )
        if existing.scalar_one_or_none():
            continue  # đã gửi gần đây → bỏ qua

        overdue_mins = round((now - order.estimated_window_end).total_seconds() / 60.0, 0)
        d_mode = order.delivery_mode.value if hasattr(order.delivery_mode, "value") else str(order.delivery_mode or "UNKNOWN")

        # Notify Operators
        for op_id in operator_ids:
            db.add(Notification(
                user_id=op_id,
                title=f"🚨 CRITICAL — Đơn #{order.id} quá hạn {int(overdue_mins)} phút",
                message=(
                    f"Đơn #{order.id} ({d_mode}) đã quá hạn giao {int(overdue_mins)} phút "
                    f"mà chưa có Mission. Cần lập lịch bay ngay!"
                ),
                notifications_type=NotificationType.MISSION_STATUS,
                mission_id=str(order.id),
                reference_id=f"CRITICAL_{order.id}",
                created_at=now,
            ))

        # Notify Customer
        if order.customer_id:
            db.add(Notification(
                user_id=order.customer_id,
                title="Đơn hàng của bạn đang bị trì hoãn",
                message=(
                    f"Đơn hàng #{order.id} của bạn đang bị trì hoãn hơn dự kiến. "
                    f"Đội ngũ của chúng tôi đang xử lý khẩn cấp và sẽ cập nhật sớm nhất."
                ),
                notifications_type=NotificationType.MISSION_STATUS,
                mission_id=str(order.id),
                reference_id=f"CRITICAL_{order.id}",
                created_at=now,
            ))

    await db.commit()
