import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Boolean, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from app.database.db import Base
from app.shared.roles import UserRole


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String, nullable=True)  # None nếu đăng nhập Google
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.CUSTOMER, nullable=False)
    google_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
