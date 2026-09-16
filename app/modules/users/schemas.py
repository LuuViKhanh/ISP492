from pydantic import BaseModel, EmailStr
from typing import Optional
from app.shared.roles import UserRole


class ProfileResponse(BaseModel):
    id: str
    email: str
    username: Optional[str]
    full_name: str
    role: UserRole
    is_active: bool

    class Config:
        from_attributes = True


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    username: Optional[str] = None


class UserCreate(BaseModel):
    email: EmailStr
    username: str
    full_name: str
    password: str
    role: UserRole


class UserAdminUpdate(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
