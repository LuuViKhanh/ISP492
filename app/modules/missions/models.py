from sqlalchemy import BigInteger, Float, String, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database.db import Base
import enum


class MissionStatus(str, enum.Enum):
    AWAITING_PAYMENT = "Awaiting Payment"
    PENDING_APPROVAL = "Pending Approval"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    FLYING = "Flying"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    drone_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    battery_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pickup_location_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dropoff_location_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    delivery_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[MissionStatus] = mapped_column(SAEnum(MissionStatus, name="missions_status", create_type=False))
    scheduled_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
