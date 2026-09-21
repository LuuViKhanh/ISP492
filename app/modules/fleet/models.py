import enum
from datetime import datetime
from sqlalchemy import BigInteger, Float, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.db import Base

# ==================== ENUMS ====================
class DroneStatus(str, enum.Enum):
    AVAILABLE = "Available"
    IN_MISSION = "In Mission"
    MAINTENANCE = "Maintenance"
    RETIRED = "Retired"

class batteries_status(str, enum.Enum):
    ACTIVE = "Active"
    REPLACED = "Replaced"

class WorkOrderStatus(str, enum.Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"

# ==================== MODELS ====================
class Drone(Base):
    __tablename__ = "drones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_capacity_kg: Mapped[float] = mapped_column(Float, nullable=False)
    max_speed: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[DroneStatus] = mapped_column("operational_status", SAEnum(DroneStatus, name="drone_status", create_type=False, values_callable=lambda x: [e.value for e in x]))
    current_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    battery_level_pct: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    batteries = relationship("Battery", back_populates="drone")


class Battery(Base):
    __tablename__ = "batteries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    serial_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    capacity_wh: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[batteries_status] = mapped_column(SAEnum(batteries_status, name="batteries_status", create_type=False, values_callable=lambda x: [e.value for e in x]))
    
    drone_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("drones.id"), nullable=True)
    
    drone = relationship("Drone", back_populates="batteries")


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