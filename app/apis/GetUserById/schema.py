"""Request/response models for the GetUserById API."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GetUserByIdResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    created_at: datetime
