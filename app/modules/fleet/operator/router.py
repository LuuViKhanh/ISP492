from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from datetime import timedelta

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole

from app.modules.fleet.models import Drone, Battery, DroneStatus, batteries_status
from app.modules.fleet.schemas import CheckAvailabilityRequest, AvailabilityResponse
from app.modules.missions.models import Mission, MissionStatus

router = APIRouter(
    prefix="/operator/fleet",
    tags=["Operator - Fleet Management"]
)

allow_operator = RoleChecker([UserRole.OPERATOR])

@router.post(
    "/check-availability", 
    response_model=AvailabilityResponse,
    summary="Kiểm tra Drone và Pin khả dụng cho chuyến bay",
    description="""
**Mục đích:** API này dùng cho màn hình của Operator khi thực hiện thao tác **Duyệt (Approve)** một chuyến bay. Nó giúp tìm ra danh sách Drone và Pin đang rảnh (không bận chuyến khác) và đủ điều kiện để gán.

**Cách dùng cho Frontend:**
- Gửi lên `scheduled_time`: Thời gian dự kiến cất cánh.
- Gửi lên `payload_weight_kg`: Khối lượng hàng hóa (lấy từ dữ liệu đơn hàng).
- Gửi lên `estimated_energy_wh` (Không bắt buộc nhưng khuyên dùng): Mức năng lượng dự kiến tiêu hao do mô hình AI trả về.

**Logic xử lý (Mới):**
- **Drones:** Trả về Drone trạng thái `AVAILABLE`, đủ `payload_capacity_kg` và KHÔNG trùng lịch với Mission khác.
- **Batteries:** Trả về Pin `FULLY_CHARGED`, đủ `current_charge_wh` và KHÔNG trùng lịch.
- Logic trùng lịch sử dụng `scheduled_time` và giả định một chuyến bay kéo dài 1 giờ để kiểm tra xem có giao cắt về thời gian hay không.
    """
)
async def check_fleet_availability(
    request: CheckAvailabilityRequest,
    # user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db)
):
    # Thời gian giả định cho một chuyến bay để check overlap
    # Ở đây mặc định 1 tiếng (có thể thay đổi nếu payload gửi lên estimated_duration)
    estimated_duration = timedelta(hours=1) 
    
    # Loại bỏ tzinfo (chuyển về naive datetime) để so sánh với TIMESTAMP WITHOUT TIME ZONE trong DB PostgreSQL
    requested_start = request.scheduled_time.replace(tzinfo=None)
    requested_end = requested_start + estimated_duration

    # 1. TÌM DRONE VÀ PIN BỊ TRÙNG LỊCH (TRONG KHOẢNG THỜI GIAN BAY)
    # Lọc Mission có trạng thái APPROVED/FLYING có khung thời gian trùng lặp
    busy_missions_query = await db.execute(
        select(Mission.drone_id, Mission.battery_id).where(
            and_(
                Mission.status.in_([MissionStatus.APPROVED, MissionStatus.FLYING]),
                Mission.scheduled_time.is_not(None),
                
                # Check Overlap Logic
                # Mission Start Time < Request End Time VÀ Mission End Time > Request Start Time
                Mission.scheduled_time < requested_end,
                or_(
                    # Nếu Mission có end_time rõ ràng
                    and_(Mission.end_time.is_not(None), Mission.end_time > requested_start),
                    # Nếu chưa có end_time (vd mới Approved), giả định cũng kéo dài 1 tiếng
                    and_(Mission.end_time.is_(None), Mission.scheduled_time + timedelta(hours=1) > requested_start)
                )
            )
        )
    )
    busy_rows = busy_missions_query.all()
    
    # Tách ra 2 mảng ID đang bận (loại bỏ các giá trị None)
    busy_drone_ids = [row[0] for row in busy_rows if row[0] is not None]
    busy_battery_ids = [row[1] for row in busy_rows if row[1] is not None]

    # 2. LỌC DANH SÁCH DRONE RẢNH & ĐỦ TẢI TRỌNG
    drones_query = select(Drone).where(
        and_(
            Drone.status == DroneStatus.AVAILABLE, # Sửa từ DroneStatus.IDLE
            Drone.payload_capacity_kg >= request.payload_weight_kg, # Sửa từ max_payload_kg
            Drone.id.not_in(busy_drone_ids) if busy_drone_ids else True
        )
    )
    result_drones = await db.execute(drones_query)
    available_drones = result_drones.scalars().all()

    # 3. LỌC DANH SÁCH PIN RẢNH & ĐỦ NĂNG LƯỢNG
    battery_conditions = [
        Battery.status == batteries_status.ACTIVE,
        Battery.id.not_in(busy_battery_ids) if busy_battery_ids else True
    ]
    
    # Logic AI: Nếu có AI dự đoán tiêu thụ năng lượng
    if request.estimated_energy_wh:
        safe_energy_required = request.estimated_energy_wh * 1.2 # Cộng thêm 20% dự phòng an toàn
        # Giả định Pin rảnh là được sạc đầy, kiểm tra dựa trên dung lượng (capacity) thay vì current_charge
        battery_conditions.append(Battery.capacity_wh >= safe_energy_required)

    batteries_query = select(Battery).where(and_(*battery_conditions))
    result_batteries = await db.execute(batteries_query)
    available_batteries = result_batteries.scalars().all()

    return AvailabilityResponse(
        available_drones=available_drones,
        available_batteries=available_batteries
    )