from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.shared.roles import UserRole
from pydantic import BaseModel
from typing import List

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

class CurrentUser(BaseModel):
    username: str
    role: UserRole

def get_current_user(token: str = Depends(oauth2_scheme)) -> CurrentUser:
    # TODO: Module Auth sẽ viết logic parse JWT Token tại đây và truy vấn Database.
    # Đây chỉ là mock data để test hệ thống phân quyền (RBAC).
    if token == "mock_admin_token":
        return CurrentUser(username="admin_user", role=UserRole.ADMIN)
    elif token == "mock_operator_token":
        return CurrentUser(username="operator_user", role=UserRole.OPERATOR)
    elif token == "mock_technician_token":
        return CurrentUser(username="technician_user", role=UserRole.TECHNICIAN)
    elif token == "mock_customer_token":
        return CurrentUser(username="customer_user", role=UserRole.CUSTOMER)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

class RoleChecker:
    """ Dependency để kiểm tra phân quyền RBAC """
    def __init__(self, allowed_roles: List[UserRole]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: CurrentUser = Depends(get_current_user)):
        if user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action"
            )
        return user
