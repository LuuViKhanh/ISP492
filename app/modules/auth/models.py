import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Boolean, Integer, BigInteger
from sqlalchemy.orm import Mapped, mapped_column
from app.database.db import Base
from app.shared.roles import UserRole

# Maps role UUID → UserRole (from public.roles table)
ROLE_ID_MAP = {
    "4dc86f0d-a22b-4b56-8911-c40ffae89f09": UserRole.ADMIN,
    "595bec91-7f4e-47ec-bfec-6f0087b84620": UserRole.OPERATOR,
    "d1c65473-7a9d-4970-8b2c-9bb76d0c8d40": UserRole.TECHNICIAN,
    "a1d9a625-d2e9-4dea-b68f-e452cf3120f5": UserRole.CUSTOMER,
}
ROLE_NAME_TO_ID = {v: k for k, v in ROLE_ID_MAP.items()}


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    role_id: Mapped[str | None] = mapped_column(String, nullable=True)
    hashed_password: Mapped[str | None] = mapped_column("password_hash", String, nullable=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    hub_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def role(self) -> UserRole:
        return ROLE_ID_MAP.get(self.role_id, UserRole.CUSTOMER)

class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    role_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
