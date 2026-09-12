"""Standard response envelope so every API in the project returns the same shape."""
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str = "OK"
    data: Optional[T] = None


def success_response(data: Any = None, message: str = "OK") -> dict:
    return {"success": True, "message": message, "data": data}
