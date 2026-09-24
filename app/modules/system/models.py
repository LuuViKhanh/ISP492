import enum
from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, Boolean, DateTime, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from app.database.db import Base


class NotificationType(str, enum.Enum):
    MISSION_STATUS = "Mission_Status"
    PAYMENT = "Payment"
    SYSTEM_ALERT = "System_Alert"
    AI_WARNING = "AI_Warning"
    MAINTENANCE = "Maintenance"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    notifications_type: Mapped[NotificationType] = mapped_column(
        SAEnum(NotificationType, name="notifications_type", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    reference_id: Mapped[str | None] = mapped_column(String, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    drone_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    work_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mission_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Hub(Base):
    __tablename__ = "hubs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float | None] = mapped_column(String, nullable=True)
    longitude: Mapped[float | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
