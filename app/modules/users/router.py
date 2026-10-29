from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.db import get_async_db
from app.modules.auth.service import get_user_by_id
from app.modules.users.schemas import ProfileResponse, ProfileUpdateRequest
from app.shared.dependencies import RoleChecker, CurrentUser, get_current_user
from app.shared.roles import UserRole

router = APIRouter(prefix="/users", tags=["Users"])

require_admin = RoleChecker([UserRole.ADMIN])


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/me", response_model=ProfileResponse)
async def get_profile(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Lấy thông tin cá nhân của người dùng hiện tại (profile).
    
    Yêu cầu:
    - Người dùng phải đăng nhập (gửi kèm token).
    
    Trả về:
    - Thông tin chi tiết của người dùng bao gồm id, họ tên, vai trò, trạng thái...
    """
    user = await get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/me", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Cập nhật thông tin cá nhân của người dùng hiện tại.
    
    Có thể cập nhật các trường như họ tên (full_name).
    
    Yêu cầu:
    - Người dùng phải đăng nhập hợp lệ.
    
    Trả về:
    - Thông tin người dùng sau khi đã được cập nhật thành công.
    """
    user = await get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.full_name is not None:
        user.full_name = body.full_name
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/me")
async def delete_my_account(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Người dùng tự xóa tài khoản cá nhân (Xóa mềm - Soft Delete).
    
    - Kiểm tra các ràng buộc nghiệp vụ:
      + Không cho phép xóa nếu Customer đang có đơn hàng chưa hoàn tất hoặc đang bay.
      + Không cho phép xóa nếu Technician đang có phiếu bảo trì (WorkOrder) chưa hoàn tất.
    - Cơ chế chuyển trạng thái:
      + Đổi `user.is_active = False`.
      + Phiên đăng nhập hiện tại lập tức bị ngắt (JWT bị từ chối).
      + Không thể đăng nhập lại vào hệ thống.
      + Lịch sử giao dịch, đơn hàng và bảo dưỡng vẫn được bảo toàn nguyên vẹn.
    """
    user = await get_user_by_id(db, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 1. Ràng buộc đối với Khách hàng (Customer): không có đơn hàng đang xử lý
    from app.modules.missions.models import Order, OrderStatus
    active_order_statuses = [
        OrderStatus.PENDING,
        OrderStatus.AWAITING_DROP_OFF,
        OrderStatus.AT_HUB,
        OrderStatus.IN_DELIVERY,
    ]
    order_check = await db.execute(
        select(Order.id).where(
            Order.customer_id == current_user.id,
            Order.status.in_(active_order_statuses)
        ).limit(1)
    )
    if order_check.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Không thể xóa tài khoản khi bạn đang có đơn hàng chưa hoàn tất hoặc đang được giao."
        )

    # 2. Ràng buộc đối với Kỹ thuật viên (Technician): không có phiếu bảo trì dang dở
    if current_user.role == UserRole.TECHNICIAN:
        from app.modules.fleet.models import WorkOrder, WorkOrderStatus
        active_wo_statuses = [
            WorkOrderStatus.PENDING,
            WorkOrderStatus.IN_PROGRESS,
        ]
        wo_check = await db.execute(
            select(WorkOrder.id).where(
                WorkOrder.technician_id == current_user.id,
                WorkOrder.status.in_(active_wo_statuses)
            ).limit(1)
        )
        if wo_check.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Không thể xóa tài khoản kỹ thuật viên khi đang có phiếu bảo trì chưa hoàn thành."
            )

    # 3. Soft Delete: chuyển trạng thái is_active = False
    user.is_active = False
    db.add(user)
    await db.commit()

    # 4. Ghi audit log
    from app.modules.system.service import log_action
    await log_action(db, user.id, "SELF_DELETE", "users", {"email": user.email})

    return {
        "success": True,
        "message": "Tài khoản của bạn đã được xóa thành công khỏi hệ thống."
    }
