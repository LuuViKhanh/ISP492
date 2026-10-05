"""
app/modules/payments/router.py
────────────────────────────────
Payment endpoints dùng PayOS.

Endpoints:
  POST /payments/orders/{order_id}/checkout   → Tạo payment link
  GET  /payments/orders/{order_id}/status     → Kiểm tra trạng thái thanh toán
  POST /payments/webhook                      → PayOS webhook callback
  POST /payments/orders/{order_id}/cancel     → Huỷ payment link
"""

import hmac
import hashlib
import json
import time
import random

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
from pydantic import BaseModel
from typing import Optional

from app.database.db import get_async_db
from app.shared.dependencies import RoleChecker, CurrentUser
from app.shared.roles import UserRole
from app.modules.missions.models import Order, PaymentStatus, OrderStatus
from app.core.config import settings
from app.modules.payments.payos_client import get_payos

router = APIRouter(prefix="/payments", tags=["Payments - PayOS"])

allow_customer = RoleChecker([UserRole.CUSTOMER, UserRole.ADMIN])
allow_all      = RoleChecker([UserRole.CUSTOMER, UserRole.OPERATOR, UserRole.ADMIN])
allow_admin    = RoleChecker([UserRole.ADMIN])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CheckoutResponse(BaseModel):
    order_id: str
    payment_order_code: int
    checkout_url: str
    qr_code: Optional[str] = None
    amount: float
    payment_status: PaymentStatus


class PaymentStatusResponse(BaseModel):
    order_id: str
    payment_status: PaymentStatus
    paid_at: Optional[datetime] = None
    payment_transaction_id: Optional[str] = None
    amount: Optional[float] = None


# ── Tạo payment link ──────────────────────────────────────────────────────────

@router.post(
    "/orders/{order_id}/checkout",
    response_model=CheckoutResponse,
    summary="Tạo link thanh toán PayOS cho đơn hàng",
)
async def create_checkout(
    order_id: str,
    user: CurrentUser = Depends(allow_customer),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Tạo payment link PayOS cho một đơn hàng.

    - Nếu đơn hàng đã có link (status=PENDING), trả về link cũ luôn.
    - `delivery_fee` phải được set trước khi gọi endpoint này.
    - Sau khi tạo thành công, `payment_status` chuyển sang `PENDING`.
    - FE redirect user đến `checkout_url` hoặc hiển thị `qr_code`.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Chỉ cho phép customer của đơn hàng đó (hoặc admin)
    if user.role.value not in ("Admin",) and order.customer_id != user.id:
        raise HTTPException(status_code=403, detail="Không có quyền thanh toán đơn hàng này")

    if order.payment_status == PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Đơn hàng đã được thanh toán")

    if not order.delivery_fee or order.delivery_fee <= 0:
        raise HTTPException(status_code=400, detail="Đơn hàng chưa có phí giao hàng (delivery_fee)")

    # Nếu đã có link PENDING → trả lại link cũ
    if order.payment_status == PaymentStatus.PENDING and order.payment_checkout_url:
        return CheckoutResponse(
            order_id=order_id,
            payment_order_code=order.payment_order_code,
            checkout_url=order.payment_checkout_url,
            amount=order.delivery_fee,
            payment_status=order.payment_status,
        )

    # Tạo orderCode duy nhất (PayOS yêu cầu số nguyên dương)
    order_code = int(time.time() * 1000) % 9999999 + random.randint(1, 999)

    amount_vnd = int(order.delivery_fee)  # PayOS nhận VND nguyên

    frontend_url = settings.FRONTEND_URL.rstrip("/")
    return_url  = f"{frontend_url}/payment/success?order_id={order_id}"
    cancel_url  = f"{frontend_url}/payment/cancel?order_id={order_id}"

    try:
        payos = get_payos()
        from payos import PaymentData, ItemData
        payment_data = PaymentData(
            orderCode=order_code,
            amount=amount_vnd,
            description=f"Thanh toan don {order_id[:12]}",
            items=[
                ItemData(
                    name=f"Giao hang {order.package_label or order_id[:8]}",
                    quantity=1,
                    price=amount_vnd,
                )
            ],
            returnUrl=return_url,
            cancelUrl=cancel_url,
        )
        response = payos.createPaymentLink(payment_data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PayOS lỗi: {str(e)}")

    # Lưu vào DB
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order.payment_status       = PaymentStatus.PENDING
    order.payment_order_code   = order_code
    order.payment_checkout_url = response.checkoutUrl
    order.updated_at           = now
    await db.commit()
    await db.refresh(order)

    return CheckoutResponse(
        order_id=order_id,
        payment_order_code=order_code,
        checkout_url=response.checkoutUrl,
        qr_code=getattr(response, "qrCode", None),
        amount=order.delivery_fee,
        payment_status=PaymentStatus.PENDING,
    )


# ── Kiểm tra trạng thái ───────────────────────────────────────────────────────

@router.get(
    "/orders/{order_id}/status",
    response_model=PaymentStatusResponse,
    summary="Kiểm tra trạng thái thanh toán",
)
async def get_payment_status(
    order_id: str,
    user: CurrentUser = Depends(allow_all),
    db: AsyncSession = Depends(get_async_db),
):
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return PaymentStatusResponse(
        order_id=order_id,
        payment_status=order.payment_status or PaymentStatus.UNPAID,
        paid_at=order.paid_at,
        payment_transaction_id=order.payment_transaction_id,
        amount=order.delivery_fee,
    )


# ── Webhook từ PayOS ──────────────────────────────────────────────────────────

@router.post("/webhook", summary="PayOS webhook callback (không cần auth)")
async def payos_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """
    PayOS gọi endpoint này sau khi user thanh toán xong.

    Verify HMAC-SHA256 signature trước khi xử lý.
    Cập nhật `payment_status = PAID` và `paid_at` cho Order tương ứng.
    """
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    # ── Verify signature ────────────────────────────────────────────────────
    # PayOS gửi signature trong payload["signature"]
    received_sig = payload.get("signature", "")
    data = payload.get("data", {})

    # Neu khong co data (PayOS test call khi register webhook) -> tra ve 200 luon
    if not data:
        return JSONResponse(status_code=200, content={"message": "OK"})

    # Build canonical string: sort keys alphabet, bo qua null/undefined
    sorted_keys = sorted(data.keys())
    parts = []
    for k in sorted_keys:
        v = data[k]
        if v is None or str(v) in ("undefined", "null"):
            v = ""
        parts.append(f"{k}={v}")
    canonical = "&".join(parts)

    expected_sig = hmac.new(
        settings.PAYOS_CHECKSUM_KEY.encode(),
        canonical.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(received_sig.lower(), expected_sig.lower()):
        return JSONResponse(status_code=400, content={"error": "Invalid signature"})

    # ── Xử lý kết quả thanh toán ────────────────────────────────────────────
    order_code     = data.get("orderCode")
    status_code    = payload.get("code")   # "00" ở root level theo docs PayOS
    transaction_id = str(data.get("reference", "") or data.get("transactionDateTime", ""))

    if not order_code:
        return JSONResponse(status_code=200, content={"message": "Ignored"})

    # Tìm Order theo payment_order_code
    result = await db.execute(
        select(Order).where(Order.payment_order_code == int(order_code))
    )
    order = result.scalar_one_or_none()
    if not order:
        return JSONResponse(status_code=200, content={"message": "Order not found, ignored"})

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if status_code == "00":
        order.payment_status        = PaymentStatus.PAID
        order.payment_transaction_id = transaction_id
        order.paid_at               = now
        order.updated_at            = now
    else:
        order.payment_status = PaymentStatus.FAILED
        order.updated_at     = now

    await db.commit()
    return JSONResponse(status_code=200, content={"message": "OK"})


# ── Đăng ký Webhook URL với PayOS ────────────────────────────────────────────

@router.post(
    "/register-webhook",
    summary="Đăng ký Webhook URL với PayOS (chạy 1 lần)",
    include_in_schema=True,
)
async def register_webhook(
    user: CurrentUser = Depends(allow_admin),
):
    """
    Gọi PayOS API để đăng ký webhook URL.
    Chỉ cần chạy 1 lần sau khi deploy.
    Webhook URL: {FRONTEND_URL}/api/v1/payments/webhook
    """
    import httpx
    webhook_url = f"{settings.BACKEND_URL.rstrip(chr(47))}/api/v1/payments/webhook"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api-merchant.payos.vn/confirm-webhook",
                json={"webhookUrl": webhook_url},
                headers={
                    "x-client-id": settings.PAYOS_CLIENT_ID,
                    "x-api-key": settings.PAYOS_API_KEY,
                },
                timeout=10,
            )
            return {"status": resp.status_code, "response": resp.json()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Huỷ payment link ──────────────────────────────────────────────────────────

@router.post(
    "/orders/{order_id}/cancel",
    summary="Huỷ payment link PayOS",
)
async def cancel_payment(
    order_id: str,
    user: CurrentUser = Depends(allow_customer),
    db: AsyncSession = Depends(get_async_db),
):
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.payment_status == PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Đơn hàng đã thanh toán, không thể huỷ")

    if not order.payment_order_code:
        raise HTTPException(status_code=400, detail="Chưa có payment link để huỷ")

    try:
        payos = get_payos()
        payos.cancelPaymentLink(order.payment_order_code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PayOS lỗi: {str(e)}")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order.payment_status = PaymentStatus.CANCELLED
    order.updated_at     = now
    await db.commit()

    return {"message": "Đã huỷ payment link", "order_id": order_id}
