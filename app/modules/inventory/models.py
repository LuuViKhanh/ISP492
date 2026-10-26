"""
Inventory Module — Kho linh kiện Hub

Models:
  PartCategory       — Danh mục linh kiện (động cơ, cánh, bo mạch, cáp...)
  HubInventory       — Tồn kho theo (hub_id, part_category_id)
  InventoryTransaction — Log mọi biến động +/- kho
  PartsRequest       — Tech đề xuất thay linh kiện, Admin duyệt

Luồng chính:
  1. Tech tạo PartsRequest (đính kèm ảnh mô tả hư hỏng, chọn part_category + số lượng)
  2. Nếu hub đủ tồn kho → status AUTO_APPROVED + trừ kho ngay
  3. Nếu không đủ → status PENDING → Admin duyệt
  4. Admin APPROVE  → trừ kho (hoặc tạo InventoryTransfer nếu từ hub khác)
  5. Admin REJECT   → ghi reason, kết thúc
  6. Mọi thay đổi kho đều sinh InventoryTransaction (audit log)
"""

import enum
from datetime import datetime
from sqlalchemy import (
    BigInteger, Integer, Float, String, Text, DateTime,
    Boolean, JSON, Enum as SAEnum, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database.db import Base


# ── Enums ──────────────────────────────────────────────────────────

class PartCategoryType(str, enum.Enum):
    MOTOR    = "Motor"        # Động cơ
    PROPELLER = "Propeller"   # Cánh quạt / Rotor blade
    ESC      = "ESC"          # Electronic Speed Controller
    FLIGHT_CONTROLLER = "FlightController"  # Bo mạch điều khiển bay
    CABLE    = "Cable"        # Cáp điện / tín hiệu
    FRAME    = "Frame"        # Khung máy
    SENSOR   = "Sensor"       # Cảm biến (GPS, lidar...)
    OTHER    = "Other"        # Linh kiện khác


class PartsRequestStatus(str, enum.Enum):
    PENDING        = "Pending"       # Chờ Admin duyệt
    AUTO_APPROVED  = "AutoApproved"  # Tự động duyệt khi hub đủ kho
    APPROVED       = "Approved"      # Admin duyệt, chờ thực hiện
    FULFILLED      = "Fulfilled"     # Đã thay thế xong
    REJECTED       = "Rejected"      # Admin từ chối
    WAITING_STOCK  = "WaitingStock"  # Cần điều phối từ hub khác


class TxnReason(str, enum.Enum):
    RECEIVE     = "Receive"      # Nhập kho từ nhà cung cấp / admin
    REPAIR_USE  = "RepairUse"    # Sử dụng cho sửa chữa (trừ kho)
    TRANSFER_IN  = "TransferIn"  # Nhận từ hub khác
    TRANSFER_OUT = "TransferOut" # Gửi sang hub khác
    ADJUST      = "Adjust"       # Điều chỉnh kiểm kê
    DISCARD     = "Discard"      # Loại bỏ linh kiện hỏng


# ── Models ─────────────────────────────────────────────────────────

class PartCategory(Base):
    """Danh mục loại linh kiện."""
    __tablename__ = "part_categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    category_type: Mapped[PartCategoryType] = mapped_column(
        SAEnum(PartCategoryType, name="part_category_type_enum", create_type=True,
               values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    # Danh sách model drone tương thích: ["DJI Mavic 3", "DJI Matrice 300"]
    compatible_drone_models: Mapped[list | None] = mapped_column(JSON, nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)  # VNĐ / đơn vị
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HubInventory(Base):
    """Tồn kho linh kiện tại từng Hub."""
    __tablename__ = "hub_inventories"
    __table_args__ = (
        UniqueConstraint("hub_id", "part_category_id", name="uq_hub_part"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hub_id: Mapped[int] = mapped_column(Integer, nullable=False)
    part_category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Ngưỡng cảnh báo tồn kho thấp
    min_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InventoryTransaction(Base):
    """
    Audit log mọi biến động kho (+/-).
    quantity_delta > 0 = nhập, < 0 = xuất.
    """
    __tablename__ = "inventory_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hub_id: Mapped[int] = mapped_column(Integer, nullable=False)
    part_category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)   # +/−
    reason: Mapped[TxnReason] = mapped_column(
        SAEnum(TxnReason, name="txn_reason_enum", create_type=True,
               values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    performed_by: Mapped[str | None] = mapped_column(String, nullable=True)  # user_id
    # Tham chiếu: parts_request_id, transfer_id...
    reference_id: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PartsRequest(Base):
    """
    Đề xuất thay linh kiện từ Technician.
    Khi hub đủ tồn kho → AUTO_APPROVED + trừ kho ngay.
    Khi không đủ → PENDING cho Admin duyệt.
    """
    __tablename__ = "parts_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Người tạo & vị trí
    requested_by: Mapped[str] = mapped_column(String, nullable=False)   # technician user_id
    hub_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Drone bị hỏng
    drone_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Linh kiện cần
    part_category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity_needed: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Mô tả & ảnh
    description: Mapped[str] = mapped_column(Text, nullable=False)
    photo_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list of URL strings

    # Trạng thái
    status: Mapped[PartsRequestStatus] = mapped_column(
        SAEnum(PartsRequestStatus, name="parts_request_status_enum", create_type=True,
               values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=PartsRequestStatus.PENDING,
    )

    # Admin review
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Chi phí thực tế (điền sau khi hoàn tất)
    actual_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
