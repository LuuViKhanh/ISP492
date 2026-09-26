from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

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
    - Thông tin chi tiết của người dùng bao gồm id, họ tên, ...
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
    
    Có thể cập nhật các trường như họ tên (full_name) và tên hiển thị (username).
    
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
    if body.username is not None:
        user.username = body.username
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

