from datetime import datetime, timedelta, timezone
from typing import Optional

import secrets
import bcrypt
import httpx
from jose import jwt, JWTError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.auth.models import User, ROLE_NAME_TO_ID
from app.shared.roles import UserRole


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _create_token(data: dict, expires_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expires_delta
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(user_id: str, role: str) -> str:
    return _create_token(
        {"sub": user_id, "role": role, "type": "access"},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: str) -> str:
    return _create_token(
        {"sub": user_id, "type": "refresh"},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


# ── DB helpers ────────────────────────────────────────────────────────────────

async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


# ── Register ──────────────────────────────────────────────────────────────────

async def register_user(db: AsyncSession, email: str, username: str, full_name: str, password: str) -> User:
    user = User(
        email=email,
        username=username,
        full_name=full_name,
        hashed_password=hash_password(password),
        role_id=ROLE_NAME_TO_ID[UserRole.CUSTOMER],
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# ── Forgot / Reset Password ───────────────────────────────────────────────────

async def create_reset_token(db: AsyncSession, email: str) -> Optional[str]:
    user = await get_user_by_email(db, email)
    if not user:
        return None
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    await db.execute(text(
        "INSERT INTO public.password_reset_tokens (user_id, token, expires_at, used, created_at) "
        "VALUES (:user_id, :token, :expires_at, false, now())"
    ), {"user_id": user.id, "token": token, "expires_at": expires_at})
    await db.commit()
    return token


async def reset_password(db: AsyncSession, token: str, new_password: str) -> bool:
    result = await db.execute(text(
        "SELECT prt.id, u.id as user_id FROM public.password_reset_tokens prt "
        "JOIN public.users u ON u.id = prt.user_id "
        "WHERE prt.token = :token AND prt.used = false AND prt.expires_at > now()"
    ), {"token": token})
    row = result.fetchone()
    if not row:
        return False
    user = await get_user_by_id(db, str(row.user_id))
    if not user:
        return False
    user.hashed_password = hash_password(new_password)
    db.add(user)
    await db.execute(text(
        "UPDATE public.password_reset_tokens SET used = true WHERE id = :id"
    ), {"id": row.id})
    await db.commit()
    return True


# ── Google OAuth ──────────────────────────────────────────────────────────────

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


async def exchange_google_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        tokens = resp.json()

        user_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        user_resp.raise_for_status()
        return user_resp.json()


async def get_or_create_google_user(db: AsyncSession, google_info: dict) -> User:
    email = google_info["email"]
    full_name = google_info.get("name", email)

    user = await get_user_by_email(db, email)
    if user:
        return user

    user = User(email=email, full_name=full_name, username=email, hashed_password="", role_id=ROLE_NAME_TO_ID[UserRole.CUSTOMER])
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
