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
    DroneEligibleSchema
)

router = APIRouter(prefix="/operator/orders", tags=["Operator - Orders"])
allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])

def calculate_planning_state(order: Order, active_mission: Mission = None) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if order.status != OrderStatus.PENDING:
        return None
    if order.replan_required_at and not active_mission:
        return "NEEDS_REPLANNING"
    if active_mission:
        return "PLANNED"
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
        2. Tự động tính toán `planningState` dựa trên giờ lên lịch (`planning_at`) và trạng thái có Mission nào đang active không.
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
    
    response_list = []
    for o in orders:
        # Tạm thời gán active_mission = None cho việc tính toán state (Thực tế cần query join)
        planning_state = calculate_planning_state(o, None)
        origin_pkg = OriginPackageSchema(
            received=True if o.origin_received_at else False,
            received_at=o.origin_received_at
        )
        order_resp = OrderResponse.model_validate(o)
        order_resp.planningState = planning_state
        order_resp.originPackage = origin_pkg
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
        2. Truy vấn DB xem đơn hàng này đã có `Mission` nào đang được lên lịch (SCHEDULED) hoặc đang bay (IN_PROGRESS) hay chưa để trả về `planningState` tương ứng (Ví dụ: `PLANNED` nếu đã có chuyến bay).
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
        
    # Get active mission
    mission_result = await db.execute(
        select(Mission).where(and_(Mission.order_code == order_id, Mission.status.in_([MissionStatus.SCHEDULED, MissionStatus.IN_PROGRESS])))
    )
    active_mission = mission_result.scalars().first()
    
    planning_state = calculate_planning_state(order, active_mission)
    origin_pkg = OriginPackageSchema(
        received=True if order.origin_received_at else False,
        received_at=order.origin_received_at
    )
    
    order_resp = OrderResponse.model_validate(order)
    order_resp.planningState = planning_state
    order_resp.originPackage = origin_pkg
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
        3. Kiểm tra Drone phải ở trạng thái rảnh (`AVAILABLE`).
        4. Đảm bảo sức tải của Drone (`payload_capacity_kg`) phải lớn hơn hoặc bằng khối lượng của đơn hàng (`payload_kg`).
    - **Lưu ý**: Khác với API check-availability cũ (chỉ kiểm tra chung chung toàn hệ thống), API này tuân thủ nguyên tắc thực tế: Hàng ở Hub nào thì chỉ được dùng Drone đang đỗ ở Hub đó.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if not order.origin_hub_id:
        raise HTTPException(status_code=400, detail="Order does not have an origin hub")
        
    hub = await db.get(Hub, order.origin_hub_id)
    if not hub:
        raise HTTPException(status_code=404, detail="Origin Hub not found")
        
    # Find all drones at this hub
    drones_query = select(Drone).where(and_(
        Drone.current_hub_id == hub.id,
        Drone.status == DroneStatus.AVAILABLE,
        Drone.payload_capacity_kg >= (order.payload_kg or 0)
    ))
    drones_result = await db.execute(drones_query)
    eligible_drones = drones_result.scalars().all()
    
    # We can refine this to check for active missions for these drones
    # but for now we rely on DroneStatus.AVAILABLE
    
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
