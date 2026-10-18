from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from datetime import datetime, timezone
import math

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.models import Order, OrderStatus, Mission, MissionStatus
from app.modules.system.models import Hub
from app.modules.fleet.models import Drone, DroneStatus
from app.modules.orders.schemas import (
    OrderResponse,
    OriginPackageSchema,
    EligibleDronesResponse,
    HubEligibleSchema,
    DroneEligibleSchema,
    OrderUpdateRequest,
)
from app.core.config import settings

router = APIRouter(prefix="/operator/orders", tags=["Orders"])
allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])

ACTIVE_MISSION_STATUSES = {
    MissionStatus.SCHEDULED,
    MissionStatus.IN_PROGRESS,
    MissionStatus.APPROVED,
    MissionStatus.FLYING,
    MissionStatus.ACTIVE_MISSION,
    MissionStatus.PENDING_APPROVAL,
}

COMPLETED_MISSION_STATUSES = {
    MissionStatus.COMPLETED,
    MissionStatus.MISSION_COMPLETED,
}

FAILED_MISSION_STATUSES = {
    MissionStatus.CANCELLED,
    MissionStatus.FAILED,
    MissionStatus.REJECTED,
    MissionStatus.INCIDENT_RETURN,
}


def calculate_planning_state(order: Order, mission: Mission = None) -> str | None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Đơn hàng đã hủy, đang giao hoặc đã giao tới Hub đích -> Không còn ở bước lập lịch
    if order.status in (
        OrderStatus.CANCELLED,
        OrderStatus.IN_DELIVERY,
        OrderStatus.DELIVERED_TO_HUB,
    ):
        return None

    # 2. Chỉ những đơn hàng đã được Technician xác nhận nhận hàng tại Hub (AT_HUB)
    # mới được phép tạo mission hoặc hiển thị trạng thái lập lịch (Issue #2)
    if order.status != OrderStatus.AT_HUB:
        return None

    # 3. Nếu đơn hàng đã có Mission (khi đã AT_HUB)
    if mission:
        # Đã bay xong -> Không hiện Ready for planning hay Planned nữa
        if mission.status in COMPLETED_MISSION_STATUSES or mission.arrived_at is not None:
            return None
        # Đang có Mission hiệu lực (Scheduled, Approved, In Progress, Flying...) -> Đã lập lịch (PLANNED)
        if mission.status in ACTIVE_MISSION_STATUSES:
            return "PLANNED"
        # Mission trước đó bị hủy/lỗi -> Cần lập lịch lại
        if mission.status in FAILED_MISSION_STATUSES:
            return "NEEDS_REPLANNING"

    if order.replan_required_at:
        return "NEEDS_REPLANNING"
    if order.planning_at and now < order.planning_at:
        return "FUTURE"
    return "READY_FOR_PLANNING"


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
    for o in orders:
        latest_mission = mission_by_order.get(o.id)
        planning_state = calculate_planning_state(o, latest_mission)
        origin_pkg = OriginPackageSchema(
            received=True if o.status == OrderStatus.AT_HUB else False,
            received_at=o.origin_received_at if o.status == OrderStatus.AT_HUB else None
        )
        order_resp = OrderResponse.model_validate(o)
        order_resp.planningState = planning_state
        order_resp.originPackage = origin_pkg
        if o.origin_hub_id in hub_dict:
            order_resp.origin_hub = {"id": o.origin_hub_id, "name": hub_dict[o.origin_hub_id]}
        if o.destination_hub_id in hub_dict:
            order_resp.destination_hub = {"id": o.destination_hub_id, "name": hub_dict[o.destination_hub_id]}
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
    origin_pkg = OriginPackageSchema(
        received=True if order.status == OrderStatus.AT_HUB else False,
        received_at=order.origin_received_at if order.status == OrderStatus.AT_HUB else None
    )
    
    order_resp = OrderResponse.model_validate(order)
    order_resp.planningState = planning_state
    order_resp.originPackage = origin_pkg
    
    if order.origin_hub_id:
        ohub = await db.get(Hub, order.origin_hub_id)
        if ohub: order_resp.origin_hub = {"id": ohub.id, "name": ohub.name}
    if order.destination_hub_id:
        dhub = await db.get(Hub, order.destination_hub_id)
        if dhub: order_resp.destination_hub = {"id": dhub.id, "name": dhub.name}
        
    return order_resp


@router.get("/{order_id}/eligible-drones", response_model=EligibleDronesResponse)
async def get_eligible_drones(
    order_id: str,
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    **[Operator API] Tìm kiếm Drone đủ điều kiện cho đơn hàng**
    
    - **Mục đích**: Liệt kê các Drone có sẵn tại trạm xuất phát (Origin Hub) của kiện hàng để Operator chọn giao việc.
    - **Hoạt động**:
        1. Tìm Origin Hub của `order_id` truyền vào.
        2. Lọc các Drone đang nằm tại Origin Hub này (`current_hub_id == origin_hub_id`).
        3. Kiểm tra Drone phải ở trạng thái rảnh (`AVAILABLE`) và chưa bị gán cho Mission active khác.
        4. Đảm bảo sức tải của Drone (`payload_capacity_kg`) phải lớn hơn hoặc bằng khối lượng của đơn hàng (`payload_kg`).
    - **Lưu ý**: Khác với API check-availability cũ (chỉ kiểm tra chung chung toàn hệ thống), API này tuân thủ nguyên tắc thực tế: Hàng ở Hub nào thì chỉ được dùng Drone đang đỗ ở Hub đó.
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
    ))
    drones_result = await db.execute(drones_query)
    eligible_drones = [d for d in drones_result.scalars().all() if d.id not in busy_drone_ids]
    
    total_drones_result = await db.execute(select(func.count(Drone.id)).where(Drone.current_hub_id == hub.id))
    total_drones = total_drones_result.scalar() or 0
    
    return EligibleDronesResponse(
        hub=HubEligibleSchema(
            id=hub.id,
            name=hub.name or f"Hub {hub.id}",
            totalDrones=total_drones,
            availableDrones=len(eligible_drones)
        ),
        drones=[
            DroneEligibleSchema(id=d.id, model=d.model, maxPayloadKg=d.payload_capacity_kg)
            for d in eligible_drones
        ]
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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
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

    order.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
    resp.planningState = calculate_planning_state(order, latest_mission)
    resp.originPackage = OriginPackageSchema(
        received=bool(order.origin_received_at or order.status == OrderStatus.AT_HUB),
        received_at=order.origin_received_at,
    )
    return resp
