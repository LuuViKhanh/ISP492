"""
Inventory Module — Backward-compatibility wrapper redirecting to Supplies Module.

Các bảng `hub_inventories` và `inventory_transactions` đã được đổi tên thành
`hub_supplies` và `supply_transactions` trong `app.modules.supplies.models`.
File này re-export toàn bộ model và enum từ `app.modules.supplies.models` để:
1. Tránh lỗi `sqlalchemy.exc.InvalidRequestError: Table 'part_categories' is already defined`
   khi `app.main` nạp cả hai module.
2. Đảm bảo các endpoint `/admin/inventory` và `/technician/inventory` truy vấn đúng
   bảng `hub_supplies` và `supply_transactions` hiện hành trong database.
"""

from app.modules.supplies.models import (
    PartCategoryType,
    PartsRequestStatus,
    SupplyTxnReason,
    TxnReason,
    PartCategory,
    HubSupply,
    HubInventory,
    SupplyTransaction,
    InventoryTransaction,
    PartsRequest,
)

__all__ = [
    "PartCategoryType",
    "PartsRequestStatus",
    "SupplyTxnReason",
    "TxnReason",
    "PartCategory",
    "HubSupply",
    "HubInventory",
    "SupplyTransaction",
    "InventoryTransaction",
    "PartsRequest",
]
