"""Request/response models for the CreateUser API."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class CreateUserRequest(BaseModel):
    full_name: str
    email: EmailStr


class CreateUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    created_at: datetime
