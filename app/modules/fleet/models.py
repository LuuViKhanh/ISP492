from sqlalchemy import BigInteger, Float, String, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum
from app.database.db import Base

class DroneStatus(str, enum.Enum):
    AVAILABLE = "Available"
    IN_MISSION = "In Mission"
    MAINTENANCE = "Maintenance"
    RETIRED = "Retired"

class batteries_status(str, enum.Enum):
    ACTIVE = "Active"
    REPLACED = "Replaced"

class Drone(Base):
    __tablename__ = "drones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_capacity_kg: Mapped[float] = mapped_column(Float, nullable=False)
    max_speed: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[DroneStatus] = mapped_column(SAEnum(DroneStatus, name="drone_status", create_type=False, values_callable=lambda x: [e.value for e in x]))
    
    batteries = relationship("Battery", back_populates="drone")

class Battery(Base):
    __tablename__ = "batteries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    serial_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    capacity_wh: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[batteries_status] = mapped_column(SAEnum(batteries_status, name="batteries_status", create_type=False, values_callable=lambda x: [e.value for e in x]))
    
    drone_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("drones.id"), nullable=True)
    
    drone = relationship("Drone", back_populates="batteries")