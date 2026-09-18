import enum
from datetime import datetime
from sqlalchemy import BigInteger, Text, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from app.database.db import Base


class WorkOrderStatus(str, enum.Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drone_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    battery_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    technician_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issue_description: Mapped[str] = mapped_column(Text, nullable=False)
    action_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[WorkOrderStatus] = mapped_column(
        SAEnum(WorkOrderStatus, name="work_orders_status", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=WorkOrderStatus.PENDING,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
