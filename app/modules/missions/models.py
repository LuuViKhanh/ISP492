import enum
from sqlalchemy import BigInteger, Float, String, DateTime, Integer, Boolean, Enum as SAEnum, ForeignKey, JSON, Index
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


class RefundStatus(str, enum.Enum):
    PENDING   = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    CANCELLED = "CANCELLED"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    AWAITING_DROP_OFF = "AWAITING_DROP_OFF"  # Đã thanh toán, chờ KH mang hàng đến hub
    AT_HUB = "AT_HUB"                        # KH đã mang hàng đến hub, chờ operator tạo mission
    IN_DELIVERY = "IN_DELIVERY"
    DELIVERED_TO_HUB = "DELIVERED_TO_HUB"
    DELIVERED_TO_CUSTOMER = "DELIVERED_TO_CUSTOMER"  # ← Khách đã nhận hàng
    CANCELLED = "CANCELLED"


class DeliveryMode(str, enum.Enum):
    EXPRESS   = "EXPRESS"
    SCHEDULED = "SCHEDULED"
    NORMAL    = "NORMAL"    # Giao linh hoạt, cam kết deadline; tự động lên Aging Priority khi gần hết hạn


class MissionLegStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class LegDestinationType(str, enum.Enum):
    """Phân biệt chuyến bay thẳng vs chuyến bay trung gian."""
    DESTINATION = "destination"   # Flying to final destination
    CONNECTION = "connection"     # Flying to relay hub (connection)


class MissionStatus(str, enum.Enum):
    SCHEDULED         = "Scheduled"
    FLYING            = "Flying"
    ARRIVAL_COMPLETED = "Arrival completed"
    MISSION_COMPLETED = "Mission completed"
    INCIDENT_RETURN   = "Incident return"
    FAILED            = "Failed"
    CANCELLED         = "Cancelled"


class HandlingStatus(str, enum.Enum):
    INCOMING = "Incoming"
    AT_HUB = "At hub"
    READY = "Ready"
    CANNOT_CONTINUE = "Cannot continue"


class IncidentStatus(str, enum.Enum):
    OPEN = "Open"
    INVESTIGATING = "Investigating"
    CLOSED = "Closed"


class IncidentSeverity(str, enum.Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class Mission(Base):
    __tablename__ = "missions"
    __table_args__ = (
        Index("idx_missions_order_id_desc", "order_id", "id"),
        Index("idx_missions_status", "status"),
        Index("idx_missions_drone_id", "drone_id"),
        Index("idx_missions_origin_hub", "origin_hub_id"),
    )

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String, nullable=True)
    drone_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    battery_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
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
    battery_charge_at_arrival: Mapped[float | None] = mapped_column(Float, nullable=True)
    leg_destination_type: Mapped[str | None] = mapped_column(String(20), default="destination", nullable=True)
    checkpoints_json: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)


class TelemetryLog(Base):
    __tablename__ = "telemetry_logs"
    __table_args__ = (
        Index("idx_telemetry_logs_mission_ts", "mission_id", "timestamp"),
        Index("idx_telemetry_logs_mission_id_desc", "mission_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mission_id: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    altitude: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    battery_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100%
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


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("idx_incidents_mission_status", "mission_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mission_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    drone_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
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
    __table_args__ = (
        Index("idx_orders_customer_created", "customer_id", "created_at"),
        Index("idx_orders_status_created", "status", "created_at"),
        Index("idx_orders_origin_hub_status", "origin_hub_id", "status"),
        Index("idx_orders_payment_status", "payment_status"),
    )

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sender_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    receiver_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    receiver_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
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
    receiver_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_status: Mapped[PaymentStatus | None] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status_enum", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=True, default=PaymentStatus.UNPAID
    )
    payment_order_code: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    payment_transaction_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_checkout_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payment_qr_code: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    payment_bin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_account_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_account_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refund_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refund_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    refund_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    refund_status: Mapped[RefundStatus | None] = mapped_column(
        SAEnum(RefundStatus, name="refund_status_enum", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=True, default=RefundStatus.PENDING
    )
    # ── Aging Priority fields (NORMAL mode) ──────────────────────────────────
    is_aging_priority: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    aging_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # ── Cancellation ──────────────────────────────────────────────────────────
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # ── Order Lifecycle Proof Images (Mục 8) ─────────────────────────────────
    proof_images_json: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)


class MissionLeg(Base):
    __tablename__ = "mission_legs"
    __table_args__ = (
        Index("idx_mission_legs_mission_seq", "mission_id", "sequence_no"),
        Index("idx_mission_legs_to_hub_status", "to_hub_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sequence_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    battery_id: Mapped[str | None] = mapped_column(String(50), ForeignKey("batteries.id"), nullable=True)
    status: Mapped[MissionLegStatus | None] = mapped_column(SAEnum(MissionLegStatus, name="mission_leg_status_enum", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ── Delivery Rating ───────────────────────────────────────────────────────────

class DeliveryRating(Base):
    """
    Đánh giá trải nghiệm giao hàng của Customer sau khi đơn DELIVERED_TO_HUB.
    Mỗi đơn chỉ được đánh giá 1 lần (unique constraint: order_id).
    """
    __tablename__ = "delivery_ratings"
    __table_args__ = (
        Index("idx_delivery_ratings_order", "order_id"),
        Index("idx_delivery_ratings_customer", "customer_id"),
        Index("idx_delivery_ratings_rating", "rating"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    customer_id: Mapped[str] = mapped_column(String(50), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)   # 1-5
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ── Saved Address (Customer Address Book) ────────────────────────────────────

class SavedAddress(Base):
    """
    Danh bạ địa chỉ giao hàng của Customer — lưu thông tin người nhận thường dùng.
    """
    __tablename__ = "saved_addresses"
    __table_args__ = (
        Index("idx_saved_addresses_customer", "customer_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)        # e.g. "Home", "Office"
    recipient_name: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hub_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # destination hub
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
