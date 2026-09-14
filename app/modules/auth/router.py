from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.db import get_async_db
from app.modules.auth import service
from app.modules.auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])

GOOGLE_AUTH_URL = (
    "https://accounts.google.com/o/oauth2/v2/auth"
    "?response_type=code"
    "&scope=openid%20email%20profile"
    "&client_id={client_id}"
    "&redirect_uri={redirect_uri}"
)


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_async_db)):
    if await service.get_user_by_email(db, body.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    user = await service.register_user(db, body.email, body.full_name, body.password)
    return user


# ── Login (email + password) ──────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_async_db)):
    user = await service.get_user_by_email(db, body.email)
    if not user or not user.hashed_password or not service.verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    return TokenResponse(
        access_token=service.create_access_token(user.id, user.role.value),
        refresh_token=service.create_refresh_token(user.id),
    )


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout():
    # JWT là stateless — client xóa token phía mình là đủ.
    # Nếu cần blacklist token, thêm Redis ở đây sau.
    return {"message": "Logged out successfully"}


# ── Refresh token ─────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str, db: AsyncSession = Depends(get_async_db)):
    payload = service.decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = await service.get_user_by_id(db, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled")
    return TokenResponse(
        access_token=service.create_access_token(user.id, user.role.value),
        refresh_token=service.create_refresh_token(user.id),
    )


# ── Google OAuth ──────────────────────────────────────────────────────────────

@router.get("/google")
async def google_login():
    url = GOOGLE_AUTH_URL.format(
        client_id=settings.GOOGLE_CLIENT_ID,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )
    return RedirectResponse(url)


@router.get("/google/callback", response_model=TokenResponse)
async def google_callback(code: str, db: AsyncSession = Depends(get_async_db)):
    try:
        google_info = await service.exchange_google_code(code)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to exchange Google code")
    user = await service.get_or_create_google_user(db, google_info)
    return TokenResponse(
        access_token=service.create_access_token(user.id, user.role.value),
        refresh_token=service.create_refresh_token(user.id),
    )
