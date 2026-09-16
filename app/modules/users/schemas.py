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
