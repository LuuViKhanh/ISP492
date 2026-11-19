from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional


class TransactionResponse(BaseModel):
    """Response schema cho một giao dịch/transaction."""
    model_config = ConfigDict(from_attributes=True)

    payment_id: str  # order_id (là primary key của transaction)
    order_id: str
    customer_id: Optional[str] = None
    amount: float  # delivery_fee
    payment_status: str  # "UNPAID", "PAID", "REFUNDED", etc.
    paid_at: Optional[datetime] = None
    refund_at: Optional[datetime] = None
    refund_amount: Optional[float] = None
    refund_reason: Optional[str] = None
    created_at: Optional[datetime] = None


class RefundListResponse(BaseModel):
    """Response schema cho một refund trong danh sách hoàn tiền."""
    model_config = ConfigDict(from_attributes=True)

    refund_id: str  # Dùng order_id làm refund_id
    order_id: str
    refund_amount: float
    reason: str
    status: str  # "PENDING", "COMPLETED", "FAILED", "CANCELLED"
    processed_at: Optional[datetime] = None  # refund_at
