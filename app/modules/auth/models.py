import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database.db import Base
from app.shared.roles import UserRole

# Maps role UUID -> UserRole
ROLE_ID_MAP = {
    "ADMIN": UserRole.ADMIN,
    "OPERATOR": UserRole.OPERATOR,
    "TECHNICIAN": UserRole.TECHNICIAN,
    "CUSTOMER": UserRole.CUSTOMER,
}
ROLE_NAME_TO_ID = {v: k for k, v in ROLE_ID_MAP.items()}


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    role_id: Mapped[str | None] = mapped_column(String, nullable=True)
    hashed_password: Mapped[str | None] = mapped_column("password_hash", String, nullable=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    hub_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("hubs.id"), nullable=True)
    reset_token: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    reset_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def role(self) -> UserRole:
        return ROLE_ID_MAP.get(self.role_id, UserRole.CUSTOMER)


# Backward compatibility alias
User = Account
