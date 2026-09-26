from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser, get_current_user
from app.shared.roles import UserRole
from app.modules.missions.models import Mission, MissionStatus
from app.modules.missions.schemas import MissionKPIResponse, MissionStatusResponse, ActiveFlightsResponse, MissionResponse

router = APIRouter(prefix="/missions", tags=["Missions"])

allow_operator = RoleChecker([UserRole.OPERATOR, UserRole.ADMIN])
allow_operator_and_customer = RoleChecker([UserRole.OPERATOR, UserRole.CUSTOMER])


# ── Operator Dashboard ────────────────────────────────────────────────────────

@router.get("/dashboard/kpi", response_model=MissionKPIResponse)
async def get_mission_kpi(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy các chỉ số KPI của nhiệm vụ.
    API này trả về tổng số nhiệm vụ, số lượng hoàn thành, bị hủy, bị từ chối,
    tỷ lệ thành công, khoảng cách trung bình, tải trọng trung bình và tổng doanh thu.
    Dành cho Operator hoặc Admin.
    """
    result = await db.execute(select(Mission))
    missions = result.scalars().all()

    total = len(missions)
    completed = sum(1 for m in missions if m.status == MissionStatus.COMPLETED)
    cancelled = sum(1 for m in missions if m.status == MissionStatus.CANCELLED)
    rejected = sum(1 for m in missions if m.status == MissionStatus.REJECTED)
    finished = completed + cancelled + rejected

    distances = [m.distance_m for m in missions if m.distance_m]
    payloads = [m.payload_weight for m in missions if m.payload_weight]
    revenues = [m.delivery_fee for m in missions if m.delivery_fee and m.status == MissionStatus.COMPLETED]

    return MissionKPIResponse(
        total_missions=total,
        completed=completed,
        cancelled=cancelled,
        rejected=rejected,
        success_rate=round(completed / finished * 100, 2) if finished else 0.0,
        avg_distance_m=round(sum(distances) / len(distances), 2) if distances else 0.0,
        avg_payload_kg=round(sum(payloads) / len(payloads), 2) if payloads else 0.0,
        total_revenue=round(sum(revenues), 2),
    )


@router.get("/dashboard/status", response_model=MissionStatusResponse)
async def get_mission_status(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy thống kê trạng thái của các nhiệm vụ.
    API này trả về số lượng nhiệm vụ được nhóm theo từng trạng thái (ví dụ: đang bay, hoàn thành, hủy bỏ).
    Dành cho Operator hoặc Admin.
    """
    result = await db.execute(
        select(Mission.status, func.count().label("count")).group_by(Mission.status)
    )
    rows = result.all()
    return MissionStatusResponse(
        status_breakdown={row.status.value: row.count for row in rows}
    )


@router.get("/dashboard/active-flights", response_model=ActiveFlightsResponse)
async def get_active_flights(
    user: CurrentUser = Depends(allow_operator),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy danh sách các chuyến bay đang hoạt động.
    API này trả về các nhiệm vụ hiện có trạng thái là FLYING cùng với thông tin chi tiết.
    Dành cho Operator hoặc Admin.
    """
    result = await db.execute(
        select(Mission).where(Mission.status == MissionStatus.FLYING)
    )
    missions = result.scalars().all()
    return ActiveFlightsResponse(
        active_count=len(missions),
        missions=[
            {
                "id": m.id,
                "drone_id": m.drone_id,
                "operator_id": m.operator_id,
                "payload_weight": m.payload_weight,
                "distance_m": m.distance_m,
                "start_time": m.start_time.isoformat() if m.start_time else None,
            }
            for m in missions
        ],
    )


# ── General ───────────────────────────────────────────────────────────────────

@router.post("/request")
async def create_mission_request(user: CurrentUser = Depends(allow_operator_and_customer)):
    """
    Tạo một yêu cầu nhiệm vụ mới.
    API này cho phép người điều khiển (Operator) hoặc khách hàng (Customer) tạo mới yêu cầu nhiệm vụ bay.
    """
    return {"message": "Mission request created."}


@router.get("/", response_model=list[MissionResponse])
async def get_missions(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy danh sách tất cả các nhiệm vụ.
    API này trả về danh sách toàn bộ nhiệm vụ được sắp xếp theo ID giảm dần.
    Yêu cầu người dùng phải đăng nhập.
    """
    result = await db.execute(select(Mission).order_by(Mission.id.desc()))
    return result.scalars().all()
