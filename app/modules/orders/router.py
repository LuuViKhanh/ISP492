from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from datetime import datetime, timezone, timedelta
from app.modules.ai_predictions.service import haversine_distance
from typing import Union, Optional, List
import math

from app.core.timezone import get_vn_naive_datetime
from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.models import Order, OrderStatus, Mission, MissionStatus, MissionLeg, MissionLegStatus
from app.modules.system.models import Hub
from app.modules.fleet.models import Drone, DroneStatus, Battery, batteries_status, compute_battery_available_status
from app.modules.missions.bundle_service import (
    resolve_effective_origin_hub_id,
    resolve_effective_origin_from_missions,
    predict_flight_energy_cached,
    get_compatible_battery_models_map,
    resolve_compatible_models_from_map,
    get_all_hubs_cached,
)
from app.modules.orders.schemas import (
    HubSchema,
    OrderResponse,
    OriginPackageSchema,
    EligibleDronesResponse,
    EligibleBatteriesResponse,
    HubEligibleSchema,
    DroneEligibleSchema,
    BatteryEligibleSchema,
    OrderUpdateRequest,
    OrderCancelRequest,
)
from app.core.config import settings

router = APIRouter(prefix="/operator/orders", tags=["Orders"])
allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])

ACTIVE_MISSION_STATUS_STRINGS = {
    "SCHEDULED", "IN_PROGRESS", "IN PROGRESS", "APPROVED",
    "FLYING", "ACTIVE_MISSION", "ACTIVE MISSION",
    "PENDING_APPROVAL", "PENDING APPROVAL",
}

ACTIVE_MISSION_STATUSES = [
    MissionStatus.SCHEDULED,
    MissionStatus.FLYING,
]

COMPLETED_MISSION_STATUS_STRINGS = {
    "COMPLETED", "ARRIVAL_COMPLETED", "ARRIVAL COMPLETED",
    "MISSION_COMPLETED", "MISSION COMPLETED",
}

FAILED_MISSION_STATUS_STRINGS = {
    "CANCELLED", "FAILED", "REJECTED",
    "INCIDENT_RETURN", "INCIDENT RETURN",
}


PLANNING_STATE_LABELS = {
    "WAITING_PACKAGE": "Awaiting Hub Drop-off",
    "READY_FOR_PLANNING": "Ready for Planning",
    "PLANNED": "Flight Scheduled",
    "FUTURE": "Not Yet Due for Planning",
    "NEEDS_REPLANNING": "Needs Replanning",
}

def compute_order_estimated_window(
    order: Order, 
    origin_hub: Optional[Hub] = None, 
    dest_hub: Optional[Hub] = None
) -> tuple[Optional[datetime], Optional[datetime]]:
    """
    Tính toán khung giờ giao hàng dự kiến (estimated_window_start, estimated_window_end).
    Đảm bảo luôn cộng thời gian bay thực tế từ AI Model (vận tốc chuẩn 10 m/s = 36 km/h)
    để không bao giờ bị trùng mốc giờ (start == end).
    """
    start = order.estimated_window_start
    end = order.estimated_window_end

    # Nếu DB đã có đủ 2 mốc và không bị trùng nhau -> giữ nguyên
    if start and end and start != end:
        return start, end

    # Tính khoảng cách giữa 2 hub
    if origin_hub and dest_hub and origin_hub.latitude and origin_hub.longitude and dest_hub.latitude and dest_hub.longitude:
        dist_km = haversine_distance(origin_hub.latitude, origin_hub.longitude, dest_hub.latitude, dest_hub.longitude)
    else:
        dist_km = 8.0  # default 8 km

    # AI Model: Vận tốc drone 10 m/s -> flight_mins = (dist_km * 1000 / 10) / 60
    flight_mins = max(math.ceil((dist_km * 1000 / 10.0) / 60.0), 10)

    base_time = order.requested_delivery_at or order.created_at or get_vn_naive_datetime()

    # Xử lý theo từng delivery_mode
    d_mode = order.delivery_mode.value if hasattr(order.delivery_mode, "value") else str(order.delivery_mode or "")
    if d_mode == "EXPRESS":
        start = base_time + timedelta(hours=1)
        end = start + timedelta(minutes=flight_mins + 20)
    elif d_mode == "NORMAL":
        start = base_time - timedelta(hours=4)
        end = base_time + timedelta(minutes=flight_mins)
    else:  # SCHEDULED
        start = base_time
        end = base_time + timedelta(minutes=flight_mins + 15)

    return start, end

def compute_origin_package_status(order: Order, latest_mission: Optional[Mission] = None) -> OriginPackageSchema:
    """
    Xác định trạng thái nhận kiện hàng tại Origin Hub (originPackage).
    - Hàng được tính là đã nhận (received = True) khi:
        1. order.origin_received_at is not None (có mốc thời gian nhận thực tế)
        2. Hoặc order.status == OrderStatus.AT_HUB
        3. Hoặc đơn hàng đã được tạo nhiệm vụ bay (latest_mission is not None)
    - Thời gian nhận hàng (received_at) ưu tiên order.origin_received_at, 
      fallback sang order.updated_at hoặc order.created_at nếu đơn đã AT_HUB hoặc đã có mission.
    """
    has_received = bool(
        order.origin_received_at is not None
        or order.status == OrderStatus.AT_HUB
        or latest_mission is not None
    )
    if has_received:
        received_time = order.origin_received_at or order.updated_at or order.created_at
        return OriginPackageSchema(
            received=True,
            received_at=received_time
        )
    return OriginPackageSchema(
        received=False,
        received_at=None
    )


def calculate_planning_state(order: Order, mission: Mission = None) -> str | None:
    now = get_vn_naive_datetime()

    # 1. Đơn hàng đã hủy, đang giao hoặc đã giao tới Hub đích -> Không còn ở bước lập lịch
    if order.status in (
        OrderStatus.CANCELLED,
        OrderStatus.IN_DELIVERY,
        OrderStatus.DELIVERED_TO_HUB,
    ):
        return None

    # Nếu đơn hàng đang ở Hub và có cờ cần lập lịch lại (do sự cố chuyến bay quay về)
    if order.status == OrderStatus.AT_HUB and order.replan_required_at:
        return "NEEDS_REPLANNING"

    # 2. Nếu đơn hàng đã có Mission liên kết
    if mission:
        ms_str = (mission.status.value if hasattr(mission.status, "value") else str(mission.status or "")).upper()
        if ms_str in ACTIVE_MISSION_STATUS_STRINGS:
            return "PLANNED"
        if ms_str in ("INCIDENT_RETURN", "INCIDENT RETURN") or ms_str in FAILED_MISSION_STATUS_STRINGS:
            # Nếu sự cố/rớt Drone chưa được Technician xác nhận thu hồi tại Hub (chưa có arrival_confirmed_at hoặc còn handling_status)
            if mission.arrival_confirmed_at is None or mission.handling_status is not None:
                return None  # Đang chờ Technician thu hồi xác nhận qua confirm-arrival, CHƯA ĐƯỢC lập lịch lại!
            if order.status == OrderStatus.AT_HUB:
                return "NEEDS_REPLANNING"
            return None
        if ms_str in COMPLETED_MISSION_STATUS_STRINGS or mission.arrived_at is not None:
            return None

    # 3. Chưa tới Hub -> Đang chờ nhận hàng
    if order.status != OrderStatus.AT_HUB:
        return "WAITING_PACKAGE"

    if order.replan_required_at:
        return "NEEDS_REPLANNING"

    # 4. Kiểm tra Aging Priority / Overdue / Urgent
    if getattr(order, "is_aging_priority", False):
        return "URGENT"

    deadline = order.estimated_window_end
    if deadline:
        overdue_mins = (now - deadline).total_seconds() / 60.0
        if overdue_mins > 0:
            return "OVERDUE"
        if overdue_mins > -120:  # còn < 2h trước deadline
            return "URGENT"

    if order.planning_at and now < order.planning_at:
        return "FUTURE"
    return "READY_FOR_PLANNING"


# Cập nhật PLANNING_STATE_LABELS
PLANNING_STATE_LABELS["OVERDUE"] = "Overdue — Schedule Immediately"
PLANNING_STATE_LABELS["URGENT"] = "Due Soon — High Priority"


def compute_urgency_fields(
    order: Order,
    w_end: Optional[datetime],
    flight_mins: int = 20,
) -> tuple[Optional[datetime], Optional[datetime], Optional[float], bool, Optional[float], Optional[float], Optional[str]]:
    """
    Tính coordination_deadline, overdue_minutes, is_overdue, slack_minutes, priority_score, priority_level.

    PriorityScore = BasePriority + UrgencyScore
      - BasePriority: EXPRESS=60, SCHEDULED=35, NORMAL=10
      - Slack = coordination_deadline - now (phút)
      - UrgencyScore = min(100, max(0, 100 × (60 - Slack) / 60))

    priority_level:
      - CRITICAL: Slack <= 0  (Overdue hoặc sắp trễ ngay)
      - HIGH:     Slack <= 30
      - MEDIUM:   Slack <= 60
      - LOW:      Slack > 60

    Returns: (customer_deadline, coordination_deadline, overdue_minutes, is_overdue,
              slack_minutes, priority_score, priority_level)
    """
    from app.core.timezone import get_vn_naive_datetime as _now
    if not w_end:
        return None, None, None, False, None, None, None

    now = _now()
    coord_deadline = w_end - timedelta(minutes=flight_mins + 30)
    overdue_raw = (now - w_end).total_seconds() / 60.0
    overdue_mins = round(overdue_raw, 1) if overdue_raw > 0 else None
    is_overdue = overdue_raw > 0

    # Slack tính từ coordination_deadline (không phải customer_deadline)
    slack_raw = (coord_deadline - now).total_seconds() / 60.0
    slack_mins = round(slack_raw, 1)

    # BasePriority theo mode
    d_mode = order.delivery_mode.value if hasattr(order.delivery_mode, "value") else str(order.delivery_mode or "")
    base = {"EXPRESS": 60.0, "SCHEDULED": 35.0, "NORMAL": 10.0}.get(d_mode, 10.0)
    if getattr(order, "is_aging_priority", False):
        base = max(base, 50.0)

    # UrgencyScore
    urgency = min(100.0, max(0.0, 100.0 * (60.0 - slack_mins) / 60.0))
    score = round(base + urgency, 1)

    # Priority level
    if slack_mins <= 0:
        level = "CRITICAL"
    elif slack_mins <= 30 or getattr(order, "is_aging_priority", False):
        level = "HIGH"
    elif slack_mins <= 60:
        level = "MEDIUM"
    else:
        level = "LOW"

    return w_end, coord_deadline, overdue_mins, is_overdue, slack_mins, score, level


@router.get("", response_model=list[OrderResponse])
async def list_orders(
    search: str = Query(None),
    status: OrderStatus = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=500, description="Giới hạn số đơn hàng trả về (mặc định trả tất cả để tương thích FE)"),
    offset: int = Query(0, ge=0, description="Vị trí bắt đầu phân trang"),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API] Lấy danh sách Đơn hàng (Orders)**
    
    - **Mục đích**: Hiển thị danh sách các đơn hàng để Operator theo dõi và chọn đơn hàng để lên lịch bay.
    - **Hoạt động**: 
        1. Lấy dữ liệu từ bảng `orders` (mới được tách ra theo Spec VI).
        2. Truy vấn bảng `missions` để kiểm tra chính xác đơn hàng nào đã tạo Mission, đã bay xong hay đang chờ lập lịch (`READY_FOR_PLANNING`).
        3. Cung cấp nhanh trạng thái nhận kiện hàng (`originPackage`).
    - **FE tương ứng**: Màn hình "Order Management / Order Planning" của Operator.
    - **[PERF NOTE]**: Hỗ trợ phân trang tùy chọn `?limit=&offset=`; giữ nguyên `get_all_hubs_cached` và batch query `Mission.order_id.in_(order_ids)` (cấm query theo từng đơn).
    """
    query = select(Order).where(Order.status != OrderStatus.CANCELLED)
    if status:
        if status == OrderStatus.CANCELLED:
            return []
        query = query.where(Order.status == status)
    if search:
        query = query.where(Order.id.ilike(f"%{search}%"))
        
    query = query.order_by(Order.created_at.desc())
    if offset > 0:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    orders = result.scalars().all()
    
    # Lấy tất cả hubs để map (dùng cache ngắn hạn tránh query lặp lại)
    hubs = await get_all_hubs_cached(db)
    hub_dict = {h.id: h.name for h in hubs}

    # Lấy Mission mới nhất của từng Order để tính chính xác planningState (loại bỏ mission đã CANCELLED)
    order_ids = [o.id for o in orders]
    mission_by_order: dict[str, Mission] = {}
    if order_ids:
        missions_result = await db.execute(
            select(Mission)
            .where(
                Mission.order_id.in_(order_ids),
                Mission.status != MissionStatus.CANCELLED,
            )
            .order_by(Mission.id.desc())
        )
        for m in missions_result.scalars().all():
            if m.order_id and m.order_id not in mission_by_order:
                mission_by_order[m.order_id] = m

    response_list = []
    hub_map_full = {h.id: h for h in hubs}
    for o in orders:
        latest_mission = mission_by_order.get(o.id)
        planning_state = calculate_planning_state(o, latest_mission)
        origin_pkg = compute_origin_package_status(o, latest_mission)
        order_resp = OrderResponse.model_validate(o)
        order_resp.mission_id = latest_mission.id if latest_mission else None
        order_resp.payment_status = o.payment_status.value if hasattr(o.payment_status, "value") else str(o.payment_status or "")
        order_resp.planningState = planning_state
        order_resp.planning_state_label = PLANNING_STATE_LABELS.get(planning_state)
        order_resp.originPackage = origin_pkg

        # Chỉ cho phép tạo mission khi hàng thực sự đã nhận tại Hub, đơn đã AT_HUB và chưa có mission active (hoặc cần lập lịch lại do sự cố)
        ms_val = (latest_mission.status.value if hasattr(latest_mission.status, "value") else str(latest_mission.status or "")).upper() if latest_mission else ""
        is_incident_active = (
            latest_mission is not None
            and ms_val in ("INCIDENT_RETURN", "INCIDENT RETURN")
            and (latest_mission.arrival_confirmed_at is None or latest_mission.handling_status is not None)
        )
        order_resp.can_create_mission = bool(
            origin_pkg.received
            and o.status == OrderStatus.AT_HUB 
            and not is_incident_active
            and (latest_mission is None or ms_val in FAILED_MISSION_STATUS_STRINGS or o.replan_required_at is not None)
        )

        # Mốc thời gian giao hàng dự kiến có cộng thời gian bay của AI Model
        ohub = hub_map_full.get(o.origin_hub_id)
        dhub = hub_map_full.get(o.destination_hub_id)
        w_start, w_end = compute_order_estimated_window(o, ohub, dhub)
        order_resp.estimated_window_start = w_start
        order_resp.estimated_window_end = w_end

        # ── Urgency / Deadline fields ──────────────────────────────────
        # Tính flight_mins từ khoảng cách thực tế
        _dist = 8.0
        if ohub and dhub and ohub.latitude is not None and ohub.longitude is not None and dhub.latitude is not None and dhub.longitude is not None:
            _dist = haversine_distance(ohub.latitude, ohub.longitude, dhub.latitude, dhub.longitude)
        _fmins = max(math.ceil((_dist * 1000 / 10.0) / 60.0), 10)
        cust_dl, coord_dl, overdue_mins, is_overdue, slack_mins, prio_score, prio_level = compute_urgency_fields(o, w_end, _fmins)
        order_resp.customer_deadline = cust_dl
        order_resp.coordination_deadline = coord_dl
        order_resp.overdue_minutes = overdue_mins
        order_resp.is_overdue = is_overdue
        order_resp.slack_minutes = slack_mins
        order_resp.priority_score = prio_score
        order_resp.priority_level = prio_level
        order_resp.created_at = o.created_at

        if o.origin_hub_id in hub_dict:
            order_resp.origin_hub = HubSchema(id=o.origin_hub_id, name=hub_dict[o.origin_hub_id])
        if o.destination_hub_id in hub_dict:
            order_resp.destination_hub = HubSchema(id=o.destination_hub_id, name=hub_dict[o.destination_hub_id])
        response_list.append(order_resp)
        
    return response_list


async def _build_order_response_detail(
    db: AsyncSession,
    order: Order,
    latest_mission: Optional[Mission],
) -> OrderResponse:
    planning_state = calculate_planning_state(order, latest_mission)
    origin_pkg = compute_origin_package_status(order, latest_mission)
    
    order_resp = OrderResponse.model_validate(order)
    order_resp.mission_id = latest_mission.id if latest_mission else None
    order_resp.payment_status = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    order_resp.planningState = planning_state
    order_resp.planning_state_label = PLANNING_STATE_LABELS.get(planning_state)
    order_resp.originPackage = origin_pkg

    ms_val = (latest_mission.status.value if hasattr(latest_mission.status, "value") else str(latest_mission.status or "")).upper() if latest_mission else ""
    is_incident_active = (
        latest_mission is not None
        and ms_val in ("INCIDENT_RETURN", "INCIDENT RETURN")
        and (latest_mission.arrival_confirmed_at is None or latest_mission.handling_status is not None)
    )
    order_resp.can_create_mission = bool(
        origin_pkg.received
        and order.status == OrderStatus.AT_HUB 
        and not is_incident_active
        and (latest_mission is None or ms_val in FAILED_MISSION_STATUS_STRINGS or order.replan_required_at is not None)
    )

    hub_map = {h.id: h for h in await get_all_hubs_cached(db)}
    ohub = hub_map.get(order.origin_hub_id) if order.origin_hub_id else None
    if ohub:
        order_resp.origin_hub = HubSchema(id=ohub.id, name=ohub.name)
    dhub = hub_map.get(order.destination_hub_id) if order.destination_hub_id else None
    if dhub:
        order_resp.destination_hub = HubSchema(id=dhub.id, name=dhub.name)

    w_start, w_end = compute_order_estimated_window(order, ohub, dhub)
    order_resp.estimated_window_start = w_start
    order_resp.estimated_window_end = w_end

    # Urgency fields
    _dist = 8.0
    if ohub and dhub and ohub.latitude is not None and ohub.longitude is not None and dhub.latitude is not None and dhub.longitude is not None:
        _dist = haversine_distance(ohub.latitude, ohub.longitude, dhub.latitude, dhub.longitude)
    _fmins = max(math.ceil((_dist * 1000 / 10.0) / 60.0), 10)
    cust_dl, coord_dl, overdue_mins, is_overdue, slack_mins, prio_score, prio_level = compute_urgency_fields(order, w_end, _fmins)
    order_resp.customer_deadline = cust_dl
    order_resp.coordination_deadline = coord_dl
    order_resp.overdue_minutes = overdue_mins
    order_resp.is_overdue = is_overdue
    order_resp.slack_minutes = slack_mins
    order_resp.priority_score = prio_score
    order_resp.priority_level = prio_level
    order_resp.created_at = order.created_at
        
    return order_resp


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API] Lấy chi tiết một Đơn hàng (Order)**
    
    - **Mục đích**: Xem chi tiết thông tin đơn hàng trước khi bấm "Create Mission".
    - **Hoạt động**:
        1. Lấy thông tin đơn hàng theo `order_id`.
        2. Truy vấn DB xem đơn hàng này đã có `Mission` nào chưa để trả về `planningState` tương ứng.
    """
    order_id = order_id.strip()
    order = await db.get(Order, order_id)
    if not order or order.status == OrderStatus.CANCELLED:
        raise HTTPException(status_code=404, detail="Order not found")
        
    # Lấy mission mới nhất của đơn hàng (loại trừ mission đã CANCELLED)
    mission_result = await db.execute(
        select(Mission)
        .where(
            Mission.order_id == order_id,
            Mission.status != MissionStatus.CANCELLED,
        )
        .order_by(Mission.id.desc())
        .limit(1)
    )
    latest_mission = mission_result.scalars().first()
    return await _build_order_response_detail(db, order, latest_mission)


async def _get_reserved_battery_info_map(
    db: AsyncSession,
    exclude_order_id: str | None = None,
    busy_drone_ids_out: Optional[set[str]] = None,
) -> dict[str, dict]:
    """
    Lấy mapping thông tin nhiệm vụ đang giữ chỗ cho từng viên pin (trong 1 query duy nhất).
    Nếu truyền `busy_drone_ids_out`, đồng thời thu thập các `drone_id` đang bận ở Mission active.
    """
    active_list = list(ACTIVE_MISSION_STATUSES)
    info_map: dict[str, dict] = {}

    stmt = (
        select(
            Mission.id,
            Mission.status,
            Mission.drone_id,
            Mission.battery_id,
            MissionLeg.battery_id,
            MissionLeg.sequence_no,
            MissionLeg.status,
        )
        .outerjoin(
            MissionLeg,
            and_(
                MissionLeg.mission_id == Mission.id,
                MissionLeg.status.in_([MissionLegStatus.PENDING, MissionLegStatus.IN_PROGRESS]),
                MissionLeg.battery_id.is_not(None),
            ),
        )
        .where(Mission.status.in_(active_list))
    )
    if exclude_order_id:
        stmt = stmt.where(Mission.order_id != exclude_order_id)

    res = await db.execute(stmt)
    for m_id, m_stat, m_drone_id, m_bat_id, leg_bat_id, leg_seq, _ in res.all():
        if busy_drone_ids_out is not None and m_drone_id is not None:
            busy_drone_ids_out.add(m_drone_id)
        m_status = m_stat.value if hasattr(m_stat, "value") else str(m_stat)
        if m_bat_id and m_bat_id not in info_map:
            info_map[m_bat_id] = {
                "mission_id": m_id,
                "mission_code": m_id,
                "mission_status": m_status,
                "reason": f"In use by Mission #{m_id} ({m_status})",
            }
        if leg_bat_id:
            seq_info = f"Leg {leg_seq}" if leg_seq else "Intermediate Leg"
            info_map[leg_bat_id] = {
                "mission_id": m_id,
                "mission_code": m_id,
                "mission_status": m_status,
                "reason": f"In use by Mission #{m_id} ({seq_info} - {m_status})",
            }

    return info_map


async def _get_reserved_battery_ids(db: AsyncSession, exclude_order_id: str | None = None) -> set[str]:
    """Lấy tập hợp battery_id đang được giữ chỗ bởi các Mission/MissionLeg đang hoạt động."""
    info_map = await _get_reserved_battery_info_map(db, exclude_order_id=exclude_order_id)
    return set(info_map.keys())


def _build_battery_eligible_schema(
    b: Battery,
    drone_id: Union[str, int, None] = None,
    reserved_info_map: Optional[dict[str, dict]] = None,
    required_energy_wh: Optional[float] = None,
) -> BatteryEligibleSchema:
    cap_wh = float(b.capacity_wh or 0.0)
    charge_pct = int(b.charge_level_pct if b.charge_level_pct is not None else 100)
    avail_energy_wh = round(cap_wh * (charge_pct / 100.0), 2)
    avail_status = compute_battery_available_status(b)

    is_selectable = True
    assigned_mission_id = None
    assigned_mission_code = None
    assigned_mission_status = None
    unavailable_reason = None
    status_str = avail_status.value if hasattr(avail_status, "value") else str(avail_status)

    res_info = reserved_info_map.get(b.id) if reserved_info_map else None
    if res_info:
        is_selectable = False
        status_str = "ASSIGNED_TO_MISSION"
        assigned_mission_id = res_info.get("mission_id")
        assigned_mission_code = res_info.get("mission_code")
        assigned_mission_status = res_info.get("mission_status")
        unavailable_reason = res_info.get("reason")
    elif b.drone_id is not None and drone_id is not None and b.drone_id != drone_id:
        is_selectable = False
        status_str = "INSTALLED_ON_OTHER_DRONE"
        unavailable_reason = f"Installed on Drone #{b.drone_id}"
    elif b.drone_id is not None and drone_id is None:
        is_selectable = False
        status_str = "In Use"
        unavailable_reason = f"Installed on Drone #{b.drone_id}"
    elif charge_pct >= 100:
        is_selectable = True
        status_str = "Ready"
        unavailable_reason = None
    elif required_energy_wh is not None and (avail_energy_wh * 0.8 >= required_energy_wh):
        # Pin < 100% nhưng năng lượng an toàn (sau khi trừ 20% dự phòng) đủ để bay thẳng tới đích
        is_selectable = True
        status_str = "Ready"
        unavailable_reason = None
    else:
        is_selectable = False
        status_str = "CHARGING"
        if required_energy_wh is not None:
            safe_e = round(avail_energy_wh * 0.8, 1)
            unavailable_reason = (
                f"Battery not fully charged ({charge_pct}% - {avail_energy_wh:.1f} Wh). "
                f"80% safe energy ({safe_e} Wh) is insufficient for direct flight to destination "
                f"(requires at least {required_energy_wh:.1f} Wh)"
            )
        else:
            unavailable_reason = f"Battery not fully charged ({charge_pct}% < 100%), cannot be selected for takeoff"

    if b.drone_id is not None and drone_id is not None and b.drone_id == drone_id and is_selectable:
        # Pin đang gắn sẵn trên chính Drone này và đủ điều kiện -> Sẵn sàng cất cánh ngay
        is_selectable = True
        status_str = "Ready"
        unavailable_reason = None

    return BatteryEligibleSchema(
        id=b.id,
        serial_number=b.serial_number,
        battery_model=b.battery_model,
        capacity_wh=cap_wh,
        charge_level_pct=charge_pct,
        available_energy_wh=avail_energy_wh,
        current_hub_id=b.current_hub_id,
        drone_id=b.drone_id,
        is_installed_on_drone=bool(b.drone_id is not None and (drone_id is None or b.drone_id == drone_id)),
        available_status=status_str,
        is_selectable=is_selectable,
        assigned_mission_id=assigned_mission_id,
        assigned_mission_code=assigned_mission_code,
        assigned_mission_status=assigned_mission_status,
        unavailable_reason=unavailable_reason,
    )


@router.get(
    "/{order_id}/eligible-drones",
    response_model=EligibleDronesResponse,
    summary="Tìm kiếm Drone & Pin khả dụng (Tự động fallback / Hỗ trợ Cross-Hub Dispatch)",
    description="""
    **[Operator API] Tìm kiếm Drone và Pin đủ điều kiện cho đơn hàng (Hỗ trợ Cross-Hub Dispatch)**
    
    ### Tính năng cốt lõi:
    1. **Drone nội bộ (Origin Hub)**:
       - Liệt kê các Drone đang đỗ tại Origin Hub (`current_hub_id == origin_hub_id`), trạng thái `AVAILABLE`, đủ tải trọng và chưa bị gán cho Mission active khác.
       - Với mỗi Drone, trả về danh sách Pin tương thích (`eligible_batteries`) kèm mức sạc thực tế (`charge_level_pct`) và năng lượng (`available_energy_wh`).
       - Viên pin 100% đang lắp sẵn trên Drone sẽ trả về `available_status: "Ready"`, `is_installed_on_drone: true` để Operator chọn cất cánh ngay.
    
    2. **Cơ chế Cross-Hub Drone Dispatch (Điều phối máy bay liên Hub)**:
       - **Tự động fallback (Mặc định `include_cross_hub=false`)**: Khi Hub xuất phát không còn cặp Drone-Pin nào đạt 100% để cất cánh (toàn bộ drone bận, pin đang sạc hoặc đã bị reserve), hệ thống **tự động tìm kiếm các Drone khả dụng từ Hub khác gần nhất** và gắn cờ `is_cross_hub: true`.
       - **Chủ động bật (`include_cross_hub=true`)**: Cho phép Operator/FE chủ động xem và lựa chọn Drone từ các Hub khác kể cả khi Hub xuất phát vẫn còn Drone nội bộ (phục vụ điều phối linh hoạt hoặc test chức năng).
    
    ### Query Parameters:
    - `include_cross_hub` (boolean, mặc định `false`):
      - `false`: Ưu tiên Drone nội bộ tại Origin Hub. Nếu Hub hết Drone/Pin khả dụng thì tự động fallback hiển thị Drone Cross-Hub.
      - `true`: Luôn hiển thị danh sách Drone nội bộ kèm danh sách Drone điều phối từ các Hub lân cận (`is_cross_hub: true`, có tên Hub hiện tại `current_hub_name`).
    """,
)
async def get_eligible_drones(
    order_id: str,
    include_cross_hub: bool = Query(
        False,
        description="Nếu True: Luôn trả về thêm danh sách Drone từ Hub khác (Cross-Hub Dispatch) kể cả khi Hub xuất phát còn Drone nội bộ. Mặc định False: Tự động fallback khi Hub xuất phát hết Drone/Pin.",
    ),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    order_id = order_id.strip()
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    eff_origin_id, _ = await resolve_effective_origin_hub_id(db, order)
    if not eff_origin_id:
        raise HTTPException(status_code=400, detail="Order does not have an origin hub")
        
    all_hubs = await get_all_hubs_cached(db)
    all_hubs_map = {h.id: h for h in all_hubs}
    hub = all_hubs_map.get(eff_origin_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Origin Hub not found")

    # Tính toán năng lượng bay thẳng ước tính từ Origin Hub đến Destination Hub (nếu có)
    required_direct_energy_wh = None
    if order.destination_hub_id:
        dest_hub_obj = all_hubs_map.get(order.destination_hub_id)
        if (
            dest_hub_obj
            and hub.latitude is not None and hub.longitude is not None
            and dest_hub_obj.latitude is not None and dest_hub_obj.longitude is not None
        ):
            direct_dist_km = haversine_distance(
                float(hub.latitude), float(hub.longitude),
                float(dest_hub_obj.latitude), float(dest_hub_obj.longitude)
            )
            payload_kg = float(order.payload_kg or 0.0)
            pred = predict_flight_energy_cached(direct_dist_km, payload_kg)
            required_direct_energy_wh = round(float(pred.get("energy_wh") or 0.0), 2)

    def _is_battery_usable(b: Battery, req_e: Optional[float] = None) -> bool:
        if b.id in reserved_info_map:
            return False
        pct = int(b.charge_level_pct if b.charge_level_pct is not None else 100)
        if pct >= 100:
            return True
        cap = float(b.capacity_wh or 0.0)
        avail_e = cap * (pct / 100.0)
        if req_e is not None:
            return (avail_e * 0.8 >= req_e)
        return False

    def _is_battery_visible(b: Battery, target_drone_id: Union[str, int, None], req_e: Optional[float] = None) -> bool:
        # 1. Pin 100%: Luôn luôn hiển thị
        pct = int(b.charge_level_pct if b.charge_level_pct is not None else 100)
        if pct >= 100:
            return True
        # 2. Pin < 100%: Chỉ hiển thị nếu đủ năng lượng an toàn bay thẳng từ Origin -> Destination
        cap = float(b.capacity_wh or 0.0)
        avail_e = cap * (pct / 100.0)
        if req_e is not None and (avail_e * 0.8 >= req_e):
            return True
        # 3. Giữ lại nếu đang gắn trên chính drone này hoặc đã bị giữ chỗ để FE hiển thị trạng thái
        if b.drone_id == target_drone_id or b.id in reserved_info_map:
            return True
        return False

    # Gộp query Mission (drone bận + pin giữ chỗ) + Drone + Pin trong ít roundtrip nhất
    busy_drone_ids: set[str] = set()
    reserved_info_map = await _get_reserved_battery_info_map(
        db, exclude_order_id=order_id, busy_drone_ids_out=busy_drone_ids
    )

    min_payload = float(order.payload_kg or 0.0)
    all_relevant_drones = (
        await db.execute(
            select(Drone)
            .where(
                or_(
                    Drone.current_hub_id == hub.id,
                    and_(
                        Drone.status == DroneStatus.AVAILABLE,
                        Drone.payload_capacity_kg >= min_payload,
                    ),
                )
            )
            .order_by(Drone.id)
        )
    ).scalars().all()

    total_drones = sum(1 for d in all_relevant_drones if d.current_hub_id == hub.id)
    eligible_drones = [
        d for d in all_relevant_drones
        if d.current_hub_id == hub.id
        and d.status == DroneStatus.AVAILABLE
        and float(d.payload_capacity_kg or 0.0) >= min_payload
        and d.id not in busy_drone_ids
    ]
    other_drones_all = [
        d for d in all_relevant_drones
        if d.current_hub_id != hub.id
        and d.status == DroneStatus.AVAILABLE
        and float(d.payload_capacity_kg or 0.0) >= min_payload
        and d.id not in busy_drone_ids
    ]

    def _drone_dist(d_cand):
        cand_hub = all_hubs_map.get(d_cand.current_hub_id)
        if not cand_hub:
            return float('inf')
        return haversine_distance(
            cand_hub.latitude or 0, cand_hub.longitude or 0,
            hub.latitude or 0, hub.longitude or 0
        )

    # Cross-Hub fallback: nếu Hub lấy hàng không có Drone phù hợp, tự động tìm Drone từ Hub khác
    is_cross_hub_mode = False
    if not eligible_drones and other_drones_all:
        is_cross_hub_mode = True
        other_drones_all.sort(key=_drone_dist)
        eligible_drones = other_drones_all

    eligible_drone_ids = {d.id for d in eligible_drones}

    all_active_batteries = (
        await db.execute(
            select(Battery)
            .where(Battery.status == batteries_status.ACTIVE)
            .order_by(Battery.id)
        )
    ).scalars().all()

    bat_hub_ids = {hub.id}
    if is_cross_hub_mode:
        for d in eligible_drones:
            if d.current_hub_id:
                bat_hub_ids.add(d.current_hub_id)
    hub_batteries = [
        b for b in all_active_batteries
        if (b.current_hub_id in bat_hub_ids or (b.drone_id is not None and b.drone_id in eligible_drone_ids))
        and (b.drone_id is None or b.drone_id in eligible_drone_ids)
    ]

    compat_models_map = await get_compatible_battery_models_map(db)
    drones_schema_list: list[DroneEligibleSchema] = []
    for d in eligible_drones:
        compat_models = resolve_compatible_models_from_map(compat_models_map, d.model)
        d_hub_id = d.current_hub_id or hub.id
        d_bats = [
            b for b in hub_batteries
            if (b.current_hub_id == d_hub_id or b.drone_id == d.id)
            and (b.drone_id is None or b.drone_id == d.id)
            and (not compat_models or (b.battery_model and b.battery_model in compat_models))
            and _is_battery_visible(b, d.id, required_direct_energy_wh)
        ]
        # Ưu tiên:
        # (1) Pin khả dụng (_is_battery_usable) xếp trước, pin bận/chưa đủ xếp sau
        # (2) Đang gắn trên chính drone này và khả dụng
        # (3) Mức sạc & dung lượng Wh cao nhất
        d_bats.sort(
            key=lambda b: (
                0 if _is_battery_usable(b, required_direct_energy_wh) else 1,
                0 if (b.drone_id == d.id and _is_battery_usable(b, required_direct_energy_wh)) else 1,
                -(b.charge_level_pct or 0),
                -float(b.capacity_wh or 0.0),
                b.id,
            )
        )
        installed_bat = next((b for b in d_bats if b.drone_id == d.id and _is_battery_usable(b, required_direct_energy_wh)), None)
        default_bat = next((b for b in d_bats if _is_battery_usable(b, required_direct_energy_wh)), None)

        d_hub = all_hubs_map.get(d.current_hub_id) if is_cross_hub_mode else hub
        d_is_cross = (d.current_hub_id is not None and d.current_hub_id != hub.id)
        has_ready_bat = (installed_bat is not None or default_bat is not None)

        d_selectable = True
        d_unavail_reason = None
        if not d_is_cross and not has_ready_bat:
            d_selectable = False
            if required_direct_energy_wh is not None:
                d_unavail_reason = (
                    f"Hub currently has no compatible battery with enough safe energy for direct flight to destination "
                    f"({required_direct_energy_wh:.1f} Wh with 20% reserve) or at 100% charge."
                )
            else:
                d_unavail_reason = "Hub currently has no compatible battery at 100% charge for takeoff (all < 100% batteries are charging or assigned to other missions)"

        drones_schema_list.append(
            DroneEligibleSchema(
                id=d.id,
                name=d.name,
                model=d.model,
                maxPayloadKg=d.payload_capacity_kg,
                battery_level_pct=d.battery_level_pct,
                installed_battery_id=installed_bat.id if installed_bat else None,
                default_battery_id=default_bat.id if default_bat else None,
                compatible_battery_models=compat_models,
                eligible_batteries=[_build_battery_eligible_schema(b, d.id, reserved_info_map, required_direct_energy_wh) for b in d_bats],
                current_hub_id=d.current_hub_id,
                current_hub_name=d_hub.name if d_hub else None,
                current_hub_code=d_hub.code or d_hub.name if d_hub else None,
                is_cross_hub=d_is_cross,
                is_selectable=d_selectable,
                unavailable_reason=d_unavail_reason,
            )
        )

    # Kiểm tra xem có Drone nội bộ (local) nào thực sự có pin sẵn sàng để cất cánh không
    has_mission_ready_local = any((not d.is_cross_hub and d.is_selectable) for d in drones_schema_list)

    # Nếu tất cả Drone nội bộ đều không có pin sẵn sàng (hoặc Hub không có drone), HOẶC nếu Operator/FE yêu cầu include_cross_hub:
    # Bổ sung các Drone từ Hub khác (Cross-Hub) có sẵn pin để Operator không bị bế tắc
    if (not has_mission_ready_local or include_cross_hub) and not is_cross_hub_mode:
        other_drones = other_drones_all

        if other_drones:
            other_drones.sort(key=_drone_dist)
            other_drone_ids = {d.id for d in other_drones}
            other_hub_ids = {d.current_hub_id for d in other_drones if d.current_hub_id}
            other_hub_batteries = [
                b for b in all_active_batteries
                if (b.current_hub_id in other_hub_ids or (b.drone_id is not None and b.drone_id in other_drone_ids))
                and (b.drone_id is None or b.drone_id in other_drone_ids)
            ]

            for d in other_drones:
                compat_models = resolve_compatible_models_from_map(compat_models_map, d.model)
                d_bats = [
                    b for b in other_hub_batteries
                    if (b.current_hub_id == d.current_hub_id or b.drone_id == d.id)
                    and (b.drone_id is None or b.drone_id == d.id)
                    and (not compat_models or (b.battery_model and b.battery_model in compat_models))
                    and _is_battery_visible(b, d.id, required_direct_energy_wh)
                ]
                d_bats.sort(
                    key=lambda b: (
                        0 if _is_battery_usable(b, required_direct_energy_wh) else 1,
                        0 if (b.drone_id == d.id and _is_battery_usable(b, required_direct_energy_wh)) else 1,
                        -(b.charge_level_pct or 0),
                        -float(b.capacity_wh or 0.0),
                        b.id,
                    )
                )
                inst_bat = next((b for b in d_bats if b.drone_id == d.id and _is_battery_usable(b, required_direct_energy_wh)), None)
                def_bat = next((b for b in d_bats if _is_battery_usable(b, required_direct_energy_wh)), None)
                has_bat = (inst_bat is not None or def_bat is not None)

                # Chỉ đưa vào danh sách nếu Drone ở Hub khác có pin khả dụng tại Hub đó
                if has_bat:
                    d_hub = all_hubs_map.get(d.current_hub_id)
                    drones_schema_list.append(
                        DroneEligibleSchema(
                            id=d.id,
                            name=d.name,
                            model=d.model,
                            maxPayloadKg=d.payload_capacity_kg,
                            battery_level_pct=d.battery_level_pct,
                            installed_battery_id=inst_bat.id if inst_bat else None,
                            default_battery_id=def_bat.id if def_bat else None,
                            compatible_battery_models=compat_models,
                            eligible_batteries=[_build_battery_eligible_schema(b, d.id, reserved_info_map, required_direct_energy_wh) for b in d_bats],
                            current_hub_id=d.current_hub_id,
                            current_hub_name=d_hub.name if d_hub else None,
                            current_hub_code=d_hub.code or d_hub.name if d_hub else None,
                            is_cross_hub=True,
                            is_selectable=True,
                            unavailable_reason=None,
                        )
                    )

    return EligibleDronesResponse(
        hub=HubEligibleSchema(
            id=hub.id,
            name=hub.name or f"Hub {hub.id}",
            totalDrones=total_drones,
            availableDrones=len([d for d in drones_schema_list if d.is_selectable])
        ),
        drones=drones_schema_list,
        batteries=[
            _build_battery_eligible_schema(b, None, reserved_info_map, required_direct_energy_wh)
            for b in hub_batteries
            if _is_battery_visible(b, None, required_direct_energy_wh)
        ],
    )


@router.get("/{order_id}/eligible-batteries", response_model=EligibleBatteriesResponse)
async def get_eligible_batteries_for_order(
    order_id: str,
    drone_id: str = Query(..., description="ID của Drone được chọn thực hiện nhiệm vụ"),
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API] Lấy danh sách Pin khả dụng cho Drone được chọn của một Đơn hàng**
    
    - Trả về danh sách các viên pin tại Origin Hub tương thích với `drone_id` (hoặc đang lắp sẵn trên `drone_id`),
      chưa bị giữ chỗ bởi nhiệm vụ khác, kèm theo năng lượng thực tế (`available_energy_wh`) để phân tích tuyến đường AI.
    """
    order_id = order_id.strip()
    # [PERF] Gộp truy vấn Order + Drone thành 1 lượt SQL duy nhất
    pair_res = await db.execute(
        select(Order, Drone)
        .outerjoin(Drone, Drone.id == drone_id)
        .where(Order.id == order_id)
    )
    pair_row = pair_res.first()
    order = pair_row[0] if pair_row else None
    drone = pair_row[1] if pair_row else None
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")

    # [PERF] Gộp 2 query missions (kiểm tra replan của Order + giữ chỗ pin active) thành 1 SQL duy nhất
    active_list = list(ACTIVE_MISSION_STATUSES)
    combined_m_stmt = (
        select(
            Mission,
            MissionLeg.battery_id,
            MissionLeg.sequence_no,
            MissionLeg.status,
        )
        .outerjoin(
            MissionLeg,
            and_(
                MissionLeg.mission_id == Mission.id,
                MissionLeg.status.in_([MissionLegStatus.PENDING, MissionLegStatus.IN_PROGRESS]),
                MissionLeg.battery_id.is_not(None),
            ),
        )
        .where(
            or_(
                Mission.order_id == order_id,
                Mission.status.in_(active_list),
            )
        )
        .order_by(Mission.id.desc())
    )
    combined_rows = (await db.execute(combined_m_stmt)).all()

    order_missions_by_id: dict[str, Mission] = {}
    reserved_info_map: dict[str, dict] = {}
    active_set = set(active_list)
    for m_obj, leg_bat_id, leg_seq, _ in combined_rows:
        if m_obj.order_id == order_id:
            order_missions_by_id.setdefault(m_obj.id, m_obj)
            continue
        if m_obj.status not in active_set:
            continue
        m_status = m_obj.status.value if hasattr(m_obj.status, "value") else str(m_obj.status)
        if m_obj.battery_id and m_obj.battery_id not in reserved_info_map:
            reserved_info_map[m_obj.battery_id] = {
                "mission_id": m_obj.id,
                "mission_code": m_obj.id,
                "mission_status": m_status,
                "reason": f"In use by Mission #{m_obj.id} ({m_status})",
            }
        if leg_bat_id:
            seq_info = f"Leg {leg_seq}" if leg_seq else "Intermediate Leg"
            reserved_info_map[leg_bat_id] = {
                "mission_id": m_obj.id,
                "mission_code": m_obj.id,
                "mission_status": m_status,
                "reason": f"In use by Mission #{m_obj.id} ({seq_info} - {m_status})",
            }

    eff_origin_id, _ = resolve_effective_origin_from_missions(order, list(order_missions_by_id.values()))
    if not eff_origin_id:
        raise HTTPException(status_code=400, detail="Order does not have an origin hub")

    hub_map = {h.id: h for h in await get_all_hubs_cached(db)}
    hub = hub_map.get(eff_origin_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Origin Hub not found")

    # Tính toán năng lượng bay thẳng ước tính từ Origin Hub đến Destination Hub
    required_direct_energy_wh = None
    if order.destination_hub_id:
        dest_hub_obj = hub_map.get(order.destination_hub_id)
        if (
            dest_hub_obj
            and hub.latitude is not None and hub.longitude is not None
            and dest_hub_obj.latitude is not None and dest_hub_obj.longitude is not None
        ):
            direct_dist_km = haversine_distance(
                float(hub.latitude), float(hub.longitude),
                float(dest_hub_obj.latitude), float(dest_hub_obj.longitude)
            )
            payload_kg = float(order.payload_kg or 0.0)
            pred = predict_flight_energy_cached(direct_dist_km, payload_kg)
            required_direct_energy_wh = round(float(pred.get("energy_wh") or 0.0), 2)

    compat_models_map = await get_compatible_battery_models_map(db)
    compat_models = resolve_compatible_models_from_map(compat_models_map, drone.model)

    def _is_battery_usable(b: Battery) -> bool:
        if b.id in reserved_info_map:
            return False
        pct = int(b.charge_level_pct if b.charge_level_pct is not None else 100)
        if pct >= 100:
            return True
        cap = float(b.capacity_wh or 0.0)
        avail_e = cap * (pct / 100.0)
        if required_direct_energy_wh is not None:
            return (avail_e * 0.8 >= required_direct_energy_wh)
        return False

    def _is_battery_visible(b: Battery) -> bool:
        pct = int(b.charge_level_pct if b.charge_level_pct is not None else 100)
        if pct >= 100:
            return True
        cap = float(b.capacity_wh or 0.0)
        avail_e = cap * (pct / 100.0)
        if required_direct_energy_wh is not None and (avail_e * 0.8 >= required_direct_energy_wh):
            return True
        if b.drone_id == drone.id or b.id in reserved_info_map:
            return True
        return False

    target_hub_id = drone.current_hub_id or hub.id
    target_hub = hub_map.get(target_hub_id) if target_hub_id != hub.id else hub

    bats_stmt = select(Battery).where(
        Battery.status == batteries_status.ACTIVE,
        or_(
            and_(Battery.current_hub_id == target_hub_id, Battery.drone_id.is_(None)),
            Battery.drone_id == drone.id,
        )
    ).order_by(Battery.id)
    bats = [
        b for b in (await db.execute(bats_stmt)).scalars().all()
        if ((b.current_hub_id == target_hub_id and b.drone_id is None) or b.drone_id == drone.id)
        and (not compat_models or (b.battery_model and b.battery_model in compat_models))
        and _is_battery_visible(b)
    ]
    bats.sort(
        key=lambda b: (
            0 if _is_battery_usable(b) else 1,
            0 if (b.drone_id == drone.id and _is_battery_usable(b)) else 1,
            -(b.charge_level_pct or 0),
            -float(b.capacity_wh or 0.0),
            b.id,
        )
    )
    default_bat = next((b for b in bats if _is_battery_usable(b)), None)

    return EligibleBatteriesResponse(
        order_id=order.id,
        hub_id=target_hub.id if target_hub else hub.id,
        hub_name=(target_hub.name if target_hub else hub.name) or f"Hub {target_hub.id if target_hub else hub.id}",
        drone_id=drone.id,
        drone_name=drone.name,
        drone_model=drone.model,
        compatible_battery_models=compat_models,
        default_battery_id=default_bat.id if default_bat else None,
        batteries=[_build_battery_eligible_schema(b, drone.id, reserved_info_map, required_direct_energy_wh) for b in bats],
    )


@router.post("/{order_id}/cancel", summary="Huỷ đơn hàng")
async def cancel_order(
    order_id: str,
    background_tasks: BackgroundTasks,
    body: Optional[OrderCancelRequest] = None,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Huỷ đơn hàng — cho phép khi order chưa cất cánh (PENDING, AWAITING_DROP_OFF, AT_HUB).
    Tự động huỷ nhiệm vụ chưa bay nếu có, và gửi email thông báo huỷ đến receiver_email.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order_status_val = order.status.value if hasattr(order.status, "value") else str(order.status or "")
    if order.status not in (OrderStatus.PENDING, OrderStatus.AWAITING_DROP_OFF, OrderStatus.AT_HUB):
        raise HTTPException(
            status_code=400,
            detail=f"Orders can only be cancelled before takeoff (PENDING, AWAITING_DROP_OFF, AT_HUB); current status: {order_status_val}"
        )

    # Chặn nếu đơn hàng đang trên chuyến bay active
    active_m_res = await db.execute(
        select(Mission).where(
            Mission.order_id == order_id,
            Mission.status.in_([
                MissionStatus.FLYING,
                MissionStatus.ARRIVAL_COMPLETED,
                MissionStatus.MISSION_COMPLETED,
            ])
        )
    )
    if active_m_res.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="Order is already in or completed a flight and cannot be cancelled."
        )

    # Nếu có mission đã lên lịch nhưng chưa bay (SCHEDULED, PENDING_APPROVAL, APPROVED) thì huỷ luôn
    sched_m_res = await db.execute(
        select(Mission).where(
            Mission.order_id == order_id,
            Mission.status == MissionStatus.SCHEDULED
        )
    )
    for m in sched_m_res.scalars().all():
        m.status = MissionStatus.CANCELLED
        m.handling_status = None

    now = get_vn_naive_datetime()
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = now
    order.updated_at = now
    await db.commit()
    from app.modules.missions.bundle_service import invalidate_route_analysis_cache
    invalidate_route_analysis_cache()

    # Gửi email thông báo huỷ ở nền sau khi commit
    if order.receiver_email:
        from app.core.email import send_delivery_email
        background_tasks.add_task(
            send_delivery_email,
            to_email=order.receiver_email,
            order_id=order_id,
            mission_code=None,
            frontend_url=settings.FRONTEND_URL,
        )
    else:
        print(f"[EMAIL] Order {order_id} không có receiver_email, bỏ qua")

    return {"message": "Order cancelled", "order_id": order_id, "status": "CANCELLED"}


@router.patch("/{order_id}", response_model=OrderResponse)
async def update_order(
    order_id: str,
    body: OrderUpdateRequest,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API] Cập nhật đơn hàng (chỉ khi còn PENDING)**

    Cho phép sửa thông tin kiện hàng, hub giao, phương thức giao, thời gian mong muốn/lên lịch
    khi đơn chưa được lên chuyến. Chỉ cập nhật các trường được truyền lên.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=400, detail="Only PENDING orders can be updated")

    if body.destination_hub_id is not None:
        if not await db.get(Hub, body.destination_hub_id):
            raise HTTPException(status_code=404, detail="Destination hub not found")
        order.destination_hub_id = body.destination_hub_id
    if body.payload_kg is not None:
        if body.payload_kg <= 0:
            raise HTTPException(status_code=400, detail="payload_kg must be greater than 0")
        order.payload_kg = body.payload_kg
    if body.package_label is not None:
        order.package_label = body.package_label
    if body.package_type is not None:
        order.package_type = body.package_type
    if body.delivery_mode is not None:
        order.delivery_mode = body.delivery_mode
    if body.requested_delivery_at is not None:
        order.requested_delivery_at = body.requested_delivery_at
    if body.planning_at is not None:
        order.planning_at = body.planning_at

    order.updated_at = get_vn_naive_datetime()
    await db.commit()
    await db.refresh(order)

    mission_result = await db.execute(
        select(Mission)
        .where(Mission.order_id == order_id)
        .order_by(Mission.id.desc())
        .limit(1)
    )
    latest_mission = mission_result.scalars().first()
    return await _build_order_response_detail(db, order, latest_mission)
