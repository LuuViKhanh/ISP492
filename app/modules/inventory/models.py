"""
inventory/models.py — Backward-compat re-exports

Module này chỉ re-export từ supplies.models để không break code cũ.
Tất cả model đã được chuyển sang app.modules.supplies.models.
Các bảng DB đã được đổi tên:
  hub_inventories       → hub_supplies
  inventory_transactions → supply_transactions
"""

from app.modules.supplies.models import (
    PartCategory,
    PartCategoryType,
    HubSupply as HubInventory,          # backward-compat alias
    SupplyTransaction as InventoryTransaction,  # backward-compat alias
    HubSupply,
    SupplyTransaction,
    PartsRequest,
    PartsRequestStatus,
    SupplyTxnReason as TxnReason,
    SupplyTxnReason,
)

__all__ = [
    "PartCategory", "PartCategoryType",
    "HubInventory", "InventoryTransaction",
    "HubSupply", "SupplyTransaction",
    "PartsRequest", "PartsRequestStatus",
    "TxnReason", "SupplyTxnReason",
]
