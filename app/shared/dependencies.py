from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from typing import List, Optional
import time
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.roles import UserRole
from app.core.config import settings
from app.database.db import get_async_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# [PERF NOTE] Cache nhẹ thông tin xác thực User (20s) để triệt tiêu round-trip SELECT accounts + pool_pre_ping
# lặp lại ở mỗi API request; bị xóa ngay lập tức khi user đổi thông tin, đổi hub hoặc bị khóa/xóa.
_USER_AUTH_CACHE: dict[str, tuple[float, "CurrentUser"]] = {}
_USER_AUTH_CACHE_TTL_SEC = 20.0
_USER_AUTH_CACHE_MAX = 512


def invalidate_user_auth_cache(user_id: Optional[str] = None) -> None:
    if user_id is None:
        _USER_AUTH_CACHE.clear()
    else:
        _USER_AUTH_CACHE.pop(str(user_id), None)


class CurrentUser(BaseModel):
    id: str
    email: str
    role: UserRole
    hub_id: int | None = None


async def _resolve_active_user_cached(db: AsyncSession, user_id: Optional[str]) -> Optional[CurrentUser]:
    if not user_id:
        return None
    uid = str(user_id)
    now_mono = time.monotonic()
    cached = _USER_AUTH_CACHE.get(uid)
    if cached is not None and (now_mono - cached[0]) <= _USER_AUTH_CACHE_TTL_SEC:
        return cached[1]

    from app.modules.auth.service import get_user_by_id
    user = await get_user_by_id(db, uid)
    if not user or not user.is_active:
        _USER_AUTH_CACHE.pop(uid, None)
        return None

    cu = CurrentUser(id=user.id, email=user.email, role=user.role, hub_id=user.hub_id)
    if len(_USER_AUTH_CACHE) >= _USER_AUTH_CACHE_MAX:
        _USER_AUTH_CACHE.clear()
    _USER_AUTH_CACHE[uid] = (now_mono, cu)
    return cu


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_async_db),
) -> CurrentUser:
    from app.modules.auth.service import decode_token

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise credentials_exception

    cu = await _resolve_active_user_cached(db, payload.get("sub"))
    if cu is None:
        raise credentials_exception
    return cu


oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_optional_current_user(
    token: str | None = Depends(oauth2_scheme_optional),
    db: AsyncSession = Depends(get_async_db),
) -> CurrentUser | None:
    """
    Dependency lấy thông tin User hiện tại nếu có truyền Token (không bắt buộc).
    Dành cho các endpoint công khai nhưng có thể cá nhân hóa nếu đã đăng nhập (như Chatbot AI).
    """
    if not token:
        return None
    try:
        from app.modules.auth.service import decode_token
        payload = decode_token(token)
        if not payload or payload.get("type") != "access":
            return None
        return await _resolve_active_user_cached(db, payload.get("sub"))
    except Exception:
        return None


class RoleChecker:
    def __init__(self, allowed_roles: List[UserRole]):
        self.allowed_roles = allowed_roles

    async def __call__(self, user: CurrentUser = Depends(get_current_user)):
        if user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return user
