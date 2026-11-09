import enum
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Integer, Float, String, Boolean, DateTime, Text, Enum as SAEnum, JSON
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
    drone_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    work_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mission_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Hub(Base):
    __tablename__ = "hubs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class AuditLog(Base):
    """Giữ lại cho backward compatibility — không dùng nữa."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource_table: Mapped[str | None] = mapped_column(String, nullable=True)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── Activity Logs ─────────────────────────────────────────────────
class ActivityLog(Base):
    """
    Ghi lại hành động do user khởi tạo.
    Ví dụ: LOGIN, CREATE_ORDER, APPROVE_REQUEST, CANCEL_MISSION...
    """
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Người thực hiện
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_email: Mapped[str | None] = mapped_column(String, nullable=True)   # denorm để dễ query
    role: Mapped[str | None] = mapped_column(String, nullable=True)          # ADMIN / OPERATOR / ...
    # Hành động
    action: Mapped[str] = mapped_column(String(100), nullable=False)         # LOGIN, CREATE_ORDER, ...
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)  # order / mission / drone...
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)   # ID của resource bị tác động
    description: Mapped[str | None] = mapped_column(Text, nullable=True)     # Mô tả ngắn gọn
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")  # success / failed
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # Payload bổ sung
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  default=lambda: datetime.now(timezone.utc))


# ── System Logs ───────────────────────────────────────────────────
class SystemLog(Base):
    """
    Ghi lại sự kiện tự động của hệ thống: workers, cron jobs, lỗi nền...
    Không phải do user trực tiếp khởi tạo.
    """
    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Nguồn phát sinh
    source: Mapped[str] = mapped_column(String(100), nullable=False)   # worker name / module path
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")   # INFO/WARNING/ERROR/CRITICAL
    event: Mapped[str] = mapped_column(String(100), nullable=False)    # WORKER_RUN / MISSION_AUTO_CANCEL / ...
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  default=lambda: datetime.now(timezone.utc))
