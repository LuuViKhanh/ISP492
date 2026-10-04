import enum
from sqlalchemy import BigInteger, Float, String, DateTime, Integer, Boolean, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from app.database.db import Base


class PaymentStatus(str, enum.Enum):
    UNPAID    = "UNPAID"
    PENDING   = "PENDING"   # Đã tạo link, chờ user thanh toán
    PAID      = "PAID"
    FAILED    = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED  = "REFUNDED"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_DELIVERY = "IN_DELIVERY"
    DELIVERED_TO_HUB = "DELIVERED_TO_HUB"
    CANCELLED = "CANCELLED"

class DeliveryMode(str, enum.Enum):
    EXPRESS = "EXPRESS"
    SCHEDULED = "SCHEDULED"

class MissionLegStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

class MissionStatus(str, enum.Enum):
    # FE Operator Spec VI Statuses — viết thường khớp với DB
    SCHEDULED    = "Scheduled"
    IN_PROGRESS  = "In Progress"
    COMPLETED    = "Completed"
    CANCELLED    = "Cancelled"
    FAILED       = "Failed"
    # Legacy Statuses (Kept for backwards compatibility)
    PENDING_APPROVAL = "Pending Approval"
    APPROVED         = "Approved"
    FLYING           = "Flying"
    REJECTED         = "Rejected"
    ACTIVE_MISSION   = "Active mission"
    MISSION_COMPLETED = "Mission completed"
    INCIDENT_RETURN  = "Incident return"
    # Awaiting Payment
    AWAITING_PAYMENT = "Awaiting Payment"


class HandlingStatus(str, enum.Enum):
    INCOMING = "Incoming"
    AT_HUB = "At hub"
    READY = "Ready"
    CANNOT_CONTINUE = "Cannot continue"


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
    order_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mission_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
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
    handling_status: Mapped[HandlingStatus | None] = mapped_column(SAEnum(HandlingStatus, name="missions_handling_status", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=True)
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
    # ── Weather fields (từ Open-Meteo) ────────────────────────
    weather_temperature: Mapped[float | None] = mapped_column(Float, nullable=True)      # °C
    weather_apparent_temp: Mapped[float | None] = mapped_column(Float, nullable=True)    # °C cảm giác
    weather_dew_point: Mapped[float | None] = mapped_column(Float, nullable=True)        # °C điểm sương
    weather_humidity: Mapped[float | None] = mapped_column(Float, nullable=True)         # %
    weather_wind_speed: Mapped[float | None] = mapped_column(Float, nullable=True)       # m/s
    weather_wind_gust: Mapped[float | None] = mapped_column(Float, nullable=True)        # m/s gió giật
    weather_wind_direction: Mapped[float | None] = mapped_column(Float, nullable=True)   # độ (0-360)
    weather_precipitation: Mapped[float | None] = mapped_column(Float, nullable=True)    # mm
    weather_pressure: Mapped[float | None] = mapped_column(Float, nullable=True)         # hPa
    weather_cloud_cover: Mapped[float | None] = mapped_column(Float, nullable=True)      # %


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

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    package_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    package_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    destination_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivery_mode: Mapped[DeliveryMode | None] = mapped_column(SAEnum(DeliveryMode, name="delivery_mode_enum", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=True)
    requested_delivery_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    estimated_window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    estimated_window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planning_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[OrderStatus | None] = mapped_column(SAEnum(OrderStatus, name="orders_status_enum", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=True)
    origin_received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    origin_received_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    destination_received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    destination_received_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    replan_required_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # ── Payment fields ────────────────────────────────────────
    delivery_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    payment_status: Mapped[PaymentStatus | None] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status_enum", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=True, default=PaymentStatus.UNPAID
    )
    payment_order_code: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    payment_transaction_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_checkout_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class MissionLeg(Base):
    __tablename__ = "mission_legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sequence_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[MissionLegStatus | None] = mapped_column(SAEnum(MissionLegStatus, name="mission_leg_status_enum", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class MissionDroneAssignmentHistory(Base):
    __tablename__ = "mission_drone_assignment_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    old_drone_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_drone_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    replaced_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
