from pydantic import BaseModel, EmailStr
from app.shared.roles import UserRole


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    full_name: str
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: UserRole
    is_active: bool

    class Config:
        from_attributes = True
