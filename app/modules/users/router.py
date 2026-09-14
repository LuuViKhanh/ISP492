from fastapi import APIRouter, Depends
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole

router = APIRouter(prefix="/users", tags=["User Management"])

# Chỉ định chỉ có ADMIN mới có quyền tạo User
require_admin = RoleChecker([UserRole.ADMIN])

@router.get("/")
def get_all_users(user: CurrentUser = Depends(require_admin)):
    return {"message": "List of all users. Only Admin can see this.", "current_user": user.username}

@router.post("/")
def create_user(user: CurrentUser = Depends(require_admin)):
    return {"message": "User created. Only Admin can do this."}
