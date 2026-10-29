from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.db import get_async_db
from app.modules.auth.service import get_user_by_id, get_user_by_email, hash_password
from app.modules.auth.models import User, ROLE_NAME_TO_ID
from app.modules.users.schemas import ProfileResponse, UserCreate, UserAdminUpdate
from app.shared.dependencies import RoleChecker
from app.shared.roles import UserRole

router = APIRouter(
    prefix="/admin/users",
    tags=["Users"],
    dependencies=[Depends(RoleChecker([UserRole.ADMIN]))]
)

@router.get("/", response_model=list[ProfileResponse])
async def get_all_users(
    is_active: Optional[bool] = Query(None, description="Lọc theo trạng thái hoạt động: true = đang hoạt động, false = đã xóa/vô hiệu hóa, null = tất cả"),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Lấy danh sách người dùng trong hệ thống (kèm bộ lọc trạng thái xóa/hoạt động).
    
    Mục đích: Cung cấp cho Admin danh sách người dùng để quản trị, có thể lọc người dùng đang hoạt động hoặc đã bị xóa mềm.
    """
    query = select(User)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    query = query.order_by(User.created_at.desc())
    result = await db.execute(query)
    users = result.scalars().all()
    return [ProfileResponse.model_validate(u) for u in users]

@router.post("/", response_model=ProfileResponse)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Tạo tài khoản người dùng mới và gán vai trò (Role).
    Nếu email thuộc về tài khoản đã bị xóa mềm, hệ thống sẽ tự động lưu trữ (archive) tài khoản cũ.
    """
    existing = await get_user_by_email(db, body.email)
    if existing:
        if existing.is_active:
            raise HTTPException(status_code=400, detail="Email already registered")
        import time
        archived_email = f"archived_{int(time.time())}_{existing.email}"
        existing.email = archived_email
        db.add(existing)
        await db.flush()
        
    new_user = User(
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role_id=ROLE_NAME_TO_ID[body.role]
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user

@router.get("/{user_id}", response_model=ProfileResponse)
async def get_user(user_id: str, db: AsyncSession = Depends(get_async_db)):
    """
    Lấy thông tin chi tiết của một người dùng cụ thể dựa trên ID.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.patch("/{user_id}", response_model=ProfileResponse)
async def update_user(
    user_id: str,
    body: UserAdminUpdate,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Cập nhật thông tin, vai trò (Role) hoặc trạng thái hoạt động của người dùng.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if body.email is not None:
        existing = await get_user_by_email(db, body.email)
        if existing and existing.id != user_id:
            raise HTTPException(status_code=400, detail="Email already taken")
        user.email = body.email
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role is not None:
        user.role_id = ROLE_NAME_TO_ID[body.role]
    if body.is_active is not None:
        if body.is_active is True and not user.is_active:
            if user.email.startswith("archived_") or user.email.startswith("deleted_"):
                raise HTTPException(
                    status_code=400,
                    detail="Tài khoản này đã bị lưu trữ vĩnh viễn do email đã được đăng ký mới bởi tài khoản khác. Không thể hoàn tác."
                )
        user.is_active = body.is_active
        
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.delete("/{user_id}")
async def delete_user(user_id: str, db: AsyncSession = Depends(get_async_db)):
    """
    Xóa mềm (Soft Delete) tài khoản người dùng khỏi hệ thống.
    
    Mục đích: Chuyển trạng thái tài khoản thành không hoạt động (is_active = False),
    ngắt quyền truy cập ngay lập tức nhưng vẫn bảo toàn toàn vẹn dữ liệu lịch sử đơn hàng,
    chuyến bay và bảo trì liên quan (tránh vi phạm Foreign Key Constraint).
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.is_active = False
    db.add(user)
    await db.commit()
    await db.refresh(user)

    from app.modules.system.service import log_action
    await log_action(db, user.id, "ADMIN_SOFT_DELETE", "users", {"email": user.email})

    return {
        "success": True,
        "message": "Tài khoản người dùng đã được xóa (vô hiệu hóa) thành công",
        "user_id": user.id,
        "is_active": user.is_active
    }

@router.patch("/{user_id}/restore", response_model=ProfileResponse)
async def restore_user(user_id: str, db: AsyncSession = Depends(get_async_db)):
    """
    Khôi phục tài khoản người dùng đã bị xóa/vô hiệu hóa (Restore Account).
    
    Mục đích: Cho phép Quản trị viên kích hoạt lại tài khoản người dùng khi cần thiết.
    Điều kiện:
    - Nếu tài khoản đã bị lưu trữ vĩnh viễn (do email đã được đăng ký mới) -> Từ chối khôi phục.
    - Nếu tài khoản chỉ mới bị soft-delete (chưa bị ai đăng ký lại email) -> Khôi phục thành công.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user.is_active:
        return user

    if user.email.startswith("archived_") or user.email.startswith("deleted_"):
        raise HTTPException(
            status_code=400,
            detail="Tài khoản này đã bị lưu trữ vĩnh viễn do email đã được đăng ký mới bởi tài khoản khác. Không thể hoàn tác."
        )

    user.is_active = True
    db.add(user)
    await db.commit()
    await db.refresh(user)

    from app.modules.system.service import log_action
    await log_action(db, user.id, "ADMIN_RESTORE", "users", {"email": user.email})

    return user
