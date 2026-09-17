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
    """Lấy danh sách tất cả người dùng"""
    result = await db.execute(select(User))
    users = result.scalars().all()
    return [ProfileResponse.model_validate(u) for u in users]

@router.post("/", response_model=ProfileResponse)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_async_db)
):
    """Admin tạo tài khoản mới và gán Role"""
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
    """Admin lấy thông tin một user cụ thể"""
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
    """Admin cập nhật thông tin hoặc Role/Trạng thái của user"""
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
    """Admin xóa tài khoản user"""
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    await db.delete(user)
    await db.commit()
    return {"message": "User deleted successfully"}
