from fastapi import APIRouter, Depends, HTTPException
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
    tags=["Admin - Users"],
    dependencies=[Depends(RoleChecker([UserRole.ADMIN]))]
)

@router.get("/", response_model=list[ProfileResponse])
async def get_all_users(db: AsyncSession = Depends(get_async_db)):
    """
    Lấy danh sách tất cả người dùng trong hệ thống.
    
    Mục đích: Cung cấp cho quản trị viên (Admin) danh sách toàn bộ người dùng để dễ dàng quản lý.
    Cách dùng: Gửi request GET tới endpoint này. Yêu cầu token hợp lệ có quyền Admin.
    """
    result = await db.execute(select(User))
    users = result.scalars().all()
    return [ProfileResponse.model_validate(u) for u in users]

@router.post("/", response_model=ProfileResponse)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Tạo tài khoản người dùng mới và gán vai trò (Role).
    
    Mục đích: Cho phép quản trị viên cấp phát tài khoản mới cho nhân viên hoặc người dùng đặc biệt với vai trò tương ứng.
    Cách dùng: Gửi request POST với payload chứa thông tin người dùng (email, username, full_name, password, role). Trả về thông tin người dùng vừa tạo.
    """
    existing = await get_user_by_email(db, body.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
        
    new_user = User(
        email=body.email,
        username=body.username,
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
    
    Mục đích: Xem thông tin hồ sơ của người dùng (email, họ tên, vai trò) để kiểm tra hoặc hỗ trợ.
    Cách dùng: Gửi request GET kèm theo `user_id` trên đường dẫn. Yêu cầu quyền Admin.
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
    
    Mục đích: Cho phép Admin chỉnh sửa hồ sơ người dùng, thăng giáng cấp, hoặc khóa/mở khóa tài khoản khi cần thiết.
    Cách dùng: Gửi request PATCH với `user_id` và payload chứa các trường cần thay đổi. Chỉ cập nhật những trường được truyền lên.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if body.email is not None:
        # Kiểm tra email trùng lặp nếu có thay đổi
        existing = await get_user_by_email(db, body.email)
        if existing and existing.id != user_id:
            raise HTTPException(status_code=400, detail="Email already taken")
        user.email = body.email
        
    if body.username is not None:
        user.username = body.username
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role is not None:
        user.role_id = ROLE_NAME_TO_ID[body.role]
    if body.is_active is not None:
        user.is_active = body.is_active
        
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.delete("/{user_id}")
async def delete_user(user_id: str, db: AsyncSession = Depends(get_async_db)):
    """
    Xóa tài khoản người dùng khỏi hệ thống.
    
    Mục đích: Xóa bỏ các tài khoản không còn sử dụng hoặc vi phạm chính sách khỏi cơ sở dữ liệu.
    Cách dùng: Gửi request DELETE kèm theo `user_id`. Hành động này không thể hoàn tác, cần cẩn trọng khi thực hiện.
    """
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    await db.delete(user)
    await db.commit()
    return {"message": "User deleted successfully"}
