 import enum
from sqlalchemy import BigInteger, Float, String, DateTime, Integer, Boolean, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from app.database.db import Base


class MissionStatus(str, enum.Enum):
    AWAITING_PAYMENT = "Awaiting Payment"
    PENDING_APPROVAL = "Pending Approval"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    FLYING = "Flying"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class LocationType(str, enum.Enum):
    HUB = "Hub"
    CUSTOMER_ADDRESS = "CustomerAddress"


class IncidentStatus(str, enum.Enum):
    OPEN = "Open"
    INVESTIGATING = "Investigating"
    CLOSED = "Closed"


class IncidentSeverity(str, enum.Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    type: Mapped[LocationType] = mapped_column(
        SAEnum(LocationType, name="locations_type", create_type=False, values_callable=lambda x: [e.value for e in x])
    )


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String, nullable=True)
    drone_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    battery_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pickup_location_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dropoff_location_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    delivery_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[MissionStatus] = mapped_column(SAEnum(MissionStatus, name="missions_status", create_type=False, values_callable=lambda x: [e.value for e in x]))
    scheduled_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    origin_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    destination_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    departed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    arrival_confirmed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    arrival_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TelemetryLog(Base):
    __tablename__ = "telemetry_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    altitude: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    battery_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    energy_consumed_wh: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed: Mapped[float | None] = mapped_column(Float, nullable=True)


class MissionHubCheckpoint(Base):
    """
    Ghi log mỗi khi Drone đi qua một Hub trung gian trong hành trình.
    Được tạo tự động bởi telemetry endpoint khi phát hiện drone vào vùng proximity của Hub.
    """
    __tablename__ = "mission_hub_checkpoints"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # hub_id tham chiếu tới bảng hubs (Hub trung gian lớn)
    hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # location_id tham chiếu tới bảng locations type=HUB (mini-hub)
    location_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hub_name: Mapped[str | None] = mapped_column(String, nullable=True)
    hub_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    hub_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Tọa độ thực tế của drone khi trigger checkpoint
    drone_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    drone_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    distance_to_hub_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Thứ tự checkpoint trong chuyến bay (1, 2, 3,...)
    checkpoint_order: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    drone_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reporter_id: Mapped[str | None] = mapped_column(String, nullable=True)
    severity: Mapped[IncidentSeverity] = mapped_column(
        SAEnum(IncidentSeverity, name="risk_level", create_type=False, values_callable=lambda x: [e.value for e in x])
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        SAEnum(IncidentStatus, name="incidents_status", create_type=False, values_callable=lambda x: [e.value for e in x])
    )
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requires_technical_inspection: Mapped[bool] = mapped_column(default=False)
