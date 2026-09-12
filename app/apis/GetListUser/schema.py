"""Request/response models for the GetListUser API."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    created_at: datetime


class GetListUserResponse(BaseModel):
    total: int
    items: list[UserItem]
