"""
Supplies Module — Quản lý linh kiện & vật tư tại Hub

Hệ thống không có kho riêng (warehouse) — linh kiện được quản lý trực tiếp
theo từng Hub và được cung cấp qua nhà cung cấp (supplier).

Models:
  PartCategory    — Danh mục loại linh kiện / vật tư
  HubSupply       — Số lượng linh kiện tại từng Hub  (đổi từ HubInventory)
  SupplyTransaction — Log biến động +/- tại Hub      (đổi từ InventoryTransaction)
  PartsRequest    — Technician đề xuất thay, Admin duyệt

Backward-compat aliases:
  HubInventory = HubSupply
  InventoryTransaction = SupplyTransaction
"""

import enum
from datetime import datetime
from sqlalchemy import (
    BigInteger, Integer, Float, String, Text, DateTime,
    Boolean, JSON, ForeignKey, Enum as SAEnum, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database.db import Base


# ── Enums ──────────────────────────────────────────────────────────

class PartCategoryType(str, enum.Enum):
    MOTOR             = "Motor"
    PROPELLER         = "Propeller"
    ESC               = "ESC"
    FLIGHT_CONTROLLER = "FlightController"
    CABLE             = "Cable"
    FRAME             = "Frame"
    SENSOR            = "Sensor"
    BATTERY           = "Battery"
    CAMERA            = "Camera"
    LANDING_GEAR      = "Landing Gear"
    GIMBAL            = "Gimbal"
    CHARGER           = "Charger"
    MAINTENANCE_KIT   = "Maintenance Kit"
    OTHER             = "Other"


class PartsRequestStatus(str, enum.Enum):
    PENDING       = "Pending"
    AUTO_APPROVED = "AutoApproved"
    APPROVED      = "Approved"
    FULFILLED     = "Fulfilled"
    REJECTED      = "Rejected"
    WAITING_STOCK = "WaitingStock"


class SupplyTxnReason(str, enum.Enum):
    """Lý do biến động vật tư tại Hub."""
    RECEIVE      = "Receive"       # Nhập từ nhà cung cấp
    REPAIR_USE   = "RepairUse"     # Sử dụng cho sửa chữa
    TRANSFER_IN  = "TransferIn"    # Nhận điều phối từ Hub khác
    TRANSFER_OUT = "TransferOut"   # Chuyển sang Hub khác
    ADJUST       = "Adjust"        # Điều chỉnh số lượng thực tế
    DISCARD      = "Discard"       # Hủy bỏ linh kiện hỏng


# Backward-compat alias
TxnReason = SupplyTxnReason


# ── Models ─────────────────────────────────────────────────────────

class PartCategory(Base):
    """Danh mục loại linh kiện / vật tư."""
    __tablename__ = "part_categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    category_type: Mapped[PartCategoryType] = mapped_column(
        SAEnum(PartCategoryType, name="part_category_type_enum", create_type=False,
               values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    compatible_drone_models: Mapped[list | dict | None] = mapped_column(JSON, nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HubSupply(Base):
    """
    Số lượng linh kiện / vật tư tại từng Hub.
    Không phải kho trung tâm — mỗi Hub tự quản lý lượng vật tư của mình.

    Đổi tên từ HubInventory (bảng hub_supplies — trước là hub_inventories).
    """
    __tablename__ = "hub_supplies"
    __table_args__ = (
        UniqueConstraint("hub_id", "part_category_id", name="uq_hub_supply_part"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hub_id: Mapped[int] = mapped_column(Integer, ForeignKey("hubs.id"), nullable=False, index=True)
    part_category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("part_categories.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Backward-compat alias
HubInventory = HubSupply


class SupplyTransaction(Base):
    """
    Log mọi biến động vật tư tại Hub (+/-).
    quantity_delta > 0 = nhập vào, < 0 = xuất ra.

    Đổi tên từ InventoryTransaction (bảng supply_transactions — trước là inventory_transactions).
    """
    __tablename__ = "supply_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hub_id: Mapped[int] = mapped_column(Integer, ForeignKey("hubs.id"), nullable=False, index=True)
    part_category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("part_categories.id"), nullable=False, index=True)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[SupplyTxnReason] = mapped_column(
        SAEnum(SupplyTxnReason, name="supply_txn_reason_enum", create_type=False,
               values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    performed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Backward-compat alias
InventoryTransaction = SupplyTransaction


class PartsRequest(Base):
    """
    Technician đề xuất sử dụng linh kiện để sửa chữa.
    Admin duyệt → trừ số lượng tại Hub hoặc điều phối từ Hub khác.
    """
    __tablename__ = "parts_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    requested_by: Mapped[str] = mapped_column(String, nullable=False, index=True)
    hub_id: Mapped[int] = mapped_column(Integer, ForeignKey("hubs.id"), nullable=False, index=True)
    drone_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    part_category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("part_categories.id"), nullable=False, index=True)
    quantity_needed: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    photo_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[PartsRequestStatus] = mapped_column(
        SAEnum(PartsRequestStatus, name="parts_request_status_enum", create_type=False,
               values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=PartsRequestStatus.PENDING, index=True,
    )
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
