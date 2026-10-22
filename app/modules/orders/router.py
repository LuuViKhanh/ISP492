from fastapi import APIRouter, Depends, HTTPException, Query
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
from app.modules.missions.technician.router import get_compatible_battery_models
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
)
from app.core.config import settings

router = APIRouter(prefix="/operator/orders", tags=["Orders"])
allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])

ACTIVE_MISSION_STATUS_STRINGS = {
    "SCHEDULED",
    "IN_PROGRESS",
    "APPROVED",
    "FLYING",
    "ACTIVE_MISSION",
    "PENDING_APPROVAL",
}

ACTIVE_MISSION_STATUSES = [
    MissionStatus.SCHEDULED,
    MissionStatus.IN_PROGRESS,
    MissionStatus.APPROVED,
    MissionStatus.FLYING,
    MissionStatus.ACTIVE_MISSION,
    MissionStatus.PENDING_APPROVAL,
]

COMPLETED_MISSION_STATUS_STRINGS = {
    "COMPLETED",
    "MISSION_COMPLETED",
}

FAILED_MISSION_STATUS_STRINGS = {
    "CANCELLED",
    "FAILED",
    "REJECTED",
    "INCIDENT_RETURN",
}


PLANNING_STATE_LABELS = {
    "WAITING_PACKAGE": "Chờ nhận hàng tại Hub",
    "READY_FOR_PLANNING": "Sẵn sàng lập lịch",
    "PLANNED": "Đã lập lịch bay",
    "FUTURE": "Chưa đến giờ lập lịch",
    "NEEDS_REPLANNING": "Cần lập lịch lại",
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

    # 2. Nếu đơn hàng đã có Mission liên kết
    if mission:
        ms_str = (mission.status.value if hasattr(mission.status, "value") else str(mission.status or "")).upper()
        if ms_str in COMPLETED_MISSION_STATUS_STRINGS or mission.arrived_at is not None:
            return None
        if ms_str in ACTIVE_MISSION_STATUS_STRINGS:
            return "PLANNED"
        if ms_str in FAILED_MISSION_STATUS_STRINGS:
            return "NEEDS_REPLANNING"

    # 3. Chưa tới Hub -> Đang chờ nhận hàng
    if order.status != OrderStatus.AT_HUB:
        return "WAITING_PACKAGE"

    if order.replan_required_at:
        return "NEEDS_REPLANNING"

    # 4. Kiểm tra Overdue / Urgent dựa trên customer_deadline (= estimated_window_end)
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
PLANNING_STATE_LABELS["OVERDUE"] = "Quá hạn giao — cần lập lịch ngay"
PLANNING_STATE_LABELS["URGENT"] = "Sắp đến hạn — ưu tiên cao"


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

    # UrgencyScore
    urgency = min(100.0, max(0.0, 100.0 * (60.0 - slack_mins) / 60.0))
    score = round(base + urgency, 1)

    # Priority level
    if slack_mins <= 0:
        level = "CRITICAL"
    elif slack_mins <= 30:
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
    """
    query = select(Order)
    if status:
        query = query.where(Order.status == status)
    if search:
        query = query.where(Order.id.ilike(f"%{search}%"))
        
    result = await db.execute(query.order_by(Order.created_at.desc()))
    orders = result.scalars().all()
    
    # Lấy tất cả hubs để map
    hubs_result = await db.execute(select(Hub))
    hubs = hubs_result.scalars().all()
    hub_dict = {h.id: h.name for h in hubs}

    # Lấy Mission mới nhất của từng Order để tính chính xác planningState
    order_ids = [o.id for o in orders]
    mission_by_order: dict[str, Mission] = {}
    if order_ids:
        missions_result = await db.execute(
            select(Mission)
            .where(Mission.order_id.in_(order_ids))
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

        # Chỉ cho phép tạo mission khi hàng thực sự đã nhận tại Hub, đơn đã AT_HUB và chưa có mission active
        ms_val = (latest_mission.status.value if hasattr(latest_mission.status, "value") else str(latest_mission.status or "")).upper() if latest_mission else ""
        order_resp.can_create_mission = bool(
            origin_pkg.received
            and o.status == OrderStatus.AT_HUB 
            and (latest_mission is None or ms_val in FAILED_MISSION_STATUS_STRINGS)
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
        if ohub and dhub and ohub.latitude and dhub.latitude:
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
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
        
    # Lấy mission mới nhất của đơn hàng
    mission_result = await db.execute(
        select(Mission)
        .where(Mission.order_id == order_id)
        .order_by(Mission.id.desc())
        .limit(1)
    )
    latest_mission = mission_result.scalars().first()
    
    planning_state = calculate_planning_state(order, latest_mission)
    origin_pkg = compute_origin_package_status(order, latest_mission)
    
    order_resp = OrderResponse.model_validate(order)
    order_resp.mission_id = latest_mission.id if latest_mission else None
    order_resp.payment_status = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    order_resp.planningState = planning_state
    order_resp.planning_state_label = PLANNING_STATE_LABELS.get(planning_state)
    order_resp.originPackage = origin_pkg

    ms_val = (latest_mission.status.value if hasattr(latest_mission.status, "value") else str(latest_mission.status or "")).upper() if latest_mission else ""
    order_resp.can_create_mission = bool(
        origin_pkg.received
        and order.status == OrderStatus.AT_HUB 
        and (latest_mission is None or ms_val in FAILED_MISSION_STATUS_STRINGS)
    )

    ohub = None
    dhub = None
    if order.origin_hub_id:
        ohub = await db.get(Hub, order.origin_hub_id)
        if ohub: order_resp.origin_hub = HubSchema(id=ohub.id, name=ohub.name)
    if order.destination_hub_id:
        dhub = await db.get(Hub, order.destination_hub_id)
        if dhub: order_resp.destination_hub = HubSchema(id=dhub.id, name=dhub.name)

    w_start, w_end = compute_order_estimated_window(order, ohub, dhub)
    order_resp.estimated_window_start = w_start
    order_resp.estimated_window_end = w_end

    # Urgency fields
    _dist = 8.0
    if ohub and dhub and ohub.latitude and dhub.latitude:
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


async def _get_reserved_battery_ids(db: AsyncSession, exclude_order_id: str | None = None) -> set[str]:
    """Lấy tập hợp battery_id đang được giữ chỗ bởi các Mission/MissionLeg đang hoạt động."""
    active_list = list(ACTIVE_MISSION_STATUSES)
    m_stmt = select(Mission.battery_id).where(
        Mission.status.in_(active_list),
        Mission.battery_id.is_not(None),
    )
    if exclude_order_id:
        m_stmt = m_stmt.where(Mission.order_id != exclude_order_id)
    m_res = await db.execute(m_stmt)
    reserved_ids = {row[0] for row in m_res.all() if row[0]}

    leg_stmt = (
        select(MissionLeg.battery_id)
        .join(Mission, MissionLeg.mission_id == Mission.id)
        .where(
            Mission.status.in_(active_list),
            MissionLeg.status.in_([MissionLegStatus.PENDING, MissionLegStatus.IN_PROGRESS]),
            MissionLeg.battery_id.is_not(None),
        )
    )
    if exclude_order_id:
        leg_stmt = leg_stmt.where(Mission.order_id != exclude_order_id)
    leg_res = await db.execute(leg_stmt)
    for row in leg_res.all():
        if row[0]:
            reserved_ids.add(row[0])
    return reserved_ids


def _build_battery_eligible_schema(b: Battery, drone_id: Union[str, int, None] = None) -> BatteryEligibleSchema:
    cap_wh = float(b.capacity_wh or 0.0)
    charge_pct = int(b.charge_level_pct if b.charge_level_pct is not None else 100)
    avail_energy_wh = round(cap_wh * (charge_pct / 100.0), 2)
    avail_status = compute_battery_available_status(b)
    return BatteryEligibleSchema(
        id=b.id,
        serial_number=b.serial_number,
        battery_model=b.battery_model,
        capacity_wh=cap_wh,
        charge_level_pct=charge_pct,
        available_energy_wh=avail_energy_wh,
        current_hub_id=b.current_hub_id,
        drone_id=b.drone_id,
        is_installed_on_drone=bool(drone_id is not None and b.drone_id == drone_id),
        available_status=avail_status.value if hasattr(avail_status, "value") else str(avail_status),
    )


@router.get("/{order_id}/eligible-drones", response_model=EligibleDronesResponse)
async def get_eligible_drones(
    order_id: str,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API] Tìm kiếm Drone và Pin đủ điều kiện cho đơn hàng**
    
    - **Mục đích**: Liệt kê các Drone có sẵn tại trạm xuất phát (Origin Hub) kèm danh sách Pin khả dụng tương thích với từng Drone để Operator chọn khi tạo Mission.
    - **Hoạt động**:
        1. Tìm Origin Hub của `order_id` truyền vào.
        2. Lọc các Drone đang nằm tại Origin Hub này (`current_hub_id == origin_hub_id`), ở trạng thái `AVAILABLE`, đủ tải trọng và chưa bị gán cho Mission active khác.
        3. Với mỗi Drone, lọc danh sách Pin tương thích (`eligible_batteries`) đang ở Origin Hub (hoặc gắn sẵn trên Drone đó), trạng thái `ACTIVE`, chưa bị giữ chỗ bởi Mission/MissionLeg khác, kèm năng lượng thực tế (`available_energy_wh`).
    """
    order_id = order_id.strip()
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if not order.origin_hub_id:
        raise HTTPException(status_code=400, detail="Order does not have an origin hub")
        
    hub = await db.get(Hub, order.origin_hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Origin Hub not found")

    # Loại trừ các drone đang được phân công cho mission active khác
    busy_drones_res = await db.execute(
        select(Mission.drone_id).where(
            Mission.drone_id.is_not(None),
            Mission.order_id != order_id,
            Mission.status.in_(list(ACTIVE_MISSION_STATUSES))
        )
    )
    busy_drone_ids = {row[0] for row in busy_drones_res.all() if row[0] is not None}

    # Find all drones at this hub
    drones_query = select(Drone).where(and_(
        Drone.current_hub_id == hub.id,
        Drone.status == DroneStatus.AVAILABLE,
        Drone.payload_capacity_kg >= (order.payload_kg or 0)
    )).order_by(Drone.id)
    drones_result = await db.execute(drones_query)
    eligible_drones = [d for d in drones_result.scalars().all() if d.id not in busy_drone_ids]
    
    total_drones_result = await db.execute(select(func.count(Drone.id)).where(Drone.current_hub_id == hub.id))
    total_drones = total_drones_result.scalar() or 0

    reserved_battery_ids = await _get_reserved_battery_ids(db, exclude_order_id=order_id)
    eligible_drone_ids = {d.id for d in eligible_drones}

    # Lấy tất cả pin ACTIVE tại Origin Hub hoặc đang gắn trên các drone đủ điều kiện
    bat_filters = [Battery.current_hub_id == hub.id]
    if eligible_drone_ids:
        bat_filters.append(Battery.drone_id.in_(eligible_drone_ids))

    bats_stmt = select(Battery).where(
        Battery.status == batteries_status.ACTIVE,
        or_(*bat_filters),
    ).order_by(Battery.id)
    hub_batteries = [
        b for b in (await db.execute(bats_stmt)).scalars().all()
        if b.id not in reserved_battery_ids
        and (b.current_hub_id == hub.id or (b.drone_id is not None and b.drone_id in eligible_drone_ids))
        and (b.drone_id is None or b.drone_id in eligible_drone_ids)
    ]

    drones_schema_list: list[DroneEligibleSchema] = []
    for d in eligible_drones:
        compat_models = await get_compatible_battery_models(db, d.model)
        d_bats = [
            b for b in hub_batteries
            if (b.drone_id is None or b.drone_id == d.id)
            and (not compat_models or (b.battery_model and b.battery_model in compat_models))
        ]
        # Ưu tiên: (1) đang gắn trên drone & sạc 100%, (2) sạc >= 100% tại hub, (3) mức sạc & dung lượng Wh cao nhất
        d_bats.sort(
            key=lambda b: (
                0 if (b.drone_id == d.id and (b.charge_level_pct or 0) >= 100) else (1 if (b.charge_level_pct or 0) >= 100 else 2),
                -(b.charge_level_pct or 0),
                -float(b.capacity_wh or 0.0),
                b.id,
            )
        )
        installed_bat = next((b for b in d_bats if b.drone_id == d.id), None)
        default_bat = d_bats[0] if d_bats else None

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
                eligible_batteries=[_build_battery_eligible_schema(b, d.id) for b in d_bats],
            )
        )

    return EligibleDronesResponse(
        hub=HubEligibleSchema(
            id=hub.id,
            name=hub.name or f"Hub {hub.id}",
            totalDrones=total_drones,
            availableDrones=len(eligible_drones)
        ),
        drones=drones_schema_list,
        batteries=[_build_battery_eligible_schema(b) for b in hub_batteries],
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
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if not order.origin_hub_id:
        raise HTTPException(status_code=400, detail="Order does not have an origin hub")

    hub = await db.get(Hub, order.origin_hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Origin Hub not found")

    drone = await db.get(Drone, drone_id)
    if not drone:
        raise HTTPException(status_code=404, detail="Drone not found")

    compat_models = await get_compatible_battery_models(db, drone.model)
    reserved_battery_ids = await _get_reserved_battery_ids(db, exclude_order_id=order_id)

    bats_stmt = select(Battery).where(
        Battery.status == batteries_status.ACTIVE,
        or_(
            and_(Battery.current_hub_id == hub.id, Battery.drone_id.is_(None)),
            Battery.drone_id == drone.id,
        )
    ).order_by(Battery.id)
    bats = [
        b for b in (await db.execute(bats_stmt)).scalars().all()
        if b.id not in reserved_battery_ids
        and ((b.current_hub_id == hub.id and b.drone_id is None) or b.drone_id == drone.id)
        and (not compat_models or (b.battery_model and b.battery_model in compat_models))
    ]
    bats.sort(
        key=lambda b: (
            0 if (b.drone_id == drone.id and (b.charge_level_pct or 0) >= 100) else (1 if (b.charge_level_pct or 0) >= 100 else 2),
            -(b.charge_level_pct or 0),
            -float(b.capacity_wh or 0.0),
            b.id,
        )
    )
    default_bat = bats[0] if bats else None

    return EligibleBatteriesResponse(
        order_id=order.id,
        hub_id=hub.id,
        hub_name=hub.name or f"Hub {hub.id}",
        drone_id=drone.id,
        drone_name=drone.name,
        drone_model=drone.model,
        compatible_battery_models=compat_models,
        default_battery_id=default_bat.id if default_bat else None,
        batteries=[_build_battery_eligible_schema(b, drone.id) for b in bats],
    )


@router.post("/{order_id}/cancel", summary="Huỷ đơn hàng")
async def cancel_order(
    order_id: str,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Huỷ đơn hàng — chỉ cho phép khi order đang PENDING.
    Tự động gửi email thông báo huỷ đến receiver_email.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Chỉ có thể huỷ order đang PENDING, hiện tại: {order.status}"
        )

    now = get_vn_naive_datetime()
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = now
    order.updated_at = now
    await db.commit()

    # Gửi email thông báo huỷ
    try:
        from app.core.email import send_delivery_email
        if order.receiver_email:
            await send_delivery_email(
                to_email=order.receiver_email,
                order_id=order_id,
                mission_code=None,
                frontend_url=settings.FRONTEND_URL,
            )
        else:
            print(f"[EMAIL] Order {order_id} không có receiver_email, bỏ qua")
    except Exception as e:
        print(f"[EMAIL] Lỗi gửi email huỷ order: {e}")

    return {"message": "Đã huỷ đơn hàng", "order_id": order_id, "status": "CANCELLED"}


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

    resp = OrderResponse.model_validate(order)
    resp.mission_id = latest_mission.id if latest_mission else None
    resp.payment_status = order.payment_status.value if hasattr(order.payment_status, "value") else str(order.payment_status or "")
    resp.planningState = calculate_planning_state(order, latest_mission)
    resp.originPackage = compute_origin_package_status(order, latest_mission)
    return resp
